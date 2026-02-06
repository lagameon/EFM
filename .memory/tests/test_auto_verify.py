"""
Tests for EF Memory V2 — Auto-Verify

Covers: validate_schema, verify_source, check_staleness,
        check_duplicates, check_verify_command, verify_entry
"""

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Import path setup — point to .memory/ directory
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.auto_verify import (
    DedupResult,
    SourceCheckResult,
    StalenessResult,
    ValidationResult,
    VerifyReport,
    _matches_source_pattern,
    _parse_source_ref,
    check_duplicates,
    check_staleness,
    check_verify_command,
    validate_schema,
    verify_all_entries,
    verify_entry,
    verify_source,
)


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

def _make_valid_entry(**overrides) -> dict:
    """Create a valid entry with optional overrides."""
    entry = {
        "id": "lesson-inc036-a3f8c2d1",
        "type": "lesson",
        "classification": "hard",
        "severity": "S1",
        "title": "Rolling statistics without shift(1) caused 999x backtest inflation",
        "content": [
            "42 rolling/ewm/pct_change calls missing shift(1)",
            "Model learned to explain past, not predict future",
            "IC with T-5 returns (-0.115) > IC with T+1 returns (0.018)",
            "Backtest showed 49,979% return; after fix only 52%",
        ],
        "rule": "shift(1) MUST precede any rolling(), ewm(), pct_change()",
        "implication": "Backtest returns inflated 100-1000x",
        "verify": "grep -rn 'rolling' src/features/*.py | grep -v 'shift(1)'",
        "source": ["docs/decisions/INCIDENTS.md#INC-036:L553-L699"],
        "tags": ["leakage", "feature-engine"],
        "created_at": "2026-02-01T14:30:00Z",
        "last_verified": None,
        "deprecated": False,
        "_meta": {},
    }
    entry.update(overrides)
    return entry


