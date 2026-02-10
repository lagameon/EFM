"""
Tests for EF Memory — Compaction + Time-Sharded Archive (M11)

Covers: compact, get_compaction_stats, _quarter_key, _archive_lines,
        _atomic_rewrite, startup hint integration, auto-compact on stop
"""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

# Import path setup
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.compaction import (
    CompactionReport,
    CompactionStats,
    _archive_lines,
    _atomic_rewrite,
    _quarter_key,
    _read_all_lines,
    _resolve_latest_wins,
    compact,
    get_compaction_stats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    entry_id: str,
    title: str = "Test entry",
    created_at: str = "2026-02-01T10:00:00Z",
    deprecated: bool = False,
    **kwargs,
) -> dict:
    """Create a minimal valid entry."""
    entry = {
        "id": entry_id,
        "type": "lesson",
        "classification": "soft",
        "severity": "S3",
        "title": title,
        "content": [f"Content for {entry_id}"],
        "rule": None,
        "source": ["test"],
        "tags": ["test"],
        "created_at": created_at,
        "deprecated": deprecated,
        "_meta": {},
    }
    entry.update(kwargs)
    return entry


def _write_events(path: Path, entries: list) -> None:
    """Write entries to JSONL file."""
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_events(path: Path) -> list:
    """Read all entries from JSONL file."""
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# Tests: _quarter_key
# ---------------------------------------------------------------------------

class TestQuarterKey(unittest.TestCase):

    def test_q1_january(self):
        self.assertEqual(_quarter_key("2026-01-15T10:00:00Z"), "2026Q1")

    def test_q1_march(self):
        self.assertEqual(_quarter_key("2026-03-31T23:59:59Z"), "2026Q1")

    def test_q2_april(self):
        self.assertEqual(_quarter_key("2026-04-01T00:00:00Z"), "2026Q2")

    def test_q3_july(self):
        self.assertEqual(_quarter_key("2026-07-15T12:00:00Z"), "2026Q3")

    def test_q4_december(self):
        self.assertEqual(_quarter_key("2026-12-25T08:00:00Z"), "2026Q4")

    def test_none_returns_unknown(self):
        self.assertEqual(_quarter_key(None), "unknown")

    def test_empty_string_returns_unknown(self):
        self.assertEqual(_quarter_key(""), "unknown")

    def test_invalid_date_returns_unknown(self):
        self.assertEqual(_quarter_key("not-a-date"), "unknown")

    def test_offset_format(self):
        self.assertEqual(_quarter_key("2026-06-01T10:00:00+00:00"), "2026Q2")


# ---------------------------------------------------------------------------
# Tests: compact
# ---------------------------------------------------------------------------

