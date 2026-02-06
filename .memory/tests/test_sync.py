"""Tests for sync module."""

import json
import tempfile
import unittest
import sys
from pathlib import Path

# Add .memory/ to path so 'lib' and 'tests' are importable
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.sync import sync_embeddings
from lib.vectordb import VectorDB
from tests.conftest import SAMPLE_ENTRIES, MockEmbedder


class TestSyncEmbeddings(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.events_path = Path(self.tmpdir) / "events.jsonl"
        self.db_path = Path(self.tmpdir) / "vectors.db"

        # Write sample entries to JSONL
        with open(self.events_path, "w") as f:
            for entry in SAMPLE_ENTRIES:
                f.write(json.dumps(entry) + "\n")

        self.db = VectorDB(self.db_path)
        self.db.open()
        self.db.ensure_schema()

        self.embedder = MockEmbedder(dimensions=8)  # Small dims for testing

    def tearDown(self):
        self.db.close()

    def test_full_sync_with_embedder(self):
        report = sync_embeddings(
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            force_full=True,
        )
        self.assertEqual(report.mode, "full")
        self.assertEqual(report.entries_scanned, 3)
        self.assertEqual(report.entries_added, 3)
        self.assertEqual(report.entries_updated, 0)
        self.assertEqual(len(report.errors), 0)

        # Vectors should exist
        for entry in SAMPLE_ENTRIES:
            self.assertTrue(self.db.has_vector(entry["id"]))

    def test_incremental_sync_skips_unchanged(self):
        # First sync
        sync_embeddings(self.events_path, self.db, self.embedder, force_full=True)

        # Second sync should skip all (no changes)
        report = sync_embeddings(self.events_path, self.db, self.embedder, force_full=False)
        self.assertEqual(report.mode, "incremental")
        self.assertEqual(report.entries_skipped, 0)  # Nothing new after cursor
        self.assertEqual(report.entries_added, 0)

    def test_sync_without_embedder_updates_fts(self):
        report = sync_embeddings(
            events_path=self.events_path,
            vectordb=self.db,
            embedder=None,  # No embedder
            force_full=True,
        )
        self.assertEqual(report.entries_fts_only, 3)
        self.assertEqual(report.entries_added, 0)

        # FTS should work
        stats = self.db.stats()
        if stats["fts5_available"]:
            self.assertEqual(stats["fts_entries"], 3)

    def test_sync_handles_deprecated(self):
        # Add a deprecated entry
        deprecated = SAMPLE_ENTRIES[0].copy()
        deprecated["deprecated"] = True
        with open(self.events_path, "a") as f:
            f.write(json.dumps(deprecated) + "\n")

        report = sync_embeddings(self.events_path, self.db, self.embedder, force_full=True)
        self.assertEqual(report.entries_deprecated, 1)
        self.assertEqual(report.entries_added, 2)  # Only 2 active

    def test_sync_empty_file(self):
        empty_path = Path(self.tmpdir) / "empty.jsonl"
        empty_path.touch()

        report = sync_embeddings(empty_path, self.db, self.embedder, force_full=True)
        self.assertEqual(report.entries_scanned, 0)

    def test_append_then_incremental(self):
        # Initial sync
        sync_embeddings(self.events_path, self.db, self.embedder, force_full=True)

        # Append a new entry
        new_entry = {
            "id": "lesson-new-12345678",
            "type": "lesson",
            "classification": "soft",
            "severity": "S2",
            "title": "New lesson about caching",
            "content": ["Cache invalidation is hard", "TTL must be set explicitly"],
            "rule": None,
            "implication": "Stale data served to users",
            "source": ["src/cache.py:L10-L20"],
            "tags": ["cache", "ttl"],
            "created_at": "2026-02-06T10:00:00Z",
            "deprecated": False,
            "_meta": {},
        }
        with open(self.events_path, "a") as f:
            f.write(json.dumps(new_entry) + "\n")

        # Incremental sync
        report = sync_embeddings(self.events_path, self.db, self.embedder, force_full=False)
        self.assertEqual(report.entries_added, 1)
        self.assertTrue(self.db.has_vector("lesson-new-12345678"))


if __name__ == "__main__":
    unittest.main()
