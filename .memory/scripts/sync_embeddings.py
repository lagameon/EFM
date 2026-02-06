#!/usr/bin/env python3
"""
EF Memory V2 — Sync Embeddings CLI

Synchronizes events.jsonl → vectors.db (embedding + FTS index).

Usage:
    python3 .memory/scripts/sync_embeddings.py          # Incremental sync
    python3 .memory/scripts/sync_embeddings.py --full    # Full rebuild
    python3 .memory/scripts/sync_embeddings.py --stats   # Show DB stats only
    python3 .memory/scripts/sync_embeddings.py --help    # Show help
"""

import sys
import json
import logging
from pathlib import Path

# Add lib to import path
_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
_LIB_DIR = _MEMORY_DIR / "lib"
sys.path.insert(0, str(_LIB_DIR.parent))

from lib.sync import sync_embeddings
from lib.vectordb import VectorDB
from lib.embedder import create_embedder


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Parse args
    args = set(sys.argv[1:])
    if "--help" in args or "-h" in args:
        print(__doc__.strip())
        sys.exit(0)

    force_full = "--full" in args
    stats_only = "--stats" in args

    # Load config
    config_path = _MEMORY_DIR / "config.json"
    if not config_path.exists():
        print(f"ERROR: Config not found: {config_path}")
        sys.exit(1)

    config = json.loads(config_path.read_text())
    embedding_config = config.get("embedding", {})

    # Resolve DB path (relative to project root, not memory dir)
    project_root = _MEMORY_DIR.parent
    db_rel_path = embedding_config.get("storage", {}).get("db_path", ".memory/vectors.db")
    db_path = project_root / db_rel_path

    # Open database
    vectordb = VectorDB(db_path)
    vectordb.open()
    vectordb.ensure_schema()

    if stats_only:
        stats = vectordb.stats()
        print("=== EF Memory Vector DB Stats ===")
        for key, value in stats.items():
            print(f"  {key}: {value}")
        vectordb.close()
        sys.exit(0)

    # Check if embedding is enabled
    if not embedding_config.get("enabled", False):
        print("Embedding layer is disabled.")
        print("To enable: set embedding.enabled = true in .memory/config.json")
        print("Then set the API key for your provider (e.g., export GOOGLE_API_KEY=...)")
        print()
        print("FTS-only sync is still available. Running FTS sync...")
        # Still run sync for FTS indexing (embedder=None)
        events_path = _MEMORY_DIR / "events.jsonl"
        if not events_path.exists() or events_path.stat().st_size <= 1:
            print("No entries in events.jsonl. Nothing to sync.")
            vectordb.close()
            sys.exit(0)

        report = sync_embeddings(
            events_path=events_path,
            vectordb=vectordb,
            embedder=None,
            force_full=force_full,
            batch_size=embedding_config.get("sync", {}).get("batch_size", 20),
        )
        _print_report(report)
        vectordb.close()
        sys.exit(0)

    # Create embedder
    embedder = create_embedder(embedding_config)

    # Run sync
    events_path = _MEMORY_DIR / "events.jsonl"
    if not events_path.exists() or events_path.stat().st_size <= 1:
        print("No entries in events.jsonl. Nothing to sync.")
        vectordb.close()
        sys.exit(0)

    report = sync_embeddings(
        events_path=events_path,
        vectordb=vectordb,
        embedder=embedder,
        force_full=force_full,
        batch_size=embedding_config.get("sync", {}).get("batch_size", 20),
    )

    _print_report(report)

    # Show stats
    stats = vectordb.stats()
    print(f"\n  DB: {stats['vectors_active']} vectors, "
          f"{stats['fts_entries']} FTS entries, "
          f"{stats['vectors_deprecated']} deprecated")

    vectordb.close()


def _print_report(report):
    """Print a formatted sync report."""
    mode_label = "full rebuild" if report.mode == "full" else "incremental"
    print(f"\nSync complete ({mode_label})")
    print(f"  Scanned:    {report.entries_scanned}")
    print(f"  Added:      {report.entries_added}")
    print(f"  Updated:    {report.entries_updated}")
    print(f"  Skipped:    {report.entries_skipped} (unchanged)")
    print(f"  FTS only:   {report.entries_fts_only} (no embedder)")
    print(f"  Deprecated: {report.entries_deprecated}")
    print(f"  Errors:     {len(report.errors)}")
    print(f"  Duration:   {report.duration_ms:.0f}ms")

    if report.errors:
        print("\n  Errors:")
        for err in report.errors:
            print(f"    - {err}")


if __name__ == "__main__":
    main()
