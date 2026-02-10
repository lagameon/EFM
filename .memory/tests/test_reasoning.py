"""
Tests for EF Memory V2 — Reasoning Engine (M6)

Covers: find_correlations, detect_contradictions, suggest_syntheses,
assess_risks, build_reasoning_report, annotate_search_results,
and all helper functions.
"""

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

# Import path setup
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.reasoning import (
    CorrelationGroup,
    CorrelationReport,
    ContradictionPair,
    ContradictionReport,
    SynthesisSuggestion,
    SynthesisReport,
    RiskAnnotation,
    RiskReport,
    ReasoningReport,
    find_correlations,
    detect_contradictions,
    suggest_syntheses,
    assess_risks,
    build_reasoning_report,
    annotate_search_results,
    _parse_llm_json,
    _safe_llm_call,
    _get_reasoning_config,
)
from tests.conftest import (
    MockLLMProvider,
    SAMPLE_ENTRIES,
    SAMPLE_ENTRIES_EXTENDED,
)


# ---------------------------------------------------------------------------
# Test config helper
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    config = {
        "reasoning": {
            "enabled": True,
            "correlation_threshold": 2,
            "synthesis_min_group_size": 3,
            "max_tokens": 4096,
            "token_budget": 16000,
            "contradiction_detection": True,
        },
    }
    config["reasoning"].update(overrides)
    return config


def _entries_dict(entries_list):
    """Convert list of entry dicts to {id: entry} dict."""
    return {e["id"]: e for e in entries_list}


# ---------------------------------------------------------------------------
# Dataclass defaults
# ---------------------------------------------------------------------------

class TestDataclassDefaults(unittest.TestCase):

    def test_correlation_group_defaults(self):
        g = CorrelationGroup()
        self.assertEqual(g.entry_ids, [])
        self.assertEqual(g.relationship, "")
        self.assertEqual(g.strength, 0.0)

    def test_correlation_report_defaults(self):
        r = CorrelationReport()
        self.assertEqual(r.total_entries, 0)
        self.assertEqual(r.groups, [])
        self.assertEqual(r.mode, "heuristic")

    def test_contradiction_pair_defaults(self):
        p = ContradictionPair()
        self.assertEqual(p.entry_id_a, "")
        self.assertEqual(p.confidence, 0.0)

    def test_reasoning_report_defaults(self):
        r = ReasoningReport()
        self.assertEqual(r.total_entries, 0)
        self.assertIsNone(r.correlation_report)
        self.assertEqual(r.llm_calls, 0)

    def test_risk_annotation_defaults(self):
        a = RiskAnnotation()
        self.assertEqual(a.related_entry_ids, [])


# ---------------------------------------------------------------------------
# Helpers: _parse_llm_json
# ---------------------------------------------------------------------------

class TestParseLLMJson(unittest.TestCase):

    def test_valid_json(self):
        result = _parse_llm_json('{"key": "value"}')
        self.assertEqual(result, {"key": "value"})

    def test_markdown_wrapped_json(self):
        text = '```json\n{"key": "value"}\n```'
        result = _parse_llm_json(text)
        self.assertEqual(result, {"key": "value"})

    def test_markdown_without_json_tag(self):
        text = '```\n{"key": "value"}\n```'
        result = _parse_llm_json(text)
        self.assertEqual(result, {"key": "value"})

    def test_json_with_surrounding_text(self):
        text = 'Here is the result: {"key": "value"} end.'
        result = _parse_llm_json(text)
        self.assertEqual(result, {"key": "value"})

    def test_invalid_json(self):
        result = _parse_llm_json("this is not json at all")
        self.assertIsNone(result)

    def test_empty_string(self):
        result = _parse_llm_json("")
        self.assertIsNone(result)

    def test_none_like(self):
        result = _parse_llm_json("   ")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Helpers: _safe_llm_call
# ---------------------------------------------------------------------------