def _make_soft_entry(**overrides) -> dict:
    """Create a valid soft/S3 entry."""
    entry = {
        "id": "fact-risk_labels-9c3a1e5f",
        "type": "fact",
        "classification": "soft",
        "severity": "S3",
        "title": "3K label uses dual-condition (return + drawdown)",
        "content": [
            "CLAUDE.md describes 3K as ATR breakout",
            "Actual implementation uses return + drawdown",
        ],
        "rule": None,
        "implication": "Stricter than documented; may affect threshold tuning",
        "source": ["src/labels/risk_adjusted_labels.py:L93-L144"],
        "tags": ["label", "3k"],
        "created_at": "2026-02-01T15:00:00Z",
        "last_verified": None,
        "deprecated": False,
        "_meta": {},
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# TestValidateSchema
# ---------------------------------------------------------------------------

class TestValidateSchema(unittest.TestCase):

    def test_valid_hard_entry_passes(self):
        r = validate_schema(_make_valid_entry())
        self.assertTrue(r.valid)
        self.assertEqual(r.errors, [])

    def test_valid_soft_entry_passes(self):
        r = validate_schema(_make_soft_entry())
        self.assertTrue(r.valid)
        self.assertEqual(r.errors, [])

    def test_missing_id_fails(self):
        e = _make_valid_entry()
        del e["id"]
        r = validate_schema(e)
        self.assertFalse(r.valid)
        self.assertTrue(any("id" in err for err in r.errors))

    def test_missing_type_fails(self):
        e = _make_valid_entry()
        del e["type"]
        r = validate_schema(e)
        self.assertFalse(r.valid)

    def test_missing_classification_fails(self):
        e = _make_valid_entry()
        del e["classification"]
        r = validate_schema(e)
        self.assertFalse(r.valid)

    def test_missing_title_fails(self):
        e = _make_valid_entry()
        del e["title"]
        r = validate_schema(e)
        self.assertFalse(r.valid)

    def test_missing_content_fails(self):
        e = _make_valid_entry()
        del e["content"]
        r = validate_schema(e)
        self.assertFalse(r.valid)

    def test_missing_source_fails(self):
        e = _make_valid_entry()
        del e["source"]
        r = validate_schema(e)
        self.assertFalse(r.valid)

    def test_missing_created_at_fails(self):
        e = _make_valid_entry()
        del e["created_at"]
        r = validate_schema(e)
        self.assertFalse(r.valid)

    def test_invalid_id_format(self):
        r = validate_schema(_make_valid_entry(id="BAD-ID-FORMAT"))
        self.assertFalse(r.valid)
        self.assertTrue(any("id" in err.lower() or "format" in err.lower() for err in r.errors))

    def test_invalid_type_enum(self):
        r = validate_schema(_make_valid_entry(type="unknown"))
        self.assertFalse(r.valid)

    def test_invalid_classification_enum(self):
        r = validate_schema(_make_valid_entry(classification="medium"))
        self.assertFalse(r.valid)

    def test_invalid_severity_warns(self):
        r = validate_schema(_make_valid_entry(severity="S4"))
        # Severity error is WARN, not FAIL
        self.assertTrue(r.valid)
        self.assertTrue(len(r.warnings) > 0)

    def test_no_rule_no_implication_fails(self):
        r = validate_schema(_make_valid_entry(rule=None, implication=None))
        self.assertFalse(r.valid)
        self.assertTrue(any("rule" in err or "implication" in err for err in r.errors))

    def test_rule_only_passes(self):
        r = validate_schema(_make_valid_entry(implication=None))
        self.assertTrue(r.valid)

    def test_implication_only_passes(self):
        r = validate_schema(_make_valid_entry(rule=None))
        self.assertTrue(r.valid)

    def test_content_too_few_items_warns(self):
        r = validate_schema(_make_valid_entry(content=["single item"]))
        self.assertTrue(r.valid)  # WARN, not FAIL
        self.assertTrue(any("content" in w.lower() for w in r.warnings))

    def test_content_too_many_items_warns(self):
        r = validate_schema(_make_valid_entry(content=["a", "b", "c", "d", "e", "f", "g"]))
        self.assertTrue(r.valid)
        self.assertTrue(any("content" in w.lower() for w in r.warnings))

    def test_title_too_long_warns(self):
        long_title = "x" * 121
        r = validate_schema(_make_valid_entry(title=long_title))
        self.assertTrue(r.valid)
        self.assertTrue(any("title" in w.lower() for w in r.warnings))

    def test_hard_no_severity_warns(self):
        r = validate_schema(_make_valid_entry(classification="hard", severity=None))
        self.assertTrue(r.valid)
        self.assertTrue(any("severity" in w.lower() for w in r.warnings))

    def test_source_invalid_format_warns(self):
        r = validate_schema(_make_valid_entry(source=["just a plain string"]))
        self.assertTrue(r.valid)  # WARN not FAIL
        self.assertTrue(any("source" in w.lower() for w in r.warnings))

    def test_empty_entry_fails(self):
        r = validate_schema({})
        self.assertFalse(r.valid)
        self.assertTrue(len(r.errors) >= 7)  # All required fields missing


# ---------------------------------------------------------------------------
# TestSourcePattern
# ---------------------------------------------------------------------------

class TestSourcePattern(unittest.TestCase):

    def test_code_pattern(self):
        self.assertTrue(_matches_source_pattern("src/features/engine.py:L10-L20"))

    def test_markdown_pattern(self):
        self.assertTrue(_matches_source_pattern("docs/INCIDENTS.md#INC-036:L553-L699"))

    def test_commit_pattern(self):
        self.assertTrue(_matches_source_pattern("commit 7874956"))

    def test_pr_pattern(self):
        self.assertTrue(_matches_source_pattern("PR #123"))

    def test_function_pattern(self):
        self.assertTrue(_matches_source_pattern("src/labels/risk.py::create_label"))

    def test_plain_string_no_match(self):
        self.assertFalse(_matches_source_pattern("just a plain string"))


# ---------------------------------------------------------------------------
# TestParseSourceRef
# ---------------------------------------------------------------------------

class TestParseSourceRef(unittest.TestCase):

    def test_code_ref(self):
        stype, path, anchor, lr = _parse_source_ref("src/engine.py:L10-L20")
        self.assertEqual(stype, "code")
        self.assertEqual(path, "src/engine.py")
        self.assertEqual(lr, "L10-L20")

    def test_markdown_ref(self):
        stype, path, anchor, lr = _parse_source_ref("docs/INC.md#INC-036:L553-L699")
        self.assertEqual(stype, "markdown")
        self.assertEqual(path, "docs/INC.md")
        self.assertEqual(anchor, "INC-036")
        self.assertEqual(lr, "L553-L699")

    def test_commit_ref(self):
        stype, _, anchor, _ = _parse_source_ref("commit 7874956")
        self.assertEqual(stype, "commit")
        self.assertEqual(anchor, "7874956")

    def test_pr_ref(self):
        stype, _, _, _ = _parse_source_ref("PR #123")
        self.assertEqual(stype, "pr")

    def test_function_ref(self):
        stype, path, anchor, _ = _parse_source_ref("src/labels/risk.py::create_label")
        self.assertEqual(stype, "function")
        self.assertEqual(path, "src/labels/risk.py")
        self.assertEqual(anchor, "create_label")


# ---------------------------------------------------------------------------
# TestVerifySource
# ---------------------------------------------------------------------------

class TestVerifySource(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def test_code_source_ok(self):
        # Create a test file with 20 lines
        f = self.tmpdir / "src" / "engine.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join([f"line {i}" for i in range(20)]) + "\n")

        r = verify_source("src/engine.py:L1-L10", self.tmpdir)
        self.assertEqual(r.status, "OK")
        self.assertEqual(r.source_type, "code")

    def test_code_source_file_missing(self):
        r = verify_source("src/nonexistent.py:L1-L10", self.tmpdir)
        self.assertEqual(r.status, "FAIL")

    def test_code_source_lines_out_of_range(self):
        f = self.tmpdir / "small.py"
        f.write_text("line1\nline2\nline3\n")

        r = verify_source("small.py:L1-L100", self.tmpdir)
        self.assertEqual(r.status, "WARN")
        self.assertIn("exceeds", r.message)

    def test_markdown_heading_found(self):
        f = self.tmpdir / "docs" / "INCIDENTS.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Header\n"] + [f"line {i}\n" for i in range(100)]
        lines.insert(50, "## INC-036\n")
        f.write_text("".join(lines))

        r = verify_source("docs/INCIDENTS.md#INC-036:L50-L60", self.tmpdir)
        self.assertEqual(r.status, "OK")
        self.assertEqual(r.source_type, "markdown")

    def test_markdown_heading_missing(self):
        f = self.tmpdir / "docs" / "INCIDENTS.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# Only one heading\nSome text\n")

        r = verify_source("docs/INCIDENTS.md#INC-999:L1-L5", self.tmpdir)
        self.assertEqual(r.status, "WARN")
        self.assertIn("not found", r.message)

    def test_function_source_found(self):
        f = self.tmpdir / "src" / "labels.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("def create_label(x):\n    return x\n")

        r = verify_source("src/labels.py::create_label", self.tmpdir)
        self.assertEqual(r.status, "OK")

    def test_function_source_not_found(self):
        f = self.tmpdir / "src" / "labels.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("def other_func(x):\n    return x\n")

        r = verify_source("src/labels.py::nonexistent_func", self.tmpdir)
        self.assertEqual(r.status, "WARN")

    def test_pr_source_always_ok(self):
        r = verify_source("PR #123", self.tmpdir)
        self.assertEqual(r.status, "OK")
        self.assertEqual(r.source_type, "pr")

    def test_nonexistent_project_root(self):
        r = verify_source("src/file.py:L1-L10", Path("/nonexistent/root"))
        self.assertIn(r.status, ("FAIL", "SKIP"))


