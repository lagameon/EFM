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
    _clean_markdown_artifacts,
    _compute_extraction_confidence,
    _convert_candidate_to_entry,
    _count_phases,
    _extract_candidates,
    _extract_field,
    _extract_tags,
    _generate_findings,
    _generate_progress,
    _generate_task_plan,
    _get_current_phase,
    _hash8,
    _is_viable_candidate,
    _sanitize_anchor,
    auto_harvest_and_persist,
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


# ===========================================================================
# Test: _hash8
# ===========================================================================

class TestHash8(unittest.TestCase):

    def test_returns_8_chars(self):
        self.assertEqual(len(_hash8("hello")), 8)

    def test_hex_only(self):
        import re
        self.assertRegex(_hash8("test input"), r"^[a-f0-9]{8}$")

    def test_deterministic(self):
        self.assertEqual(_hash8("same"), _hash8("same"))

    def test_different_inputs(self):
        self.assertNotEqual(_hash8("a"), _hash8("b"))


# ===========================================================================
# Test: _sanitize_anchor
# ===========================================================================

class TestSanitizeAnchor(unittest.TestCase):

    def test_working_findings(self):
        result = _sanitize_anchor(".memory/working/findings.md")
        self.assertEqual(result, "working_findings")

    def test_working_progress(self):
        result = _sanitize_anchor(".memory/working/progress.md")
        self.assertEqual(result, "working_progress")

    def test_simple_filename(self):
        result = _sanitize_anchor("test.md")
        self.assertEqual(result, "test")

    def test_strips_special_chars(self):
        result = _sanitize_anchor("path/to/MY-FILE.py")
        # Lowercased, hyphens removed
        self.assertRegex(result, r"^[a-z0-9_]+$")


# ===========================================================================
# Test: _convert_candidate_to_entry
# ===========================================================================

class TestConvertCandidateToEntry(unittest.TestCase):

    def _make_candidate(self, **kwargs):
        defaults = {
            "suggested_type": "lesson",
            "title": "Test lesson title here",
            "content": ["Point one", "Point two"],
            "rule": None,
            "implication": "Things could break",
            "source_hint": ".memory/working/findings.md",
            "extraction_reason": "Explicit LESSON: marker",
        }
        defaults.update(kwargs)
        return HarvestCandidate(**defaults)

    def test_lesson_type_soft(self):
        entry = _convert_candidate_to_entry(self._make_candidate(), Path("/proj"))
        self.assertEqual(entry["type"], "lesson")
        self.assertEqual(entry["classification"], "soft")

    def test_constraint_type_hard(self):
        entry = _convert_candidate_to_entry(
            self._make_candidate(suggested_type="constraint", rule="MUST check input"),
            Path("/proj"),
        )
        self.assertEqual(entry["type"], "constraint")
        self.assertEqual(entry["classification"], "hard")
        self.assertEqual(entry["severity"], "S1")

    def test_risk_type_hard_s2(self):
        entry = _convert_candidate_to_entry(
            self._make_candidate(suggested_type="risk"),
            Path("/proj"),
        )
        self.assertEqual(entry["classification"], "hard")
        self.assertEqual(entry["severity"], "S2")

    def test_id_format(self):
        import re
        entry = _convert_candidate_to_entry(self._make_candidate(), Path("/proj"))
        self.assertRegex(entry["id"], r"^[a-z]+-[a-z0-9_]+-[a-f0-9]{8}$")

    def test_has_required_fields(self):
        entry = _convert_candidate_to_entry(self._make_candidate(), Path("/proj"))
        for field in ("id", "type", "classification", "title", "content", "source", "created_at"):
            self.assertIn(field, entry, f"Missing required field: {field}")

    def test_content_min_2_items(self):
        entry = _convert_candidate_to_entry(
            self._make_candidate(content=["Single item"]),
            Path("/proj"),
        )
        self.assertGreaterEqual(len(entry["content"]), 2)

    def test_title_truncated_to_120(self):
        long_title = "A" * 200
        entry = _convert_candidate_to_entry(
            self._make_candidate(title=long_title),
            Path("/proj"),
        )
        self.assertLessEqual(len(entry["title"]), 120)

    def test_auto_harvested_meta(self):
        entry = _convert_candidate_to_entry(self._make_candidate(), Path("/proj"))
        self.assertTrue(entry.get("_meta", {}).get("auto_harvested"))


