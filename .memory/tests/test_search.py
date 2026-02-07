"""Tests for search module — hybrid search engine."""

import json
import tempfile
import unittest
import sys
from pathlib import Path

# Add .memory/ to path so 'lib' and 'tests' are importable
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.search import (
    search_memory,
    _search_basic,
    _compute_boost,
    _get_search_weights,
    _load_entries,
    _determine_mode,
)
from lib.vectordb import VectorDB
from lib.sync import sync_embeddings
from tests.conftest import SAMPLE_ENTRIES, MockEmbedder


# Default config for testing
TEST_CONFIG = {
    "embedding": {
        "search": {
            "bm25_weight": 0.4,
            "vector_weight": 0.6,
            "hard_s1_boost": 0.15,
            "hard_s2_boost": 0.10,
            "hard_s3_boost": 0.05,
            "min_score": 0.01,  # Low threshold for testing
        }
    },
    "search": {
        "max_results": 5,
        "priority": ["hard", "soft"],
        "severity_order": ["S1", "S2", "S3"],
    },
}


class SearchTestBase(unittest.TestCase):
    """Base class that sets up events.jsonl and vectors.db with sample data."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.events_path = Path(self.tmpdir) / "events.jsonl"
        self.db_path = Path(self.tmpdir) / "vectors.db"

        # Write sample entries to JSONL
        with open(self.events_path, "w") as f:
            for entry in SAMPLE_ENTRIES:
                f.write(json.dumps(entry) + "\n")

        # Init DB
        self.db = VectorDB(self.db_path)
        self.db.open()
        self.db.ensure_schema()

        # Create mock embedder
        self.embedder = MockEmbedder(dimensions=8)

        # Sync entries (creates vectors + FTS index)
        sync_embeddings(
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            force_full=True,
        )

    def tearDown(self):
        self.db.close()


class TestDetermineMode(unittest.TestCase):

    def test_hybrid_when_all_available(self):
        db = VectorDB(Path(tempfile.mkdtemp()) / "test.db")
        db.open()
        db.ensure_schema()
        embedder = MockEmbedder()
        mode, degraded, reason = _determine_mode(db, embedder)
        self.assertEqual(mode, "hybrid")
        self.assertFalse(degraded)
        db.close()

    def test_keyword_when_no_embedder(self):
        db = VectorDB(Path(tempfile.mkdtemp()) / "test.db")
        db.open()
        db.ensure_schema()
        mode, degraded, reason = _determine_mode(db, None)
        self.assertEqual(mode, "keyword")
        self.assertTrue(degraded)
        db.close()

    def test_basic_when_nothing_available(self):
        mode, degraded, reason = _determine_mode(None, None)
        self.assertEqual(mode, "basic")
        self.assertTrue(degraded)

    def test_force_mode(self):
        """Force mode degrades when required components are missing."""
        mode, degraded, reason = _determine_mode(None, None, force_mode="keyword")
        # keyword requires vectordb; without it, degrades to basic
        self.assertEqual(mode, "basic")
        self.assertTrue(degraded)
        self.assertIn("missing", reason)

    def test_force_mode_with_components(self):
        """Force mode works when required components are available."""
        db = VectorDB(Path(tempfile.mkdtemp()) / "test.db")
        db.open()
        db.ensure_schema()
        mode, degraded, reason = _determine_mode(db, None, force_mode="keyword")
        self.assertEqual(mode, "keyword")
        self.assertFalse(degraded)
        db.close()


class TestComputeBoost(unittest.TestCase):

    def setUp(self):
        self.weights = _get_search_weights(TEST_CONFIG)

    def test_hard_s1_boost(self):
        entry = {"classification": "hard", "severity": "S1"}
        self.assertAlmostEqual(_compute_boost(entry, self.weights), 0.15)

    def test_hard_s2_boost(self):
        entry = {"classification": "hard", "severity": "S2"}
        self.assertAlmostEqual(_compute_boost(entry, self.weights), 0.10)

    def test_hard_s3_boost(self):
        entry = {"classification": "hard", "severity": "S3"}
        self.assertAlmostEqual(_compute_boost(entry, self.weights), 0.05)

    def test_soft_no_boost(self):
        entry = {"classification": "soft", "severity": "S3"}
        self.assertAlmostEqual(_compute_boost(entry, self.weights), 0.0)

    def test_no_classification(self):
        entry = {"severity": "S1"}
        self.assertAlmostEqual(_compute_boost(entry, self.weights), 0.0)


class TestLoadEntries(unittest.TestCase):

    def test_load_from_jsonl(self):
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "events.jsonl"
        with open(path, "w") as f:
            for entry in SAMPLE_ENTRIES:
                f.write(json.dumps(entry) + "\n")

        entries = _load_entries(path)
        self.assertEqual(len(entries), 3)
        self.assertIn("lesson-inc036-a3f8c2d1", entries)

    def test_deprecated_filtered(self):
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "events.jsonl"
        dep = SAMPLE_ENTRIES[0].copy()
        dep["deprecated"] = True
        with open(path, "w") as f:
            f.write(json.dumps(dep) + "\n")
            f.write(json.dumps(SAMPLE_ENTRIES[1]) + "\n")

        entries = _load_entries(path)
        self.assertEqual(len(entries), 1)
        self.assertNotIn("lesson-inc036-a3f8c2d1", entries)

    def test_empty_file(self):
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "events.jsonl"
        path.touch()
        entries = _load_entries(path)
        self.assertEqual(len(entries), 0)

    def test_nonexistent_file(self):
        path = Path(tempfile.mkdtemp()) / "nonexistent.jsonl"
        entries = _load_entries(path)
        self.assertEqual(len(entries), 0)

    def test_latest_wins(self):
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "events.jsonl"
        v1 = SAMPLE_ENTRIES[0].copy()
        v1["title"] = "Version 1"
        v2 = SAMPLE_ENTRIES[0].copy()
        v2["title"] = "Version 2"
        with open(path, "w") as f:
            f.write(json.dumps(v1) + "\n")
            f.write(json.dumps(v2) + "\n")

        entries = _load_entries(path)
        self.assertEqual(entries["lesson-inc036-a3f8c2d1"]["title"], "Version 2")


class TestHybridSearch(SearchTestBase):

    def test_search_finds_relevant_entries(self):
        report = search_memory(
            query="leakage rolling shift",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
        )
        self.assertEqual(report.mode, "hybrid")
        self.assertFalse(report.degraded)
        self.assertGreater(report.total_found, 0)

    def test_hard_s1_boost_applied(self):
        """Hard+S1 entries should have boost > 0 in their score breakdown."""
        report = search_memory(
            query="leakage rolling shift",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
        )
        hard_results = [r for r in report.results if r.entry.get("classification") == "hard"]
        soft_results = [r for r in report.results if r.entry.get("classification") == "soft"]

        # Hard entries should have positive boost
        for r in hard_results:
            self.assertGreater(r.boost, 0.0, f"Hard entry {r.entry_id} should have boost > 0")

        # Soft entries should have zero boost
        for r in soft_results:
            self.assertEqual(r.boost, 0.0, f"Soft entry {r.entry_id} should have boost == 0")

    def test_returns_search_mode(self):
        report = search_memory(
            query="rolling",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
        )
        for result in report.results:
            self.assertEqual(result.search_mode, "hybrid")


class TestKeywordSearch(SearchTestBase):

    def test_keyword_mode_without_embedder(self):
        report = search_memory(
            query="rolling shift leakage",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=None,  # No embedder
            config=TEST_CONFIG,
        )
        self.assertEqual(report.mode, "keyword")
        self.assertTrue(report.degraded)
        self.assertGreater(report.total_found, 0)

    def test_keyword_mode_forced(self):
        report = search_memory(
            query="rolling",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
            force_mode="keyword",
        )
        self.assertEqual(report.mode, "keyword")

    def test_keyword_no_match(self):
        report = search_memory(
            query="nonexistent_term_xyz_123",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=None,
            config=TEST_CONFIG,
        )
        self.assertEqual(report.total_found, 0)


class TestBasicSearch(SearchTestBase):

    def test_basic_mode_fallback(self):
        report = search_memory(
            query="rolling shift",
            events_path=self.events_path,
            vectordb=None,  # No vectordb
            embedder=None,  # No embedder
            config=TEST_CONFIG,
        )
        self.assertEqual(report.mode, "basic")
        self.assertTrue(report.degraded)
        self.assertGreater(report.total_found, 0)

    def test_basic_mode_forced(self):
        report = search_memory(
            query="leakage",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
            force_mode="basic",
        )
        self.assertEqual(report.mode, "basic")
        self.assertGreater(report.total_found, 0)

    def test_basic_finds_by_tag(self):
        report = search_memory(
            query="leakage",
            events_path=self.events_path,
            vectordb=None,
            embedder=None,
            config=TEST_CONFIG,
        )
        ids = [r.entry_id for r in report.results]
        self.assertIn("lesson-inc036-a3f8c2d1", ids)  # Has "leakage" tag

    def test_basic_empty_query(self):
        report = search_memory(
            query="",
            events_path=self.events_path,
            vectordb=None,
            embedder=None,
            config=TEST_CONFIG,
        )
        self.assertEqual(report.total_found, 0)

    def test_basic_deprecated_excluded(self):
        # Add a deprecated entry
        dep = SAMPLE_ENTRIES[0].copy()
        dep["deprecated"] = True
        with open(self.events_path, "a") as f:
            f.write(json.dumps(dep) + "\n")

        report = search_memory(
            query="leakage",
            events_path=self.events_path,
            vectordb=None,
            embedder=None,
            config=TEST_CONFIG,
        )
        ids = [r.entry_id for r in report.results]
        # The entry might still show up because the non-deprecated version comes first
        # But the deprecated version (last write) should cause it to be excluded
        # Actually, latest-wins means deprecated=True supersedes the original
        self.assertNotIn("lesson-inc036-a3f8c2d1", ids)


class TestSearchReport(SearchTestBase):

    def test_report_has_duration(self):
        report = search_memory(
            query="rolling",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
        )
        self.assertGreater(report.duration_ms, 0)

    def test_report_query_preserved(self):
        report = search_memory(
            query="my specific query",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
        )
        self.assertEqual(report.query, "my specific query")

    def test_max_results_respected(self):
        report = search_memory(
            query="leakage",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
            max_results=1,
        )
        self.assertLessEqual(report.total_found, 1)

    def test_empty_events(self):
        empty_path = Path(self.tmpdir) / "empty.jsonl"
        empty_path.touch()
        report = search_memory(
            query="anything",
            events_path=empty_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
        )
        self.assertEqual(report.total_found, 0)

    def test_results_have_entry_data(self):
        report = search_memory(
            query="rolling",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
        )
        for result in report.results:
            self.assertIn("id", result.entry)
            self.assertIn("title", result.entry)
            self.assertIn("type", result.entry)


class TestMinScoreFilter(unittest.TestCase):

    def test_min_score_filters_low_results(self):
        tmpdir = tempfile.mkdtemp()
        events_path = Path(tmpdir) / "events.jsonl"
        with open(events_path, "w") as f:
            for entry in SAMPLE_ENTRIES:
                f.write(json.dumps(entry) + "\n")

        # High min_score should filter out most basic matches
        config = {
            "embedding": {
                "search": {
                    "bm25_weight": 0.4,
                    "vector_weight": 0.6,
                    "hard_s1_boost": 0.15,
                    "hard_s2_boost": 0.10,
                    "hard_s3_boost": 0.05,
                    "min_score": 100.0,  # Impossibly high
                }
            },
            "search": {"max_results": 5},
        }
        report = search_memory(
            query="leakage",
            events_path=events_path,
            vectordb=None,
            embedder=None,
            config=config,
        )
        self.assertEqual(report.total_found, 0)


class TestContextSearch(SearchTestBase):

    def test_search_with_context(self):
        """Search with file context should not error."""
        report = search_memory(
            query="shift rolling",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=TEST_CONFIG,
            context={"current_file": "src/features/engine.py", "tags": ["rolling"]},
        )
        self.assertIsNotNone(report)
        self.assertEqual(report.mode, "hybrid")


class TestForceModeDegradation(unittest.TestCase):

    def test_force_hybrid_no_embedder(self):
        db = VectorDB(Path(tempfile.mkdtemp()) / "test.db")
        db.open()
        db.ensure_schema()
        mode, degraded, reason = _determine_mode(db, None, force_mode="hybrid")
        self.assertEqual(mode, "basic")
        self.assertTrue(degraded)
        self.assertIn("embedder", reason)
        db.close()

    def test_force_hybrid_no_vectordb(self):
        mode, degraded, reason = _determine_mode(None, MockEmbedder(), force_mode="hybrid")
        self.assertEqual(mode, "basic")
        self.assertTrue(degraded)

    def test_force_vector_no_embedder(self):
        db = VectorDB(Path(tempfile.mkdtemp()) / "test.db")
        db.open()
        db.ensure_schema()
        mode, degraded, reason = _determine_mode(db, None, force_mode="vector")
        self.assertEqual(mode, "basic")
        self.assertTrue(degraded)
        db.close()

    def test_force_basic_always_works(self):
        mode, degraded, reason = _determine_mode(None, None, force_mode="basic")
        self.assertEqual(mode, "basic")
        self.assertFalse(degraded)


class TestDetermineModeVectorOnly(unittest.TestCase):

    def test_vector_mode_when_fts_unavailable(self):
        db = VectorDB(Path(tempfile.mkdtemp()) / "test.db")
        db.open()
        db.ensure_schema()
        db._fts5_available = False
        mode, degraded, reason = _determine_mode(db, MockEmbedder())
        self.assertEqual(mode, "vector")
        self.assertTrue(degraded)
        db.close()


class TestSearchExceptionFallback(SearchTestBase):

    def test_hybrid_failure_falls_back_to_basic(self):
        class BrokenEmbedder(MockEmbedder):
            def embed_query(self, text):
                raise RuntimeError("API error")

        report = search_memory(
            query="rolling",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=BrokenEmbedder(dimensions=8),
            config=TEST_CONFIG,
        )
        self.assertEqual(report.mode, "basic")
        self.assertTrue(report.degraded)
        self.assertIn("fell back to basic", report.degradation_reason)


class TestConfigMaxResults(SearchTestBase):

    def test_config_max_results_used_when_no_caller_override(self):
        config = dict(TEST_CONFIG)
        config["search"] = {"max_results": 1}
        report = search_memory(
            query="leakage rolling shift",
            events_path=self.events_path,
            vectordb=self.db,
            embedder=self.embedder,
            config=config,
        )
        self.assertLessEqual(report.total_found, 1)


if __name__ == "__main__":
    unittest.main()
