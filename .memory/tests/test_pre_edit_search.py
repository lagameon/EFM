"""Tests for pre_edit_search hook — PreToolUse hook for Edit/Write."""

import json
import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from io import StringIO

_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

# Import SearchReport and SearchResult for building mock return values
from lib.search import SearchReport, SearchResult


def _run_hook(stdin_data: dict, mock_search=None):
    """
    Helper: run the pre_edit_search hook's main() with mocked stdin/stdout.

    Args:
        stdin_data: dict to serialize as JSON stdin
        mock_search: if provided, patches lib.search.search_memory

    Returns:
        (stdout_output, exit_code)
    """
    stdin_json = json.dumps(stdin_data)
    stdout_buf = StringIO()

    patches = [
        patch("sys.stdin", StringIO(stdin_json)),
        patch("sys.stdout", stdout_buf),
    ]
    if mock_search is not None:
        patches.append(patch("lib.search.search_memory", mock_search))

    for p in patches:
        p.start()

    exit_code = 0
    try:
        # Re-import to pick up fresh module state with patches active
        import hooks.pre_edit_search as hook_mod
        hook_mod.main()
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    finally:
        for p in patches:
            p.stop()

    return stdout_buf.getvalue(), exit_code


class TestPreEditSearchSkips(unittest.TestCase):
    """Tests for fast-path skip conditions."""

    def test_skip_no_file_path(self):
        """No file_path in tool_input -> exit 0, no output."""
        output, code = _run_hook({"tool_input": {}})
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    def test_skip_memory_prefix(self):
        """Files inside .memory/ should be skipped."""
        # Use a path that will be relative to _PROJECT_ROOT and start with .memory/
        # The hook resolves relative_to(_PROJECT_ROOT); if that fails it uses
        # the raw path.  We pass a path starting with .memory/ directly.
        output, code = _run_hook({
            "tool_input": {"file_path": ".memory/lib/foo.py"}
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    def test_skip_config_extension(self):
        """Config files (.json, .yaml, etc.) should be skipped."""
        output, code = _run_hook({
            "tool_input": {"file_path": "/some/project/config.json"}
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")


class TestPreEditSearchQuery(unittest.TestCase):
    """Tests for query construction and enrichment."""

    def test_query_from_filename(self):
        """Query should include the filename stem and parent dir."""
        captured_query = {}

        def fake_search(query, events_path, **kwargs):
            captured_query["q"] = query
            return SearchReport(query=query, mode="basic")

        _run_hook(
            {"tool_input": {"file_path": "/project/src/auth.py"}},
            mock_search=fake_search,
        )

        q = captured_query.get("q", "")
        self.assertIn("auth", q)
        self.assertIn("src", q)

    def test_query_enrichment_old_string(self):
        """old_string content should enrich the query with extracted tokens."""
        captured_query = {}

        def fake_search(query, events_path, **kwargs):
            captured_query["q"] = query
            return SearchReport(query=query, mode="basic")

        _run_hook(
            {
                "tool_input": {
                    "file_path": "/project/src/auth.py",
                    "old_string": "def calculate_margin():\n    return price * factor",
                }
            },
            mock_search=fake_search,
        )

        q = captured_query.get("q", "")
        # Should contain base query parts
        self.assertIn("auth", q)
        # Should contain enriched tokens from old_string
        # At least one of these identifiers should appear
        self.assertTrue(
            "calculate_margin" in q or "factor" in q or "price" in q,
            f"Expected enrichment tokens in query, got: {q}",
        )


class TestPreEditSearchOutput(unittest.TestCase):
    """Tests for output formatting and search result handling."""

    def _make_report_with_results(self, results):
        """Helper to build a SearchReport with given SearchResults."""
        return SearchReport(
            query="test",
            mode="basic",
            total_found=len(results),
            results=results,
        )

    def test_output_json_format(self):
        """Output must be valid JSON with an 'additionalContext' key."""
        sr = SearchResult(
            entry_id="e1",
            entry={"title": "Test Title", "rule": "Test Rule"},
            score=0.9,
            search_mode="basic",
        )
        report = self._make_report_with_results([sr])

        def fake_search(query, events_path, **kwargs):
            return report

        output, code = _run_hook(
            {"tool_input": {"file_path": "/project/src/utils.py"}},
            mock_search=fake_search,
        )

        self.assertEqual(code, 0)
        self.assertTrue(len(output.strip()) > 0, "Expected non-empty output")
        parsed = json.loads(output)
        self.assertIn("additionalContext", parsed)

    def test_search_results_formatted(self):
        """Two search results should produce formatted output with titles and rules."""
        sr1 = SearchResult(
            entry_id="e1",
            entry={"title": "First Finding", "rule": "Always do X"},
            score=0.9,
            search_mode="basic",
        )
        sr2 = SearchResult(
            entry_id="e2",
            entry={"title": "Second Finding", "rule": None},
            score=0.5,
            search_mode="basic",
        )
        report = self._make_report_with_results([sr1, sr2])

        def fake_search(query, events_path, **kwargs):
            return report

        output, code = _run_hook(
            {"tool_input": {"file_path": "/project/src/service.py"}},
            mock_search=fake_search,
        )

        parsed = json.loads(output)
        ctx = parsed["additionalContext"]
        self.assertIn("First Finding", ctx)
        self.assertIn("Always do X", ctx)
        self.assertIn("Second Finding", ctx)
        # Second result has no rule, so "Rule:" should appear only once
        self.assertEqual(ctx.count("Rule:"), 1)

    def test_search_exception_silent(self):
        """If search raises, hook should not crash — exit 0, no output."""

        def exploding_search(query, events_path, **kwargs):
            raise RuntimeError("search kaboom")

        output, code = _run_hook(
            {"tool_input": {"file_path": "/project/src/handler.py"}},
            mock_search=exploding_search,
        )

        self.assertEqual(code, 0)
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