# ===========================================================================
# Test: auto_harvest_and_persist
# ===========================================================================

class TestAutoHarvestAndPersist(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_root = Path(self.tmpdir)
        self.working_dir = self.project_root / ".memory" / "working"
        self.working_dir.mkdir(parents=True)
        self.events_path = self.project_root / ".memory" / "events.jsonl"
        self.events_path.write_text("")
        self.config = {"v3": {"working_memory_dir": ".memory/working"}}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _write_session_with_markers(self):
        """Create session files with harvestable markers."""
        (self.working_dir / TASK_PLAN_FILE).write_text("# Task Plan\n**Task**: Test\n")
        (self.working_dir / FINDINGS_FILE).write_text(
            "# Findings\n\nLESSON: Always validate input before processing\n"
            "CONSTRAINT: MUST use shift(1) before rolling\n"
        )
        (self.working_dir / PROGRESS_FILE).write_text("# Progress\n- Started\n")

    def test_writes_entries_to_events(self):
        self._write_session_with_markers()
        result = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
        )
        self.assertGreater(result["candidates_found"], 0)
        self.assertGreater(result["entries_written"], 0)
        # Verify events.jsonl has content
        content = self.events_path.read_text()
        self.assertTrue(content.strip())
        # Each line should be valid JSON
        for line in content.strip().split("\n"):
            entry = json.loads(line)
            self.assertIn("id", entry)
            self.assertIn("type", entry)

    def test_no_candidates_still_clears(self):
        # Session with no harvestable markers
        (self.working_dir / TASK_PLAN_FILE).write_text("# Task Plan\n")
        (self.working_dir / FINDINGS_FILE).write_text("# Findings\nNothing special\n")
        (self.working_dir / PROGRESS_FILE).write_text("# Progress\n- Done\n")

        result = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
        )
        self.assertEqual(result["candidates_found"], 0)
        self.assertEqual(result["entries_written"], 0)
        self.assertTrue(result["session_cleared"])

    def test_clears_session(self):
        self._write_session_with_markers()
        result = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
        )
        self.assertTrue(result["session_cleared"])
        # Session files should be gone
        self.assertFalse((self.working_dir / TASK_PLAN_FILE).exists())
        self.assertFalse((self.working_dir / FINDINGS_FILE).exists())

    def test_no_session_returns_early(self):
        """No working files → harvest returns empty, clear returns False."""
        # Don't create any session files
        result = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
        )
        self.assertEqual(result["candidates_found"], 0)

    def test_skips_duplicate_entries(self):
        """Entries already in events.jsonl are skipped (not written again)."""
        # Pre-populate events.jsonl with an entry that matches our harvest marker.
        # _convert_candidate_to_entry for "LESSON: Always validate input..."
        # produces title="Always validate input before processing", rule=None,
        # source=[".memory/working/findings.md:L0-L0"].
        # build_dedup_text uses: title | rule | content.  The harvested candidate
        # will have content like "Always validate input before processing\n
        # Extracted via: Explicit LESSON: marker".  The existing entry must have
        # similar title + content for >0.85 SequenceMatcher similarity.
        existing_entry = {
            "id": "lesson-existing-aaaabbbb",
            "type": "lesson",
            "classification": "soft",
            "severity": "S3",
            "title": "Always validate input before processing",
            "content": [
                "Always validate input before processing",
                "Extracted via: Explicit LESSON: marker",
            ],
            "rule": None,
            "source": [".memory/working/findings.md:L0-L0"],
            "tags": ["validation"],
            "created_at": "2026-02-01T00:00:00Z",
            "deprecated": False,
            "_meta": {},
        }
        self.events_path.write_text(
            json.dumps(existing_entry) + "\n", encoding="utf-8"
        )

        # Session files contain the same knowledge
        self._write_session_with_markers()

        result = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
        )

        # Candidates found, but at least one should be skipped as duplicate
        self.assertGreater(result["candidates_found"], 0)
        self.assertGreater(result["entries_skipped"], 0)

    def test_dedup_threshold_respected(self):
        """Low dedup threshold catches more duplicates, high threshold catches fewer."""
        # Pre-populate with an entry
        existing_entry = {
            "id": "constraint-existing-ccccdddd",
            "type": "constraint",
            "classification": "hard",
            "severity": "S2",
            "title": "MUST use shift(1) before rolling",
            "content": ["Use shift(1) before rolling operations"],
            "rule": "MUST use shift(1) before rolling",
            "source": ["manual"],
            "tags": ["shift", "rolling"],
            "created_at": "2026-02-01T00:00:00Z",
            "deprecated": False,
            "_meta": {},
        }
        self.events_path.write_text(
            json.dumps(existing_entry) + "\n", encoding="utf-8"
        )

        self._write_session_with_markers()

        # With very low threshold (0.5) — more things are considered duplicates
        config_low = {
            "v3": {"working_memory_dir": ".memory/working"},
            "automation": {"dedup_threshold": 0.5},
        }
        result_low = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, config_low,
            run_pipeline_after=False,
        )

        # Recreate session (cleared after harvest)
        self._write_session_with_markers()
        # Reset events to only the existing entry (remove appended entries from first run)
        self.events_path.write_text(
            json.dumps(existing_entry) + "\n", encoding="utf-8"
        )

        # With very high threshold (0.99) — almost nothing is a duplicate
        config_high = {
            "v3": {"working_memory_dir": ".memory/working"},
            "automation": {"dedup_threshold": 0.99},
        }
        result_high = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, config_high,
            run_pipeline_after=False,
        )

        # Higher threshold should skip fewer entries
        self.assertGreaterEqual(
            result_low["entries_skipped"],
            result_high["entries_skipped"],
            "Lower dedup threshold should catch more duplicates"
        )


