"""Tests for EF Memory post-merge repair."""
import json
import tempfile
import unittest
from pathlib import Path

from lib.repair import (
    RepairReport,
    detect_merge_markers,
    repair_events,
    _read_raw_lines,
    _resolve_by_newest,
    _check_orphan_sources,
)


def _make_entry(entry_id, title="test", created_at="2026-02-01T00:00:00Z", **extra):
    entry = {
        "id": entry_id,
        "type": "fact",
        "classification": "soft",
        "title": title,
        "content": ["test"],
        "source": extra.pop("source", ["test.py:L1"]),
        "created_at": created_at,
    }
    entry.update(extra)
    return entry


def _write_events(path, lines):
    """Write raw lines (strings or dicts) to a file."""
    with open(path, "w") as f:
        for line in lines:
            if isinstance(line, dict):
                f.write(json.dumps(line) + "\n")
            else:
                f.write(line + "\n")


# ===========================================================================
# detect_merge_markers
# ===========================================================================

class TestDetectMergeMarkers(unittest.TestCase):

    def test_clean_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_make_entry("EV-001")) + "\n")
            f.write(json.dumps(_make_entry("EV-002")) + "\n")
            path = Path(f.name)
        self.assertEqual(detect_merge_markers(path), 0)
        path.unlink()

    def test_file_with_markers(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_make_entry("EV-001")) + "\n")
            f.write("<<<<<<< HEAD\n")
            f.write(json.dumps(_make_entry("EV-002")) + "\n")
            f.write("=======\n")
            f.write(json.dumps(_make_entry("EV-003")) + "\n")
            f.write(">>>>>>> feature-branch\n")
            path = Path(f.name)
        self.assertEqual(detect_merge_markers(path), 3)
        path.unlink()

    def test_nonexistent_file(self):
        self.assertEqual(detect_merge_markers(Path("/nonexistent/events.jsonl")), 0)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = Path(f.name)
        self.assertEqual(detect_merge_markers(path), 0)
        path.unlink()


# ===========================================================================
# _read_raw_lines
# ===========================================================================

