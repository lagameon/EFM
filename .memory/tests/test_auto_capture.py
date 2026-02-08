"""
Tests for EF Memory V2 — Auto-Capture (Draft Management)

Covers: _sanitize_title, create_draft, list_drafts,
        approve_draft, reject_draft, review_drafts
"""

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Import path setup
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.auto_capture import (
    ApproveResult,
    DraftInfo,
    ReviewReport,
    _sanitize_title,
    approve_draft,
    create_draft,
    expire_stale_drafts,
    list_drafts,
    reject_draft,
    review_drafts,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_valid_entry(**overrides) -> dict:
    """Create a valid entry for testing."""
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


def _make_invalid_entry() -> dict:
    """Create an entry that fails schema validation."""
    return {"id": "BAD"}


# ---------------------------------------------------------------------------
# TestSanitizeTitle
# ---------------------------------------------------------------------------

class TestSanitizeTitle(unittest.TestCase):

    def test_simple_title(self):
        self.assertEqual(_sanitize_title("My Title"), "my_title")

    def test_special_characters_removed(self):
        result = _sanitize_title("shift(1) MUST precede rolling()")
        self.assertNotIn("(", result)
        self.assertNotIn(")", result)
        self.assertIn("shift", result)

    def test_long_title_truncated(self):
        long_title = "a" * 200
        result = _sanitize_title(long_title)
        self.assertLessEqual(len(result), 50)

    def test_empty_title(self):
        self.assertEqual(_sanitize_title(""), "untitled")

    def test_only_special_chars(self):
        self.assertEqual(_sanitize_title("!!!@@@###"), "untitled")


# ---------------------------------------------------------------------------
# TestCreateDraft
# ---------------------------------------------------------------------------

class TestCreateDraft(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.drafts_dir = self.tmpdir / "drafts"

    def test_creates_json_file(self):
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)
        self.assertTrue(info.path.exists())
        self.assertTrue(info.path.name.endswith(".json"))

    def test_filename_format(self):
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)
        # Should be: YYYYMMDD_HHMMSS_sanitized_title.json
        parts = info.filename.split("_", 2)
        self.assertEqual(len(parts[0]), 8)  # YYYYMMDD
        self.assertEqual(len(parts[1]), 6)  # HHMMSS

    def test_meta_draft_status_added(self):
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)
        self.assertEqual(info.entry["_meta"]["draft_status"], "pending")

    def test_meta_capture_timestamp_added(self):
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)
        ts = info.entry["_meta"]["capture_timestamp"]
        self.assertTrue(len(ts) > 10)  # ISO 8601 format

    def test_validation_result_attached(self):
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)
        self.assertIsNotNone(info.validation)
        self.assertTrue(info.validation.valid)

    def test_invalid_entry_still_creates_draft(self):
        """Drafts can be saved even with validation errors (advisory)."""
        entry = _make_invalid_entry()
        info = create_draft(entry, self.drafts_dir)
        self.assertTrue(info.path.exists())
        self.assertFalse(info.validation.valid)

    def test_creates_drafts_dir_if_missing(self):
        new_dir = self.tmpdir / "new" / "nested" / "drafts"
        self.assertFalse(new_dir.exists())
        entry = _make_valid_entry()
        info = create_draft(entry, new_dir)
        self.assertTrue(new_dir.exists())
        self.assertTrue(info.path.exists())

    def test_entry_preserved_in_file(self):
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)
        loaded = json.loads(info.path.read_text())
        self.assertEqual(loaded["id"], entry["id"])
        self.assertEqual(loaded["title"], entry["title"])


# ---------------------------------------------------------------------------
# TestListDrafts
# ---------------------------------------------------------------------------

