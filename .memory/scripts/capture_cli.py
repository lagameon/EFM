#!/usr/bin/env python3
"""
EF Memory V2 — Draft Capture CLI

Manage memory draft files (human-in-the-loop capture queue).

Usage:
    python3 .memory/scripts/capture_cli.py list               # List pending drafts
    python3 .memory/scripts/capture_cli.py review              # Review with verification
    python3 .memory/scripts/capture_cli.py approve <filename>  # Approve -> events.jsonl
    python3 .memory/scripts/capture_cli.py reject <filename>   # Delete draft
    python3 .memory/scripts/capture_cli.py --help              # Show help
"""

import json
import logging
import sys
from pathlib import Path

# Add .memory/ to import path
_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_MEMORY_DIR))

from lib.auto_capture import (
    approve_draft,
    list_drafts,
    reject_draft,
    review_drafts,
)


def _parse_args(argv: list) -> dict:
    """Simple argument parser."""
    args = {
        "command": None,    # list, review, approve, reject
        "filename": None,   # for approve/reject
        "help": False,
    }

    positionals = []
    for arg in argv:
        if arg in ("--help", "-h"):
            args["help"] = True
        elif arg.startswith("--"):
            print(f"ERROR: Unknown option: {arg}")
            sys.exit(1)
        else:
            positionals.append(arg)

    if positionals:
        args["command"] = positionals[0]
    if len(positionals) > 1:
        args["filename"] = positionals[1]

    return args


def _cmd_list(drafts_dir: Path):
    """List pending drafts."""
    drafts = list_drafts(drafts_dir)

    if not drafts:
        print("No pending drafts.")
        return

    print(f"Pending drafts: {len(drafts)}\n")
    for i, draft in enumerate(drafts, 1):
        entry = draft.entry
        entry_id = entry.get("id", "<no id>")
        title = entry.get("title", "<no title>")
        classification = entry.get("classification", "?")
        severity = entry.get("severity", "")
        ts = draft.capture_timestamp or ""

        severity_str = f" [{severity}]" if severity else ""
        print(f"  {i}. [{classification.upper()}{severity_str}] {title}")
        print(f"     ID:   {entry_id}")
        print(f"     File: {draft.filename}")
        print(f"     Date: {ts[:19] if ts else '?'}")
        print()


def _cmd_review(drafts_dir: Path, events_path: Path, project_root: Path, config: dict):
    """Review drafts with full verification."""
    report = review_drafts(drafts_dir, events_path, project_root, config)

    if report.total_drafts == 0:
        print("No pending drafts to review.")
        return

    print(f"Review: {report.total_drafts} draft(s)\n")

    for draft, verify_result in zip(report.drafts, report.verification_results):
        entry = draft.entry
        entry_id = entry.get("id", "<no id>")
        title = entry.get("title", "<no title>")
        overall = verify_result["overall"]

        print(f"  [{overall}] {title}")
        print(f"     ID:   {entry_id}")
        print(f"     File: {draft.filename}")

        # Show issues
        schema = verify_result["schema"]
        for err in schema.errors:
            print(f"     ERROR: {err}")
        for warn in schema.warnings:
            print(f"     WARN:  {warn}")

        for sr in verify_result["sources"]:
            if sr.status != "OK":
                print(f"     SOURCE [{sr.status}]: {sr.message}")

        if verify_result["dedup"].is_duplicate:
            sim = verify_result["dedup"].similar_entries[0]
            print(f"     DEDUP: Similar to {sim[0]} (score: {sim[1]:.2f})")

        print()

    print(f"  Valid: {report.valid_drafts}  Invalid: {report.invalid_drafts}")
    print(f"  Duration: {report.duration_ms:.0f}ms")


def _cmd_approve(drafts_dir: Path, events_path: Path, filename: str):
    """Approve a draft."""
    draft_path = drafts_dir / filename

    if not draft_path.exists():
        # Try adding .json extension
        if not filename.endswith(".json"):
            draft_path = drafts_dir / f"{filename}.json"

    result = approve_draft(draft_path, events_path)

    if result.success:
        print(f"Approved: {result.entry_id}")
        print(f"  Appended to: {events_path}")
        print(f"  Draft deleted: {filename}")
    else:
        print(f"FAILED: {result.message}")
        sys.exit(1)


def _cmd_reject(drafts_dir: Path, filename: str):
    """Reject (delete) a draft."""
    draft_path = drafts_dir / filename

    if not draft_path.exists() and not filename.endswith(".json"):
        draft_path = drafts_dir / f"{filename}.json"

    if reject_draft(draft_path):
        print(f"Rejected: {filename}")
    else:
        print(f"Draft not found: {filename}")
        sys.exit(1)


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    args = _parse_args(sys.argv[1:])

    if args["help"] or args["command"] is None:
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

    cmd = args["command"]

    if cmd == "list":
        _cmd_list(drafts_dir)
    elif cmd == "review":
        _cmd_review(drafts_dir, events_path, project_root, config)
    elif cmd == "approve":
        if not args["filename"]:
            print("ERROR: approve requires a filename")
            print("Usage: capture_cli.py approve <filename>")
            sys.exit(1)
        _cmd_approve(drafts_dir, events_path, args["filename"])
    elif cmd == "reject":
        if not args["filename"]:
            print("ERROR: reject requires a filename")
            print("Usage: capture_cli.py reject <filename>")
            sys.exit(1)
        _cmd_reject(drafts_dir, args["filename"])
    else:
        print(f"ERROR: Unknown command: {cmd}")
        print("Valid commands: list, review, approve, reject")
        sys.exit(1)


if __name__ == "__main__":
    main()