# ===========================================================================
# Test: _extract_tags
# ===========================================================================

class TestExtractTags(unittest.TestCase):

    def test_extracts_keywords(self):
        tags = _extract_tags("Rolling statistics leakage", ["shift(1) prevents leakage"])
        self.assertIn("rolling", tags)
        self.assertIn("statistics", tags)
        self.assertIn("leakage", tags)

    def test_max_5_tags(self):
        tags = _extract_tags(
            "one two three four five six seven",
            ["eight nine ten eleven twelve"],
        )
        self.assertLessEqual(len(tags), 5)

    def test_filters_stop_words(self):
        tags = _extract_tags("the and for with", ["this that are was"])
        self.assertEqual(tags, [])


# ===========================================================================
# Test: _clean_markdown_artifacts
# ===========================================================================

class TestCleanMarkdownArtifacts(unittest.TestCase):

    def test_removes_pipe_chars(self):
        result = _clean_markdown_artifacts("MUST use ib.schedule() |")
        self.assertNotIn("|", result)
        self.assertIn("MUST", result)

    def test_removes_bold_markers(self):
        result = _clean_markdown_artifacts("**Important** rule here")
        self.assertNotIn("**", result)
        self.assertIn("Important", result)

    def test_removes_backticks(self):
        result = _clean_markdown_artifacts("Use `shift(1)` before rolling")
        self.assertNotIn("`", result)
        self.assertIn("shift(1)", result)

    def test_collapses_whitespace(self):
        result = _clean_markdown_artifacts("too   many    spaces")
        self.assertEqual(result, "too many spaces")


# ===========================================================================
# Test: _is_viable_candidate
# ===========================================================================