class TestSafeLLMCall(unittest.TestCase):

    def test_success(self):
        mock = MockLLMProvider()
        result = _safe_llm_call(mock, "sys", "user")
        self.assertIsNotNone(result)

    def test_failure_returns_none(self):
        class FailingProvider:
            def complete(self, *args, **kwargs):
                raise RuntimeError("API error")
        result = _safe_llm_call(FailingProvider(), "sys", "user")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Helpers: _get_reasoning_config
# ---------------------------------------------------------------------------

class TestGetReasoningConfig(unittest.TestCase):

    def test_defaults(self):
        rc = _get_reasoning_config({})
        self.assertEqual(rc["correlation_threshold"], 2)
        self.assertEqual(rc["synthesis_min_group_size"], 3)
        self.assertEqual(rc["max_tokens"], 4096)
        self.assertTrue(rc["contradiction_detection"])

    def test_custom_values(self):
        config = {"reasoning": {"correlation_threshold": 3, "max_tokens": 8192}}
        rc = _get_reasoning_config(config)
        self.assertEqual(rc["correlation_threshold"], 3)
        self.assertEqual(rc["max_tokens"], 8192)


# ---------------------------------------------------------------------------
# find_correlations — Heuristic
# ---------------------------------------------------------------------------

class TestCorrelationHeuristic(unittest.TestCase):

    def test_tag_overlap_grouping(self):
        entries = _entries_dict(SAMPLE_ENTRIES_EXTENDED)
        config = _make_config(correlation_threshold=2)
        report = find_correlations(entries, config)
        self.assertEqual(report.mode, "heuristic")
        self.assertGreater(len(report.groups), 0)
        # Entries sharing "leakage" + "shift" should be grouped
        all_grouped_ids = set()
        for g in report.groups:
            all_grouped_ids.update(g.entry_ids)
        self.assertIn("lesson-inc036-a3f8c2d1", all_grouped_ids)

    def test_source_overlap_grouping(self):
        # Two entries referencing same file
        entries = {
            "a": {"id": "a", "tags": [], "source": ["src/foo.py:L1-L10"], "created_at": "2026-01-01T00:00:00Z"},
            "b": {"id": "b", "tags": [], "source": ["src/foo.py:L20-L30"], "created_at": "2026-01-02T00:00:00Z"},
        }
        report = find_correlations(entries, _make_config())
        source_groups = [g for g in report.groups if "source" in g.relationship]
        self.assertGreater(len(source_groups), 0)

    def test_temporal_proximity(self):
        entries = {
            "a": {"id": "a", "tags": [], "source": [], "created_at": "2026-02-01T14:00:00Z"},
            "b": {"id": "b", "tags": [], "source": [], "created_at": "2026-02-01T14:30:00Z"},
        }
        report = find_correlations(entries, _make_config())
        temporal_groups = [g for g in report.groups if "temporal" in g.relationship]
        self.assertGreater(len(temporal_groups), 0)

    def test_no_correlations_for_diverse_entries(self):
        entries = {
            "a": {"id": "a", "tags": ["x"], "source": ["file1.py:L1"], "created_at": "2026-01-01T00:00:00Z"},
            "b": {"id": "b", "tags": ["y"], "source": ["file2.py:L1"], "created_at": "2026-06-01T00:00:00Z"},
        }
        report = find_correlations(entries, _make_config())
        # No tag overlap, no source overlap, not temporal
        # May still have temporal if within 24h — these are far apart
        tag_groups = [g for g in report.groups if "tag" in g.relationship]
        self.assertEqual(len(tag_groups), 0)

    def test_empty_entries(self):
        report = find_correlations({}, _make_config())
        self.assertEqual(report.total_entries, 0)
        self.assertEqual(len(report.groups), 0)

    def test_single_entry(self):
        entries = {"a": {"id": "a", "tags": ["x"], "source": [], "created_at": "2026-01-01T00:00:00Z"}}
        report = find_correlations(entries, _make_config())
        self.assertEqual(len(report.groups), 0)

    def test_threshold_configuration(self):
        entries = {
            "a": {"id": "a", "tags": ["x", "y"], "source": [], "created_at": "2026-01-01T00:00:00Z"},
            "b": {"id": "b", "tags": ["x", "y", "z"], "source": [], "created_at": "2026-06-01T00:00:00Z"},
        }
        # threshold=2: should find overlap (x, y)
        report_low = find_correlations(entries, _make_config(correlation_threshold=2))
        tag_groups = [g for g in report_low.groups if "tag" in g.relationship]
        self.assertGreater(len(tag_groups), 0)

        # threshold=3: only 2 overlapping tags, should NOT match
        report_high = find_correlations(entries, _make_config(correlation_threshold=3))
        tag_groups_high = [g for g in report_high.groups if "tag" in g.relationship]
        self.assertEqual(len(tag_groups_high), 0)

    def test_duration_tracked(self):
        report = find_correlations({}, _make_config())
        self.assertGreaterEqual(report.duration_ms, 0)


