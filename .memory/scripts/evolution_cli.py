#!/usr/bin/env python3
"""
EF Memory V2 — Evolution Report CLI

Analyze memory health: duplicates, confidence, deprecation suggestions.

Usage:
    python3 .memory/scripts/evolution_cli.py                  # Full report
    python3 .memory/scripts/evolution_cli.py --duplicates      # Find duplicates only
    python3 .memory/scripts/evolution_cli.py --confidence      # Score all entries
    python3 .memory/scripts/evolution_cli.py --deprecations    # Suggest deprecations
    python3 .memory/scripts/evolution_cli.py --merges          # Suggest merges
    python3 .memory/scripts/evolution_cli.py --id=<id>         # Confidence for single entry
    python3 .memory/scripts/evolution_cli.py --help            # Show help
"""

import json
import logging
import sys
from pathlib import Path

# Add .memory/ to import path
_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_MEMORY_DIR))

from lib.evolution import (
    build_evolution_report,
    calculate_confidence,
    find_duplicates,
    suggest_deprecations,
    suggest_merges,
)
from lib.auto_verify import _load_entries_latest_wins


def _parse_args(argv: list) -> dict:
    """Simple argument parser."""
    args = {
        "duplicates": False,
        "confidence": False,
        "deprecations": False,
        "merges": False,
        "entry_id": None,
        "help": False,
    }
    for arg in argv:
        if arg in ("--help", "-h"):
            args["help"] = True
        elif arg == "--duplicates":
            args["duplicates"] = True
        elif arg == "--confidence":
            args["confidence"] = True
        elif arg == "--deprecations":
            args["deprecations"] = True
        elif arg == "--merges":
            args["merges"] = True
        elif arg.startswith("--id="):
            args["entry_id"] = arg[5:]
        elif arg.startswith("--"):
            print(f"ERROR: Unknown option: {arg}")
            sys.exit(1)
    return args


def _print_duplicates(report):
    """Print duplicate report."""
    print(f"Duplicate Detection  [{report.mode}]")
    print(f"  Entries checked: {report.entries_checked}")
    print(f"  Groups found:   {len(report.groups)}")
    print(f"  Text threshold: {report.text_threshold}")
    if report.mode == "hybrid":
        print(f"  Embed threshold: {report.embedding_threshold}")

    for i, group in enumerate(report.groups, 1):
        print(f"\n  Group {i} ({len(group.member_ids)} entries, avg similarity: {group.avg_similarity:.2f}):")
        print(f"    Canonical: {group.canonical_id}")
        for mid in group.member_ids:
            marker = " *" if mid == group.canonical_id else "  "
            print(f"   {marker} {mid}")
        for id_a, id_b, score in group.pairwise_scores:
            print(f"      {id_a} <-> {id_b}: {score:.3f}")

    print(f"\n  Duration: {report.duration_ms:.0f}ms")


def _print_confidence(scores):
    """Print confidence scores."""
    print(f"Confidence Scores ({len(scores)} entries)")
    print()

    # Sort by score descending
    sorted_scores = sorted(scores, key=lambda s: s.score, reverse=True)

    for cs in sorted_scores:
        cls_marker = {"high": "+", "medium": "~", "low": "-"}.get(cs.classification, "?")
        print(f"  [{cls_marker}] {cs.score:.3f}  {cs.entry_id}")
        b = cs.breakdown
        print(f"       src_quality={b.source_quality:.2f}  age={b.age_factor:.2f}"
              f"  verified={b.verification_boost:.2f}  src_valid={b.source_validity:.2f}")

    # Summary
    high = sum(1 for s in scores if s.classification == "high")
    medium = sum(1 for s in scores if s.classification == "medium")
    low = sum(1 for s in scores if s.classification == "low")
    print(f"\n  High: {high}  Medium: {medium}  Low: {low}")


def _print_single_confidence(cs):
    """Print confidence for a single entry."""
    print(f"Confidence: {cs.entry_id}")
    print(f"  Score:          {cs.score:.3f} ({cs.classification})")
    print(f"  Source quality:  {cs.breakdown.source_quality:.3f}")
    print(f"  Age factor:     {cs.breakdown.age_factor:.3f}")
    print(f"  Verified boost: {cs.breakdown.verification_boost:.3f}")
    print(f"  Source validity: {cs.breakdown.source_validity:.3f}")


