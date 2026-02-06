#!/usr/bin/env python3
"""
EF Memory V2 — Verify Entries CLI

Validate memory entries against schema, sources, and staleness.

Usage:
    python3 .memory/scripts/verify_cli.py                     # Verify all entries
    python3 .memory/scripts/verify_cli.py --id=<id>           # Verify single entry
    python3 .memory/scripts/verify_cli.py --drafts             # Verify pending drafts
    python3 .memory/scripts/verify_cli.py --schema-only        # Schema checks only
    python3 .memory/scripts/verify_cli.py --help               # Show help
"""

import json
import logging
import sys
from pathlib import Path

# Add .memory/ to import path
_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_MEMORY_DIR))

from lib.auto_verify import (
    validate_schema,
    verify_all_entries,
    verify_entry,
    _load_entries_latest_wins,
)
from lib.auto_capture import list_drafts


def _parse_args(argv: list) -> dict:
    """Simple argument parser."""
    args = {
        "entry_id": None,
        "drafts": False,
        "schema_only": False,
        "help": False,
    }
    for arg in argv:
        if arg in ("--help", "-h"):
            args["help"] = True
        elif arg.startswith("--id="):
            args["entry_id"] = arg[5:]
        elif arg == "--drafts":
            args["drafts"] = True
        elif arg == "--schema-only":
            args["schema_only"] = True
        elif arg.startswith("--"):
            print(f"ERROR: Unknown option: {arg}")
            sys.exit(1)
    return args


def _print_validation_result(entry_id: str, result):
    """Print a schema validation result."""
    status = "OK" if result.valid else "FAIL"
    print(f"  {entry_id}: [{status}]")
    for err in result.errors:
        print(f"    ERROR: {err}")
    for warn in result.warnings:
        print(f"    WARN:  {warn}")


def _print_verify_result(result: dict):
    """Print a full verify_entry result."""
    entry_id = result["entry_id"]
    overall = result["overall"]
    schema = result["schema"]
    sources = result["sources"]
    staleness = result["staleness"]
    dedup = result["dedup"]
    verify_status, verify_msg = result["verify_cmd"]

    print(f"\n  {entry_id}: [{overall}]")

    # Schema
    schema_status = "OK" if schema.valid else "FAIL"
    print(f"    Schema:    [{schema_status}]", end="")
    if schema.errors:
        print(f" — {len(schema.errors)} errors")
        for err in schema.errors:
            print(f"      - {err}")
    elif schema.warnings:
        print(f" — {len(schema.warnings)} warnings")
        for w in schema.warnings:
            print(f"      - {w}")
    else:
        print()

    # Sources
    for sr in sources:
        print(f"    Source:    [{sr.status}] {sr.source_type}: {sr.message}")

    # Staleness
    stale_str = "STALE" if staleness.stale else "OK"
    age = staleness.days_since_verified or staleness.days_since_created
    print(f"    Staleness: [{stale_str}] {age}d since {'verified' if staleness.days_since_verified is not None else 'created'}")

    # Dedup
    if dedup.is_duplicate:
        print(f"    Dedup:     [WARN] similar to: {dedup.similar_entries[0]}")
    else:
        print(f"    Dedup:     [OK]")

    # Verify command
    print(f"    Verify:    [{verify_status}] {verify_msg}")


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    args = _parse_args(sys.argv[1:])

    if args["help"]:
        print(__doc__.strip())
        sys.exit(0)

    # Resolve paths
    project_root = _MEMORY_DIR.parent
    events_path = _MEMORY_DIR / "events.jsonl"
    drafts_dir = _MEMORY_DIR / "drafts"

    # Load config
    config_path = _MEMORY_DIR / "config.json"
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text())

    # --- Mode: --drafts ---
    if args["drafts"]:
        drafts = list_drafts(drafts_dir)
        if not drafts:
            print("No pending drafts.")
            sys.exit(0)

        print(f"Verifying {len(drafts)} pending draft(s):\n")
        for draft in drafts:
            if args["schema_only"]:
                result = validate_schema(draft.entry)
                _print_validation_result(draft.filename, result)
            else:
                result = verify_entry(draft.entry, events_path, project_root, config)
                _print_verify_result(result)
        sys.exit(0)

    # --- Mode: --id=<id> ---
    if args["entry_id"]:
        if not events_path.exists():
            print("No events.jsonl found.")
            sys.exit(1)

        entries = _load_entries_latest_wins(events_path)
        entry = entries.get(args["entry_id"])
        if not entry:
            print(f"Entry not found: {args['entry_id']}")
            sys.exit(1)

        if args["schema_only"]:
            result = validate_schema(entry)
            _print_validation_result(args["entry_id"], result)
        else:
            result = verify_entry(entry, events_path, project_root, config)
            _print_verify_result(result)
        sys.exit(0)

    # --- Mode: default (all entries) ---
    if not events_path.exists() or events_path.stat().st_size <= 1:
        print("No entries in events.jsonl. Nothing to verify.")
        sys.exit(0)

    if args["schema_only"]:
        entries = _load_entries_latest_wins(events_path)
        print(f"Schema validation for {len(entries)} entries:\n")
        ok = warn = fail = 0
        for eid, entry in entries.items():
            if entry.get("deprecated", False):
                continue
            result = validate_schema(entry)
            _print_validation_result(eid, result)
            if result.valid and not result.warnings:
                ok += 1
            elif result.valid:
                warn += 1
            else:
                fail += 1
        print(f"\n  OK: {ok}  WARN: {warn}  FAIL: {fail}")
    else:
        report = verify_all_entries(events_path, project_root, config)
        print(f"\nVerification complete")
        print(f"  Entries checked: {report.entries_checked}")
        print(f"  Valid:           {report.entries_valid}")
        print(f"  Warnings:        {report.entries_warnings}")
        print(f"  Errors:          {report.entries_errors}")
        print(f"  Duration:        {report.duration_ms:.0f}ms")

        for result in report.results:
            if result["overall"] != "OK":
                _print_verify_result(result)


if __name__ == "__main__":
    main()
