"""Tests for generate_rules module — Layer 1 auto-injection."""

import json
import tempfile
import unittest
import sys
from pathlib import Path

# Add .memory/ to path so 'lib' and 'tests' are importable
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.generate_rules import (
    extract_domain,
    generate_rule_files,
    clean_rule_files,
    _load_hard_entries,
    _generate_domain_markdown,
)
from tests.conftest import SAMPLE_ENTRIES


class TestExtractDomain(unittest.TestCase):

    def test_source_feature_path(self):
        entry = {"source": ["src/features/feature_engine.py:L10-L20"], "tags": []}
        self.assertEqual(extract_domain(entry), "feature-engine")

    def test_source_labels_path(self):
        entry = {"source": ["src/labels/risk_adjusted_labels.py:L93-L144"], "tags": []}
        self.assertEqual(extract_domain(entry), "labels")

    def test_source_incidents_path(self):
        entry = {"source": ["docs/decisions/INCIDENTS.md#INC-036:L553-L699"], "tags": []}
        self.assertEqual(extract_domain(entry), "incidents")

    def test_source_deployment_path(self):
        entry = {"source": ["deployment/production.py:L1-L10"], "tags": []}
        self.assertEqual(extract_domain(entry), "deployment")

    def test_source_claude_md(self):
        entry = {"source": ["CLAUDE.md#Protocol-A:L10-L19"], "tags": []}
        self.assertEqual(extract_domain(entry), "protocols")

    def test_fallback_to_tags(self):
        entry = {"source": ["unknown/path.py:L1"], "tags": ["cache", "ttl"]}
        self.assertEqual(extract_domain(entry), "cache")

    def test_fallback_to_type(self):
        entry = {"source": ["unknown/path.py:L1"], "tags": [], "type": "constraint"}
        self.assertEqual(extract_domain(entry), "constraint")

    def test_fallback_to_general(self):
        entry = {"source": [], "tags": []}
        self.assertEqual(extract_domain(entry), "general")

    def test_generic_tags_skipped(self):
        """Generic tags like 'leakage', 'bug' should be skipped in favor of more specific ones."""
        entry = {"source": ["unknown/path.py"], "tags": ["leakage", "feature-engine"]}
        self.assertEqual(extract_domain(entry), "feature-engine")

    def test_sample_entry_0_domain(self):
        """SAMPLE_ENTRIES[0] has source 'docs/decisions/INCIDENTS.md#INC-036' → 'incidents'."""
        domain = extract_domain(SAMPLE_ENTRIES[0])
        self.assertEqual(domain, "incidents")

    def test_sample_entry_2_domain(self):
        """SAMPLE_ENTRIES[2] has source 'src/labels/risk_adjusted_labels.py' → 'labels'."""
        domain = extract_domain(SAMPLE_ENTRIES[2])
        self.assertEqual(domain, "labels")