class TestIsViableCandidate(unittest.TestCase):

    def test_rejects_short_title(self):
        self.assertFalse(_is_viable_candidate("short", ["content here"]))

    def test_accepts_adequate_title(self):
        self.assertTrue(_is_viable_candidate(
            "This is a sufficiently long title for testing",
            ["Some meaningful content"]
        ))

    def test_rejects_boilerplate_only_content(self):
        self.assertFalse(_is_viable_candidate(
            "A long enough title here yes",
            ["Extracted via: Error/Fix pattern"]
        ))

    def test_accepts_content_matching_title(self):
        """Single-item content matching title is valid for auto-harvested entries."""
        title = "Always validate input before processing"
        self.assertTrue(_is_viable_candidate(title, [title]))

    def test_custom_min_length(self):
        self.assertTrue(_is_viable_candidate("12345", ["content"], min_length=5))
        self.assertFalse(_is_viable_candidate("1234", ["content"], min_length=5))


# ===========================================================================
# Test: Quality gate and confidence penalties


# ===========================================================================
# Test: _extract_candidates quality improvements (Step 1)
# ===========================================================================

class TestExtractCandidatesQuality(unittest.TestCase):

    def test_cleans_markdown_table_fragments(self):
        """Table fragments like 'MUST use ib.schedule() |' should be cleaned."""
        text = "MUST use ib.schedule() for all timer callbacks |"
        result = _extract_candidates(text, "test.md")
        if result:
            self.assertNotIn("|", result[0].title)

    def test_filters_short_must_statements(self):
        """Very short MUST statements (< 15 chars after cleanup) should be filtered."""
        text = "MUST do x"  # Only 9 chars
        result = _extract_candidates(text, "test.md")
        must_candidates = [c for c in result if "MUST/NEVER/ALWAYS" in c.extraction_reason]
        self.assertEqual(len(must_candidates), 0)

    def test_confidence_penalizes_null_rule_and_implication(self):
        """Entry with both rule=None and implication=None should have lower confidence."""
        high_quality = HarvestCandidate(
            suggested_type="lesson",
            title="A detailed lesson about database pooling strategies",
            content=["A detailed lesson about database pooling strategies"],
            rule="MUST pool connections",
            implication="Connections exhaust without pooling",
            source_hint="test.md",
            extraction_reason="Explicit LESSON: marker",
        )
        low_quality = HarvestCandidate(
            suggested_type="lesson",
            title="A detailed lesson about database pooling strategies",
            content=["A detailed lesson about database pooling strategies"],
            rule=None,
            implication=None,
            source_hint="test.md",
            extraction_reason="Explicit LESSON: marker",
        )
        self.assertGreater(
            _compute_extraction_confidence(high_quality),
            _compute_extraction_confidence(low_quality),
        )

# ===========================================================================

class TestQualityGateExtraction(unittest.TestCase):

    def test_table_fragment_cleaned(self):
        """Markdown table fragments should be cleaned during extraction."""
        text = "LESSON: | Some table cell | content here |"
        result = _extract_candidates(text, "test.md")
        if result:
            self.assertNotIn("|", result[0].title)

    def test_short_must_statement_filtered(self):
        """MUST statements shorter than 15 chars should be filtered."""
        text = "MUST do it"
        result = _extract_candidates(text, "test.md")
        # "MUST do it" is only 10 chars, should be filtered by the 10-char minimum in regex
        # or by the quality gate
        must_candidates = [c for c in result if "MUST do it" == c.title.strip()]
        self.assertEqual(len(must_candidates), 0)

    def test_confidence_penalty_no_rule_no_implication(self):
        """Entries with no rule and no implication should get lower confidence."""
        candidate_with = HarvestCandidate(
            suggested_type="lesson",
            title="A lesson with rule and implication",
            content=["Some content"],
            rule="MUST do something",
            implication="Bad things happen",
            source_hint="test.md",
            extraction_reason="Explicit LESSON: marker",
        )
        candidate_without = HarvestCandidate(
            suggested_type="lesson",
            title="A lesson without rule or implication",
            content=["Some content"],
            rule=None,
            implication=None,
            source_hint="test.md",
            extraction_reason="Explicit LESSON: marker",
        )
        conf_with = _compute_extraction_confidence(candidate_with)
        conf_without = _compute_extraction_confidence(candidate_without)
        self.assertGreater(conf_with, conf_without)


