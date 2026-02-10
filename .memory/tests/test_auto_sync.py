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
from lib.auto_capture import create_draft, list_drafts


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
    from lib.config_presets import EFM_VERSION
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
        "efm_version": EFM_VERSION,
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

    def test_hint_format_with_drafts(self):
        entry = _make_valid_entry()
        create_draft(entry, self.drafts_dir)
        self.events_path.write_text("")

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertIn("EF Memory", report.hint)
        self.assertIn("pending drafts", report.hint)

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
        self.assertIn("EF Memory", hint)
        self.assertIn("3 pending drafts", hint)
        self.assertIn("1 source warnings", hint)
        self.assertIn("2 stale entries", hint)

    def test_no_issues(self):
        report = StartupReport(total_entries=5)
        hint = _format_hint(report)
        self.assertIn("healthy", hint)
        self.assertIn("5", hint)

    def test_only_drafts(self):
        report = StartupReport(pending_drafts=2, total_entries=5)
        hint = _format_hint(report)
        self.assertIn("2 pending drafts", hint)
        self.assertNotIn("source", hint)


    def test_drafts_with_age(self):
        report = StartupReport(
            pending_drafts=4,
            oldest_draft_age_days=3,
            total_entries=5,
        )
        hint = _format_hint(report)
        self.assertIn("4 pending drafts", hint)
        self.assertIn("oldest: 3d", hint)
        self.assertIn("/memory-save", hint)

    def test_drafts_expired_with_remaining(self):
        report = StartupReport(
            pending_drafts=2,
            drafts_expired=3,
            oldest_draft_age_days=4,
            total_entries=5,
        )
        hint = _format_hint(report)
        self.assertIn("auto-expired 3 stale drafts", hint)
        self.assertIn("2 pending", hint)
        self.assertIn("/memory-save", hint)

    def test_drafts_expired_none_remaining(self):
        report = StartupReport(
            pending_drafts=0,
            drafts_expired=5,
            total_entries=10,
        )
        hint = _format_hint(report)
        self.assertIn("auto-expired 5 stale drafts", hint)
        self.assertNotIn("pending", hint)

    def test_fresh_drafts_no_age_suffix(self):
        """When oldest_draft_age_days is 0, no age suffix should appear."""
        report = StartupReport(
            pending_drafts=1,
            oldest_draft_age_days=0,
            total_entries=5,
        )
        hint = _format_hint(report)
        self.assertIn("1 pending drafts", hint)
        self.assertNotIn("oldest", hint)
        self.assertIn("/memory-save", hint)


# ---------------------------------------------------------------------------
# TestStartupDraftExpiry (integration)
# ---------------------------------------------------------------------------