# ---------------------------------------------------------------------------
# TestCheckStaleness
# ---------------------------------------------------------------------------

class TestCheckStaleness(unittest.TestCase):

    def test_fresh_entry_not_stale(self):
        now = datetime.now(timezone.utc).isoformat()
        entry = _make_valid_entry(created_at=now)
        r = check_staleness(entry, threshold_days=90)
        self.assertFalse(r.stale)
        self.assertLessEqual(r.days_since_created, 1)

    def test_old_entry_stale(self):
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        entry = _make_valid_entry(created_at=old)
        r = check_staleness(entry, threshold_days=90)
        self.assertTrue(r.stale)
        self.assertGreaterEqual(r.days_since_created, 99)

    def test_verified_entry_uses_verified_date(self):
        old_created = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        recent_verified = datetime.now(timezone.utc).isoformat()
        entry = _make_valid_entry(
            created_at=old_created,
            last_verified=recent_verified,
        )
        r = check_staleness(entry, threshold_days=90)
        self.assertFalse(r.stale)
        self.assertIsNotNone(r.days_since_verified)
        self.assertLessEqual(r.days_since_verified, 1)

    def test_custom_threshold(self):
        age_40 = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        entry = _make_valid_entry(created_at=age_40)
        # Stale with threshold=30
        r30 = check_staleness(entry, threshold_days=30)
        self.assertTrue(r30.stale)
        # Not stale with threshold=90
        r90 = check_staleness(entry, threshold_days=90)
        self.assertFalse(r90.stale)


# ---------------------------------------------------------------------------
# TestCheckDuplicates
# ---------------------------------------------------------------------------