# ---------------------------------------------------------------------------
# find_correlations — LLM
# ---------------------------------------------------------------------------

class TestCorrelationWithLLM(unittest.TestCase):

    def test_llm_enrichment(self):
        entries = _entries_dict(SAMPLE_ENTRIES[:2])
        mock = MockLLMProvider(responses={
            "correlation": json.dumps({
                "groups": [{
                    "entry_ids": list(entries.keys()),
                    "relationship": "both about leakage",
                    "strength": 0.9,
                }]
            })
        })
        report = find_correlations(entries, _make_config(), llm_provider=mock)
        self.assertEqual(report.mode, "llm_enriched")
        self.assertEqual(mock._call_count, 1)

    def test_llm_failure_degrades(self):
        entries = _entries_dict(SAMPLE_ENTRIES[:2])

        class FailingLLM(MockLLMProvider):
            def complete(self, *args, **kwargs):
                raise RuntimeError("API error")

        report = find_correlations(entries, _make_config(), llm_provider=FailingLLM())
        self.assertEqual(report.mode, "heuristic")

    def test_llm_invalid_json_degrades(self):
        entries = _entries_dict(SAMPLE_ENTRIES[:2])
        mock = MockLLMProvider(responses={
            "correlation": "this is not valid json"
        })
        report = find_correlations(entries, _make_config(), llm_provider=mock)
        # Should still have heuristic groups, mode stays heuristic
        self.assertEqual(report.mode, "heuristic")

    def test_llm_validates_entry_ids(self):
        entries = _entries_dict(SAMPLE_ENTRIES[:2])
        mock = MockLLMProvider(responses={
            "correlation": json.dumps({
                "groups": [{
                    "entry_ids": ["nonexistent-1", "nonexistent-2"],
                    "relationship": "fake",
                    "strength": 0.5,
                }]
            })
        })
        report = find_correlations(entries, _make_config(), llm_provider=mock)
        # Invalid IDs should be filtered out
        llm_groups = [g for g in report.groups if "fake" in g.relationship or "llm" in g.relationship.lower()]
        # The group with invalid IDs should not appear (< 2 valid IDs)
        for g in llm_groups:
            self.assertGreaterEqual(len(g.entry_ids), 2)


# ---------------------------------------------------------------------------
# detect_contradictions — Heuristic
# ---------------------------------------------------------------------------

