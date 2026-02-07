"""Tests for vectordb module."""

import math
import tempfile
import unittest
import sys
from pathlib import Path

# Add .memory/ to path so 'lib' is importable
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.vectordb import VectorDB, cosine_similarity, pack_vector, unpack_vector


class TestCosineSimiarity(unittest.TestCase):

    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(cosine_similarity(v, v), 1.0, places=5)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(a, b), 0.0, places=5)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(a, b), -1.0, places=5)

    def test_zero_vector(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        self.assertEqual(cosine_similarity(a, b), 0.0)

    def test_known_similarity(self):
        a = [1.0, 1.0]
        b = [1.0, 0.0]
        expected = 1.0 / math.sqrt(2)
        self.assertAlmostEqual(cosine_similarity(a, b), expected, places=5)


class TestPackUnpack(unittest.TestCase):

    def test_roundtrip(self):
        vec = [0.1, 0.2, 0.3, -0.5, 1.0]
        blob = pack_vector(vec)
        recovered = unpack_vector(blob, len(vec))
        for a, b in zip(vec, recovered):
            self.assertAlmostEqual(a, b, places=5)

    def test_empty_vector(self):
        blob = pack_vector([])
        recovered = unpack_vector(blob, 0)
        self.assertEqual(recovered, [])


class TestVectorDB(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test_vectors.db"
        self.db = VectorDB(self.db_path)
        self.db.open()
        self.db.ensure_schema()

    def tearDown(self):
        self.db.close()

    def test_upsert_and_get(self):
        vec = [0.1, 0.2, 0.3]
        self.db.upsert_vector("entry-1", "hash1", "mock", "mock-v1", 3, vec)
        result = self.db.get_vector("entry-1")
        self.assertIsNotNone(result)
        for a, b in zip(vec, result):
            self.assertAlmostEqual(a, b, places=5)

    def test_get_nonexistent(self):
        result = self.db.get_vector("nonexistent")
        self.assertIsNone(result)

    def test_has_vector(self):
        self.assertFalse(self.db.has_vector("entry-1"))
        self.db.upsert_vector("entry-1", "hash1", "mock", "mock-v1", 3, [0.1, 0.2, 0.3])
        self.assertTrue(self.db.has_vector("entry-1"))

    def test_needs_update(self):
        # Missing → needs update
        self.assertTrue(self.db.needs_update("entry-1", "hash1"))

        # Insert
        self.db.upsert_vector("entry-1", "hash1", "mock", "mock-v1", 3, [0.1, 0.2, 0.3])

        # Same hash → no update needed
        self.assertFalse(self.db.needs_update("entry-1", "hash1"))

        # Different hash → needs update
        self.assertTrue(self.db.needs_update("entry-1", "hash2"))

    def test_search_vectors(self):
        # Insert 3 vectors
        self.db.upsert_vector("a", "h1", "mock", "m", 3, [1.0, 0.0, 0.0])
        self.db.upsert_vector("b", "h2", "mock", "m", 3, [0.9, 0.1, 0.0])
        self.db.upsert_vector("c", "h3", "mock", "m", 3, [0.0, 0.0, 1.0])

        # Query similar to "a"
        results = self.db.search_vectors([1.0, 0.0, 0.0], limit=3)
        self.assertEqual(len(results), 3)
        # "a" should be most similar
        self.assertEqual(results[0][0], "a")
        self.assertAlmostEqual(results[0][1], 1.0, places=5)
        # "b" should be second
        self.assertEqual(results[1][0], "b")

    def test_deprecated_excluded_from_search(self):
        self.db.upsert_vector("a", "h1", "mock", "m", 3, [1.0, 0.0, 0.0])
        self.db.upsert_vector("b", "h2", "mock", "m", 3, [0.9, 0.1, 0.0])
        self.db.mark_deprecated("a")

        results = self.db.search_vectors([1.0, 0.0, 0.0], limit=3, exclude_deprecated=True)
        ids = [r[0] for r in results]
        self.assertNotIn("a", ids)
        self.assertIn("b", ids)

    def test_fts_upsert_and_search(self):
        self.db.upsert_fts("entry-1", "Rolling statistics shift", "shift must precede rolling", "leakage rolling")
        self.db.upsert_fts("entry-2", "Cache key collision", "cache invalidation", "cache")

        results = self.db.search_fts("rolling", limit=5)
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0][0], "entry-1")

    def test_fts_no_match(self):
        self.db.upsert_fts("entry-1", "Rolling statistics", "shift text", "leakage")
        results = self.db.search_fts("nonexistent_term_xyz", limit=5)
        self.assertEqual(len(results), 0)

    def test_sync_cursor(self):
        self.assertIsNone(self.db.get_sync_cursor())
        self.db.set_sync_cursor(42)
        self.assertEqual(self.db.get_sync_cursor(), 42)
        self.db.set_sync_cursor(100)
        self.assertEqual(self.db.get_sync_cursor(), 100)

    def test_stats(self):
        self.db.upsert_vector("a", "h1", "mock", "m", 3, [1.0, 0.0, 0.0])
        self.db.upsert_vector("b", "h2", "mock", "m", 3, [0.0, 1.0, 0.0])
        self.db.mark_deprecated("b")

        stats = self.db.stats()
        self.assertEqual(stats["vectors_total"], 2)
        self.assertEqual(stats["vectors_active"], 1)
        self.assertEqual(stats["vectors_deprecated"], 1)


