"""
Tests for EF Memory V3 — Working Memory (PWF Integration)

Covers: start_session, resume_session, get_session_status,
        harvest_session, read_plan_summary, clear_session,
        _extract_candidates, _extract_field, _count_phases,
        _get_current_phase, _search_for_prefill,
        template generators, PrefillEntry, dataclasses
"""

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import path setup
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.working_memory import (
    FINDINGS_FILE,
    PROGRESS_FILE,
    TASK_PLAN_FILE,
    HarvestCandidate,
    HarvestReport,
    PrefillEntry,
    SessionResumeReport,
    SessionStartReport,
    SessionStatus,
    _count_phases,
    _extract_candidates,
    _extract_field,
    _generate_findings,
    _generate_progress,
    _generate_task_plan,
    _get_current_phase,
    clear_session,
    get_session_status,
    harvest_session,
    read_plan_summary,
    resume_session,
    start_session,
)

from conftest import SAMPLE_ENTRIES


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> dict:
    config = {
        "v3": {
            "auto_startup": True,
            "working_memory_dir": ".memory/working",
            "prefill_on_plan_start": True,
            "max_prefill_entries": 5,
        },
    }
    config.update(overrides)
    return config


def _write_events(path: Path, entries=None) -> None:
    """Write sample entries to events.jsonl."""
    if entries is None:
        entries = SAMPLE_ENTRIES
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _setup_project(tmpdir: str):
    """Set up a minimal project structure in tmpdir."""
    project = Path(tmpdir)
    memory_dir = project / ".memory"
    memory_dir.mkdir()
    working_dir = memory_dir / "working"
    events_path = memory_dir / "events.jsonl"
    _write_events(events_path)
    return project, working_dir, events_path


# ===========================================================================
# Test: Template generators
# ===========================================================================

class TestGenerateTaskPlan(unittest.TestCase):

    def test_contains_task_description(self):
        content = _generate_task_plan("Refactor auth module")
        self.assertIn("Refactor auth module", content)

    def test_contains_phases(self):
        content = _generate_task_plan("test task")
        self.assertIn("Phase 1", content)
        self.assertIn("Phase 2", content)
        self.assertIn("Phase 3", content)

    def test_contains_created_timestamp(self):
        content = _generate_task_plan("test")
        self.assertIn("Created", content)

    def test_contains_acceptance_criteria(self):
        content = _generate_task_plan("test")
        self.assertIn("Acceptance Criteria", content)


class TestGenerateFindings(unittest.TestCase):

    def test_no_prefill(self):
        content = _generate_findings("test")
        self.assertIn("Findings", content)
        self.assertIn("Session Discoveries", content)
        self.assertNotIn("Pre-loaded Context", content)

    def test_with_prefill(self):
        prefill = [PrefillEntry(
            entry_id="test-1",
            title="Test lesson",
            classification="hard",
            severity="S1",
            rule="MUST test",
            source=["src/test.py:L1-L10"],
            score=0.85,
        )]
        content = _generate_findings("test", prefill)
        self.assertIn("Pre-loaded Context", content)
        self.assertIn("Test lesson", content)
        self.assertIn("MUST test", content)
        self.assertIn("0.85", content)

    def test_prefill_no_severity(self):
        prefill = [PrefillEntry(
            entry_id="test-2",
            title="A fact",
            classification="soft",
            severity=None,
            rule=None,
            source=[],
            score=0.5,
        )]
        content = _generate_findings("test", prefill)
        self.assertIn("[Soft]", content)
        self.assertNotIn("[None]", content)

    def test_multiple_prefill(self):
        prefill = [
            PrefillEntry("id1", "Title 1", "hard", "S1", "Rule 1", ["s1"], 0.9),
            PrefillEntry("id2", "Title 2", "soft", "S3", None, ["s2"], 0.6),
        ]
        content = _generate_findings("test", prefill)
        self.assertIn("Title 1", content)
        self.assertIn("Title 2", content)


class TestGenerateProgress(unittest.TestCase):

    def test_contains_task(self):
        content = _generate_progress("test task")
        self.assertIn("test task", content)

    def test_contains_session_started(self):
        content = _generate_progress("test")
        self.assertIn("Session started", content)


# ===========================================================================
# Test: start_session
# ===========================================================================