class TestContradictionHeuristic(unittest.TestCase):

    def test_opposing_rules_detected(self):
        entries = _entries_dict(SAMPLE_ENTRIES_EXTENDED)
        config = _make_config()
        report = detect_contradictions(entries, config)
        # SAMPLE_ENTRIES_EXTENDED has "MUST" vs "NEVER" conflict on shift/rolling
        self.assertGreater(len(report.pairs), 0)
        conflict_types = [p.type for p in report.pairs]
        self.assertIn("rule_conflict", conflict_types)

    def test_severity_mismatch(self):
        entries = {
            "a": {"id": "a", "tags": ["x", "y"], "severity": "S1", "rule": "do X", "source": [], "created_at": "2026-01-01T00:00:00Z"},
            "b": {"id": "b", "tags": ["x", "y"], "severity": "S3", "rule": "do Y", "source": [], "created_at": "2026-01-01T00:00:00Z"},
        }
        report = detect_contradictions(entries, _make_config())
        severity_pairs = [p for p in report.pairs if p.type == "severity_mismatch"]
        self.assertGreater(len(severity_pairs), 0)

    def test_no_contradictions_for_compatible_entries(self):
        entries = {
            "a": {"id": "a", "tags": ["x", "y"], "severity": "S1", "rule": "MUST do X", "source": [], "created_at": "2026-01-01T00:00:00Z"},
            "b": {"id": "b", "tags": ["x", "y"], "severity": "S1", "rule": "MUST do Y", "source": [], "created_at": "2026-01-01T00:00:00Z"},
        }
        report = detect_contradictions(entries, _make_config())
        conflict_pairs = [p for p in report.pairs if p.type == "rule_conflict"]
        self.assertEqual(len(conflict_pairs), 0)

    def test_empty_entries(self):
        report = detect_contradictions({}, _make_config())
        self.assertEqual(len(report.pairs), 0)

    def test_single_entry(self):
        entries = {"a": {"id": "a", "tags": ["x"], "rule": "do X", "source": [], "created_at": "2026-01-01T00:00:00Z"}}
        report = detect_contradictions(entries, _make_config())
        self.assertEqual(len(report.pairs), 0)

    def test_no_shared_tags_no_contradiction(self):
        entries = {
            "a": {"id": "a", "tags": ["x"], "rule": "MUST do X", "source": [], "created_at": "2026-01-01T00:00:00Z"},
            "b": {"id": "b", "tags": ["y"], "rule": "NEVER do X", "source": [], "created_at": "2026-01-01T00:00:00Z"},
        }
        report = detect_contradictions(entries, _make_config())
        self.assertEqual(len(report.pairs), 0)

    def test_contradiction_detection_disabled(self):
        entries = _entries_dict(SAMPLE_ENTRIES_EXTENDED)
        config = _make_config(contradiction_detection=False)
        report = detect_contradictions(entries, config)
        self.assertEqual(len(report.pairs), 0)


# ---------------------------------------------------------------------------
# detect_contradictions — LLM
# ---------------------------------------------------------------------------

class TestContradictionWithLLM(unittest.TestCase):

    def test_llm_enrichment(self):
        entries = _entries_dict(SAMPLE_ENTRIES_EXTENDED)
        eids = list(entries.keys())[:2]
        mock = MockLLMProvider(responses={
            "contradiction": json.dumps({
                "contradictions": [{
                    "entry_id_a": eids[0],
                    "entry_id_b": eids[1],
                    "type": "semantic",
                    "explanation": "LLM found semantic conflict",
                    "confidence": 0.85,
                }]
            })
        })
        report = detect_contradictions(entries, _make_config(), llm_provider=mock)
        self.assertEqual(report.mode, "llm_enriched")

    def test_llm_failure_degrades(self):
        entries = _entries_dict(SAMPLE_ENTRIES_EXTENDED)

        class FailingLLM(MockLLMProvider):
            def complete(self, *args, **kwargs):
                raise RuntimeError("API error")

        report = detect_contradictions(entries, _make_config(), llm_provider=FailingLLM())
        self.assertEqual(report.mode, "heuristic")

    def test_llm_no_candidates_no_call(self):
        # If no heuristic candidates, LLM is not called
        entries = {
            "a": {"id": "a", "tags": ["unique1"], "rule": "do X", "source": [], "created_at": "2026-01-01T00:00:00Z"},
        }
        mock = MockLLMProvider()
        report = detect_contradictions(entries, _make_config(), llm_provider=mock)
        self.assertEqual(mock._call_count, 0)


# ---------------------------------------------------------------------------
# suggest_syntheses — Heuristic
# ---------------------------------------------------------------------------