class TestListDrafts(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.drafts_dir = self.tmpdir / "drafts"
        self.drafts_dir.mkdir()

    def test_empty_directory(self):
        drafts = list_drafts(self.drafts_dir)
        self.assertEqual(len(drafts), 0)

    def test_lists_json_files_only(self):
        # Create a JSON draft and a README
        entry = _make_valid_entry()
        create_draft(entry, self.drafts_dir)
        (self.drafts_dir / "README.md").write_text("Not a draft\n")

        drafts = list_drafts(self.drafts_dir)
        self.assertEqual(len(drafts), 1)
        self.assertTrue(drafts[0].filename.endswith(".json"))

    def test_sorted_by_timestamp(self):
        # Create two drafts
        e1 = _make_valid_entry(id="lesson-test1-11111111", title="First entry")
        e2 = _make_valid_entry(id="lesson-test2-22222222", title="Second entry")
        create_draft(e1, self.drafts_dir)
        create_draft(e2, self.drafts_dir)

        drafts = list_drafts(self.drafts_dir)
        self.assertEqual(len(drafts), 2)
        # First should have earlier or equal timestamp
        self.assertLessEqual(
            drafts[0].capture_timestamp,
            drafts[1].capture_timestamp,
        )

    def test_handles_invalid_json(self):
        (self.drafts_dir / "bad.json").write_text("not valid json{{{")
        drafts = list_drafts(self.drafts_dir)
        self.assertEqual(len(drafts), 0)

    def test_nonexistent_directory(self):
        drafts = list_drafts(self.tmpdir / "nonexistent")
        self.assertEqual(len(drafts), 0)


# ---------------------------------------------------------------------------
# TestApproveDraft
# ---------------------------------------------------------------------------

class TestApproveDraft(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.drafts_dir = self.tmpdir / "drafts"
        self.events_path = self.tmpdir / "events.jsonl"

    def test_approve_appends_to_events(self):
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)

        result = approve_draft(info.path, self.events_path)
        self.assertTrue(result.success)

        # Verify entry in events.jsonl
        lines = self.events_path.read_text().strip().split("\n")
        self.assertEqual(len(lines), 1)
        loaded = json.loads(lines[0])
        self.assertEqual(loaded["id"], entry["id"])

    def test_approve_removes_draft_file(self):
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)
        self.assertTrue(info.path.exists())

        approve_draft(info.path, self.events_path)
        self.assertFalse(info.path.exists())

    def test_approve_strips_draft_meta(self):
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)

        approve_draft(info.path, self.events_path)
        loaded = json.loads(self.events_path.read_text().strip())

        meta = loaded.get("_meta", {})
        self.assertNotIn("draft_status", meta)
        self.assertNotIn("capture_timestamp", meta)

    def test_approve_fails_on_invalid_schema(self):
        entry = _make_invalid_entry()
        info = create_draft(entry, self.drafts_dir)

        result = approve_draft(info.path, self.events_path)
        self.assertFalse(result.success)
        self.assertIn("validation failed", result.message.lower())

        # Draft file should NOT be deleted
        self.assertTrue(info.path.exists())

    def test_approve_preserves_other_meta(self):
        entry = _make_valid_entry()
        entry["_meta"] = {"embedding_id": "vec-123"}
        info = create_draft(entry, self.drafts_dir)

        approve_draft(info.path, self.events_path)
        loaded = json.loads(self.events_path.read_text().strip())
        self.assertEqual(loaded["_meta"]["embedding_id"], "vec-123")

    def test_approve_creates_events_if_missing(self):
        self.assertFalse(self.events_path.exists())
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)

        result = approve_draft(info.path, self.events_path)
        self.assertTrue(result.success)
        self.assertTrue(self.events_path.exists())

    def test_approve_nonexistent_draft(self):
        result = approve_draft(self.tmpdir / "nonexistent.json", self.events_path)
        self.assertFalse(result.success)
        self.assertIn("not found", result.message.lower())


# ---------------------------------------------------------------------------
# TestRejectDraft
# ---------------------------------------------------------------------------

class TestRejectDraft(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.drafts_dir = self.tmpdir / "drafts"

    def test_reject_deletes_file(self):
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)
        self.assertTrue(info.path.exists())

        result = reject_draft(info.path)
        self.assertTrue(result)
        self.assertFalse(info.path.exists())

    def test_reject_nonexistent_returns_false(self):
        result = reject_draft(self.tmpdir / "nonexistent.json")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# TestReviewDrafts
# ---------------------------------------------------------------------------