class TestStartSession(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project, self.working_dir, self.events_path = _setup_project(self.tmpdir)
        self.config = _make_config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_creates_three_files(self):
        report = start_session("test task", self.events_path, self.working_dir, self.config)
        self.assertEqual(len(report.files_created), 3)
        self.assertIn(TASK_PLAN_FILE, report.files_created)
        self.assertIn(FINDINGS_FILE, report.files_created)
        self.assertIn(PROGRESS_FILE, report.files_created)

    def test_files_exist_on_disk(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        self.assertTrue((self.working_dir / TASK_PLAN_FILE).exists())
        self.assertTrue((self.working_dir / FINDINGS_FILE).exists())
        self.assertTrue((self.working_dir / PROGRESS_FILE).exists())

    def test_task_description_in_plan(self):
        start_session("Refactor auth", self.events_path, self.working_dir, self.config)
        content = (self.working_dir / TASK_PLAN_FILE).read_text()
        self.assertIn("Refactor auth", content)

    def test_already_exists(self):
        start_session("first", self.events_path, self.working_dir, self.config)
        report2 = start_session("second", self.events_path, self.working_dir, self.config)
        self.assertTrue(report2.already_exists)
        # Original task should be preserved
        content = (self.working_dir / TASK_PLAN_FILE).read_text()
        self.assertIn("first", content)

    def test_prefill_with_matching_entries(self):
        """Prefill should find entries matching the task description."""
        report = start_session(
            "leakage shift rolling",
            self.events_path, self.working_dir, self.config
        )
        # Should have prefill entries (SAMPLE_ENTRIES contain leakage/shift/rolling)
        self.assertGreater(report.prefill_count, 0)

    def test_prefill_injected_into_findings(self):
        start_session("leakage shift", self.events_path, self.working_dir, self.config)
        content = (self.working_dir / FINDINGS_FILE).read_text()
        self.assertIn("Pre-loaded Context", content)

    def test_no_prefill_when_disabled(self):
        config = _make_config(v3={"prefill_on_plan_start": False})
        report = start_session("leakage", self.events_path, self.working_dir, config)
        self.assertEqual(report.prefill_count, 0)

    def test_no_prefill_when_no_events(self):
        os.unlink(self.events_path)
        report = start_session("test", self.events_path, self.working_dir, self.config)
        self.assertEqual(report.prefill_count, 0)

    def test_creates_working_dir(self):
        import shutil
        shutil.rmtree(self.working_dir, ignore_errors=True)
        start_session("test", self.events_path, self.working_dir, self.config)
        self.assertTrue(self.working_dir.exists())

    def test_duration_tracked(self):
        report = start_session("test", self.events_path, self.working_dir, self.config)
        self.assertGreater(report.duration_ms, 0)

    def test_empty_config_uses_defaults(self):
        report = start_session("test", self.events_path, self.working_dir, {})
        self.assertFalse(report.already_exists)
        self.assertEqual(len(report.files_created), 3)


# ===========================================================================
# Test: resume_session
# ===========================================================================

class TestResumeSession(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project, self.working_dir, self.events_path = _setup_project(self.tmpdir)
        self.config = _make_config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_no_session(self):
        result = resume_session(self.working_dir)
        self.assertIsNone(result)

    def test_resume_existing_session(self):
        start_session("auth refactor", self.events_path, self.working_dir, self.config)
        report = resume_session(self.working_dir)
        self.assertIsNotNone(report)
        self.assertIn("auth refactor", report.task_description)

    def test_phases_counted(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        report = resume_session(self.working_dir)
        self.assertEqual(report.phases_total, 3)
        self.assertEqual(report.phases_done, 0)

    def test_current_phase(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        report = resume_session(self.working_dir)
        self.assertIn("Phase 1", report.current_phase)

    def test_last_progress_line(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        # Add a progress entry
        progress_path = self.working_dir / PROGRESS_FILE
        content = progress_path.read_text()
        content += "\n- Investigated the bug in auth.py\n"
        progress_path.write_text(content)

        report = resume_session(self.working_dir)
        self.assertIn("Investigated", report.last_progress_line)

    def test_duration_tracked(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        report = resume_session(self.working_dir)
        self.assertGreater(report.duration_ms, 0)


# ===========================================================================
# Test: get_session_status
# ===========================================================================

class TestGetSessionStatus(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project, self.working_dir, self.events_path = _setup_project(self.tmpdir)
        self.config = _make_config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_no_session(self):
        status = get_session_status(self.working_dir)
        self.assertFalse(status.active)

    def test_active_session(self):
        start_session("test task", self.events_path, self.working_dir, self.config)
        status = get_session_status(self.working_dir)
        self.assertTrue(status.active)
        self.assertIn("test task", status.task_description)

    def test_phases_counted(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        status = get_session_status(self.working_dir)
        self.assertEqual(status.phases_total, 3)

    def test_timestamps_present(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        status = get_session_status(self.working_dir)
        self.assertNotEqual(status.created_at, "")
        self.assertNotEqual(status.last_modified, "")

    def test_findings_count_excludes_prefill(self):
        """findings_count should only count Session Discoveries, not pre-loaded context."""
        start_session("leakage shift", self.events_path, self.working_dir, self.config)

        # Session starts with prefill; add one discovery
        findings_path = self.working_dir / FINDINGS_FILE
        content = findings_path.read_text()
        content += "\nMy new discovery here\n"
        findings_path.write_text(content)

        status = get_session_status(self.working_dir)
        # Should count only the discovery, not the prefill entries
        self.assertLessEqual(status.findings_count, 3,
            "findings_count should NOT include pre-loaded context entries")


# ===========================================================================
# Test: harvest_session
# ===========================================================================

class TestHarvestSession(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project, self.working_dir, self.events_path = _setup_project(self.tmpdir)
        self.config = _make_config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_no_session(self):
        report = harvest_session(self.working_dir, self.events_path, self.config)
        self.assertEqual(len(report.candidates), 0)
        self.assertFalse(report.findings_scanned)

    def test_empty_session(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        report = harvest_session(self.working_dir, self.events_path, self.config)
        self.assertTrue(report.findings_scanned)
        self.assertTrue(report.progress_scanned)

    def test_harvest_lesson_pattern(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        findings_path = self.working_dir / FINDINGS_FILE
        content = findings_path.read_text()
        content += "\n\nLESSON: Database connections must be pooled to avoid exhaustion\n"
        findings_path.write_text(content)

        report = harvest_session(self.working_dir, self.events_path, self.config)
        lessons = [c for c in report.candidates if c.suggested_type == "lesson"]
        self.assertGreater(len(lessons), 0)
        self.assertTrue(any("pooled" in c.title for c in lessons))

    def test_harvest_constraint_pattern(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        findings_path = self.working_dir / FINDINGS_FILE
        content = findings_path.read_text()
        content += "\n\nCONSTRAINT: MUST validate all user inputs before processing\n"
        findings_path.write_text(content)

        report = harvest_session(self.working_dir, self.events_path, self.config)
        constraints = [c for c in report.candidates if c.suggested_type == "constraint"]
        self.assertGreater(len(constraints), 0)

    def test_harvest_decision_pattern(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        progress_path = self.working_dir / PROGRESS_FILE
        content = progress_path.read_text()
        content += "\nDECISION: Using Redis for session storage instead of JWT\n"
        progress_path.write_text(content)

        report = harvest_session(self.working_dir, self.events_path, self.config)
        decisions = [c for c in report.candidates if c.suggested_type == "decision"]
        self.assertGreater(len(decisions), 0)

    def test_harvest_must_never_pattern(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        findings_path = self.working_dir / FINDINGS_FILE
        content = findings_path.read_text()
        content += "\n\nMUST always close database connections in finally blocks\n"
        findings_path.write_text(content)

        report = harvest_session(self.working_dir, self.events_path, self.config)
        self.assertGreater(len(report.candidates), 0)

    def test_harvest_warning_pattern(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        findings_path = self.working_dir / FINDINGS_FILE
        content = findings_path.read_text()
        content += "\n\nWARNING: Large datasets may cause OOM with current batch size\n"
        findings_path.write_text(content)

        report = harvest_session(self.working_dir, self.events_path, self.config)
        risks = [c for c in report.candidates if c.suggested_type == "risk"]
        self.assertGreater(len(risks), 0)

    def test_harvest_error_fix_pattern(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        progress_path = self.working_dir / PROGRESS_FILE
        content = progress_path.read_text()
        content += "\nFix: Added missing null check in auth middleware\n"
        progress_path.write_text(content)

        report = harvest_session(self.working_dir, self.events_path, self.config)
        self.assertGreater(len(report.candidates), 0)

    def test_harvest_deduplicates(self):
        """Same text should not produce duplicate candidates."""
        start_session("test", self.events_path, self.working_dir, self.config)
        findings_path = self.working_dir / FINDINGS_FILE
        content = findings_path.read_text()
        content += "\n\nLESSON: Always use prepared statements\n"
        content += "\nLESSON: Always use prepared statements\n"
        findings_path.write_text(content)

        report = harvest_session(self.working_dir, self.events_path, self.config)
        titles = [c.title for c in report.candidates if "prepared" in c.title]
        self.assertEqual(len(titles), 1)

    def test_harvest_cross_file_dedup(self):
        """Same candidate in both findings and progress should appear only once."""
        start_session("test", self.events_path, self.working_dir, self.config)

        # Add same lesson to BOTH files
        lesson = "\n\nLESSON: Cross-file dedup test candidate\n"
        findings_path = self.working_dir / FINDINGS_FILE
        findings_path.write_text(findings_path.read_text() + lesson)
        progress_path = self.working_dir / PROGRESS_FILE
        progress_path.write_text(progress_path.read_text() + lesson)

        report = harvest_session(self.working_dir, self.events_path, self.config)
        matches = [c for c in report.candidates if "Cross-file dedup" in c.title]
        self.assertEqual(len(matches), 1, "Cross-file duplicate not deduplicated")

    def test_duration_tracked(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        report = harvest_session(self.working_dir, self.events_path, self.config)
        self.assertIsInstance(report.duration_ms, float)


# ===========================================================================
# Test: read_plan_summary
# ===========================================================================

class TestReadPlanSummary(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project, self.working_dir, self.events_path = _setup_project(self.tmpdir)
        self.config = _make_config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_no_session(self):
        result = read_plan_summary(self.working_dir)
        self.assertEqual(result, "")

    def test_reads_plan(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        result = read_plan_summary(self.working_dir)
        self.assertIn("Task", result)
        self.assertIn("test", result)

    def test_truncates_to_max_lines(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        result = read_plan_summary(self.working_dir, max_lines=5)
        lines = result.splitlines()
        self.assertLessEqual(len(lines), 5)

    def test_default_30_lines(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        result = read_plan_summary(self.working_dir)
        lines = result.splitlines()
        self.assertLessEqual(len(lines), 30)


# ===========================================================================
# Test: clear_session
# ===========================================================================

class TestClearSession(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project, self.working_dir, self.events_path = _setup_project(self.tmpdir)
        self.config = _make_config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_no_session(self):
        result = clear_session(self.working_dir)
        self.assertFalse(result)

    def test_clears_files(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        result = clear_session(self.working_dir)
        self.assertTrue(result)
        self.assertFalse((self.working_dir / TASK_PLAN_FILE).exists())
        self.assertFalse((self.working_dir / FINDINGS_FILE).exists())
        self.assertFalse((self.working_dir / PROGRESS_FILE).exists())

    def test_directory_preserved(self):
        start_session("test", self.events_path, self.working_dir, self.config)
        clear_session(self.working_dir)
        # Directory itself should still exist
        self.assertTrue(self.working_dir.exists())


# ===========================================================================
# Test: _extract_candidates (internal)
# ===========================================================================

class TestExtractCandidates(unittest.TestCase):

    def test_empty_text(self):
        result = _extract_candidates("", "test.md")
        self.assertEqual(len(result), 0)

    def test_lesson_marker(self):
        text = "LESSON: Always validate inputs before processing"
        result = _extract_candidates(text, "test.md")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].suggested_type, "lesson")

    def test_constraint_marker(self):
        text = "CONSTRAINT: MUST use HTTPS for all API calls"
        result = _extract_candidates(text, "test.md")
        constraints = [c for c in result if c.suggested_type == "constraint"]
        self.assertGreater(len(constraints), 0)

    def test_decision_marker(self):
        text = "DECISION: Using PostgreSQL over MongoDB for ACID compliance"
        result = _extract_candidates(text, "test.md")
        decisions = [c for c in result if c.suggested_type == "decision"]
        self.assertGreater(len(decisions), 0)

    def test_warning_marker(self):
        text = "WARNING: Memory usage spikes during batch processing"
        result = _extract_candidates(text, "test.md")
        risks = [c for c in result if c.suggested_type == "risk"]
        self.assertGreater(len(risks), 0)

    def test_must_statement(self):
        text = "MUST ensure all file handles are closed after use in production code"
        result = _extract_candidates(text, "test.md")
        self.assertGreater(len(result), 0)

    def test_never_statement(self):
        text = "NEVER store plaintext passwords in the database or configuration"
        result = _extract_candidates(text, "test.md")
        self.assertGreater(len(result), 0)

    def test_error_fix_pattern(self):
        text = "Fix: Added index on user_id column to resolve slow queries"
        result = _extract_candidates(text, "test.md")
        self.assertGreater(len(result), 0)

    def test_source_hint_preserved(self):
        text = "LESSON: Test everything"
        result = _extract_candidates(text, "findings.md")
        self.assertEqual(result[0].source_hint, "findings.md")

    def test_title_truncation(self):
        long_text = "LESSON: " + "x" * 200
        result = _extract_candidates(long_text, "test.md")
        self.assertLessEqual(len(result[0].title), 120)

    def test_deduplication(self):
        text = "LESSON: Same thing\nLESSON: Same thing"
        result = _extract_candidates(text, "test.md")
        self.assertEqual(len(result), 1)

    def test_multiple_patterns(self):
        text = """
LESSON: Database connections leak when not closed
CONSTRAINT: MUST close connections in finally blocks
DECISION: Using connection pooling with max 10 connections
WARNING: Pool exhaustion under heavy load
"""
        result = _extract_candidates(text, "test.md")
        types = {c.suggested_type for c in result}
        self.assertIn("lesson", types)
        self.assertIn("constraint", types)
        self.assertIn("decision", types)
        self.assertIn("risk", types)


# ===========================================================================
# Test: _extract_field, _count_phases, _get_current_phase
# ===========================================================================

class TestHelpers(unittest.TestCase):

    def test_extract_field(self):
        text = "**Task**: Refactor auth module\n**Status**: Done"
        self.assertEqual(_extract_field(text, "Task"), "Refactor auth module")
        self.assertEqual(_extract_field(text, "Status"), "Done")

    def test_extract_field_missing(self):
        self.assertEqual(_extract_field("No fields here", "Task"), "")

    def test_count_phases_default_template(self):
        plan = _generate_task_plan("test")
        total, done = _count_phases(plan)
        self.assertEqual(total, 3)
        self.assertEqual(done, 0)

    def test_count_phases_with_done(self):
        plan = """## Phases
### Phase 1: Investigation [DONE]
### Phase 2: Implementation
### Phase 3: Verification [DONE]
## Other
"""
        total, done = _count_phases(plan)
        self.assertEqual(total, 3)
        self.assertEqual(done, 2)

    def test_get_current_phase_first(self):
        plan = _generate_task_plan("test")
        phase = _get_current_phase(plan)
        self.assertIn("Phase 1", phase)

    def test_get_current_phase_second(self):
        plan = """## Phases
### Phase 1: Investigation [DONE]
### Phase 2: Implementation
### Phase 3: Verification
"""
        phase = _get_current_phase(plan)
        self.assertIn("Phase 2", phase)

    def test_get_current_phase_all_done(self):
        plan = """## Phases
### Phase 1: Investigation [DONE]
### Phase 2: Implementation [DONE]
### Phase 3: Verification [DONE]
"""
        phase = _get_current_phase(plan)
        self.assertEqual(phase, "Unknown")


# ===========================================================================
# Test: Dataclasses
# ===========================================================================

class TestDataclasses(unittest.TestCase):

    def test_prefill_entry_defaults(self):
        entry = PrefillEntry("id", "title", "hard", "S1", None, [], 0.5)
        self.assertIsNone(entry.rule)
        self.assertEqual(entry.source, [])

    def test_session_start_report_defaults(self):
        report = SessionStartReport(task_description="t", working_dir="w")
        self.assertEqual(report.files_created, [])
        self.assertFalse(report.already_exists)
        self.assertEqual(report.prefill_count, 0)

    def test_harvest_report_defaults(self):
        report = HarvestReport()
        self.assertEqual(report.candidates, [])
        self.assertFalse(report.findings_scanned)

    def test_session_status_defaults(self):
        status = SessionStatus(active=False)
        self.assertEqual(status.task_description, "")
        self.assertEqual(status.phases_total, 0)

    def test_harvest_candidate_fields(self):
        c = HarvestCandidate(
            suggested_type="lesson",
            title="Test",
            content=["Test content"],
            rule="MUST test",
            implication="Things break",
            source_hint="test.md",
            extraction_reason="Explicit marker",
        )
        self.assertEqual(c.suggested_type, "lesson")
        self.assertEqual(c.rule, "MUST test")


if __name__ == "__main__":
    unittest.main()