class TestSynthesisHeuristic(unittest.TestCase):

    def test_tag_clustering(self):
        entries = _entries_dict(SAMPLE_ENTRIES_EXTENDED)
        config = _make_config(synthesis_min_group_size=3)
        report = suggest_syntheses(entries, config)
        self.assertGreater(len(report.suggestions), 0)
        # "leakage" tag has 4 entries in SAMPLE_ENTRIES_EXTENDED
        leakage_sugg = [
            s for s in report.suggestions
            if "leakage" in s.rationale
        ]
        self.assertGreater(len(leakage_sugg), 0)

    def test_min_group_size_filter(self):
        entries = _entries_dict(SAMPLE_ENTRIES[:2])  # Only 2 entries
        config = _make_config(synthesis_min_group_size=3)
        report = suggest_syntheses(entries, config)
        self.assertEqual(len(report.suggestions), 0)

    def test_empty_entries(self):
        report = suggest_syntheses({}, _make_config())
        self.assertEqual(len(report.suggestions), 0)

    def test_single_entry(self):
        entries = {"a": {"id": "a", "tags": ["x"], "source": [], "created_at": "2026-01-01T00:00:00Z"}}
        report = suggest_syntheses(entries, _make_config())
        self.assertEqual(len(report.suggestions), 0)

    def test_no_synthesis_text_without_llm(self):
        entries = _entries_dict(SAMPLE_ENTRIES_EXTENDED)
        config = _make_config(synthesis_min_group_size=3)
        report = suggest_syntheses(entries, config)
        for s in report.suggestions:
            self.assertEqual(s.proposed_title, "")
            self.assertEqual(s.proposed_principle, "")


# ---------------------------------------------------------------------------
# suggest_syntheses — LLM
# ---------------------------------------------------------------------------

class TestSynthesisWithLLM(unittest.TestCase):

    def test_llm_generates_principle(self):
        entries = _entries_dict(SAMPLE_ENTRIES_EXTENDED)
        eids = [e["id"] for e in SAMPLE_ENTRIES_EXTENDED[:3]]
        mock = MockLLMProvider(responses={
            "cluster": json.dumps({
                "syntheses": [{
                    "source_entry_ids": eids,
                    "proposed_title": "Anti-Leakage Principle",
                    "proposed_principle": "Always use shift before rolling on price data",
                    "rationale": "Multiple incidents confirm this pattern",
                }]
            })
        })
        config = _make_config(synthesis_min_group_size=3)
        report = suggest_syntheses(entries, config, llm_provider=mock)
        self.assertEqual(report.mode, "llm_enriched")
        self.assertTrue(any(s.proposed_title for s in report.suggestions))

    def test_llm_failure_returns_groups_only(self):
        entries = _entries_dict(SAMPLE_ENTRIES_EXTENDED)

        class FailingLLM(MockLLMProvider):
            def complete(self, *args, **kwargs):
                raise RuntimeError("API error")

        config = _make_config(synthesis_min_group_size=3)
        report = suggest_syntheses(entries, config, llm_provider=FailingLLM())
        self.assertEqual(report.mode, "heuristic")

    def test_multiple_clusters(self):
        # Create entries that form two distinct clusters
        entries = _entries_dict(SAMPLE_ENTRIES_EXTENDED)
        config = _make_config(synthesis_min_group_size=2)
        report = suggest_syntheses(entries, config)
        # With min_group_size=2, should find multiple clusters
        self.assertGreater(len(report.suggestions), 0)


# ---------------------------------------------------------------------------
# assess_risks — Heuristic
# ---------------------------------------------------------------------------

@dataclass
class _FakeSearchResult:
    entry_id: str


