"""
Tests for EF Memory V2 — Auto-Sync (Pipeline Orchestration)

Covers: run_pipeline, check_startup, StepResult, PipelineReport, StartupReport
"""

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Import path setup
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.auto_sync import (
    PipelineReport,
    StartupReport,
    StepResult,
    _format_hint,
    check_startup,
    run_pipeline,
)
from lib.auto_capture import create_draft


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_valid_entry(**overrides) -> dict:
    entry = {
        "id": "lesson-inc036-a3f8c2d1",
        "type": "lesson",
        "classification": "hard",
        "severity": "S1",
        "title": "Rolling statistics without shift(1) caused inflation",
        "content": [
            "42 rolling/ewm/pct_change calls missing shift(1)",
            "Model learned to explain past, not predict future",
        ],
        "rule": "shift(1) MUST precede any rolling()",
        "implication": "Backtest returns inflated 100-1000x",
        "source": ["docs/INCIDENTS.md#INC-036:L553-L699"],
        "tags": ["leakage"],
        "created_at": "2026-02-01T14:30:00Z",
        "deprecated": False,
    }
    entry.update(overrides)
    return entry


def _make_config(**overrides) -> dict:
    config = {
        "embedding": {
            "enabled": False,
            "storage": {"db_path": ".memory/vectors.db"},
            "sync": {"batch_size": 20},
        },
        "verify": {"staleness_threshold_days": 90},
        "automation": {
            "startup_check": True,
            "pipeline_steps": ["sync_embeddings", "generate_rules"],
            "dedup_threshold": 0.85,
            "startup_source_sample_size": 10,
        },
    }
    config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# TestRunPipeline
# ---------------------------------------------------------------------------

