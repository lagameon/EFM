#!/usr/bin/env python3
"""
EF Memory V2 — Generate Rules CLI

Generate Claude Code rule files from Hard memory entries.

Usage:
    python3 .memory/scripts/generate_rules_cli.py               # Generate all rules
    python3 .memory/scripts/generate_rules_cli.py --dry-run      # Preview without writing
    python3 .memory/scripts/generate_rules_cli.py --clean        # Remove generated files
    python3 .memory/scripts/generate_rules_cli.py --stats        # Show current rule stats
    python3 .memory/scripts/generate_rules_cli.py --help         # Show help
"""

import sys
import json
import logging
from pathlib import Path

# Add .memory/ to import path
_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_MEMORY_DIR))

from lib.generate_rules import generate_rule_files, clean_rule_files


def _parse_args(argv: list) -> dict:
    """Simple argument parser."""
    args = {
        "dry_run": False,
        "clean": False,
        "stats": False,
        "help": False,
    }
    for arg in argv:
        if arg in ("--help", "-h"):
            args["help"] = True
        elif arg == "--dry-run":
            args["dry_run"] = True
        elif arg == "--clean":
            args["clean"] = True
        elif arg == "--stats":
            args["stats"] = True
        elif arg.startswith("--"):
            print(f"ERROR: Unknown option: {arg}")
            sys.exit(1)
    return args


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
    output_dir = project_root / ".claude" / "rules" / "ef-memory"

    # Load config
    config_path = _MEMORY_DIR / "config.json"
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text())

    # Handle --clean
    if args["clean"]:
        removed = clean_rule_files(output_dir)
        if removed:
            print(f"Cleaned {len(removed)} files:")
            for f in removed:
                print(f"  - {f}")
        else:
            print("No generated rule files to clean.")
        sys.exit(0)

    # Handle --stats
    if args["stats"]:
        if not output_dir.exists():
            print("No generated rules directory found.")
            print(f"  Expected: {output_dir}")
            print("  Run without --stats to generate rules.")
            sys.exit(0)

        md_files = list(output_dir.glob("*.md"))
        if not md_files:
            print("No rule files found in generated directory.")
            sys.exit(0)

        print(f"=== EF Memory Generated Rules ===")
        print(f"  Directory: {output_dir}")
        print(f"  Files: {len(md_files)}")
        for f in sorted(md_files):
            lines = f.read_text().splitlines()
            # Count rule sections (## headers)
            rule_count = sum(1 for l in lines if l.startswith("## "))
            print(f"    {f.name}: {rule_count} rules")
        sys.exit(0)

    # Check events.jsonl exists
    if not events_path.exists() or events_path.stat().st_size <= 1:
        print("No entries in events.jsonl. Nothing to generate.")
        sys.exit(0)

    # Generate
    report = generate_rule_files(
        events_path=events_path,
        output_dir=output_dir,
        config=config,
        dry_run=args["dry_run"],
    )

    # Report
    mode_str = "DRY RUN" if report.dry_run else "Generated"
    print(f"\n{mode_str}: Rule files from Hard memory entries")
    print(f"  Hard entries found: {report.entries_hard}")
    print(f"  Entries injected:   {report.entries_injected}")
    print(f"  Domains:            {len(report.domains)}")

    if report.domains:
        print(f"\n  Domain breakdown:")
        for domain, count in sorted(report.domains.items()):
            print(f"    {domain}: {count} entries")

    if report.files_written:
        action = "Would write" if report.dry_run else "Wrote"
        print(f"\n  {action}:")
        for f in report.files_written:
            print(f"    {f}")

    if report.files_removed:
        print(f"\n  Cleaned:")
        for f in report.files_removed:
            print(f"    {f}")

    print(f"\n  Duration: {report.duration_ms:.0f}ms")


if __name__ == "__main__":
    main()
