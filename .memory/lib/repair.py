"""
EF Memory — Post-Merge Repair

Repairs events.jsonl after git merge operations:
  1. Removes git merge conflict markers (<<<<<<, ======, >>>>>>)
  2. Deduplicates entries by ID (newest created_at wins)
  3. Sorts entries by created_at
  4. Detects orphan sources (files referenced but missing)
  5. Creates backup before modification

Usage:
    from lib.repair import repair_events, detect_merge_markers
    report = repair_events(events_path, project_root)

No external dependencies — pure Python stdlib.
"""

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("efm.repair")

# Git merge conflict marker patterns
_MERGE_MARKER_RE = re.compile(r"^(<{7}|={7}|>{7})(\s|$)")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OrphanSource:
    """An entry whose source file cannot be found."""
    entry_id: str
    title: str
    missing_sources: List[str]


@dataclass
class RepairReport:
    """Result of a repair operation."""
    merge_markers_removed: int = 0
    duplicate_ids_resolved: int = 0
    entries_before: int = 0          # total raw lines (valid JSON)
    entries_after: int = 0           # unique entries after dedup
    orphan_sources: List[OrphanSource] = field(default_factory=list)
    backup_path: str = ""
    dry_run: bool = False
    duration_ms: float = 0.0
    errors: List[str] = field(default_factory=list)

    @property
    def needs_repair(self) -> bool:
        return self.merge_markers_removed > 0 or self.duplicate_ids_resolved > 0


# ---------------------------------------------------------------------------
# Detection (read-only)
# ---------------------------------------------------------------------------

def detect_merge_markers(events_path: Path) -> int:
    """
    Fast check: count git merge conflict marker lines in events.jsonl.

    Returns the number of marker lines found (0 = clean).
    Designed for startup health check (<1ms on typical files).
    """
    if not events_path.exists():
        return 0

    count = 0
    try:
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                if _MERGE_MARKER_RE.match(line):
                    count += 1
    except OSError:
        pass
    return count


# ---------------------------------------------------------------------------
# Core repair
# ---------------------------------------------------------------------------

def _read_raw_lines(events_path: Path) -> Tuple[List[dict], int, int]:
    """
    Read events.jsonl, separating valid entries from merge markers.

    Returns:
        (entries_with_position, total_valid, markers_found)
        Each entry has an extra '_pos' key with its line index.
    """
    entries: List[dict] = []
    markers = 0
    total_valid = 0

    if not events_path.exists():
        return entries, 0, 0

    with open(events_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            stripped = line.strip()
            if not stripped:
                continue
            if _MERGE_MARKER_RE.match(stripped):
                markers += 1
                continue
            try:
                entry = json.loads(stripped)
                entry_id = entry.get("id")
                if entry_id:
                    entry["_pos"] = i  # track file position for tiebreaking
                    entries.append(entry)
                    total_valid += 1
            except json.JSONDecodeError:
                logger.debug("Skipping invalid JSON at line %d", i + 1)

    return entries, total_valid, markers


def _resolve_by_newest(entries: List[dict]) -> Tuple[List[dict], int]:
    """
    Deduplicate entries by ID, keeping the one with the newest created_at.

    For entries with the same created_at (or missing timestamps), the one
    appearing later in the file (higher _pos) wins — consistent with the
    original latest-wins semantics.

    Returns:
        (unique_entries, duplicates_resolved)
    """
    best: Dict[str, dict] = {}

    for entry in entries:
        entry_id = entry["id"]
        if entry_id not in best:
            best[entry_id] = entry
            continue

        existing = best[entry_id]
        new_ts = entry.get("created_at", "")
        old_ts = existing.get("created_at", "")

        if new_ts > old_ts:
            best[entry_id] = entry
        elif new_ts == old_ts:
            # Same timestamp: file position tiebreak (later wins)
            if entry.get("_pos", 0) > existing.get("_pos", 0):
                best[entry_id] = entry

    duplicates = len(entries) - len(best)
    # Remove internal _pos marker before returning
    result = []
    for entry in best.values():
        clean = {k: v for k, v in entry.items() if k != "_pos"}
        result.append(clean)

    return result, duplicates


def _check_orphan_sources(
    entries: List[dict],
    project_root: Path,
) -> List[OrphanSource]:
    """
    Check which entries reference source files that don't exist.

    Only checks file-path sources (not commit/PR references).
    """
    orphans: List[OrphanSource] = []

    for entry in entries:
        sources = entry.get("source", [])
        if not sources:
            continue

        missing = []
        for src in sources:
            # Skip non-file sources (commits, PRs, URLs)
            if src.startswith("commit ") or src.startswith("PR #"):
                continue
            # Extract file path: "path/file.py:L10-L20" -> "path/file.py"
            # Also: "path/file.py#heading:L10" -> "path/file.py"
            file_part = src.split("#")[0].split(":")[0].split("::")[0]
            if not file_part:
                continue
            full_path = project_root / file_part
            if not full_path.exists():
                missing.append(src)

        if missing:
            orphans.append(OrphanSource(
                entry_id=entry.get("id", "?"),
                title=entry.get("title", "?")[:80],
                missing_sources=missing,
            ))

    return orphans


def repair_events(
    events_path: Path,
    project_root: Path,
    dry_run: bool = False,
    create_backup: bool = True,
) -> RepairReport:
    """
    Repair events.jsonl after git merge conflicts.

    Steps:
      1. Read all lines, strip merge markers
      2. Deduplicate by ID (newest created_at wins)
      3. Sort by created_at
      4. Detect orphan sources
      5. Atomically rewrite (unless dry_run)

    Args:
        events_path: Path to events.jsonl
        project_root: Project root for source file checks
        dry_run: If True, report issues without modifying files
        create_backup: If True, create .bak before writing

    Returns:
        RepairReport with all findings
    """
    start = time.monotonic()
    report = RepairReport(dry_run=dry_run)

    if not events_path.exists():
        report.duration_ms = (time.monotonic() - start) * 1000
        return report

    try:
        # Step 1: Read and strip markers
        raw_entries, total_valid, markers = _read_raw_lines(events_path)
        report.merge_markers_removed = markers
        report.entries_before = total_valid

        # Step 2: Deduplicate
        unique_entries, dups = _resolve_by_newest(raw_entries)
        report.duplicate_ids_resolved = dups
        report.entries_after = len(unique_entries)

        # Step 3: Sort by created_at
        unique_entries.sort(key=lambda e: e.get("created_at", ""))

        # Step 4: Check orphan sources
        report.orphan_sources = _check_orphan_sources(unique_entries, project_root)

        # Step 5: Write (unless dry_run)
        if not dry_run and report.needs_repair:
            if create_backup:
                bak_path = events_path.with_suffix(".jsonl.bak")
                shutil.copy2(str(events_path), str(bak_path))
                report.backup_path = str(bak_path)

            # Atomic rewrite
            tmp_path = events_path.with_suffix(".jsonl.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                for entry in unique_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            os.replace(str(tmp_path), str(events_path))

            # Reset vectordb sync cursor (force re-index)
            try:
                db_path = events_path.parent / "vectors.db"
                if db_path.exists():
                    from .vectordb import VectorDB
                    db = VectorDB(db_path)
                    db.open()
                    try:
                        db.set_sync_cursor(0)
                    finally:
                        db.close()
            except Exception as e:
                logger.warning("Could not reset sync cursor: %s", e)

    except Exception as e:
        report.errors.append(str(e))
        logger.error("Repair failed: %s", e)

    report.duration_ms = (time.monotonic() - start) * 1000
    return report