class TestRunPipeline(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.config = _make_config()

    def test_full_pipeline_both_steps(self):
        self.events_path.write_text("")
        report = run_pipeline(self.events_path, self.config, self.tmpdir)
        self.assertEqual(report.steps_run, 2)
        self.assertEqual(len(report.step_results), 2)

    def test_sync_only(self):
        self.events_path.write_text("")
        report = run_pipeline(
            self.events_path, self.config, self.tmpdir,
            steps=["sync_embeddings"],
        )
        self.assertEqual(report.steps_run, 1)
        self.assertEqual(report.step_results[0].step, "sync_embeddings")

    def test_rules_only(self):
        self.events_path.write_text("")
        report = run_pipeline(
            self.events_path, self.config, self.tmpdir,
            steps=["generate_rules"],
        )
        self.assertEqual(report.steps_run, 1)
        self.assertEqual(report.step_results[0].step, "generate_rules")

    def test_embedding_disabled_still_runs(self):
        """Sync step should succeed even with embedding disabled (FTS mode)."""
        entry = _make_valid_entry()
        self.events_path.write_text(json.dumps(entry) + "\n")

        report = run_pipeline(
            self.events_path, self.config, self.tmpdir,
            steps=["sync_embeddings"],
        )
        self.assertTrue(report.step_results[0].success)

    def test_unknown_step(self):
        self.events_path.write_text("")
        report = run_pipeline(
            self.events_path, self.config, self.tmpdir,
            steps=["unknown_step"],
        )
        self.assertEqual(report.steps_failed, 1)
        self.assertFalse(report.step_results[0].success)

    def test_empty_events(self):
        self.events_path.write_text("")
        report = run_pipeline(self.events_path, self.config, self.tmpdir)
        # Should not crash
        self.assertGreater(report.duration_ms, 0)

    def test_pipeline_report_timing(self):
        self.events_path.write_text("")
        report = run_pipeline(self.events_path, self.config, self.tmpdir)
        self.assertGreater(report.duration_ms, 0)

    def test_default_steps_from_config(self):
        self.events_path.write_text("")
        config = _make_config()
        config["automation"]["pipeline_steps"] = ["generate_rules"]
        report = run_pipeline(self.events_path, config, self.tmpdir)
        self.assertEqual(report.steps_run, 1)
        self.assertEqual(report.step_results[0].step, "generate_rules")

    def test_step_failure_doesnt_block_next(self):
        """If sync fails, rules should still run."""
        self.events_path.write_text("")
        # Use a deliberately broken config for sync
        config = _make_config()
        config["embedding"]["storage"]["db_path"] = "/dev/null/impossible/path.db"

        report = run_pipeline(
            self.events_path, config, self.tmpdir,
            steps=["sync_embeddings", "generate_rules"],
        )
        # Even if sync fails, rules should have been attempted
        self.assertEqual(report.steps_run, 2)
        # At least rules step should succeed
        rules_result = report.step_results[1]
        self.assertTrue(rules_result.success)


# ---------------------------------------------------------------------------
# TestCheckStartup
# ---------------------------------------------------------------------------

class TestCheckStartup(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.drafts_dir = self.tmpdir / "drafts"
        self.config = _make_config()

    def test_no_issues(self):
        now = datetime.now(timezone.utc).isoformat()
        # Use PR source (always OK, no file needed)
        entry = _make_valid_entry(created_at=now, source=["PR #123"])
        self.events_path.write_text(json.dumps(entry) + "\n")
        self.drafts_dir.mkdir()

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertEqual(report.pending_drafts, 0)
        self.assertIn("healthy", report.hint)

    def test_pending_drafts_counted(self):
        self.events_path.write_text("")
        entry = _make_valid_entry()
        create_draft(entry, self.drafts_dir)
        create_draft(
            _make_valid_entry(id="lesson-test2-22222222", title="Second"),
            self.drafts_dir,
        )

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertEqual(report.pending_drafts, 2)

    def test_stale_entries_counted(self):
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        entry = _make_valid_entry(created_at=old)
        self.events_path.write_text(json.dumps(entry) + "\n")
        self.drafts_dir.mkdir()

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertEqual(report.stale_entries, 1)

    def test_source_warnings_counted(self):
        # Source points to nonexistent file
        entry = _make_valid_entry(
            created_at=datetime.now(timezone.utc).isoformat(),
            source=["src/nonexistent.py:L1-L10"],
        )
        self.events_path.write_text(json.dumps(entry) + "\n")
        self.drafts_dir.mkdir()

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertEqual(report.source_warnings, 1)

    def test_hint_format_chinese(self):
        entry = _make_valid_entry()
        create_draft(entry, self.drafts_dir)
        self.events_path.write_text("")

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertIn("\u53d1\u73b0", report.hint)
        self.assertIn("\u5f85\u5ba1\u8bb0\u5fc6", report.hint)

    def test_hint_format_healthy(self):
        now = datetime.now(timezone.utc).isoformat()
        # Use PR source (always OK, no file needed)
        entry = _make_valid_entry(created_at=now, source=["PR #123"])
        self.events_path.write_text(json.dumps(entry) + "\n")
        self.drafts_dir.mkdir()

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertIn("healthy", report.hint)

    def test_fast_execution(self):
        self.events_path.write_text("")
        self.drafts_dir.mkdir()

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertLess(report.duration_ms, 200)

    def test_empty_events_and_drafts(self):
        self.events_path.write_text("")
        self.drafts_dir.mkdir()

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertEqual(report.total_entries, 0)
        self.assertEqual(report.pending_drafts, 0)
        self.assertIn("healthy", report.hint)

    def test_config_threshold_used(self):
        # Entry 40 days old, threshold 30 → stale
        age_40 = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        entry = _make_valid_entry(created_at=age_40)
        self.events_path.write_text(json.dumps(entry) + "\n")
        self.drafts_dir.mkdir()

        config = _make_config()
        config["verify"]["staleness_threshold_days"] = 30

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, config
        )
        self.assertEqual(report.stale_entries, 1)


# ---------------------------------------------------------------------------
# TestFormatHint
# ---------------------------------------------------------------------------

class TestFormatHint(unittest.TestCase):

    def test_all_issues(self):
        report = StartupReport(
            pending_drafts=3,
            source_warnings=1,
            stale_entries=2,
            total_entries=15,
        )
        hint = _format_hint(report)
        self.assertIn("\u53d1\u73b0", hint)
        self.assertIn("3", hint)
        self.assertIn("1", hint)
        self.assertIn("2", hint)

    def test_no_issues(self):
        report = StartupReport(total_entries=5)
        hint = _format_hint(report)
        self.assertIn("healthy", hint)
        self.assertIn("5", hint)

    def test_only_drafts(self):
        report = StartupReport(pending_drafts=2, total_entries=5)
        hint = _format_hint(report)
        self.assertIn("\u5f85\u5ba1\u8bb0\u5fc6", hint)
        self.assertNotIn("source", hint)


if __name__ == "__main__":
    unittest.main()
