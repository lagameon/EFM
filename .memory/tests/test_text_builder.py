"""Tests for text_builder module."""

import unittest
import sys
from pathlib import Path

# Add .memory/ to path so 'lib' and 'tests' are importable
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.text_builder import (
    build_embedding_text,
    build_query_text,
    build_dedup_text,
    build_fts_fields,
)
from tests.conftest import SAMPLE_ENTRIES


class TestBuildEmbeddingText(unittest.TestCase):

    def test_title_repeated_twice(self):
        entry = SAMPLE_ENTRIES[0]
        text = build_embedding_text(entry)
        title = entry["title"]
        # Title should appear exactly twice at the start
        self.assertEqual(text.count(title), 2)

    def test_contains_rule(self):
        entry = SAMPLE_ENTRIES[0]
        text = build_embedding_text(entry)
        self.assertIn("Rule:", text)
        self.assertIn("shift(1) MUST", text)

    def test_contains_implication(self):
        entry = SAMPLE_ENTRIES[0]
        text = build_embedding_text(entry)
        self.assertIn("Impact:", text)

    def test_contains_all_tags(self):
        entry = SAMPLE_ENTRIES[0]
        text = build_embedding_text(entry)
        for tag in entry["tags"]:
            self.assertIn(tag, text)

    def test_contains_classification_metadata(self):
        entry = SAMPLE_ENTRIES[0]
        text = build_embedding_text(entry)
        self.assertIn("lesson", text)
        self.assertIn("hard", text)
        self.assertIn("S1", text)

    def test_entry_without_rule(self):
        entry = SAMPLE_ENTRIES[2]  # fact with rule=None
        text = build_embedding_text(entry)
        self.assertNotIn("Rule:", text)
        self.assertIn("Impact:", text)  # Has implication

    def test_empty_entry(self):
        text = build_embedding_text({})
        self.assertIsInstance(text, str)


class TestBuildQueryText(unittest.TestCase):

    def test_simple_query(self):
        text = build_query_text("leakage shift")
        self.assertEqual(text, "leakage shift")

    def test_query_with_file_context(self):
        text = build_query_text("leakage", {"current_file": "src/features/engine.py"})
        self.assertIn("leakage", text)
        self.assertIn("src/features/engine.py", text)

    def test_query_with_tags_context(self):
        text = build_query_text("bug", {"tags": ["rolling", "ewm"]})
        self.assertIn("rolling", text)
        self.assertIn("ewm", text)


class TestBuildDedupText(unittest.TestCase):

    def test_contains_title_and_rule(self):
        entry = SAMPLE_ENTRIES[0]
        text = build_dedup_text(entry)
        self.assertIn(entry["title"], text)
        self.assertIn(entry["rule"], text)

    def test_contains_source(self):
        entry = SAMPLE_ENTRIES[0]
        text = build_dedup_text(entry)
        self.assertIn(entry["source"][0], text)


class TestBuildFtsFields(unittest.TestCase):

    def test_returns_expected_keys(self):
        fields = build_fts_fields(SAMPLE_ENTRIES[0])
        self.assertIn("title", fields)
        self.assertIn("text", fields)
        self.assertIn("tags", fields)

    def test_title_is_title(self):
        fields = build_fts_fields(SAMPLE_ENTRIES[0])
        self.assertEqual(fields["title"], SAMPLE_ENTRIES[0]["title"])

    def test_tags_joined(self):
        fields = build_fts_fields(SAMPLE_ENTRIES[0])
        for tag in SAMPLE_ENTRIES[0]["tags"]:
            self.assertIn(tag, fields["tags"])


if __name__ == "__main__":
    unittest.main()
