#!/usr/bin/env python3
"""
EF Memory V2 — Search CLI

Search project memory with hybrid BM25 + vector + re-rank engine.

Usage:
    python3 .memory/scripts/search_cli.py "leakage shift"
    python3 .memory/scripts/search_cli.py "rolling" --max-results 3
    python3 .memory/scripts/search_cli.py "label" --mode keyword
    python3 .memory/scripts/search_cli.py "shift" --full
    python3 .memory/scripts/search_cli.py --debug "leakage"
    python3 .memory/scripts/search_cli.py --help

Modes:
    hybrid   — BM25 + Vector + Re-rank (default, requires embedder + FTS5)
    vector   — Pure semantic search (requires embedder)
    keyword  — Pure BM25 full-text search (requires FTS5)
    basic    — Token overlap on events.jsonl (zero dependencies)
"""

import sys
import json
import logging
from pathlib import Path

# Add .memory/ to import path
_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_MEMORY_DIR.parent))
sys.path.insert(0, str(_MEMORY_DIR))

from lib.search import search_memory, SearchReport
from lib.vectordb import VectorDB
from lib.embedder import create_embedder


def _parse_args(argv: list) -> dict:
    """Simple arg parser (no argparse dependency style)."""
    args = {
        "query": None,
        "max_results": 5,
        "mode": None,
        "full": False,
        "debug": False,
        "help": False,
    }

    i = 0
    positionals = []
    while i < len(argv):
        arg = argv[i]
        if arg in ("--help", "-h"):
            args["help"] = True
        elif arg == "--full":
            args["full"] = True
        elif arg == "--debug":
            args["debug"] = True
        elif arg == "--max-results" and i + 1 < len(argv):
            i += 1
            try:
                args["max_results"] = int(argv[i])
            except ValueError:
                print(f"ERROR: --max-results must be an integer, got '{argv[i]}'")
                sys.exit(1)
        elif arg == "--mode" and i + 1 < len(argv):
            i += 1
            mode = argv[i]
            if mode not in ("hybrid", "vector", "keyword", "basic"):
                print(f"ERROR: --mode must be one of: hybrid, vector, keyword, basic")
                sys.exit(1)
            args["mode"] = mode
        elif not arg.startswith("--"):
            positionals.append(arg)
        else:
            print(f"ERROR: Unknown option: {arg}")
            sys.exit(1)
        i += 1

    if positionals:
        args["query"] = " ".join(positionals)

    return args


def _format_result(result, full: bool = False, debug: bool = False) -> str:
    """Format a single search result for display."""
    entry = result.entry
    lines = []

    # Classification + severity + type header
    classification = entry.get("classification", "?").capitalize()
    severity = entry.get("severity", "")
    entry_type = entry.get("type", "?")

    header_parts = [f"[{classification}]"]
    if severity:
        header_parts.append(f"[{severity}]")
    header_parts.append(entry_type)

    if debug:
        header_parts.append(f"    score: {result.score:.3f}")

    lines.append(" ".join(header_parts))

    # Title
    title = entry.get("title", "(no title)")
    lines.append(f"Title: {title}")

    # Rule
    rule = entry.get("rule")
    if rule:
        lines.append(f"Rule: {rule}")

    # Implication
    implication = entry.get("implication")
    if implication:
        lines.append(f"Implication: {implication}")

    # Source
    sources = entry.get("source", [])
    if isinstance(sources, list) and sources:
        lines.append(f"Source: {sources[0]}")
        if len(sources) > 1:
            for src in sources[1:]:
                lines.append(f"        {src}")

    # Full mode: show content and verify
    if full:
        content = entry.get("content", [])
        if isinstance(content, list) and content:
            lines.append("Content:")
            for item in content:
                lines.append(f"  - {item}")

        verify = entry.get("verify")
        if verify:
            lines.append(f"Verify: {verify}")

    # Debug mode: show score breakdown
    if debug:
        lines.append(f"  [debug] bm25={result.bm25_score:.3f} "
                      f"vector={result.vector_score:.3f} "
                      f"boost={result.boost:.3f} "
                      f"final={result.score:.3f} "
                      f"mode={result.search_mode}")

    lines.append("---")
    return "\n".join(lines)


def _format_report(report: SearchReport, full: bool = False, debug: bool = False) -> str:
    """Format the full search report for display."""
    lines = []

    # Header with mode indicator
    mode_str = report.mode
    if report.degraded:
        mode_str += " \u26a0"  # ⚠ warning sign
    lines.append(f"/memory-search {report.query}  [{mode_str}]")
    lines.append("")

    if report.degraded and report.degradation_reason:
        lines.append(f"Note: {report.degradation_reason}")
        lines.append("")

    if report.total_found == 0:
        lines.append("No matching memories found.")
        lines.append("Consider `/memory-save` if this is a new lesson worth remembering.")
        return "\n".join(lines)

    lines.append(f"Found {report.total_found} entries (showing top {len(report.results)}):")
    lines.append("")

    for result in report.results:
        lines.append(_format_result(result, full=full, debug=debug))
        lines.append("")

    # Tips
    tips = []
    hard_count = sum(1 for r in report.results if r.entry.get("classification") == "hard")
    soft_count = sum(1 for r in report.results if r.entry.get("classification") == "soft")

    if hard_count > 0 and soft_count == 0:
        tips.append(f"All {hard_count} results are Hard (high-confidence)")
    elif hard_count > 0 and soft_count > 0:
        tips.append(f"{hard_count} Hard + {soft_count} Soft entries returned")

    if not full:
        tips.append("Use `--full` to see Content and Verify fields")

    if debug:
        tips.append(f"Search completed in {report.duration_ms:.0f}ms")

    if tips:
        lines.append("Tips:")
        for tip in tips:
            lines.append(f"- {tip}")

    return "\n".join(lines)


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    args = _parse_args(sys.argv[1:])

    if args["help"]:
        print(__doc__.strip())
        sys.exit(0)

    if not args["query"]:
        print("ERROR: No search query provided.")
        print("Usage: python3 .memory/scripts/search_cli.py \"your query\"")
        print("Run with --help for more options.")
        sys.exit(1)

    # Load config
    config_path = _MEMORY_DIR / "config.json"
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text())

    embedding_config = config.get("embedding", {})

    # Resolve paths
    project_root = _MEMORY_DIR.parent
    events_path = _MEMORY_DIR / "events.jsonl"
    db_rel_path = embedding_config.get("storage", {}).get("db_path", ".memory/vectors.db")
    db_path = project_root / db_rel_path

    # Open vector DB (if exists)
    vectordb = None
    if db_path.exists():
        vectordb = VectorDB(db_path)
        vectordb.open()
        vectordb.ensure_schema()

    # Create embedder (if enabled)
    embedder = None
    if embedding_config.get("enabled", False):
        try:
            embedder = create_embedder(embedding_config)
        except Exception as e:
            logging.warning(f"Failed to create embedder: {e}")

    # Run search
    report = search_memory(
        query=args["query"],
        events_path=events_path,
        vectordb=vectordb,
        embedder=embedder,
        config=config,
        max_results=args["max_results"],
        force_mode=args["mode"],
    )

    # Format and display
    output = _format_report(report, full=args["full"], debug=args["debug"])
    print(output)

    # Cleanup
    if vectordb:
        vectordb.close()


if __name__ == "__main__":
    main()
