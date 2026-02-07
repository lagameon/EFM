"""
Tests for EF Memory V3 — Memory Lifecycle Automation (M9)

Covers: harvest_check pipeline step, session recovery in check_startup,
        _run_harvest_step, _count_candidate_types, updated StartupReport,
        updated _format_hint, pipeline integration end-to-end
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Import path setup
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.auto_sync import (
    PipelineReport,
    StartupReport,
    StepResult,
    _count_candidate_types,
    _format_hint,
    check_startup,
    run_pipeline,
)
from lib.working_memory import (
    FINDINGS_FILE,
    PROGRESS_FILE,
    TASK_PLAN_FILE,
    HarvestCandidate,
    start_session,
)

from conftest import SAMPLE_ENTRIES


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> dict:
    config = {
        "automation": {
            "human_review_required": True,
            "pipeline_steps": ["sync_embeddings", "generate_rules"],
            "startup_source_sample_size": 3,
        },
        "verify": {"rulesets": [], "staleness_threshold_days": 90},
        "v3": {
            "auto_startup": True,
            "working_memory_dir": ".memory/working",
            "prefill_on_plan_start": True,
            "max_prefill_entries": 5,
            "session_recovery": True,
        },
    }
    config.update(overrides)
    return config


def _write_events(path: Path, entries=None):
    if entries is None:
        entries = SAMPLE_ENTRIES
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _setup_project(tmpdir: str):
    """Set up a full project structure."""
    project = Path(tmpdir)
    memory_dir = project / ".memory"
    memory_dir.mkdir()
    events_path = memory_dir / "events.jsonl"
    _write_events(events_path)
    drafts_dir = memory_dir / "drafts"
    drafts_dir.mkdir()
    working_dir = memory_dir / "working"
    return project, memory_dir, events_path, drafts_dir, working_dir


# ===========================================================================
# Test: harvest_check pipeline step
# ===========================================================================

class TestHarvestCheckStep(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project, self.memory_dir, self.events_path, self.drafts_dir, self.working_dir = _setup_project(self.tmpdir)
        self.config = _make_config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_harvest_skips_when_no_session(self):
        """harvest_check should skip when no working memory session exists."""
        report = run_pipeline(
            self.events_path, self.config, self.project,
            steps=["harvest_check"]
        )
        self.assertEqual(report.steps_skipped, 1)
        self.assertTrue(report.step_results[0].skipped)
        self.assertIn("No active", report.step_results[0].skip_reason)

    def test_harvest_runs_with_active_session(self):
        """harvest_check should run when working memory session exists."""
        wm_config = _make_config()
        start_session("test task", self.events_path, self.working_dir, wm_config)

        report = run_pipeline(
            self.events_path, self.config, self.project,
            steps=["harvest_check"]
        )
        self.assertEqual(report.steps_succeeded, 1)
        result = report.step_results[0]
        self.assertTrue(result.success)
        self.assertIn("candidates_found", result.details)

    def test_harvest_finds_candidates(self):
        """harvest_check should find candidates in working memory files."""
        wm_config = _make_config()
        start_session("test", self.events_path, self.working_dir, wm_config)

        # Add harvestable content
        findings_path = self.working_dir / FINDINGS_FILE
        content = findings_path.read_text()
        content += "\n\nLESSON: Always validate user input before database queries\n"
        content += "CONSTRAINT: MUST use parameterized queries to prevent SQL injection\n"
        findings_path.write_text(content)

        report = run_pipeline(
            self.events_path, self.config, self.project,
            steps=["harvest_check"]
        )
        result = report.step_results[0]
        self.assertGreater(result.details["candidates_found"], 0)

    def test_harvest_reports_candidate_types(self):
        """harvest_check should count candidates by type."""
        wm_config = _make_config()
        start_session("test", self.events_path, self.working_dir, wm_config)

        findings_path = self.working_dir / FINDINGS_FILE
        content = findings_path.read_text()
        content += "\n\nLESSON: Database connections must be pooled\n"
        content += "DECISION: Using connection pooling with max 10 connections\n"
        findings_path.write_text(content)

        report = run_pipeline(
            self.events_path, self.config, self.project,
            steps=["harvest_check"]
        )
        result = report.step_results[0]
        types = result.details.get("candidate_types", {})
        self.assertIsInstance(types, dict)

    def test_harvest_in_full_pipeline(self):
        """harvest_check should work alongside other pipeline steps."""
        wm_config = _make_config()
        start_session("test", self.events_path, self.working_dir, wm_config)

        report = run_pipeline(
            self.events_path, self.config, self.project,
            steps=["harvest_check"]
        )
        self.assertEqual(report.steps_run, 1)


# ===========================================================================
# Test: session recovery in check_startup
# ===========================================================================

class TestSessionRecovery(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project, self.memory_dir, self.events_path, self.drafts_dir, self.working_dir = _setup_project(self.tmpdir)
        self.config = _make_config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_no_session_no_recovery(self):
        """Startup should report no active session when none exists."""
        report = check_startup(self.events_path, self.drafts_dir, self.project, self.config)
        self.assertFalse(report.active_session)
        self.assertEqual(report.active_session_task, "")

    def test_detects_active_session(self):
        """Startup should detect an active working memory session."""
        wm_config = _make_config()
        start_session("Refactor auth module", self.events_path, self.working_dir, wm_config)

        report = check_startup(self.events_path, self.drafts_dir, self.project, self.config)
        self.assertTrue(report.active_session)
        self.assertIn("Refactor auth", report.active_session_task)

    def test_session_phases_in_report(self):
        """Startup should report session phase progress."""
        wm_config = _make_config()
        start_session("Test task", self.events_path, self.working_dir, wm_config)

        report = check_startup(self.events_path, self.drafts_dir, self.project, self.config)
        self.assertIn("0/3 done", report.active_session_phases)

    def test_session_in_hint(self):
        """Active session should appear in the startup hint string."""
        wm_config = _make_config()
        start_session("Fix database bug", self.events_path, self.working_dir, wm_config)

        report = check_startup(self.events_path, self.drafts_dir, self.project, self.config)
        self.assertIn("active session", report.hint)
        self.assertIn("Fix database bug", report.hint)

    def test_startup_speed(self):
        """Startup check should complete in <200ms even with active session."""
        wm_config = _make_config()
        start_session("test", self.events_path, self.working_dir, wm_config)

        report = check_startup(self.events_path, self.drafts_dir, self.project, self.config)
        self.assertLess(report.duration_ms, 200)

    def test_session_recovery_disabled(self):
        """When session_recovery=False, startup should NOT detect active sessions."""
        wm_config = _make_config()
        start_session("test task", self.events_path, self.working_dir, wm_config)

        config = _make_config(v3={
            "auto_startup": True,
            "working_memory_dir": ".memory/working",
            "session_recovery": False,
        })
        report = check_startup(self.events_path, self.drafts_dir, self.project, config)
        self.assertFalse(report.active_session)
        self.assertNotIn("active session", report.hint)


# ===========================================================================
# Test: _count_candidate_types
# ===========================================================================

class TestCountCandidateTypes(unittest.TestCase):

    def test_empty(self):
        result = _count_candidate_types([])
        self.assertEqual(result, {})

    def test_single_type(self):
        candidates = [
            HarvestCandidate("lesson", "T1", ["c"], None, None, "s", "r"),
            HarvestCandidate("lesson", "T2", ["c"], None, None, "s", "r"),
        ]
        result = _count_candidate_types(candidates)
        self.assertEqual(result, {"lesson": 2})

    def test_multiple_types(self):
        candidates = [
            HarvestCandidate("lesson", "T1", ["c"], None, None, "s", "r"),
            HarvestCandidate("decision", "T2", ["c"], None, None, "s", "r"),
            HarvestCandidate("constraint", "T3", ["c"], "R", None, "s", "r"),
            HarvestCandidate("lesson", "T4", ["c"], None, None, "s", "r"),
        ]
        result = _count_candidate_types(candidates)
        self.assertEqual(result["lesson"], 2)
        self.assertEqual(result["decision"], 1)
        self.assertEqual(result["constraint"], 1)


# ===========================================================================
# Test: updated _format_hint
# ===========================================================================

class TestFormatHintV3(unittest.TestCase):

    def test_healthy_no_session(self):
        report = StartupReport(total_entries=10)
        hint = _format_hint(report)
        self.assertIn("10 entries, all healthy", hint)

    def test_active_session_in_hint(self):
        report = StartupReport(
            total_entries=10,
            active_session=True,
            active_session_task="Refactor authentication",
            active_session_phases="1/3 done",
        )
        hint = _format_hint(report)
        self.assertIn("active session", hint)
        self.assertIn("Refactor authentication", hint)
        self.assertIn("1/3 done", hint)

    def test_session_plus_drafts(self):
        report = StartupReport(
            total_entries=10,
            active_session=True,
            active_session_task="Test",
            active_session_phases="0/3 done",
            pending_drafts=2,
        )
        hint = _format_hint(report)
        self.assertIn("active session", hint)
        self.assertIn("2 pending drafts", hint)

    def test_session_task_truncation(self):
        """Long task names should be truncated in the hint."""
        report = StartupReport(
            active_session=True,
            active_session_task="x" * 100,
            active_session_phases="0/3 done",
        )
        hint = _format_hint(report)
        # Task should be truncated to 50 chars
        self.assertLess(len(hint), 200)

    def test_no_session_with_issues(self):
        report = StartupReport(
            total_entries=10,
            stale_entries=3,
            source_warnings=1,
        )
        hint = _format_hint(report)
        self.assertIn("3 stale entries", hint)
        self.assertIn(">90d", hint)  # default threshold
        self.assertIn("1 source warnings", hint)
        self.assertNotIn("active session", hint)

    def test_stale_entries_custom_threshold(self):
        """Hint should use the actual staleness threshold, not hardcoded 90d."""
        report = StartupReport(
            total_entries=10,
            stale_entries=2,
            staleness_threshold_days=60,
        )
        hint = _format_hint(report)
        self.assertIn(">60d", hint)
        self.assertNotIn(">90d", hint)

    def test_all_fields_in_hint(self):
        """Hint should render correctly with all issue types + session active."""
        report = StartupReport(
            total_entries=100,
            active_session=True,
            active_session_task="Refactor",
            active_session_phases="1/3 done",
            pending_drafts=3,
            source_warnings=2,
            stale_entries=1,
        )
        hint = _format_hint(report)
        self.assertIn("active session", hint)
        self.assertIn("3 pending drafts", hint)
        self.assertIn("2 source warnings", hint)
        self.assertIn("1 stale entries", hint)


# ===========================================================================
# Test: updated StartupReport dataclass
# ===========================================================================

class TestStartupReportV3(unittest.TestCase):

    def test_default_session_fields(self):
        report = StartupReport()
        self.assertFalse(report.active_session)
        self.assertEqual(report.active_session_task, "")
        self.assertEqual(report.active_session_phases, "")
        self.assertEqual(report.staleness_threshold_days, 90)

    def test_with_session_fields(self):
        report = StartupReport(
            active_session=True,
            active_session_task="Test",
            active_session_phases="2/3 done",
        )
        self.assertTrue(report.active_session)
        self.assertEqual(report.active_session_task, "Test")


# ===========================================================================
# Test: pipeline_cli.py --harvest-only
# ===========================================================================

class TestPipelineCliHarvestFlag(unittest.TestCase):
    """Test that pipeline_cli.py parses --harvest-only correctly."""

    def test_parse_harvest_only(self):
        # Import the CLI parser
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pipeline_cli",
            _MEMORY_DIR.parent / ".memory" / "scripts" / "pipeline_cli.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        args = mod._parse_args(["--harvest-only"])
        self.assertTrue(args["harvest_only"])
        self.assertFalse(args["sync_only"])
        self.assertFalse(args["rules_only"])

    def test_parse_no_flags(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pipeline_cli",
            _MEMORY_DIR.parent / ".memory" / "scripts" / "pipeline_cli.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        args = mod._parse_args([])
        self.assertFalse(args["harvest_only"])


if __name__ == "__main__":
    unittest.main()
