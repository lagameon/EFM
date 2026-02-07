"""
EF Memory V2 — Sync Engine

One-way sync: events.jsonl → vectors.db

Algorithm:
1. Read sync cursor (last processed line) from vectors.db
2. Read events.jsonl from cursor (incremental) or from start (full)
3. Resolve latest-wins: same entry_id → last occurrence wins
4. For each active entry:
   a. Compute text_hash — skip if unchanged
   b. If embedder available → generate embedding → upsert vector
   c. Always update FTS index (even without embedder)
5. Mark deprecated entries in vectors.db
6. Update sync cursor

Idempotent: running twice with no changes produces no writes.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .embedder import EmbeddingProvider
from .vectordb import VectorDB
from .text_builder import build_embedding_text, build_fts_fields

logger = logging.getLogger("efm.sync")


@dataclass
class SyncReport:
    """Summary of a sync operation."""
    mode: str = "incremental"     # "full" or "incremental"
    entries_scanned: int = 0
    entries_added: int = 0
    entries_updated: int = 0
    entries_skipped: int = 0      # unchanged hash
    entries_deprecated: int = 0
    entries_fts_only: int = 0     # FTS updated without vector (no embedder)
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0


def _compute_text_hash(text: str) -> str:
    """SHA-256 hash of the embedding text (first 16 hex chars)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read_events(
    events_path: Path,
    start_line: int = 0,
) -> tuple[dict[str, dict], int]:
    """
    Read events.jsonl and resolve latest-wins semantics.

    Args:
        events_path: Path to events.jsonl
        start_line: Line number to start reading from (0-based)

    Returns:
        (entries_dict, total_lines)
        entries_dict: {entry_id: latest_entry_dict}
        total_lines: total number of non-empty lines in file
    """
    entries: dict[str, dict] = {}
    total_lines = 0

    if not events_path.exists():
        return entries, 0

    with open(events_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            total_lines = i + 1  # Track total lines (including blank) for cursor
            line = line.strip()
            if not line:
                continue

            if i < start_line:
                continue

            try:
                entry = json.loads(line)
                entry_id = entry.get("id")
                if entry_id:
                    # Store with line number for cursor tracking
                    entry["_line"] = i
                    entries[entry_id] = entry
            except json.JSONDecodeError as e:
                logger.warning(f"Skipping invalid JSON at line {i + 1}: {e}")

    return entries, total_lines


def sync_embeddings(
    events_path: Path,
    vectordb: VectorDB,
    embedder: Optional[EmbeddingProvider] = None,
    force_full: bool = False,
    batch_size: int = 20,
) -> SyncReport:
    """
    Synchronize events.jsonl → vectors.db.

    Args:
        events_path: Path to events.jsonl
        vectordb: Open VectorDB instance
        embedder: Optional embedding provider (None = FTS-only mode)
        force_full: If True, ignore cursor and reprocess all entries
        batch_size: Number of texts to embed per API call

    Returns:
        SyncReport with operation summary
    """
    report = SyncReport()
    start_time = time.monotonic()

    # Determine sync mode
    cursor = None if force_full else vectordb.get_sync_cursor()
    start_line = cursor if cursor is not None else 0
    report.mode = "incremental" if cursor is not None and not force_full else "full"

    # Read entries
    entries, total_lines = _read_events(events_path, start_line=start_line)
    report.entries_scanned = len(entries)

    if not entries:
        report.duration_ms = (time.monotonic() - start_time) * 1000
        return report

    # Separate active and deprecated entries
    active_entries: dict[str, dict] = {}
    deprecated_ids: list[str] = []

    for entry_id, entry in entries.items():
        if entry.get("deprecated", False):
            deprecated_ids.append(entry_id)
        else:
            active_entries[entry_id] = entry

    # Handle deprecated entries (mark in vectors + remove from FTS)
    vectordb.begin_batch()
    for entry_id in deprecated_ids:
        vectordb.mark_deprecated(entry_id)
        vectordb.delete_fts(entry_id)
        report.entries_deprecated += 1
    vectordb.end_batch()

    # Prepare embedding batches
    to_embed: list[tuple[str, dict, str, str]] = []  # (entry_id, entry, text, text_hash)

    vectordb.begin_batch()
    for entry_id, entry in active_entries.items():
        embed_text = build_embedding_text(entry)
        text_hash = _compute_text_hash(embed_text)

        # Always update FTS (cheap, local)
        fts_fields = build_fts_fields(entry)
        vectordb.upsert_fts(
            entry_id=entry_id,
            title=fts_fields["title"],
            text=fts_fields["text"],
            tags=fts_fields["tags"],
        )

        # Check if vector needs update
        if not vectordb.needs_update(entry_id, text_hash):
            report.entries_skipped += 1
            continue

        if embedder is None:
            report.entries_fts_only += 1
            continue

        to_embed.append((entry_id, entry, embed_text, text_hash))
    vectordb.end_batch()

    # Batch embed and store
    for batch_start in range(0, len(to_embed), batch_size):
        batch = to_embed[batch_start:batch_start + batch_size]
        texts = [item[2] for item in batch]

        try:
            results = embedder.embed_documents(texts)
        except Exception as e:
            error_msg = f"Batch embed failed (items {batch_start}-{batch_start + len(batch)}): {e}"
            logger.error(error_msg)
            report.errors.append(error_msg)
            continue

        vectordb.begin_batch()
        for (entry_id, entry, embed_text, text_hash), result in zip(batch, results):
            try:
                is_new = not vectordb.has_vector(entry_id)
                vectordb.upsert_vector(
                    entry_id=entry_id,
                    text_hash=text_hash,
                    provider=embedder.provider_id,
                    model=embedder.model_name,
                    dimensions=result.dimensions,
                    embedding=result.vector,
                    deprecated=False,
                )
                if is_new:
                    report.entries_added += 1
                else:
                    report.entries_updated += 1
            except Exception as e:
                error_msg = f"Failed to store vector for {entry_id}: {e}"
                logger.error(error_msg)
                report.errors.append(error_msg)
        vectordb.end_batch()

    # Update sync cursor only if no errors occurred.
    # When errors exist, don't advance — failed entries will be retried
    # on the next incremental sync.
    if not report.errors:
        vectordb.set_sync_cursor(total_lines)
    else:
        logger.warning(
            f"Sync had {len(report.errors)} errors — cursor NOT advanced. "
            f"Failed entries will be retried on next sync."
        )

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report