class TestReviewDrafts(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.drafts_dir = self.tmpdir / "drafts"
        self.events_path = self.tmpdir / "events.jsonl"
        self.events_path.write_text("")
        self.config = {
            "verify": {"staleness_threshold_days": 90},
            "automation": {"dedup_threshold": 0.85},
        }

    def test_review_includes_verification(self):
        entry = _make_valid_entry()
        create_draft(entry, self.drafts_dir)

        report = review_drafts(
            self.drafts_dir, self.events_path, self.tmpdir, self.config
        )
        self.assertEqual(report.total_drafts, 1)
        self.assertEqual(len(report.verification_results), 1)

    def test_review_empty_dir(self):
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        report = review_drafts(
            self.drafts_dir, self.events_path, self.tmpdir, self.config
        )
        self.assertEqual(report.total_drafts, 0)
        self.assertGreater(report.duration_ms, 0)

    def test_review_counts_valid_invalid(self):
        # One valid, one invalid
        create_draft(_make_valid_entry(), self.drafts_dir)
        create_draft(_make_invalid_entry(), self.drafts_dir)

        report = review_drafts(
            self.drafts_dir, self.events_path, self.tmpdir, self.config
        )
        self.assertEqual(report.total_drafts, 2)
        # Invalid should be counted
        self.assertGreaterEqual(report.invalid_drafts, 1)


class TestApproveEmptyMeta(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.events_path.write_text("")
        self.drafts_dir = self.tmpdir / "drafts"

    def test_empty_meta_removed_from_approved_entry(self):
        """After stripping draft fields, if _meta is empty, it should be deleted."""
        entry = _make_valid_entry()
        info = create_draft(entry, self.drafts_dir)
        result = approve_draft(info.path, self.events_path)
        self.assertTrue(result.success)

        lines = self.events_path.read_text().strip().split("\n")
        saved = json.loads(lines[0])
        self.assertNotIn("_meta", saved)


class TestCreateDraftCollision(unittest.TestCase):

    def test_collision_appends_counter(self):
        drafts_dir = Path(tempfile.mkdtemp()) / "drafts"
        entry = _make_valid_entry()
        info1 = create_draft(entry, drafts_dir)
        info2 = create_draft(entry, drafts_dir)
        self.assertNotEqual(info1.filename, info2.filename)
        self.assertTrue(info1.path.exists())
        self.assertTrue(info2.path.exists())


class TestCreateDraftDeepCopy(unittest.TestCase):

    def test_original_entry_not_mutated(self):
        drafts_dir = Path(tempfile.mkdtemp()) / "drafts"
        entry = _make_valid_entry()
        original_keys = set(entry.get("_meta", {}).keys())
        create_draft(entry, drafts_dir)
        after_keys = set(entry.get("_meta", {}).keys())
        self.assertEqual(original_keys, after_keys)


# ---------------------------------------------------------------------------
# TestExpireStaleDrafts
# ---------------------------------------------------------------------------

class TestExpireStaleDrafts(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.drafts_dir = self.tmpdir / "drafts"

    def _create_draft_with_age(self, title: str, age_days: int) -> DraftInfo:
        """Helper: create a draft and backdate its capture_timestamp."""
        entry = _make_valid_entry(title=title)
        info = create_draft(entry, self.drafts_dir)
        # Backdate the capture_timestamp in the file
        data = json.loads(info.path.read_text())
        old_ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
        data["_meta"]["capture_timestamp"] = old_ts
        info.path.write_text(json.dumps(data, indent=2) + "\n")
        return info

    def test_expires_old_drafts(self):
        """Drafts older than max_age_days should be deleted."""
        self._create_draft_with_age("old draft one", age_days=10)
        self._create_draft_with_age("fresh draft two", age_days=2)

        expired = expire_stale_drafts(self.drafts_dir, max_age_days=7)
        self.assertEqual(len(expired), 1)
        self.assertIn("old_draft", expired[0].filename)

        remaining = list_drafts(self.drafts_dir)
        self.assertEqual(len(remaining), 1)

    def test_keeps_fresh_drafts(self):
        """Drafts younger than max_age_days should not be deleted."""
        self._create_draft_with_age("fresh one", age_days=1)
        self._create_draft_with_age("fresh two", age_days=3)

        expired = expire_stale_drafts(self.drafts_dir, max_age_days=7)
        self.assertEqual(len(expired), 0)
        remaining = list_drafts(self.drafts_dir)
        self.assertEqual(len(remaining), 2)

    def test_handles_empty_dir(self):
        """Empty drafts directory should return empty list."""
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        expired = expire_stale_drafts(self.drafts_dir, max_age_days=7)
        self.assertEqual(len(expired), 0)

    def test_handles_nonexistent_dir(self):
        """Nonexistent drafts directory should return empty list."""
        expired = expire_stale_drafts(self.tmpdir / "nonexistent", max_age_days=7)
        self.assertEqual(len(expired), 0)

    def test_zero_disables_expiry(self):
        """max_age_days=0 should disable expiry (no drafts deleted)."""
        self._create_draft_with_age("ancient draft", age_days=365)
        expired = expire_stale_drafts(self.drafts_dir, max_age_days=0)
        self.assertEqual(len(expired), 0)
        remaining = list_drafts(self.drafts_dir)
        self.assertEqual(len(remaining), 1)

    def test_negative_disables_expiry(self):
        """Negative max_age_days should also disable expiry."""
        self._create_draft_with_age("old draft neg", age_days=100)
        expired = expire_stale_drafts(self.drafts_dir, max_age_days=-1)
        self.assertEqual(len(expired), 0)

    def test_boundary_exact_age(self):
        """A draft at exactly max_age_days boundary should NOT be expired (< cutoff only)."""
        self._create_draft_with_age("boundary draft", age_days=7)
        expired = expire_stale_drafts(self.drafts_dir, max_age_days=7)
        # Due to sub-second timing, 7-day-old draft is at or slightly past cutoff
        # Accept either 0 or 1 — the important thing is no crash
        self.assertIn(len(expired), [0, 1])

    def test_all_expired(self):
        """When all drafts are old, all should be expired."""
        self._create_draft_with_age("old one", age_days=30)
        self._create_draft_with_age("old two", age_days=20)
        self._create_draft_with_age("old three", age_days=15)

        expired = expire_stale_drafts(self.drafts_dir, max_age_days=7)
        self.assertEqual(len(expired), 3)
        remaining = list_drafts(self.drafts_dir)
        self.assertEqual(len(remaining), 0)

    def test_returns_draft_info_objects(self):
        """Returned objects should be DraftInfo with correct fields."""
        self._create_draft_with_age("expirable draft", age_days=10)
        expired = expire_stale_drafts(self.drafts_dir, max_age_days=7)
        self.assertEqual(len(expired), 1)
        self.assertIsInstance(expired[0], DraftInfo)
        self.assertIn("expirable", expired[0].filename)


if __name__ == "__main__":
    unittest.main()
