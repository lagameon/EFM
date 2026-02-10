"""
EF Memory — Compaction + Time-Sharded Archive (M11)

Compaction resolves events.jsonl into a clean hot file:
  - One line per unique entry ID (latest-wins)
  - No deprecated entries
  - Sorted by created_at (optional)

Removed lines are archived by quarter into .memory/archive/events_YYYYQN.jsonl.
A compaction_log.jsonl records each operation for audit.

Usage:
  compact(events_path, archive_dir, config) -> CompactionReport
  get_compaction_stats(events_path)          -> CompactionStats  (read-only)

No external dependencies — pure Python stdlib.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("efm.compaction")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CompactionReport:
    """Result of a compaction operation."""
    lines_before: int = 0
    lines_after: int = 0
    entries_kept: int = 0
    entries_archived: int = 0       # unique IDs moved to archive
    lines_archived: int = 0         # total raw lines moved
    quarters_touched: List[str] = field(default_factory=list)
    archive_dir: str = ""
    duration_ms: float = 0.0


@dataclass
class CompactionStats:
    """Read-only statistics about events.jsonl waste level."""
    total_lines: int = 0
    unique_entries: int = 0
    active_entries: int = 0
    deprecated_entries: int = 0
    superseded_lines: int = 0       # lines that are older versions of an ID
    waste_ratio: float = 0.0        # total_lines / active_entries (1.0 = no waste)
    suggest_compact: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _quarter_key(created_at: Optional[str]) -> str:
    """Extract quarter key from ISO 8601 timestamp.

    '2026-02-01T14:30:00Z' -> '2026Q1'
    Returns 'unknown' if the timestamp cannot be parsed.
    """
    if not created_at:
        return "unknown"
    try:
        # Handle both 'Z' suffix and '+00:00' format
        ts = created_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}Q{q}"
    except (ValueError, TypeError):
        return "unknown"


def _read_all_lines(events_path: Path) -> List[str]:
    """Read all non-empty lines from events.jsonl as raw strings."""
    if not events_path.exists():
        return []
    lines = []
    with open(events_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    return lines


def _resolve_latest_wins(raw_lines: List[str]) -> Tuple[
    Dict[str, dict],                    # latest_wins: {id: entry}
    Dict[str, int],                      # latest_line_idx: {id: line_index}
    List[Tuple[int, str, dict]],         # all_parsed: [(line_idx, entry_id, entry)]
]:
    """Build latest-wins dict, track line indices, and return all parsed entries.

    The all_parsed list eliminates the need for callers to re-parse raw lines
    when identifying superseded entries (saves one full JSON parse pass).
    """
    latest: Dict[str, dict] = {}
    latest_idx: Dict[str, int] = {}
    all_parsed: List[Tuple[int, str, dict]] = []
    for i, line in enumerate(raw_lines):
        try:
            entry = json.loads(line)
            entry_id = entry.get("id")
            if entry_id:
                latest[entry_id] = entry
                latest_idx[entry_id] = i
                all_parsed.append((i, entry_id, entry))
        except json.JSONDecodeError:
            continue
    return latest, latest_idx, all_parsed


def _archive_lines(
    archive_entries: List[dict],
    archive_dir: Path,
) -> Dict[str, int]:
    """Group entries by quarter and append to archive files.

    Returns: {quarter_key: count_appended}
    """
    if not archive_entries:
        return {}

    # Group by quarter
    by_quarter: Dict[str, List[dict]] = {}
    for entry in archive_entries:
        qk = _quarter_key(entry.get("created_at"))
        by_quarter.setdefault(qk, []).append(entry)

    archive_dir.mkdir(parents=True, exist_ok=True)
    counts: Dict[str, int] = {}
    for quarter, entries in sorted(by_quarter.items()):
        archive_path = archive_dir / f"events_{quarter}.jsonl"
        with open(archive_path, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        counts[quarter] = len(entries)

    return counts


def _atomic_rewrite(
    events_path: Path,
    keep_entries: List[dict],
    sort_by_created_at: bool = True,
) -> None:
    """Atomically rewrite events.jsonl with only the keep entries.

    Writes to a .tmp file first, then uses os.replace() for atomic swap.
    """
    if sort_by_created_at:
        keep_entries = sorted(
            keep_entries,
            key=lambda e: e.get("created_at", ""),
        )

    tmp_path = events_path.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for entry in keep_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    os.replace(str(tmp_path), str(events_path))


def _reset_sync_cursor(events_path: Path) -> None:
    """Reset the vectordb sync cursor to force a full re-sync after compaction."""
    try:
        db_path = events_path.parent / "vectors.db"
        if not db_path.exists():
            return
        from .vectordb import VectorDB
        db = VectorDB(db_path)
        db.open()
        try:
            db.set_sync_cursor(0)
        finally:
            db.close()
    except Exception:
        pass  # vectors.db is optional — failing is fine


def _reset_evolution_checkpoint(events_path: Path) -> None:
    """Delete the evolution checkpoint to force a full re-analysis after compaction."""
    try:
        cp_path = events_path.parent / "evolution_checkpoint.json"
        if cp_path.exists():
            cp_path.unlink()
    except OSError:
        pass  # evolution checkpoint is optional


def _log_compaction(archive_dir: Path, report: CompactionReport) -> None:
    """Append a compaction log entry to archive/compaction_log.jsonl."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    log_path = archive_dir / "compaction_log.jsonl"
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lines_before": report.lines_before,
        "lines_after": report.lines_after,
        "entries_kept": report.entries_kept,
        "entries_archived": report.entries_archived,
        "lines_archived": report.lines_archived,
        "quarters_touched": report.quarters_touched,
        "duration_ms": round(report.duration_ms, 1),
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_compaction_stats(events_path: Path, threshold: float = 2.0) -> CompactionStats:
    """Read-only analysis of events.jsonl waste level.

    Fast — only reads the file once, no writes.
    Used by check_startup() for the compaction hint.
    """
    stats = CompactionStats()
    raw_lines = _read_all_lines(events_path)
    stats.total_lines = len(raw_lines)

    if not raw_lines:
        return stats

    latest, latest_idx, _all_parsed = _resolve_latest_wins(raw_lines)
    stats.unique_entries = len(latest)

    # Count active vs deprecated
    for entry_id, entry in latest.items():
        if entry.get("deprecated", False):
            stats.deprecated_entries += 1
        else:
            stats.active_entries += 1

    # Superseded = total lines minus unique entries
    stats.superseded_lines = stats.total_lines - stats.unique_entries

    # Waste ratio: how many times larger the file is than needed
    if stats.active_entries > 0:
        stats.waste_ratio = round(stats.total_lines / stats.active_entries, 2)
    elif stats.total_lines > 0:
        # All entries are deprecated — infinite waste
        stats.waste_ratio = float(stats.total_lines)

    stats.suggest_compact = stats.waste_ratio >= threshold

    return stats