class TestStartupDraftExpiry(unittest.TestCase):
    """Integration tests for draft auto-expire during startup."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.events_path.write_text("")
        self.drafts_dir = self.tmpdir / "drafts"
        self.config = _make_config()
        self.config["v3"] = {"draft_auto_expire_days": 7}

    def _create_aged_draft(self, title: str, age_days: int):
        """Create a draft and backdate its timestamp."""
        entry = _make_valid_entry(title=title)
        info = create_draft(entry, self.drafts_dir)
        data = json.loads(info.path.read_text())
        old_ts = (
            datetime.now(timezone.utc) - timedelta(days=age_days)
        ).isoformat()
        data["_meta"]["capture_timestamp"] = old_ts
        info.path.write_text(json.dumps(data, indent=2) + "\n")

    def test_startup_expires_old_drafts(self):
        self._create_aged_draft("old draft expire", age_days=10)
        self._create_aged_draft("fresh draft keep", age_days=2)

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertEqual(report.drafts_expired, 1)
        self.assertEqual(report.pending_drafts, 1)

    def test_startup_reports_oldest_age(self):
        self._create_aged_draft("three days old", age_days=3)
        self._create_aged_draft("five days old", age_days=5)

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertEqual(report.oldest_draft_age_days, 5)

    def test_startup_hint_includes_expiry(self):
        self._create_aged_draft("stale for test", age_days=14)
        self._create_aged_draft("fresh for test", age_days=1)

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertIn("auto-expired", report.hint)
        self.assertIn("pending", report.hint)

    def test_startup_no_expire_when_disabled(self):
        self.config["v3"]["draft_auto_expire_days"] = 0
        self._create_aged_draft("ancient draft disable", age_days=365)

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertEqual(report.drafts_expired, 0)
        self.assertEqual(report.pending_drafts, 1)

    def test_startup_speed_with_expiry(self):
        """Startup should still complete in <200ms with expiry enabled."""
        for i in range(10):
            self._create_aged_draft(f"speed draft {i}", age_days=i)

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, self.config
        )
        self.assertLess(report.duration_ms, 200)


class TestEvolutionStep(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.config = _make_config()
        self.config["evolution"] = {
            "confidence_half_life_days": 120,
            "deprecation_confidence_threshold": 0.3,
        }

    def test_evolution_step_runs(self):
        now = datetime.now(timezone.utc).isoformat()
        entry = _make_valid_entry(created_at=now, source=["PR #123"])
        self.events_path.write_text(json.dumps(entry) + "\n")

        report = run_pipeline(
            self.events_path, self.config, self.tmpdir,
            steps=["evolution_check"],
        )
        self.assertEqual(report.steps_run, 1)
        result = report.step_results[0]
        self.assertEqual(result.step, "evolution_check")
        self.assertTrue(result.success)
        self.assertIn("total_entries", result.details)

    def test_evolution_step_empty_events(self):
        self.events_path.write_text("")
        report = run_pipeline(
            self.events_path, self.config, self.tmpdir,
            steps=["evolution_check"],
        )
        self.assertTrue(report.step_results[0].success)


class TestStartupWithDeprecated(unittest.TestCase):

    def test_deprecated_not_counted_in_total(self):
        tmpdir = Path(tempfile.mkdtemp())
        events_path = tmpdir / "events.jsonl"
        drafts_dir = tmpdir / "drafts"
        drafts_dir.mkdir()

        now = datetime.now(timezone.utc).isoformat()
        active = _make_valid_entry(
            id="lesson-act-11111111",
            created_at=now,
            source=["PR #1"],
        )
        deprecated = _make_valid_entry(
            id="lesson-dep-22222222",
            created_at=now,
            source=["PR #2"],
            deprecated=True,
        )
        with open(events_path, "w") as f:
            f.write(json.dumps(active) + "\n")
            f.write(json.dumps(deprecated) + "\n")

        config = _make_config()
        report = check_startup(events_path, drafts_dir, tmpdir, config)
        self.assertEqual(report.total_entries, 1)  # Only active


# ---------------------------------------------------------------------------
# TestReasoningStep (M6 integration)
# ---------------------------------------------------------------------------

class TestReasoningStep(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.config = _make_config()
        self.config["reasoning"] = {
            "enabled": False,
            "correlation_threshold": 2,
            "synthesis_min_group_size": 3,
        }

    def test_reasoning_step_runs(self):
        now = datetime.now(timezone.utc).isoformat()
        entry = _make_valid_entry(created_at=now, source=["PR #123"])
        self.events_path.write_text(json.dumps(entry) + "\n")

        report = run_pipeline(
            self.events_path, self.config, self.tmpdir,
            steps=["reasoning_check"],
        )
        self.assertEqual(report.steps_run, 1)
        result = report.step_results[0]
        self.assertEqual(result.step, "reasoning_check")
        self.assertTrue(result.success)
        self.assertIn("total_entries", result.details)
        self.assertIn("mode", result.details)
        self.assertEqual(result.details["mode"], "heuristic")

    def test_reasoning_step_empty_events(self):
        self.events_path.write_text("")
        report = run_pipeline(
            self.events_path, self.config, self.tmpdir,
            steps=["reasoning_check"],
        )
        self.assertTrue(report.step_results[0].success)
        self.assertEqual(report.step_results[0].details["total_entries"], 0)

    def test_reasoning_step_details_structure(self):
        now = datetime.now(timezone.utc).isoformat()
        entry = _make_valid_entry(created_at=now, source=["PR #123"])
        self.events_path.write_text(json.dumps(entry) + "\n")

        report = run_pipeline(
            self.events_path, self.config, self.tmpdir,
            steps=["reasoning_check"],
        )
        details = report.step_results[0].details
        self.assertIn("correlation_groups", details)
        self.assertIn("contradiction_pairs", details)
        self.assertIn("synthesis_suggestions", details)
        self.assertIn("llm_calls", details)
        self.assertEqual(details["llm_calls"], 0)

    def test_reasoning_not_in_default_steps(self):
        """reasoning_check should NOT be in default pipeline steps."""
        self.events_path.write_text("")
        report = run_pipeline(self.events_path, self.config, self.tmpdir)
        step_names = [r.step for r in report.step_results]
        self.assertNotIn("reasoning_check", step_names)

    def test_reasoning_with_multiple_entries(self):
        now = datetime.now(timezone.utc).isoformat()
        entries = [
            _make_valid_entry(id="lesson-a-11111111", created_at=now, source=["PR #1"]),
            _make_valid_entry(id="lesson-b-22222222", created_at=now, source=["PR #2"],
                              title="Different lesson about testing"),
        ]
        with open(self.events_path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        report = run_pipeline(
            self.events_path, self.config, self.tmpdir,
            steps=["reasoning_check"],
        )
        self.assertTrue(report.step_results[0].success)
        self.assertEqual(report.step_results[0].details["total_entries"], 2)


# ===========================================================================
# Test: Version Check (Step 4)
# ===========================================================================

class TestVersionCheck(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.drafts_dir = self.tmpdir / "drafts"
        self.drafts_dir.mkdir()
        self.events_path.write_text("")

    def test_detects_update_available(self):
        """When installed version differs from current, update_available should be True."""
        from lib.config_presets import EFM_VERSION
        config = _make_config()
        config["efm_version"] = "3.0.0"  # Older than current

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, config
        )
        if EFM_VERSION != "3.0.0":
            self.assertTrue(report.update_available)
            self.assertEqual(report.efm_version_installed, "3.0.0")

    def test_no_update_when_current(self):
        """When installed version matches current, no update available."""
        from lib.config_presets import EFM_VERSION
        config = _make_config()
        config["efm_version"] = EFM_VERSION

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, config
        )
        self.assertFalse(report.update_available)

    def test_hint_shows_update(self):
        """Startup hint should mention update when available."""
        from lib.config_presets import EFM_VERSION
        config = _make_config()
        config["efm_version"] = "2.0.0"

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, config
        )
        if EFM_VERSION != "2.0.0":
            self.assertIn("update available", report.hint)
            self.assertIn("--upgrade", report.hint)

    def test_efm_version_format(self):
        """EFM_VERSION should be a valid semver string."""
        from lib.config_presets import EFM_VERSION
        import re
        self.assertRegex(EFM_VERSION, r"^\d+\.\d+\.\d+$")

    def test_efm_version_type(self):
        """EFM_VERSION should be a string."""
        from lib.config_presets import EFM_VERSION
        self.assertIsInstance(EFM_VERSION, str)


# ===========================================================================
# Test: Waste Ratio Enhancement (Step 5)
# ===========================================================================

class TestWasteRatio(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.drafts_dir = self.tmpdir / "drafts"
        self.drafts_dir.mkdir()

    def test_hint_includes_waste_lines(self):
        """Compact hint should include waste_lines count."""
        # Create events with duplicates to trigger compaction
        now = datetime.now(timezone.utc).isoformat()
        entry = _make_valid_entry(created_at=now, source=["PR #1"])
        # Write same entry 5 times (waste ratio = 5.0)
        with open(self.events_path, "w") as f:
            for _ in range(5):
                f.write(json.dumps(entry) + "\n")

        config = _make_config()
        config["compaction"] = {"auto_suggest_threshold": 2.0}
        from lib.config_presets import EFM_VERSION
        config["efm_version"] = EFM_VERSION  # Prevent update hint noise

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, config
        )
        if report.compaction_suggested:
            self.assertIn("obsolete lines", report.hint)

    def test_healthy_no_waste_in_hint(self):
        """When healthy, no waste info should appear in hint."""
        now = datetime.now(timezone.utc).isoformat()
        entry = _make_valid_entry(created_at=now, source=["PR #1"])
        self.events_path.write_text(json.dumps(entry) + "\n")

        config = _make_config()
        from lib.config_presets import EFM_VERSION
        config["efm_version"] = EFM_VERSION

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, config
        )
        if not report.compaction_suggested:
            self.assertNotIn("obsolete", report.hint)

    def test_waste_lines_computed(self):
        """waste_lines should equal total_lines - active_count."""
        now = datetime.now(timezone.utc).isoformat()
        entry1 = _make_valid_entry(id="lesson-a-11111111", created_at=now, source=["PR #1"])
        entry2 = _make_valid_entry(id="lesson-b-22222222", created_at=now, source=["PR #2"], title="Second entry")
        # Write 3 lines total but only 2 unique entries
        with open(self.events_path, "w") as f:
            f.write(json.dumps(entry1) + "\n")
            f.write(json.dumps(entry1) + "\n")  # duplicate
            f.write(json.dumps(entry2) + "\n")

        config = _make_config()
        from lib.config_presets import EFM_VERSION
        config["efm_version"] = EFM_VERSION

        report = check_startup(
            self.events_path, self.drafts_dir, self.tmpdir, config
        )
        self.assertEqual(report.waste_lines, 1)  # 3 lines - 2 active = 1 waste


if __name__ == "__main__":
    unittest.main()