def _print_deprecations(report):
    """Print deprecation suggestions."""
    print(f"Deprecation Suggestions ({report.total_entries} entries checked)")
    print(f"  Candidates: {len(report.candidates)}")

    for c in report.candidates:
        print(f"\n  [{c.suggested_action.upper()}] {c.entry_id}")
        print(f"    Title:      {c.title}")
        print(f"    Confidence: {c.confidence:.3f}")
        for reason in c.reasons:
            print(f"    Reason:     {reason}")

    print(f"\n  Duration: {report.duration_ms:.0f}ms")


def _print_merges(suggestions):
    """Print merge suggestions."""
    print(f"Merge Suggestions ({len(suggestions)} groups)")

    for i, ms in enumerate(suggestions, 1):
        print(f"\n  {i}. {ms.merge_reason}")
        print(f"     Keep:      {ms.keep_id}")
        for did in ms.deprecate_ids:
            print(f"     Deprecate: {did}")
        print(f"     Similarity: {ms.group_similarity:.2f}")


def _print_evolution_report(report):
    """Print full evolution report."""
    print("EF Memory Evolution Report")
    print(f"  Total entries:     {report.total_entries}")
    print(f"  Active:            {report.active_entries}")
    print(f"  Deprecated:        {report.deprecated_entries}")
    print(f"  Health score:      {report.health_score:.3f}")
    print(f"  Avg confidence:    {report.avg_confidence:.3f}")
    print()
    print(f"  Confidence:  High={report.entries_high_confidence}"
          f"  Medium={report.entries_medium_confidence}"
          f"  Low={report.entries_low_confidence}")

    if report.duplicate_report:
        dr = report.duplicate_report
        print(f"\n  Duplicates:  {len(dr.groups)} group(s) [{dr.mode}]")
        for group in dr.groups:
            print(f"    - {group.member_ids} (avg: {group.avg_similarity:.2f})")

    if report.deprecation_report:
        depr = report.deprecation_report
        if depr.candidates:
            print(f"\n  Deprecation candidates: {len(depr.candidates)}")
            for c in depr.candidates:
                print(f"    [{c.suggested_action}] {c.entry_id} ({c.confidence:.2f})")

    if report.merge_suggestions:
        print(f"\n  Merge suggestions: {len(report.merge_suggestions)}")
        for ms in report.merge_suggestions:
            print(f"    Keep {ms.keep_id}, deprecate {ms.deprecate_ids}")

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

    # Load config
    config_path = _MEMORY_DIR / "config.json"
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text())

    # Check for events
    if not events_path.exists() or events_path.stat().st_size <= 1:
        print("No entries in events.jsonl. Nothing to analyze.")
        sys.exit(0)

    # --- Mode: --id=<id> ---
    if args["entry_id"]:
        entries = _load_entries_latest_wins(events_path)
        entry = entries.get(args["entry_id"])
        if not entry:
            print(f"Entry not found: {args['entry_id']}")
            sys.exit(1)
        cs = calculate_confidence(entry, events_path, project_root, config)
        _print_single_confidence(cs)
        sys.exit(0)

    # --- Mode: --duplicates ---
    if args["duplicates"]:
        report = find_duplicates(events_path, config)
        _print_duplicates(report)
        sys.exit(0)

    # --- Mode: --confidence ---
    if args["confidence"]:
        entries = _load_entries_latest_wins(events_path)
        active = {eid: e for eid, e in entries.items() if not e.get("deprecated", False)}
        scores = []
        for eid, entry in active.items():
            cs = calculate_confidence(entry, events_path, project_root, config)
            scores.append(cs)
        _print_confidence(scores)
        sys.exit(0)

    # --- Mode: --deprecations ---
    if args["deprecations"]:
        report = suggest_deprecations(events_path, config, project_root)
        _print_deprecations(report)
        sys.exit(0)

    # --- Mode: --merges ---
    if args["merges"]:
        dup_report = find_duplicates(events_path, config)
        entries = _load_entries_latest_wins(events_path)
        active = {eid: e for eid, e in entries.items() if not e.get("deprecated", False)}
        suggestions = suggest_merges(dup_report.groups, active)
        _print_merges(suggestions)
        sys.exit(0)

    # --- Default: full report ---
    report = build_evolution_report(events_path, config, project_root)
    _print_evolution_report(report)


if __name__ == "__main__":
    main()
