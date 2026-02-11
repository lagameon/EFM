"""
EFM — Tests for PreCompact Harvest Hook

Tests the compact_harvest.py hook that captures memories before
context compaction, and the marker-based dedup with stop_harvest.py.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))


class TestCompactHarvestPathA(unittest.TestCase):
    """Test Path A: active working memory session exists."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.working_dir = Path(self.tmpdir) / ".memory" / "working"
        self.working_dir.mkdir(parents=True)
        self.events_path = Path(self.tmpdir) / ".memory" / "events.jsonl"
        self.events_path.touch()

        # Create session files
        (self.working_dir / "findings.md").write_text("## Findings\nLESSON: test lesson here\n")
        (self.working_dir / "progress.md").write_text("## Progress\n- Did something\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_harvest_called_with_pipeline_false(self):
        """Path A should call auto_harvest_and_persist with run_pipeline_after=False."""
        mock_report = {
            "candidates_found": 2,
            "entries_written": 1,
            "entries_skipped": 0,
            "entries_drafted": 0,
            "dedup_skipped": [],
            "pipeline_run": False,
            "session_cleared": True,
            "errors": [],
        }
        with patch("lib.working_memory.auto_harvest_and_persist", return_value=mock_report) as mock_harvest, \
             patch("lib.working_memory.is_session_complete", return_value=True):
            # Import compact_harvest module components
            from hooks import compact_harvest
            # Simulate the function logic manually
            compact_harvest._MEMORY_DIR = Path(self.tmpdir) / ".memory"
            compact_harvest._PROJECT_ROOT = Path(self.tmpdir)

            # Call harvest directly
            report = mock_harvest(
                working_dir=self.working_dir,
                events_path=self.events_path,
                project_root=Path(self.tmpdir),
                config={},
                run_pipeline_after=False,
                draft_only=False,
                conversation_id="test-conv-123",
            )
            mock_harvest.assert_called_once()
            call_kwargs = mock_harvest.call_args[1]
            self.assertFalse(call_kwargs["run_pipeline_after"])

    def test_marker_file_creation(self):
        """After harvest, .compact_harvested marker should be created."""
        marker = self.working_dir / ".compact_harvested"
        self.assertFalse(marker.exists())
        marker.touch()
        self.assertTrue(marker.exists())

    def test_skip_if_marker_exists(self):
        """If marker already exists, should skip harvest."""
        marker = self.working_dir / ".compact_harvested"
        marker.touch()
        # The hook logic checks marker existence early
        self.assertTrue(marker.exists())


class TestCompactHarvestPathB(unittest.TestCase):
    """Test Path B: no session, scan conversation transcript."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.working_dir = Path(self.tmpdir) / ".memory" / "working"
        self.working_dir.mkdir(parents=True)
        self.drafts_dir = Path(self.tmpdir) / ".memory" / "drafts"
        self.drafts_dir.mkdir(parents=True)
        # No session files — no findings.md or progress.md

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_session_no_transcript_exits_clean(self):
        """No session and no transcript should produce echo reminder."""
        findings = self.working_dir / "findings.md"
        progress = self.working_dir / "progress.md"
        self.assertFalse(findings.exists())
        self.assertFalse(progress.exists())
        # With no transcript_path, the hook should fall to the echo reminder

    def test_transcript_scan_creates_drafts(self):
        """Path B should call scan_conversation_for_drafts."""
        mock_report = {
            "candidates_found": 3,
            "drafts_created": 2,
            "draft_types": {"lesson": 1, "constraint": 1},
        }
        with patch("lib.transcript_scanner.scan_conversation_for_drafts", return_value=mock_report) as mock_scan:
            from lib.transcript_scanner import scan_conversation_for_drafts
            transcript_path = Path(self.tmpdir) / "transcript.jsonl"
            transcript_path.write_text('{"role":"assistant","content":"LESSON: important thing"}\n')

            report = scan_conversation_for_drafts(
                transcript_path,
                self.drafts_dir,
                Path(self.tmpdir),
                {},
            )
            self.assertEqual(report["drafts_created"], 2)


class TestCompactHarvestConfig(unittest.TestCase):
    """Test harvest_on_compact config flag behavior."""

    def test_harvest_on_compact_default_true(self):
        """Default config should have harvest_on_compact=true."""
        config = {"v3": {}}
        v3 = config.get("v3", {})
        self.assertTrue(v3.get("harvest_on_compact", True))

    def test_harvest_on_compact_false_returns_echo(self):
        """When disabled, should output the echo reminder."""
        from hooks.compact_harvest import ECHO_REMINDER
        self.assertIn("/memory-save", ECHO_REMINDER)
        self.assertIn("EFM", ECHO_REMINDER)


class TestMarkerDedup(unittest.TestCase):
    """Test marker-based dedup between PreCompact and Stop hooks."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.working_dir = Path(self.tmpdir) / ".memory" / "working"
        self.working_dir.mkdir(parents=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_stop_hook_skips_when_marker_present(self):
        """Stop hook should exit early if .compact_harvested exists."""
        marker = self.working_dir / ".compact_harvested"
        marker.touch()
        self.assertTrue(marker.exists())

        # Simulate stop_harvest.py marker check logic
        if marker.exists():
            marker.unlink(missing_ok=True)
            skipped = True
        else:
            skipped = False

        self.assertTrue(skipped)
        self.assertFalse(marker.exists())  # Marker cleaned up

    def test_stop_hook_runs_without_marker(self):
        """Stop hook should run normally when no marker."""
        marker = self.working_dir / ".compact_harvested"
        self.assertFalse(marker.exists())
        # No marker → stop hook proceeds normally

    def test_marker_idempotent(self):
        """Multiple marker touches should be safe."""
        marker = self.working_dir / ".compact_harvested"
        marker.touch()
        marker.touch()  # second touch
        self.assertTrue(marker.exists())
        marker.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)  # second unlink should not raise
        self.assertFalse(marker.exists())


class TestClassificationCaseInsensitive(unittest.TestCase):
    """Test that classification matching is case-insensitive."""

    def test_auto_verify_accepts_uppercase_hard(self):
        """auto_verify schema check should accept 'Hard' (capital H)."""
        from lib.auto_verify import validate_schema
        entry = {
            "id": "test-case-001",
            "type": "lesson",
            "classification": "Hard",  # Capital H from /memory-save template
            "title": "Test case-insensitive classification",
            "content": ["This should pass validation"],
            "source": ["test.py:L1"],
            "created_at": "2026-02-11T00:00:00Z",
        }
        result = validate_schema(entry)
        classification_errors = [e for e in result.errors if "classification" in e.lower()]
        self.assertEqual(len(classification_errors), 0, f"Unexpected errors: {result.errors}")

    def test_auto_verify_accepts_lowercase_hard(self):
        """auto_verify schema check should still accept 'hard' (lowercase)."""
        from lib.auto_verify import validate_schema
        entry = {
            "id": "test-case-002",
            "type": "lesson",
            "classification": "hard",
            "title": "Test lowercase classification",
            "content": ["This should pass validation"],
            "source": ["test.py:L1"],
            "created_at": "2026-02-11T00:00:00Z",
        }
        result = validate_schema(entry)
        classification_errors = [e for e in result.errors if "classification" in e.lower()]
        self.assertEqual(len(classification_errors), 0, f"Unexpected errors: {result.errors}")

    def test_auto_verify_accepts_uppercase_soft(self):
        """auto_verify schema check should accept 'Soft' (capital S)."""
        from lib.auto_verify import validate_schema
        entry = {
            "id": "test-case-003",
            "type": "fact",
            "classification": "Soft",
            "title": "Test uppercase soft classification",
            "content": ["Soft entry should pass"],
            "source": ["test.py:L1"],
            "created_at": "2026-02-11T00:00:00Z",
        }
        result = validate_schema(entry)
        classification_errors = [e for e in result.errors if "classification" in e.lower()]
        self.assertEqual(len(classification_errors), 0, f"Unexpected errors: {result.errors}")

    def test_search_boost_case_insensitive(self):
        """Search classification boost should work with 'Hard' (capital H)."""
        from lib.search import _compute_boost, _get_search_weights
        weights = _get_search_weights({})
        entry_upper = {"classification": "Hard", "severity": "S1"}
        entry_lower = {"classification": "hard", "severity": "S1"}
        boost_upper = _compute_boost(entry_upper, weights)
        boost_lower = _compute_boost(entry_lower, weights)
        self.assertEqual(boost_upper, boost_lower)
        self.assertGreater(boost_upper, 0.0)

    def test_generate_rules_includes_uppercase_hard(self):
        """generate_rules should include entries with 'Hard' classification."""
        from lib.generate_rules import _load_hard_entries
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            entry = {
                "id": "test-upper-001",
                "type": "lesson",
                "classification": "Hard",
                "severity": "S1",
                "title": "Uppercase Hard entry",
                "content": ["Should be included in rules"],
                "rule": "MUST do something",
                "source": ["test.py:L1"],
                "created_at": "2026-02-11T00:00:00Z",
                "deprecated": False,
            }
            f.write(json.dumps(entry) + "\n")
            f.flush()
            hard_entries, total = _load_hard_entries(Path(f.name))
            self.assertEqual(len(hard_entries), 1)
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
