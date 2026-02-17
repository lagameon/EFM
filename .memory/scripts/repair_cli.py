#!/usr/bin/env python3
"""
EF Memory — Post-Merge Repair CLI

Repair events.jsonl after git merge conflicts: strip markers, dedup, sort.

Usage:
    python3 .memory/scripts/repair_cli.py              # Run repair
    python3 .memory/scripts/repair_cli.py --dry-run    # Show what would happen
    python3 .memory/scripts/repair_cli.py --no-backup  # Skip .bak creation
    python3 .memory/scripts/repair_cli.py --help       # Show help
"""

import json
import sys
from pathlib import Path

# Add .memory/ to import path
_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
_PROJECT_ROOT = _MEMORY_DIR.parent
sys.path.insert(0, str(_MEMORY_DIR))

from lib.repair import repair_events


def _parse_args(argv: list) -> dict:
    """Simple argument parser."""
    args = {
        "dry_run": False,
        "no_backup": False,
        "help": False,
    }
    for arg in argv[1:]:
        if arg == "--dry-run":
            args["dry_run"] = True
        elif arg == "--no-backup":
            args["no_backup"] = True
        elif arg in ("--help", "-h"):
            args["help"] = True
    return args


def main():
    args = _parse_args(sys.argv)

    if args["help"]:
        print(__doc__)
        sys.exit(0)

    events_path = _MEMORY_DIR / "events.jsonl"

    report = repair_events(
        events_path=events_path,
        project_root=_PROJECT_ROOT,
        dry_run=args["dry_run"],
        create_backup=not args["no_backup"],
    )

    output = json.dumps({
        "merge_markers_removed": report.merge_markers_removed,
        "duplicate_ids_resolved": report.duplicate_ids_resolved,
        "entries_before": report.entries_before,
        "entries_after": report.entries_after,
        "orphan_sources": [
            {
                "entry_id": o.entry_id,
                "title": o.title,
                "missing": o.missing_sources,
            }
            for o in report.orphan_sources
        ],
        "backup_path": report.backup_path,
        "dry_run": report.dry_run,
        "needs_repair": report.needs_repair,
        "duration_ms": round(report.duration_ms, 1),
        "errors": report.errors,
    }, indent=2)
    print(output)


if __name__ == "__main__":
    main()