class TestReadRawLines(unittest.TestCase):

    def test_strips_markers(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_events(f.name, [
                _make_entry("EV-001"),
                "<<<<<<< HEAD",
                _make_entry("EV-002", title="ours"),
                "=======",
                _make_entry("EV-003", title="theirs"),
                ">>>>>>> feature",
            ])
            path = Path(f.name)

        entries, total_valid, markers = _read_raw_lines(path)
        self.assertEqual(markers, 3)
        self.assertEqual(total_valid, 3)
        ids = [e["id"] for e in entries]
        self.assertIn("EV-001", ids)
        self.assertIn("EV-002", ids)
        self.assertIn("EV-003", ids)
        path.unlink()

    def test_tracks_position(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_events(f.name, [
                _make_entry("EV-001"),
                _make_entry("EV-002"),
            ])
            path = Path(f.name)

        entries, _, _ = _read_raw_lines(path)
        self.assertEqual(entries[0]["_pos"], 0)
        self.assertEqual(entries[1]["_pos"], 1)
        path.unlink()


# ===========================================================================
# _resolve_by_newest
# ===========================================================================

class TestResolveByNewest(unittest.TestCase):

    def test_no_duplicates(self):
        entries = [
            {**_make_entry("EV-001"), "_pos": 0},
            {**_make_entry("EV-002"), "_pos": 1},
        ]
        result, dups = _resolve_by_newest(entries)
        self.assertEqual(len(result), 2)
        self.assertEqual(dups, 0)

    def test_same_id_different_timestamps(self):
        entries = [
            {**_make_entry("EV-001", created_at="2026-01-01T00:00:00Z", title="old"), "_pos": 0},
            {**_make_entry("EV-001", created_at="2026-02-01T00:00:00Z", title="new"), "_pos": 1},
        ]
        result, dups = _resolve_by_newest(entries)
        self.assertEqual(len(result), 1)
        self.assertEqual(dups, 1)
        self.assertEqual(result[0]["title"], "new")

    def test_same_id_same_timestamp_file_order(self):
        """When timestamps are equal, later file position wins."""
        entries = [
            {**_make_entry("EV-001", created_at="2026-02-01T00:00:00Z", title="first"), "_pos": 0},
            {**_make_entry("EV-001", created_at="2026-02-01T00:00:00Z", title="second"), "_pos": 5},
        ]
        result, dups = _resolve_by_newest(entries)
        self.assertEqual(len(result), 1)
        self.assertEqual(dups, 1)
        self.assertEqual(result[0]["title"], "second")

    def test_newer_timestamp_wins_regardless_of_position(self):
        """Newer timestamp wins even if it appears earlier in file."""
        entries = [
            {**_make_entry("EV-001", created_at="2026-03-01T00:00:00Z", title="newer-first"), "_pos": 0},
            {**_make_entry("EV-001", created_at="2026-01-01T00:00:00Z", title="older-second"), "_pos": 5},
        ]
        result, dups = _resolve_by_newest(entries)
        self.assertEqual(result[0]["title"], "newer-first")

    def test_pos_field_stripped(self):
        entries = [
            {**_make_entry("EV-001"), "_pos": 3},
        ]
        result, _ = _resolve_by_newest(entries)
        self.assertNotIn("_pos", result[0])


# ===========================================================================
# _check_orphan_sources
# ===========================================================================

class TestCheckOrphanSources(unittest.TestCase):

    def test_existing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "file.py").write_text("pass")
            entries = [_make_entry("EV-001", source=["src/file.py:L1"])]
            orphans = _check_orphan_sources(entries, root)
            self.assertEqual(len(orphans), 0)

    def test_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = [_make_entry("EV-001", source=["missing/file.py:L1"])]
            orphans = _check_orphan_sources(entries, root)
            self.assertEqual(len(orphans), 1)
            self.assertEqual(orphans[0].entry_id, "EV-001")

    def test_commit_source_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [_make_entry("EV-001", source=["commit abc123"])]
            orphans = _check_orphan_sources(entries, Path(tmp))
            self.assertEqual(len(orphans), 0)

    def test_pr_source_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [_make_entry("EV-001", source=["PR #42"])]
            orphans = _check_orphan_sources(entries, Path(tmp))
            self.assertEqual(len(orphans), 0)

    def test_heading_anchor_source(self):
        """Source like docs/readme.md#section:L10 should check docs/readme.md."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "readme.md").write_text("# hi")
            entries = [_make_entry("EV-001", source=["docs/readme.md#section:L10"])]
            orphans = _check_orphan_sources(entries, root)
            self.assertEqual(len(orphans), 0)

    def test_function_ref_source(self):
        """Source like src/file.py::func should check src/file.py."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "file.py").write_text("pass")
            entries = [_make_entry("EV-001", source=["src/file.py::my_func"])]
            orphans = _check_orphan_sources(entries, root)
            self.assertEqual(len(orphans), 0)


# ===========================================================================
# repair_events (integration)
# ===========================================================================

class TestRepairEvents(unittest.TestCase):

    def test_clean_file_no_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            _write_events(str(events), [
                _make_entry("EV-001"),
                _make_entry("EV-002"),
            ])
            report = repair_events(events, root)
            self.assertFalse(report.needs_repair)
            self.assertEqual(report.merge_markers_removed, 0)
            self.assertEqual(report.duplicate_ids_resolved, 0)

    def test_removes_markers_and_keeps_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            _write_events(str(events), [
                _make_entry("EV-001"),
                "<<<<<<< HEAD",
                _make_entry("EV-002", title="ours"),
                "=======",
                _make_entry("EV-003", title="theirs"),
                ">>>>>>> feature",
            ])
            report = repair_events(events, root)
            self.assertTrue(report.needs_repair)
            self.assertEqual(report.merge_markers_removed, 3)
            self.assertEqual(report.entries_after, 3)

            # Verify file is clean
            self.assertEqual(detect_merge_markers(events), 0)
            # All 3 entries present
            with open(events) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(lines), 3)

    def test_deduplicates_by_newest_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            _write_events(str(events), [
                _make_entry("EV-001", title="old", created_at="2026-01-01T00:00:00Z"),
                _make_entry("EV-001", title="new", created_at="2026-02-01T00:00:00Z"),
                _make_entry("EV-002"),
            ])
            report = repair_events(events, root)
            self.assertTrue(report.needs_repair)
            self.assertEqual(report.duplicate_ids_resolved, 1)
            self.assertEqual(report.entries_after, 2)

            # Verify newest was kept
            with open(events) as f:
                entries = {json.loads(l)["id"]: json.loads(l) for l in f if l.strip()}
            self.assertEqual(entries["EV-001"]["title"], "new")

    def test_dry_run_no_modification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            _write_events(str(events), [
                _make_entry("EV-001", created_at="2026-01-01T00:00:00Z"),
                _make_entry("EV-001", created_at="2026-02-01T00:00:00Z"),
                "<<<<<<< HEAD",
            ])
            original = events.read_text()

            report = repair_events(events, root, dry_run=True)
            self.assertTrue(report.needs_repair)
            self.assertTrue(report.dry_run)

            # File unchanged
            self.assertEqual(events.read_text(), original)

    def test_backup_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            _write_events(str(events), [
                "<<<<<<< HEAD",
                _make_entry("EV-001"),
                "=======",
                ">>>>>>> branch",
            ])
            report = repair_events(events, root, create_backup=True)
            self.assertTrue(report.backup_path)
            self.assertTrue(Path(report.backup_path).exists())
            # Backup should contain original content (with markers)
            bak_content = Path(report.backup_path).read_text()
            self.assertIn("<<<<<<<", bak_content)

    def test_no_backup_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            _write_events(str(events), [
                "<<<<<<< HEAD",
                _make_entry("EV-001"),
                ">>>>>>> branch",
            ])
            report = repair_events(events, root, create_backup=False)
            self.assertEqual(report.backup_path, "")

    def test_sorted_by_created_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            _write_events(str(events), [
                _make_entry("EV-003", created_at="2026-03-01T00:00:00Z"),
                _make_entry("EV-001", created_at="2026-01-01T00:00:00Z"),
                # Duplicate to force repair
                _make_entry("EV-003", created_at="2026-03-01T00:00:00Z"),
            ])
            repair_events(events, root)

            with open(events) as f:
                entries = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(entries[0]["id"], "EV-001")  # Jan first
            self.assertEqual(entries[1]["id"], "EV-003")  # Mar second

    def test_nonexistent_file(self):
        report = repair_events(Path("/tmp/nonexistent.jsonl"), Path("/tmp"))
        self.assertFalse(report.needs_repair)
        self.assertEqual(report.entries_before, 0)

    def test_orphan_sources_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            _write_events(str(events), [
                "<<<<<<< HEAD",  # force repair
                _make_entry("EV-001", source=["nonexistent/file.py:L1"]),
                ">>>>>>> branch",
            ])
            report = repair_events(events, root)
            self.assertEqual(len(report.orphan_sources), 1)
            self.assertEqual(report.orphan_sources[0].entry_id, "EV-001")


# ===========================================================================
# Startup integration
# ===========================================================================

class TestStartupMergeDetection(unittest.TestCase):

    def test_format_hint_includes_merge_warning(self):
        """StartupReport.merge_markers should appear in hint."""
        from lib.auto_sync import StartupReport, _format_hint
        report = StartupReport()
        report.merge_markers = 6
        report.total_entries = 10
        hint = _format_hint(report)
        self.assertIn("merge conflict markers", hint)
        self.assertIn("/memory-repair", hint)

    def test_format_hint_no_warning_when_clean(self):
        from lib.auto_sync import StartupReport, _format_hint
        report = StartupReport()
        report.merge_markers = 0
        report.total_entries = 10
        hint = _format_hint(report)
        self.assertNotIn("merge", hint)


if __name__ == "__main__":
    unittest.main()