def compact(
    events_path: Path,
    archive_dir: Path,
    config: dict,
) -> CompactionReport:
    """Compact events.jsonl: resolve latest-wins, archive removed lines by quarter.

    Steps:
      1. Read all raw lines
      2. Resolve latest-wins
      3. Partition into KEEP (active latest) and ARCHIVE (everything else)
      4. Append ARCHIVE to quarterly archive files
      5. Atomic rewrite events.jsonl with KEEP entries
      6. Reset vectordb sync cursor
      7. Log compaction operation

    Returns CompactionReport with statistics.
    """
    start_time = time.monotonic()
    report = CompactionReport()
    compact_config = config.get("compaction", {})
    sort_output = compact_config.get("sort_output", True)

    # Step 1: Read all raw lines
    raw_lines = _read_all_lines(events_path)
    report.lines_before = len(raw_lines)

    if not raw_lines:
        report.duration_ms = (time.monotonic() - start_time) * 1000
        return report

    # Step 2: Resolve latest-wins (single parse pass for all lines)
    latest, latest_idx, all_parsed = _resolve_latest_wins(raw_lines)

    # Step 3: Partition into KEEP and ARCHIVE
    keep_entries: List[dict] = []
    archive_entries: List[dict] = []
    archived_ids: set = set()

    # The latest-wins dict has one entry per unique ID
    # KEEP = latest version AND not deprecated
    for entry_id, entry in latest.items():
        if not entry.get("deprecated", False):
            keep_entries.append(entry)
        else:
            archive_entries.append(entry)
            archived_ids.add(entry_id)

    # Archive all superseded lines (older versions of any ID)
    # Uses pre-parsed entries from _resolve_latest_wins — no second JSON parse
    for i, entry_id, entry in all_parsed:
        if latest_idx.get(entry_id) != i:
            archive_entries.append(entry)
            archived_ids.add(entry_id)

    report.entries_kept = len(keep_entries)
    report.lines_archived = len(archive_entries)
    # Count unique IDs that have at least one line archived
    report.entries_archived = len(archived_ids)

    # Step 4: Archive to quarterly files
    if archive_entries:
        quarter_counts = _archive_lines(archive_entries, archive_dir)
        report.quarters_touched = sorted(quarter_counts.keys())
        report.archive_dir = str(archive_dir)

    # Step 5: Atomic rewrite
    _atomic_rewrite(events_path, keep_entries, sort_by_created_at=sort_output)
    report.lines_after = len(keep_entries)

    # Step 6: Reset sync cursor
    _reset_sync_cursor(events_path)
    # Step 6b: Reset evolution checkpoint
    _reset_evolution_checkpoint(events_path)

    # Step 7: Log
    report.duration_ms = (time.monotonic() - start_time) * 1000
    _log_compaction(archive_dir, report)

    logger.info(
        f"Compacted events.jsonl: {report.lines_before} → {report.lines_after} lines, "
        f"{report.lines_archived} archived to {report.quarters_touched}"
    )

    return report