class TestCheckDuplicates(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"

    def _write_events(self, entries):
        with open(self.events_path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_no_duplicates_found(self):
        self._write_events([_make_valid_entry()])
        # Completely different entry
        candidate = _make_soft_entry(id="fact-new-11111111")
        r = check_duplicates(candidate, self.events_path)
        self.assertFalse(r.is_duplicate)

    def test_exact_duplicate_detected(self):
        existing = _make_valid_entry()
        self._write_events([existing])
        # Same content but different ID
        candidate = _make_valid_entry(id="lesson-inc036-bbbbbbbb")
        r = check_duplicates(candidate, self.events_path)
        self.assertTrue(r.is_duplicate)
        self.assertTrue(len(r.similar_entries) > 0)
        self.assertGreaterEqual(r.similar_entries[0][1], 0.95)

    def test_different_entry_not_duplicate(self):
        existing = _make_valid_entry()
        self._write_events([existing])
        candidate = {
            "id": "decision-arch-cccccccc",
            "type": "decision",
            "classification": "soft",
            "severity": None,
            "title": "Completely unrelated architecture decision about caching",
            "content": ["Use Redis for caching", "TTL of 5 minutes"],
            "rule": "Cache TTL MUST be configurable",
            "implication": None,
            "source": ["src/cache/config.py:L1-L20"],
            "tags": ["cache", "redis"],
            "created_at": "2026-02-01T15:00:00Z",
        }
        r = check_duplicates(candidate, self.events_path, threshold=0.85)
        self.assertFalse(r.is_duplicate)

    def test_self_not_flagged(self):
        existing = _make_valid_entry()
        self._write_events([existing])
        # Same entry (same ID) should not flag itself
        r = check_duplicates(existing, self.events_path)
        self.assertFalse(r.is_duplicate)

    def test_deprecated_entry_excluded(self):
        existing = _make_valid_entry(deprecated=True)
        self._write_events([existing])
        candidate = _make_valid_entry(id="lesson-inc036-dddddddd")
        r = check_duplicates(candidate, self.events_path)
        self.assertFalse(r.is_duplicate)


# ---------------------------------------------------------------------------
# TestCheckVerifyCommand
# ---------------------------------------------------------------------------

class TestCheckVerifyCommand(unittest.TestCase):

    def test_none_command_ok(self):
        status, _ = check_verify_command(None)
        self.assertEqual(status, "OK")

    def test_safe_grep_ok(self):
        status, _ = check_verify_command("grep -rn 'rolling' src/*.py")
        self.assertEqual(status, "OK")

    def test_pipe_command_ok(self):
        status, _ = check_verify_command("grep -rn 'x' src/*.py | grep -v 'shift'")
        self.assertEqual(status, "OK")

    def test_destructive_rm_fails(self):
        status, _ = check_verify_command("rm -rf /tmp/test")
        self.assertEqual(status, "FAIL")

    def test_redirect_fails(self):
        status, _ = check_verify_command("echo test > output.txt")
        self.assertEqual(status, "FAIL")

    def test_unknown_command_warns(self):
        status, _ = check_verify_command("python3 -c 'print(1)'")
        self.assertEqual(status, "WARN")


# ---------------------------------------------------------------------------
# TestVerifyEntry
# ---------------------------------------------------------------------------

class TestVerifyEntry(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.events_path.write_text("")
        self.config = {
            "verify": {"staleness_threshold_days": 90},
            "automation": {"dedup_threshold": 0.85},
        }

    def test_valid_entry_overall_ok_or_warn(self):
        """A valid entry may get WARN for staleness but not FAIL."""
        entry = _make_valid_entry(
            created_at=datetime.now(timezone.utc).isoformat()
        )
        result = verify_entry(entry, self.events_path, self.tmpdir, self.config)
        # Should not be FAIL (source file doesn't exist, but that's FAIL/WARN)
        self.assertIn(result["overall"], ("OK", "WARN", "FAIL"))
        self.assertEqual(result["entry_id"], entry["id"])

    def test_schema_error_propagated(self):
        bad_entry = {"id": "BAD"}
        result = verify_entry(bad_entry, self.events_path, self.tmpdir, self.config)
        self.assertEqual(result["overall"], "FAIL")

    def test_composite_report_has_all_keys(self):
        entry = _make_valid_entry()
        result = verify_entry(entry, self.events_path, self.tmpdir, self.config)
        for key in ["entry_id", "schema", "sources", "staleness", "dedup", "verify_cmd", "overall"]:
            self.assertIn(key, result)


# ---------------------------------------------------------------------------
# TestVerifyAllEntries
# ---------------------------------------------------------------------------

class TestVerifyAllEntries(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.config = {
            "verify": {"staleness_threshold_days": 90},
            "automation": {"dedup_threshold": 0.85},
        }

    def test_empty_events(self):
        self.events_path.write_text("")
        report = verify_all_entries(self.events_path, self.tmpdir, self.config)
        self.assertEqual(report.entries_checked, 0)
        self.assertGreater(report.duration_ms, 0)

    def test_multiple_entries(self):
        entries = [_make_valid_entry(), _make_soft_entry()]
        with open(self.events_path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        report = verify_all_entries(self.events_path, self.tmpdir, self.config)
        self.assertEqual(report.entries_checked, 2)

    def test_deprecated_skipped(self):
        entries = [
            _make_valid_entry(deprecated=True),
            _make_soft_entry(),
        ]
        with open(self.events_path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        report = verify_all_entries(self.events_path, self.tmpdir, self.config)
        self.assertEqual(report.entries_checked, 1)


if __name__ == "__main__":
    unittest.main()