class TestAutoHarvestQualityGate(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_root = Path(self.tmpdir)
        self.working_dir = self.project_root / ".memory" / "working"
        self.working_dir.mkdir(parents=True)
        self.events_path = self.project_root / ".memory" / "events.jsonl"
        self.events_path.write_text("")
        self.config = {"v3": {"working_memory_dir": ".memory/working"}}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_quality_gate_rejects_short_title(self):
        """Auto-harvest should skip candidates with short titles."""
        (self.working_dir / TASK_PLAN_FILE).write_text("# Task Plan\n**Task**: Test\n")
        (self.working_dir / FINDINGS_FILE).write_text(
            "# Findings\n\nLESSON: Short\n"
        )
        (self.working_dir / PROGRESS_FILE).write_text("# Progress\n- Started\n")

        result = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
        )
        # "Short" is < 15 chars, should be skipped
        # The candidate may or may not be found depending on regex, but if found, quality gate skips it
        self.assertEqual(result["entries_written"], 0)


# ===========================================================================
# Test: Session-Level Dedup
# ===========================================================================

class TestSessionLevelDedup(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_root = Path(self.tmpdir)
        self.working_dir = self.project_root / ".memory" / "working"
        self.working_dir.mkdir(parents=True)
        self.events_path = self.project_root / ".memory" / "events.jsonl"
        self.events_path.write_text("")
        self.config = {"v3": {"working_memory_dir": ".memory/working"}}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _write_session(self):
        (self.working_dir / TASK_PLAN_FILE).write_text("# Task Plan\n**Task**: Test\n")
        (self.working_dir / FINDINGS_FILE).write_text(
            "# Findings\n\nLESSON: Always validate input before processing data\n"
            "CONSTRAINT: MUST use shift(1) before rolling operations\n"
        )
        (self.working_dir / PROGRESS_FILE).write_text("# Progress\n- Started\n")

    def test_same_conversation_skips_duplicates(self):
        """Second harvest with same conversation_id should skip already-written entries."""
        conv_id = "test-conv-001"
        self._write_session()
        result1 = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
            conversation_id=conv_id,
        )
        written_count = result1["entries_written"]
        self.assertGreater(written_count, 0)

        # Recreate session and harvest again with same conversation_id
        self._write_session()
        result2 = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
            conversation_id=conv_id,
        )
        # Should have session dedup skips
        self.assertGreater(result2.get("session_dedup_skipped", 0), 0)

    def test_different_conversation_passes(self):
        """Different conversation_id should NOT trigger session dedup."""
        self._write_session()
        result1 = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
            conversation_id="conv-001",
        )
        written1 = result1["entries_written"]

        self._write_session()
        result2 = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
            conversation_id="conv-002",
        )
        # Different conversation should still attempt to write (may be caught by regular dedup though)
        self.assertEqual(result2.get("session_dedup_skipped", 0), 0)

    def test_no_conversation_id_unchanged_behavior(self):
        """Without conversation_id, behavior should be unchanged."""
        self._write_session()
        result = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
        )
        self.assertGreater(result["entries_written"], 0)

    def test_conversation_id_stored_in_meta(self):
        """Written entries should have conversation_id in _meta."""
        conv_id = "test-conv-meta"
        self._write_session()
        auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
            conversation_id=conv_id,
        )
        # Read events and check _meta
        content = self.events_path.read_text().strip()
        if content:
            for line in content.split("\n"):
                entry = json.loads(line)
                self.assertEqual(
                    entry.get("_meta", {}).get("conversation_id"),
                    conv_id
                )

    def test_session_dedup_count_in_result(self):
        """Result dict should include session_dedup_skipped count."""
        conv_id = "test-conv-count"
        self._write_session()
        result1 = auto_harvest_and_persist(
            self.working_dir, self.events_path,
            self.project_root, self.config,
            run_pipeline_after=False,
            conversation_id=conv_id,
        )
        self.assertIn("session_dedup_skipped", result1)


if __name__ == "__main__":
    unittest.main()