class TestCompact(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.events_path = Path(self.tmpdir) / "events.jsonl"
        self.archive_dir = Path(self.tmpdir) / "archive"
        self.config = {"compaction": {"sort_output": True}}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_compact_removes_superseded(self):
        """v1 + v2 of same ID → hot file keeps only v2."""
        v1 = _make_entry("a", title="Version 1", created_at="2026-01-10T10:00:00Z")
        v2 = _make_entry("a", title="Version 2", created_at="2026-01-10T10:00:00Z")
        _write_events(self.events_path, [v1, v2])

        report = compact(self.events_path, self.archive_dir, self.config)

        self.assertEqual(report.lines_before, 2)
        self.assertEqual(report.lines_after, 1)
        entries = _read_events(self.events_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Version 2")

    def test_compact_removes_deprecated(self):
        """Deprecated entries are moved to archive."""
        active = _make_entry("a", title="Active", created_at="2026-02-01T10:00:00Z")
        deprecated = _make_entry("b", title="Old", deprecated=True, created_at="2026-01-01T10:00:00Z")
        _write_events(self.events_path, [active, deprecated])

        report = compact(self.events_path, self.archive_dir, self.config)

        self.assertEqual(report.entries_kept, 1)
        self.assertEqual(report.lines_archived, 1)
        entries = _read_events(self.events_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "a")

    def test_archive_quarterly_partitioning(self):
        """Entries from Q1 and Q2 go to separate archive files."""
        q1 = _make_entry("a", title="Q1", deprecated=True, created_at="2026-02-01T10:00:00Z")
        q2 = _make_entry("b", title="Q2", deprecated=True, created_at="2026-05-01T10:00:00Z")
        active = _make_entry("c", title="Active", created_at="2026-06-01T10:00:00Z")
        _write_events(self.events_path, [q1, q2, active])

        report = compact(self.events_path, self.archive_dir, self.config)

        self.assertIn("2026Q1", report.quarters_touched)
        self.assertIn("2026Q2", report.quarters_touched)
        self.assertTrue((self.archive_dir / "events_2026Q1.jsonl").exists())
        self.assertTrue((self.archive_dir / "events_2026Q2.jsonl").exists())

    def test_atomic_rewrite(self):
        """Hot file is correct after compaction — one line per active entry."""
        entries = [
            _make_entry("a", title="A", created_at="2026-01-01T10:00:00Z"),
            _make_entry("b", title="B", created_at="2026-02-01T10:00:00Z"),
            _make_entry("c", title="C", deprecated=True, created_at="2026-03-01T10:00:00Z"),
        ]
        _write_events(self.events_path, entries)

        compact(self.events_path, self.archive_dir, self.config)

        result = _read_events(self.events_path)
        self.assertEqual(len(result), 2)
        ids = {e["id"] for e in result}
        self.assertEqual(ids, {"a", "b"})

    def test_idempotent(self):
        """Compacting already-compact file produces no change."""
        entries = [
            _make_entry("a", created_at="2026-01-01T10:00:00Z"),
            _make_entry("b", created_at="2026-02-01T10:00:00Z"),
        ]
        _write_events(self.events_path, entries)

        report1 = compact(self.events_path, self.archive_dir, self.config)
        content_after_first = self.events_path.read_text()

        report2 = compact(self.events_path, self.archive_dir, self.config)

        self.assertEqual(report2.lines_before, report1.lines_after)
        self.assertEqual(report2.lines_after, report1.lines_after)
        self.assertEqual(report2.lines_archived, 0)

    def test_empty_file(self):
        """Empty events.jsonl compacts gracefully."""
        self.events_path.write_text("", encoding="utf-8")

        report = compact(self.events_path, self.archive_dir, self.config)

        self.assertEqual(report.lines_before, 0)
        self.assertEqual(report.lines_after, 0)
        self.assertEqual(report.lines_archived, 0)

    def test_missing_file(self):
        """Missing events.jsonl compacts gracefully."""
        report = compact(self.events_path, self.archive_dir, self.config)

        self.assertEqual(report.lines_before, 0)
        self.assertEqual(report.lines_after, 0)

    def test_sort_by_created_at(self):
        """Post-compaction entries are sorted by created_at."""
        entries = [
            _make_entry("c", created_at="2026-03-01T10:00:00Z"),
            _make_entry("a", created_at="2026-01-01T10:00:00Z"),
            _make_entry("b", created_at="2026-02-01T10:00:00Z"),
        ]
        _write_events(self.events_path, entries)

        compact(self.events_path, self.archive_dir, self.config)

        result = _read_events(self.events_path)
        dates = [e["created_at"] for e in result]
        self.assertEqual(dates, sorted(dates))

    def test_archive_append_not_overwrite(self):
        """Running compaction twice appends to (not overwrites) archive files."""
        # First round: deprecated Q1 entry
        entries1 = [
            _make_entry("a", deprecated=True, created_at="2026-01-15T10:00:00Z"),
            _make_entry("b", created_at="2026-02-01T10:00:00Z"),
        ]
        _write_events(self.events_path, entries1)
        compact(self.events_path, self.archive_dir, self.config)

        archive_q1 = self.archive_dir / "events_2026Q1.jsonl"
        lines_after_first = len(_read_events(archive_q1))

        # Second round: add another deprecated Q1 entry and compact again
        entries2 = _read_events(self.events_path)
        entries2.append(_make_entry("c", deprecated=True, created_at="2026-02-20T10:00:00Z"))
        _write_events(self.events_path, entries2)
        compact(self.events_path, self.archive_dir, self.config)

        lines_after_second = len(_read_events(archive_q1))
        self.assertGreater(lines_after_second, lines_after_first)

    def test_compaction_log(self):
        """Compaction log is appended with report."""
        entries = [
            _make_entry("a", deprecated=True, created_at="2026-01-01T10:00:00Z"),
            _make_entry("b", created_at="2026-02-01T10:00:00Z"),
        ]
        _write_events(self.events_path, entries)

        compact(self.events_path, self.archive_dir, self.config)

        log_path = self.archive_dir / "compaction_log.jsonl"
        self.assertTrue(log_path.exists())
        log_entries = _read_events(log_path)
        self.assertEqual(len(log_entries), 1)
        self.assertIn("timestamp", log_entries[0])
        self.assertIn("lines_before", log_entries[0])
        self.assertEqual(log_entries[0]["lines_before"], 2)

    def test_unparseable_created_at(self):
        """Entries with bad dates go to events_unknown.jsonl archive."""
        bad = _make_entry("a", deprecated=True, created_at="not-a-date")
        good = _make_entry("b", created_at="2026-02-01T10:00:00Z")
        _write_events(self.events_path, [bad, good])

        report = compact(self.events_path, self.archive_dir, self.config)

        self.assertIn("unknown", report.quarters_touched)
        self.assertTrue((self.archive_dir / "events_unknown.jsonl").exists())


# ---------------------------------------------------------------------------
# Tests: get_compaction_stats
# ---------------------------------------------------------------------------

class TestCompactionStats(unittest.TestCase):

    def test_stats_clean_file(self):
        """Clean file with no duplicates or deprecated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events_path = Path(tmpdir) / "events.jsonl"
            _write_events(events_path, [
                _make_entry("a"),
                _make_entry("b"),
            ])

            stats = get_compaction_stats(events_path)

            self.assertEqual(stats.total_lines, 2)
            self.assertEqual(stats.active_entries, 2)
            self.assertEqual(stats.deprecated_entries, 0)
            self.assertEqual(stats.superseded_lines, 0)
            self.assertAlmostEqual(stats.waste_ratio, 1.0)
            self.assertFalse(stats.suggest_compact)

    def test_stats_with_duplicates(self):
        """File with superseded entries shows correct waste."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events_path = Path(tmpdir) / "events.jsonl"
            _write_events(events_path, [
                _make_entry("a", title="v1"),
                _make_entry("a", title="v2"),
                _make_entry("a", title="v3"),
                _make_entry("b"),
            ])

            stats = get_compaction_stats(events_path)

            self.assertEqual(stats.total_lines, 4)
            self.assertEqual(stats.unique_entries, 2)
            self.assertEqual(stats.active_entries, 2)
            self.assertEqual(stats.superseded_lines, 2)
            self.assertEqual(stats.waste_ratio, 2.0)

    def test_stats_suggest_compact(self):
        """Suggest compact when waste ratio >= threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events_path = Path(tmpdir) / "events.jsonl"
            _write_events(events_path, [
                _make_entry("a", title="v1"),
                _make_entry("a", title="v2"),
                _make_entry("a", title="v3"),
            ])

            stats = get_compaction_stats(events_path, threshold=2.0)
            # 3 lines / 1 active = 3.0x waste > 2.0 threshold
            self.assertTrue(stats.suggest_compact)

    def test_stats_empty_file(self):
        """Empty file returns zero stats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events_path = Path(tmpdir) / "events.jsonl"
            events_path.write_text("", encoding="utf-8")

            stats = get_compaction_stats(events_path)

            self.assertEqual(stats.total_lines, 0)
            self.assertFalse(stats.suggest_compact)


# ---------------------------------------------------------------------------
# Tests: startup hint integration
# ---------------------------------------------------------------------------

class TestStartupHintIntegration(unittest.TestCase):

    def test_startup_report_includes_compaction_fields(self):
        """StartupReport dataclass has compaction_suggested and waste_ratio."""
        sys.path.insert(0, str(_MEMORY_DIR))
        from lib.auto_sync import StartupReport

        report = StartupReport()
        self.assertFalse(report.compaction_suggested)
        self.assertEqual(report.waste_ratio, 0.0)

    def test_format_hint_includes_compact_suggestion(self):
        """_format_hint includes compact suggestion when suggested."""
        sys.path.insert(0, str(_MEMORY_DIR))
        from lib.auto_sync import StartupReport, _format_hint

        report = StartupReport()
        report.total_entries = 10
        report.compaction_suggested = True
        report.waste_ratio = 3.5

        hint = _format_hint(report)
        self.assertIn("compact suggested", hint)
        self.assertIn("3.5x waste", hint)


# ---------------------------------------------------------------------------
# Tests: auto-compact on stop
# ---------------------------------------------------------------------------

class TestAutoCompactOnStop(unittest.TestCase):

    def test_auto_compact_fires_above_threshold(self):
        """Compaction runs when waste ratio exceeds threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events_path = Path(tmpdir) / "events.jsonl"
            archive_dir = Path(tmpdir) / "archive"
            config = {"compaction": {"auto_suggest_threshold": 2.0}}

            # Create wasteful file: 3 versions of same entry = 3.0x waste
            _write_events(events_path, [
                _make_entry("a", title="v1"),
                _make_entry("a", title="v2"),
                _make_entry("a", title="v3"),
            ])

            stats = get_compaction_stats(events_path, threshold=2.0)
            self.assertTrue(stats.suggest_compact)

            report = compact(events_path, archive_dir, config)
            self.assertEqual(report.lines_after, 1)
            self.assertEqual(report.lines_archived, 2)

    def test_auto_compact_skips_below_threshold(self):
        """Compaction does NOT run when waste ratio is below threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events_path = Path(tmpdir) / "events.jsonl"

            # Clean file: 2 unique entries = 1.0x waste
            _write_events(events_path, [
                _make_entry("a"),
                _make_entry("b"),
            ])

            stats = get_compaction_stats(events_path, threshold=2.0)
            self.assertFalse(stats.suggest_compact)
            # In the stop hook, compact() would NOT be called


# ---------------------------------------------------------------------------
# Tests: evolution checkpoint reset
# ---------------------------------------------------------------------------

class TestEvolutionCheckpointReset(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.archive_dir = self.tmpdir / "archive"

    def test_compact_resets_evolution_checkpoint(self):
        """Compaction should delete evolution_checkpoint.json."""
        # Create entries with a deprecated one to trigger actual compaction
        e1 = _make_entry("a-test-00000001", title="Active entry")
        e2 = _make_entry("b-test-00000002", title="Old entry", deprecated=True)
        _write_events(self.events_path, [e1, e2])

        # Create a fake checkpoint file
        cp_path = self.tmpdir / "evolution_checkpoint.json"
        cp_path.write_text('{"hash": "old"}')

        compact(self.events_path, self.archive_dir, {})

        self.assertFalse(cp_path.exists(), "evolution_checkpoint.json should be deleted after compaction")

    def test_compact_no_checkpoint_ok(self):
        """Compaction should not fail if no checkpoint exists."""
        e1 = _make_entry("a-test-00000001")
        e2 = _make_entry("b-test-00000002", deprecated=True)
        _write_events(self.events_path, [e1, e2])

        # No checkpoint file exists
        report = compact(self.events_path, self.archive_dir, {})
        self.assertEqual(report.entries_kept, 1)

    def test_compact_checkpoint_permission_error(self):
        """Compaction should not fail if checkpoint can't be deleted."""
        e1 = _make_entry("a-test-00000001")
        e2 = _make_entry("b-test-00000002", deprecated=True)
        _write_events(self.events_path, [e1, e2])

        cp_path = self.tmpdir / "evolution_checkpoint.json"
        cp_path.write_text('{"hash": "old"}')

        # Mock unlink to raise OSError
        with unittest.mock.patch.object(Path, 'unlink', side_effect=OSError("Permission denied")):
            report = compact(self.events_path, self.archive_dir, {})
            self.assertEqual(report.entries_kept, 1)  # Compaction still succeeds


if __name__ == "__main__":
    unittest.main()
