#!/usr/bin/env python3
"""
EF Memory V3 — Automation Pipeline CLI

Run sync + rules generation pipeline, or startup health check.

Usage:
    python3 .memory/scripts/pipeline_cli.py                    # Run full pipeline
    python3 .memory/scripts/pipeline_cli.py --startup          # Startup status check
    python3 .memory/scripts/pipeline_cli.py --sync-only        # Just sync embeddings
    python3 .memory/scripts/pipeline_cli.py --rules-only       # Just generate rules
    python3 .memory/scripts/pipeline_cli.py --harvest-only     # Just harvest working memory
    python3 .memory/scripts/pipeline_cli.py --help             # Show help
"""

import json
import logging
import sys
from pathlib import Path

# Add .memory/ to import path
_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_MEMORY_DIR))

from lib.auto_sync import check_startup, run_pipeline


def _parse_args(argv: list) -> dict:
    """Simple argument parser."""
    args = {
        "startup": False,
        "sync_only": False,
        "rules_only": False,
        "harvest_only": False,
        "help": False,
    }
    for arg in argv:
        if arg in ("--help", "-h"):
            args["help"] = True
        elif arg == "--startup":
            args["startup"] = True
        elif arg == "--sync-only":
            args["sync_only"] = True
        elif arg == "--rules-only":
            args["rules_only"] = True
        elif arg == "--harvest-only":
            args["harvest_only"] = True
        elif arg.startswith("--"):
            print(f"ERROR: Unknown option: {arg}")
            sys.exit(1)
    return args


def _print_startup(report):
    """Print the startup health check report."""
    print("EF Memory Startup Check")
    print(f"  Total entries:    {report.total_entries}")
    print(f"  Pending drafts:   {report.pending_drafts}")
    print(f"  Stale entries:    {report.stale_entries}")
    print(f"  Source warnings:  {report.source_warnings}")
    if report.active_session:
        print(f"  Active session:   \"{report.active_session_task}\" ({report.active_session_phases})")
    print()
    print(f"  {report.hint}")
    print(f"\n  Duration: {report.duration_ms:.0f}ms")


def _print_pipeline(report):
    """Print the pipeline report."""
    print("\nPipeline complete")
    print(f"  Steps run:      {report.steps_run}")
    print(f"  Succeeded:      {report.steps_succeeded}")
    print(f"  Failed:         {report.steps_failed}")
    print(f"  Skipped:        {report.steps_skipped}")

    for sr in report.step_results:
        status = "OK" if sr.success else ("SKIP" if sr.skipped else "FAIL")
        print(f"\n  [{status}] {sr.step}")

        if sr.error:
            print(f"    Error: {sr.error}")
        if sr.skip_reason:
            print(f"    Reason: {sr.skip_reason}")

        if sr.details:
            for key, value in sr.details.items():
                if key == "errors" and value:
                    print(f"    Errors: {value}")
                elif isinstance(value, (dict, list)):
                    continue  # Skip complex values in summary
                else:
                    print(f"    {key}: {value}")

    print(f"\n  Duration: {report.duration_ms:.0f}ms")


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
    from lib.config_presets import load_config
    config = load_config(config_path)

    # --- Mode: --startup ---
    if args["startup"]:
        report = check_startup(events_path, drafts_dir, project_root, config)
        _print_startup(report)
        sys.exit(0)

    # --- Mode: --sync-only / --rules-only / default ---
    if not events_path.exists() or events_path.stat().st_size <= 1:
        print("No entries in events.jsonl. Nothing to process.")
        sys.exit(0)

    steps = None
    if args["sync_only"]:
        steps = ["sync_embeddings"]
    elif args["rules_only"]:
        steps = ["generate_rules"]
    elif args["harvest_only"]:
        steps = ["harvest_check"]

    report = run_pipeline(events_path, config, project_root, steps=steps)
    _print_pipeline(report)


if __name__ == "__main__":
    main()