class TestRiskHeuristic(unittest.TestCase):

    def test_old_entry_gets_medium_risk(self):
        entries = {
            "old": {
                "id": "old",
                "created_at": "2024-01-01T00:00:00Z",
                "last_verified": None,
                "tags": [],
                "source": [],
                "_meta": {},
            },
        }
        results = [_FakeSearchResult(entry_id="old")]
        report = assess_risks("query", results, entries, _make_config())
        self.assertGreater(len(report.annotations), 0)
        self.assertEqual(report.annotations[0].risk_level, "medium")

    def test_superseded_entry_gets_high_risk(self):
        entries = {
            "sup": {
                "id": "sup",
                "created_at": "2026-02-01T00:00:00Z",
                "last_verified": None,
                "tags": [],
                "source": [],
                "_meta": {"superseded_by": "new-entry"},
            },
        }
        results = [_FakeSearchResult(entry_id="sup")]
        report = assess_risks("query", results, entries, _make_config())
        high_anns = [a for a in report.annotations if a.risk_level == "high"]
        self.assertGreater(len(high_anns), 0)

    def test_no_risk_for_fresh_entry(self):
        entries = {
            "fresh": {
                "id": "fresh",
                "created_at": "2026-02-07T00:00:00Z",
                "last_verified": "2026-02-07T00:00:00Z",
                "tags": [],
                "source": [],
                "_meta": {},
                "classification": "soft",
            },
        }
        results = [_FakeSearchResult(entry_id="fresh")]
        report = assess_risks("query", results, entries, _make_config())
        # Fresh entries may get "info" annotation but not medium/high
        high_medium = [a for a in report.annotations if a.risk_level in ("high", "medium")]
        self.assertEqual(len(high_medium), 0)

    def test_empty_results(self):
        report = assess_risks("query", [], {}, _make_config())
        self.assertEqual(len(report.annotations), 0)

    def test_hard_s1_gets_info_annotation(self):
        entries = {
            "critical": {
                "id": "critical",
                "created_at": "2026-02-07T00:00:00Z",
                "last_verified": "2026-02-07T00:00:00Z",
                "tags": [],
                "source": [],
                "_meta": {},
                "classification": "hard",
                "severity": "S1",
            },
        }
        results = [_FakeSearchResult(entry_id="critical")]
        report = assess_risks("query", results, entries, _make_config())
        info_anns = [a for a in report.annotations if a.risk_level == "info"]
        self.assertGreater(len(info_anns), 0)


# ---------------------------------------------------------------------------
# assess_risks — LLM
# ---------------------------------------------------------------------------

class TestRiskWithLLM(unittest.TestCase):

    def test_llm_enrichment(self):
        entries = {
            "old": {
                "id": "old",
                "created_at": "2024-01-01T00:00:00Z",
                "last_verified": None,
                "tags": [],
                "source": [],
                "_meta": {},
            },
        }
        results = [_FakeSearchResult(entry_id="old")]
        mock = MockLLMProvider(responses={
            "risk": json.dumps({
                "annotations": [{
                    "entry_id": "old",
                    "risk_level": "high",
                    "annotation": "LLM-assessed high risk",
                    "related_entry_ids": [],
                }]
            })
        })
        report = assess_risks("query", results, entries, _make_config(), llm_provider=mock)
        self.assertEqual(report.mode, "llm_enriched")

    def test_llm_failure_degrades(self):
        entries = {
            "old": {
                "id": "old",
                "created_at": "2024-01-01T00:00:00Z",
                "last_verified": None,
                "tags": [],
                "source": [],
                "_meta": {},
            },
        }
        results = [_FakeSearchResult(entry_id="old")]

        class FailingLLM(MockLLMProvider):
            def complete(self, *args, **kwargs):
                raise RuntimeError("API error")

        report = assess_risks("query", results, entries, _make_config(), llm_provider=FailingLLM())
        self.assertEqual(report.mode, "heuristic")


# ---------------------------------------------------------------------------
# build_reasoning_report
# ---------------------------------------------------------------------------