class TestContextManager(unittest.TestCase):

    def test_with_statement_opens_and_closes(self):
        """Context manager should open+schema on enter, close on exit."""
        db_path = Path(tempfile.mkdtemp()) / "ctx.db"
        with VectorDB(db_path) as db:
            db.upsert_vector("e1", "h1", "mock", "m", 3, [1.0, 0.0, 0.0])
            self.assertTrue(db.has_vector("e1"))
        self.assertIsNone(db._conn)

    def test_context_manager_creates_tables(self):
        db_path = Path(tempfile.mkdtemp()) / "ctx2.db"
        with VectorDB(db_path) as db:
            stats = db.stats()
            self.assertIn("vectors_total", stats)


class TestRequireConn(unittest.TestCase):

    def test_operations_fail_before_open(self):
        db = VectorDB(Path(tempfile.mkdtemp()) / "unopened.db")
        with self.assertRaises(RuntimeError):
            db.ensure_schema()
        with self.assertRaises(RuntimeError):
            db.upsert_vector("e1", "h1", "p", "m", 3, [1.0, 0.0, 0.0])
        with self.assertRaises(RuntimeError):
            db.get_vector("e1")
        with self.assertRaises(RuntimeError):
            db.search_fts("test")
        with self.assertRaises(RuntimeError):
            db.stats()

    def test_operations_fail_after_close(self):
        db = VectorDB(Path(tempfile.mkdtemp()) / "closed.db")
        db.open()
        db.ensure_schema()
        db.close()
        with self.assertRaises(RuntimeError):
            db.upsert_vector("e1", "h1", "p", "m", 3, [1.0, 0.0, 0.0])


class TestSanitizeFtsQuery(unittest.TestCase):

    def test_normal_query(self):
        result = VectorDB._sanitize_fts_query("rolling shift")
        self.assertEqual(result, '"rolling" "shift"')

    def test_operator_heavy_query(self):
        result = VectorDB._sanitize_fts_query("OR AND NOT")
        self.assertEqual(result, '"OR" "AND" "NOT"')

    def test_punctuation_only_returns_empty(self):
        result = VectorDB._sanitize_fts_query("!@#$%^&*()")
        self.assertEqual(result, "")

    def test_empty_query(self):
        result = VectorDB._sanitize_fts_query("")
        self.assertEqual(result, "")


class TestBatchNesting(unittest.TestCase):

    def setUp(self):
        self.db = VectorDB(Path(tempfile.mkdtemp()) / "batch.db")
        self.db.open()
        self.db.ensure_schema()

    def tearDown(self):
        self.db.close()

    def test_nested_batch_depth_tracking(self):
        self.db.begin_batch()
        self.assertEqual(self.db._batch_depth, 1)
        self.db.begin_batch()
        self.assertEqual(self.db._batch_depth, 2)
        self.db.end_batch()
        self.assertEqual(self.db._batch_depth, 1)
        self.db.end_batch()
        self.assertEqual(self.db._batch_depth, 0)

    def test_end_batch_at_zero_no_underflow(self):
        self.db.end_batch()
        self.assertEqual(self.db._batch_depth, 0)


class TestDeleteVector(unittest.TestCase):

    def setUp(self):
        self.db = VectorDB(Path(tempfile.mkdtemp()) / "del.db")
        self.db.open()
        self.db.ensure_schema()

    def tearDown(self):
        self.db.close()

    def test_delete_removes_vector(self):
        self.db.upsert_vector("e1", "h1", "mock", "m", 3, [1.0, 0.0, 0.0])
        self.assertTrue(self.db.has_vector("e1"))
        self.db.delete_vector("e1")
        self.assertFalse(self.db.has_vector("e1"))

    def test_delete_nonexistent_no_error(self):
        self.db.delete_vector("nonexistent")


class TestFTSDisabled(unittest.TestCase):

    def test_fts_ops_noop_when_disabled(self):
        db = VectorDB(Path(tempfile.mkdtemp()) / "nofts.db")
        db.open()
        db.ensure_schema()
        db._fts5_available = False
        db.upsert_fts("e1", "title", "text", "tags")
        db.delete_fts("e1")
        results = db.search_fts("test")
        self.assertEqual(results, [])
        db.close()


class TestSearchFtsEdgeCases(unittest.TestCase):

    def setUp(self):
        self.db = VectorDB(Path(tempfile.mkdtemp()) / "fts_edge.db")
        self.db.open()
        self.db.ensure_schema()
        self.db.upsert_fts("e1", "Rolling statistics", "shift precede rolling", "leakage")

    def tearDown(self):
        self.db.close()

    def test_punctuation_only_query_returns_empty(self):
        results = self.db.search_fts("!!!")
        self.assertEqual(results, [])

    def test_empty_query_returns_empty(self):
        results = self.db.search_fts("")
        self.assertEqual(results, [])


class TestSearchVectorsDeprecatedIncluded(unittest.TestCase):

    def test_exclude_deprecated_false_returns_all(self):
        db = VectorDB(Path(tempfile.mkdtemp()) / "depr.db")
        db.open()
        db.ensure_schema()
        db.upsert_vector("a", "h1", "mock", "m", 3, [1.0, 0.0, 0.0])
        db.upsert_vector("b", "h2", "mock", "m", 3, [0.9, 0.1, 0.0])
        db.mark_deprecated("a")
        results = db.search_vectors([1.0, 0.0, 0.0], limit=3, exclude_deprecated=False)
        ids = [r[0] for r in results]
        self.assertIn("a", ids)
        self.assertIn("b", ids)
        db.close()


if __name__ == "__main__":
    unittest.main()