class TestLoadHardEntries(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.events_path = Path(self.tmpdir) / "events.jsonl"

    def test_filters_hard_only(self):
        with open(self.events_path, "w") as f:
            for entry in SAMPLE_ENTRIES:
                f.write(json.dumps(entry) + "\n")

        entries, total_scanned = _load_hard_entries(self.events_path)
        # SAMPLE_ENTRIES[0] and [1] are hard, [2] is soft
        self.assertEqual(len(entries), 2)
        for e in entries:
            self.assertEqual(e["classification"], "hard")
        self.assertEqual(total_scanned, 3)  # All 3 entries scanned

    def test_filters_deprecated(self):
        dep = SAMPLE_ENTRIES[0].copy()
        dep["deprecated"] = True
        with open(self.events_path, "w") as f:
            f.write(json.dumps(dep) + "\n")
            f.write(json.dumps(SAMPLE_ENTRIES[1]) + "\n")

        entries, total_scanned = _load_hard_entries(self.events_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], SAMPLE_ENTRIES[1]["id"])

    def test_sorted_by_severity(self):
        # Create entries with different severities
        e_s3 = SAMPLE_ENTRIES[0].copy()
        e_s3["id"] = "test-s3"
        e_s3["severity"] = "S3"

        e_s1 = SAMPLE_ENTRIES[0].copy()
        e_s1["id"] = "test-s1"
        e_s1["severity"] = "S1"

        e_s2 = SAMPLE_ENTRIES[0].copy()
        e_s2["id"] = "test-s2"
        e_s2["severity"] = "S2"

        with open(self.events_path, "w") as f:
            f.write(json.dumps(e_s3) + "\n")
            f.write(json.dumps(e_s1) + "\n")
            f.write(json.dumps(e_s2) + "\n")

        entries, total_scanned = _load_hard_entries(self.events_path)
        severities = [e["severity"] for e in entries]
        self.assertEqual(severities, ["S1", "S2", "S3"])

    def test_empty_file(self):
        self.events_path.touch()
        entries, total_scanned = _load_hard_entries(self.events_path)
        self.assertEqual(len(entries), 0)
        self.assertEqual(total_scanned, 0)

    def test_nonexistent_file(self):
        entries, total_scanned = _load_hard_entries(Path(self.tmpdir) / "nonexistent.jsonl")
        self.assertEqual(len(entries), 0)
        self.assertEqual(total_scanned, 0)

    def test_latest_wins_semantics(self):
        v1 = SAMPLE_ENTRIES[0].copy()
        v1["title"] = "Version 1"
        v2 = SAMPLE_ENTRIES[0].copy()
        v2["title"] = "Version 2"

        with open(self.events_path, "w") as f:
            f.write(json.dumps(v1) + "\n")
            f.write(json.dumps(v2) + "\n")

        entries, total_scanned = _load_hard_entries(self.events_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Version 2")


class TestGenerateRuleFiles(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.events_path = Path(self.tmpdir) / "events.jsonl"
        self.output_dir = Path(self.tmpdir) / "rules" / "ef-memory"

        with open(self.events_path, "w") as f:
            for entry in SAMPLE_ENTRIES:
                f.write(json.dumps(entry) + "\n")

    def test_generates_domain_files(self):
        report = generate_rule_files(self.events_path, self.output_dir)
        self.assertGreater(report.entries_hard, 0)
        self.assertGreater(len(report.files_written), 0)
        self.assertTrue(self.output_dir.exists())

        # Should have at least one .md file
        md_files = list(self.output_dir.glob("*.md"))
        self.assertGreater(len(md_files), 0)

    def test_generates_index(self):
        generate_rule_files(self.events_path, self.output_dir)
        index_path = self.output_dir / "_index.md"
        self.assertTrue(index_path.exists())

        content = index_path.read_text()
        self.assertIn("Auto-Injected Rules Index", content)
        self.assertIn("DO NOT EDIT MANUALLY", content)

    def test_domain_file_contains_rules(self):
        generate_rule_files(self.events_path, self.output_dir)

        # Find any generated domain file (not _index.md)
        domain_files = [f for f in self.output_dir.glob("*.md") if f.name != "_index.md"]
        self.assertGreater(len(domain_files), 0)

        content = domain_files[0].read_text()
        self.assertIn("Auto-generated from Memory", content)
        self.assertIn("DO NOT EDIT MANUALLY", content)
        # Should contain at least one rule section
        self.assertIn("## ", content)
        # Should reference a memory entry ID
        self.assertIn("Memory:", content)

    def test_soft_entries_excluded(self):
        report = generate_rule_files(self.events_path, self.output_dir)
        # SAMPLE_ENTRIES has 2 hard + 1 soft; only hard should be injected
        self.assertEqual(report.entries_hard, 2)
        self.assertEqual(report.entries_injected, 2)

    def test_dry_run_no_files(self):
        report = generate_rule_files(self.events_path, self.output_dir, dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertGreater(len(report.files_written), 0)  # Reports what would be written
        self.assertFalse(self.output_dir.exists())  # But nothing actually written

    def test_empty_events(self):
        empty_path = Path(self.tmpdir) / "empty.jsonl"
        empty_path.touch()

        report = generate_rule_files(empty_path, self.output_dir)
        self.assertEqual(report.entries_hard, 0)
        self.assertEqual(len(report.files_written), 0)

    def test_clean_first_removes_old(self):
        # First generation
        generate_rule_files(self.events_path, self.output_dir)
        files_before = set(f.name for f in self.output_dir.glob("*.md"))
        self.assertGreater(len(files_before), 0)

        # Second generation with clean_first=True (default)
        report = generate_rule_files(self.events_path, self.output_dir, clean_first=True)
        self.assertGreater(len(report.files_removed), 0)

    def test_report_has_domains(self):
        report = generate_rule_files(self.events_path, self.output_dir)
        self.assertGreater(len(report.domains), 0)
        # All domain counts should be positive
        for domain, count in report.domains.items():
            self.assertGreater(count, 0)

    def test_report_has_duration(self):
        report = generate_rule_files(self.events_path, self.output_dir)
        self.assertGreater(report.duration_ms, 0)


class TestCleanRuleFiles(unittest.TestCase):

    def test_clean_removes_md_files(self):
        tmpdir = tempfile.mkdtemp()
        output_dir = Path(tmpdir) / "rules"
        output_dir.mkdir()

        # Create some fake rule files
        (output_dir / "feature-engine.md").write_text("# test")
        (output_dir / "_index.md").write_text("# index")

        removed = clean_rule_files(output_dir)
        self.assertEqual(len(removed), 2)
        self.assertFalse(output_dir.exists())  # Dir removed since empty

    def test_clean_nonexistent_dir(self):
        removed = clean_rule_files(Path(tempfile.mkdtemp()) / "nonexistent")
        self.assertEqual(len(removed), 0)


class TestDomainMarkdown(unittest.TestCase):

    def test_contains_header(self):
        entries = [SAMPLE_ENTRIES[0]]
        md = _generate_domain_markdown("feature-engine", entries)
        self.assertIn("Feature Engine Rules", md)
        self.assertIn("Auto-generated from Memory", md)

    def test_contains_entry_rule(self):
        entries = [SAMPLE_ENTRIES[0]]
        md = _generate_domain_markdown("test-domain", entries)
        self.assertIn(SAMPLE_ENTRIES[0]["title"], md)
        self.assertIn(SAMPLE_ENTRIES[0]["rule"], md)
        self.assertIn(SAMPLE_ENTRIES[0]["id"], md)

    def test_contains_severity(self):
        entries = [SAMPLE_ENTRIES[0]]
        md = _generate_domain_markdown("test-domain", entries)
        self.assertIn("[S1]", md)

    def test_contains_source(self):
        entries = [SAMPLE_ENTRIES[0]]
        md = _generate_domain_markdown("test-domain", entries)
        self.assertIn(SAMPLE_ENTRIES[0]["source"][0], md)


if __name__ == "__main__":
    unittest.main()