class TestBuildReasoningReport(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        with open(self.events_path, "w") as f:
            for entry in SAMPLE_ENTRIES_EXTENDED:
                f.write(json.dumps(entry) + "\n")

    def test_full_report_structure(self):
        report = build_reasoning_report(
            self.events_path, _make_config(), self.tmpdir,
        )
        self.assertGreater(report.total_entries, 0)
        self.assertIsNotNone(report.correlation_report)
        self.assertIsNotNone(report.contradiction_report)
        self.assertIsNotNone(report.synthesis_report)
        self.assertEqual(report.mode, "heuristic")
        self.assertGreater(report.duration_ms, 0)

    def test_skip_flags(self):
        report = build_reasoning_report(
            self.events_path, _make_config(), self.tmpdir,
            skip_correlations=True,
            skip_contradictions=True,
            skip_syntheses=True,
        )
        self.assertIsNone(report.correlation_report)
        self.assertIsNone(report.contradiction_report)
        self.assertIsNone(report.synthesis_report)

    def test_empty_events(self):
        empty_path = self.tmpdir / "empty.jsonl"
        empty_path.touch()
        report = build_reasoning_report(empty_path, _make_config(), self.tmpdir)
        self.assertEqual(report.total_entries, 0)

    def test_with_mock_llm(self):
        mock = MockLLMProvider()
        report = build_reasoning_report(
            self.events_path, _make_config(), self.tmpdir,
            llm_provider=mock,
        )
        self.assertIsNotNone(report)
        # Mock LLM returns default response which isn't valid JSON for any analysis
        # So mode stays heuristic unless responses are configured
        self.assertIn(report.mode, ("heuristic", "llm_enriched"))

    def test_deprecated_entries_excluded(self):
        dep_path = self.tmpdir / "with_deprecated.jsonl"
        with open(dep_path, "w") as f:
            for entry in SAMPLE_ENTRIES:
                f.write(json.dumps(entry) + "\n")
            deprecated = SAMPLE_ENTRIES[0].copy()
            deprecated["deprecated"] = True
            f.write(json.dumps(deprecated) + "\n")

        report = build_reasoning_report(dep_path, _make_config(), self.tmpdir)
        # Should have fewer entries due to deprecated filtering
        self.assertEqual(report.total_entries, 2)  # 3 - 1 deprecated


# ---------------------------------------------------------------------------
# annotate_search_results
# ---------------------------------------------------------------------------

class TestAnnotateSearchResults(unittest.TestCase):

    def test_returns_annotation_dicts(self):
        entries = {
            "old": {
                "id": "old",
                "created_at": "2024-01-01T00:00:00Z",
                "last_verified": None,
                "tags": [],
                "source": [],
                "_meta": {},
            },
        }
        results = [_FakeSearchResult(entry_id="old")]
        annotations = annotate_search_results(results, entries, _make_config())
        self.assertIsInstance(annotations, list)
        self.assertGreater(len(annotations), 0)
        self.assertIn("entry_id", annotations[0])
        self.assertIn("risk_level", annotations[0])

    def test_no_results_returns_empty(self):
        annotations = annotate_search_results([], {}, _make_config())
        self.assertEqual(annotations, [])

    def test_with_mock_llm(self):
        entries = {
            "old": {
                "id": "old",
                "created_at": "2024-01-01T00:00:00Z",
                "last_verified": None,
                "tags": [],
                "source": [],
                "_meta": {},
            },
        }
        results = [_FakeSearchResult(entry_id="old")]
        mock = MockLLMProvider()
        annotations = annotate_search_results(
            results, entries, _make_config(),
            llm_provider=mock, query="test",
        )
        self.assertIsInstance(annotations, list)


# ---------------------------------------------------------------------------
# TestCorrelationTimestampSafety — B2: _parse_iso8601("") crash
# ---------------------------------------------------------------------------

class TestCorrelationTimestampSafety(unittest.TestCase):

    def test_empty_created_at_no_crash(self):
        """find_correlations should NOT raise ValueError when created_at is empty."""
        entries = {
            "a": {
                "id": "a",
                "tags": ["x", "y"],
                "source": [],
                "created_at": "",
            },
            "b": {
                "id": "b",
                "tags": ["x", "y"],
                "source": [],
                "created_at": "2026-02-01T14:00:00Z",
            },
        }
        # Should not raise ValueError
        report = find_correlations(entries, _make_config())
        self.assertIsNotNone(report)
        self.assertEqual(report.total_entries, 2)

    def test_missing_created_at_no_crash(self):
        """find_correlations should NOT raise when created_at key is missing."""
        entries = {
            "a": {
                "id": "a",
                "tags": ["x", "y"],
                "source": [],
                # No created_at key at all
            },
            "b": {
                "id": "b",
                "tags": ["x", "y"],
                "source": [],
                "created_at": "2026-02-01T14:00:00Z",
            },
        }
        # Should not raise
        report = find_correlations(entries, _make_config())
        self.assertIsNotNone(report)
        self.assertEqual(report.total_entries, 2)


if __name__ == "__main__":
    unittest.main()
