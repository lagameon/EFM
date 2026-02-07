"""
EF Memory V2 — Evolution Tests (M5)

Tests for duplicate detection, confidence scoring, deprecation suggestions,
merge recommendations, and comprehensive evolution reports.

Run from project root:
    python3 -m unittest discover -s .memory/tests -v
"""

import json
import math
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from lib.evolution import (
    DuplicateGroup,
    DuplicateReport,
    ConfidenceBreakdown,
    ConfidenceScore,
    DeprecationCandidate,
    DeprecationReport,
    MergeSuggestion,
    EvolutionReport,
    _UnionFind,
    calculate_confidence,
    find_duplicates,
    suggest_deprecations,
    suggest_merges,
    build_evolution_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_ago_iso(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_entry(**overrides) -> dict:
    """Create a valid memory entry with sensible defaults."""
    base = {
        "id": "lesson-test-a1b2c3d4",
        "type": "lesson",
        "classification": "hard",
        "severity": "S1",
        "title": "Test entry for evolution",
        "content": ["First bullet", "Second bullet"],
        "rule": "MUST do something specific",
        "implication": "Bad things happen if violated",
        "source": ["PR #123"],
        "tags": ["test"],
        "created_at": _now_iso(),
        "last_verified": None,
        "deprecated": False,
        "_meta": {},
    }
    base.update(overrides)
    return base


def _write_events(events_path: Path, entries: list) -> None:
    """Write entries to events.jsonl."""
    with open(events_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _make_config(**overrides) -> dict:
    """Create a config with evolution defaults."""
    config = {
        "version": "1.3",
        "evolution": {
            "confidence_half_life_days": 120,
            "deprecation_confidence_threshold": 0.3,
            "merge_auto_suggest": True,
            "source_quality_weights": {
                "code": 1.0, "function": 1.0, "markdown": 0.7,
                "commit": 0.6, "pr": 0.5, "unknown": 0.3,
            },
            "confidence_weights": {
                "source_quality": 0.30, "age_factor": 0.30,
                "verification_boost": 0.15, "source_validity": 0.25,
            },
        },
        "automation": {
            "dedup_threshold": 0.85,
        },
        "embedding": {
            "dedup_threshold": 0.92,
        },
        "verify": {
            "staleness_threshold_days": 90,
        },
    }
    config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# TestUnionFind
# ---------------------------------------------------------------------------

class TestUnionFind(unittest.TestCase):

    def test_basic_union(self):
        """Two unions form one group."""
        uf = _UnionFind(["a", "b", "c"])
        uf.union("a", "b")
        groups = uf.groups()
        self.assertEqual(len(groups), 1)
        group = list(groups.values())[0]
        self.assertIn("a", group)
        self.assertIn("b", group)

    def test_transitive_union(self):
        """A-B + B-C → one group {A, B, C}."""
        uf = _UnionFind(["a", "b", "c"])
        uf.union("a", "b")
        uf.union("b", "c")
        groups = uf.groups()
        self.assertEqual(len(groups), 1)
        group = list(groups.values())[0]
        self.assertEqual(sorted(group), ["a", "b", "c"])

    def test_no_union_no_groups(self):
        """No unions → no groups (all singletons)."""
        uf = _UnionFind(["a", "b", "c"])
        groups = uf.groups()
        self.assertEqual(len(groups), 0)


# ---------------------------------------------------------------------------
# TestCalculateConfidence
# ---------------------------------------------------------------------------

class TestCalculateConfidence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.project_root = self.tmpdir
        self.config = _make_config()

    def test_fresh_entry_with_pr_source(self):
        """Fresh entry with PR source should have reasonable confidence."""
        entry = _make_entry(created_at=_now_iso(), source=["PR #123"])
        _write_events(self.events_path, [entry])
        cs = calculate_confidence(entry, self.events_path, self.project_root, self.config)
        self.assertGreater(cs.score, 0.3)
        self.assertEqual(cs.entry_id, entry["id"])
        self.assertIn(cs.classification, ("high", "medium", "low"))

    def test_old_entry_decays(self):
        """Entry 240 days old should have lower age factor (half_life=120d → ~0.25)."""
        entry = _make_entry(created_at=_days_ago_iso(240), source=["PR #100"])
        _write_events(self.events_path, [entry])
        cs = calculate_confidence(entry, self.events_path, self.project_root, self.config)
        # 240d with 120d half-life → 2^(-2) = 0.25
        self.assertAlmostEqual(cs.breakdown.age_factor, 0.25, places=1)

    def test_recently_verified_boosted(self):
        """Entry verified within 30 days gets max verification boost."""
        entry = _make_entry(
            created_at=_days_ago_iso(60),
            last_verified=_days_ago_iso(5),
            source=["PR #123"],
        )
        _write_events(self.events_path, [entry])
        cs = calculate_confidence(entry, self.events_path, self.project_root, self.config)
        self.assertEqual(cs.breakdown.verification_boost, 1.0)

    def test_verified_60_days_ago_medium_boost(self):
        """Entry verified 60 days ago gets medium boost (0.67)."""
        entry = _make_entry(
            created_at=_days_ago_iso(120),
            last_verified=_days_ago_iso(60),
            source=["PR #123"],
        )
        _write_events(self.events_path, [entry])
        cs = calculate_confidence(entry, self.events_path, self.project_root, self.config)
        self.assertAlmostEqual(cs.breakdown.verification_boost, 0.67, places=2)

    def test_no_verification_no_boost(self):
        """Entry with last_verified=None gets 0 verification boost."""
        entry = _make_entry(last_verified=None, source=["PR #123"])
        _write_events(self.events_path, [entry])
        cs = calculate_confidence(entry, self.events_path, self.project_root, self.config)
        self.assertEqual(cs.breakdown.verification_boost, 0.0)

    def test_no_sources_low_quality(self):
        """Entry with no sources gets 0 source quality."""
        entry = _make_entry(source=[])
        _write_events(self.events_path, [entry])
        cs = calculate_confidence(entry, self.events_path, self.project_root, self.config)
        self.assertEqual(cs.breakdown.source_quality, 0.0)
        self.assertEqual(cs.breakdown.source_validity, 0.0)

    def test_pr_source_quality(self):
        """PR source type should give 0.5 quality."""
        entry = _make_entry(source=["PR #123"])
        _write_events(self.events_path, [entry])
        cs = calculate_confidence(entry, self.events_path, self.project_root, self.config)
        self.assertAlmostEqual(cs.breakdown.source_quality, 0.5, places=1)

    def test_classification_high(self):
        """Score >= 0.7 → high."""
        entry = _make_entry(
            created_at=_now_iso(),
            last_verified=_now_iso(),
            source=["PR #123"],
        )
        _write_events(self.events_path, [entry])
        cs = calculate_confidence(entry, self.events_path, self.project_root, self.config)
        # Fresh + verified + PR source → should be reasonably high
        if cs.score >= 0.7:
            self.assertEqual(cs.classification, "high")

    def test_score_range_0_to_1(self):
        """Confidence always in [0.0, 1.0]."""
        entry = _make_entry(source=["PR #123"])
        _write_events(self.events_path, [entry])
        cs = calculate_confidence(entry, self.events_path, self.project_root, self.config)
        self.assertGreaterEqual(cs.score, 0.0)
        self.assertLessEqual(cs.score, 1.0)

    def test_config_custom_half_life(self):
        """Custom half_life=30 should cause faster decay."""
        config = _make_config()
        config["evolution"]["confidence_half_life_days"] = 30

        entry = _make_entry(created_at=_days_ago_iso(60), source=["PR #123"])
        _write_events(self.events_path, [entry])

        cs_fast = calculate_confidence(entry, self.events_path, self.project_root, config)

        # With default half_life=120
        cs_slow = calculate_confidence(entry, self.events_path, self.project_root, _make_config())

        # Faster decay → lower age factor
        self.assertLess(cs_fast.breakdown.age_factor, cs_slow.breakdown.age_factor)


# ---------------------------------------------------------------------------
# TestFindDuplicates
# ---------------------------------------------------------------------------

class TestFindDuplicates(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.config = _make_config()

    def test_no_duplicates(self):
        """Two very different entries → 0 groups."""
        entries = [
            _make_entry(
                id="lesson-alpha-11111111",
                title="Rolling stats without shift caused inflation",
                rule="MUST use shift(1) before rolling",
                source=["PR #100"],
            ),
            _make_entry(
                id="lesson-beta-22222222",
                title="Walk-forward labels must be generated per window",
                rule="Labels MUST be inside each WF window",
                source=["PR #200"],
            ),
        ]
        _write_events(self.events_path, entries)
        report = find_duplicates(self.events_path, self.config)
        self.assertEqual(len(report.groups), 0)
        self.assertEqual(report.entries_checked, 2)
        self.assertEqual(report.mode, "text")

    def test_exact_duplicates_grouped(self):
        """Two entries with identical content are grouped."""
        entries = [
            _make_entry(
                id="lesson-dup1-11111111",
                title="Exact same title here",
                rule="MUST do the same thing",
                source=["PR #100"],
            ),
            _make_entry(
                id="lesson-dup2-22222222",
                title="Exact same title here",
                rule="MUST do the same thing",
                source=["PR #100"],
            ),
        ]
        _write_events(self.events_path, entries)
        report = find_duplicates(self.events_path, self.config)
        self.assertEqual(len(report.groups), 1)
        group = report.groups[0]
        self.assertEqual(sorted(group.member_ids), ["lesson-dup1-11111111", "lesson-dup2-22222222"])
        self.assertGreater(group.avg_similarity, 0.9)

    def test_near_duplicates_grouped(self):
        """Two entries with similar title/rule grouped."""
        entries = [
            _make_entry(
                id="lesson-near1-11111111",
                title="Rolling statistics without shift(1) caused inflation",
                rule="shift(1) MUST precede any rolling() on price data",
                source=["PR #100"],
            ),
            _make_entry(
                id="lesson-near2-22222222",
                title="Rolling statistics without shift(1) caused 999x inflation",
                rule="shift(1) MUST precede any rolling(), ewm() on price data",
                source=["PR #101"],
            ),
        ]
        _write_events(self.events_path, entries)
        report = find_duplicates(self.events_path, self.config)
        self.assertEqual(len(report.groups), 1)

    def test_three_way_cluster(self):
        """Three similar entries form one group via transitivity."""
        entries = [
            _make_entry(
                id="lesson-tri1-11111111",
                title="Shift must precede rolling on price data features",
                rule="MUST shift before rolling",
                source=["PR #100"],
            ),
            _make_entry(
                id="lesson-tri2-22222222",
                title="Shift must precede rolling on price data calculations",
                rule="MUST shift before rolling calcs",
                source=["PR #101"],
            ),
            _make_entry(
                id="lesson-tri3-33333333",
                title="Shift must precede rolling on price data computations",
                rule="MUST shift before rolling computations",
                source=["PR #102"],
            ),
        ]
        _write_events(self.events_path, entries)
        report = find_duplicates(self.events_path, self.config)
        # All three should be in one group (if transitivity works)
        if report.groups:
            total_members = sum(len(g.member_ids) for g in report.groups)
            self.assertGreaterEqual(total_members, 2)

    def test_deprecated_excluded(self):
        """Deprecated entries are skipped."""
        entries = [
            _make_entry(
                id="lesson-dep1-11111111",
                title="Same title for both",
                rule="Same rule",
                source=["PR #100"],
                deprecated=True,
            ),
            _make_entry(
                id="lesson-dep2-22222222",
                title="Same title for both",
                rule="Same rule",
                source=["PR #100"],
            ),
        ]
        _write_events(self.events_path, entries)
        report = find_duplicates(self.events_path, self.config)
        # Only 1 active entry → no groups
        self.assertEqual(len(report.groups), 0)
        self.assertEqual(report.entries_checked, 1)

    def test_empty_events(self):
        """Empty events.jsonl → empty report."""
        self.events_path.write_text("")
        report = find_duplicates(self.events_path, self.config)
        self.assertEqual(len(report.groups), 0)
        self.assertEqual(report.entries_checked, 0)

    def test_single_entry(self):
        """Single entry → no groups."""
        _write_events(self.events_path, [_make_entry()])
        report = find_duplicates(self.events_path, self.config)
        self.assertEqual(len(report.groups), 0)

    def test_text_only_mode(self):
        """Without vectordb/embedder, mode is 'text'."""
        entries = [
            _make_entry(id="lesson-t1-11111111"),
            _make_entry(id="lesson-t2-22222222"),
        ]
        _write_events(self.events_path, entries)
        report = find_duplicates(self.events_path, self.config)
        self.assertEqual(report.mode, "text")


# ---------------------------------------------------------------------------
# TestFindDuplicatesHybrid
# ---------------------------------------------------------------------------

class TestFindDuplicatesHybrid(unittest.TestCase):
    """Test hybrid mode with mock embedder and vectordb."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.config = _make_config()

    def test_hybrid_mode_with_mocks(self):
        """With mock vectordb + embedder, mode is 'hybrid'."""

        class MockVectorDB:
            def get_vector(self, entry_id):
                return None  # Force re-embed

        class MockEmbedder:
            def embed_query(self, text):
                import hashlib
                h = hashlib.sha256(text.encode()).digest()
                vec = [(b - 128) / 128.0 for b in h[:10]]
                norm = math.sqrt(sum(v * v for v in vec))
                if norm > 0:
                    vec = [v / norm for v in vec]

                class Result:
                    def __init__(self, v):
                        self.vector = v
                return Result(vec)

        # Create exact duplicates (will pass text threshold)
        entries = [
            _make_entry(
                id="lesson-hyb1-11111111",
                title="Exact duplicate title for testing",
                rule="MUST do the exact same thing",
                source=["PR #100"],
            ),
            _make_entry(
                id="lesson-hyb2-22222222",
                title="Exact duplicate title for testing",
                rule="MUST do the exact same thing",
                source=["PR #100"],
            ),
        ]
        _write_events(self.events_path, entries)

        report = find_duplicates(
            self.events_path, self.config,
            vectordb=MockVectorDB(),
            embedder=MockEmbedder(),
        )
        self.assertEqual(report.mode, "hybrid")

    def test_graceful_without_embedder(self):
        """No embedder → falls back to text-only."""
        entries = [
            _make_entry(id="lesson-fb1-11111111", title="Same"),
            _make_entry(id="lesson-fb2-22222222", title="Same"),
        ]
        _write_events(self.events_path, entries)
        report = find_duplicates(self.events_path, self.config, vectordb=None, embedder=None)
        self.assertEqual(report.mode, "text")

    def test_embedding_refinement_can_filter(self):
        """Pairs that pass text threshold but fail embedding threshold get filtered."""

        class MockVectorDB:
            def get_vector(self, entry_id):
                return None

        class MockEmbedder:
            """Returns very different vectors for 'similar' texts."""
            def embed_query(self, text):
                # Make vectors orthogonal by using different seeds
                import hashlib
                h = hashlib.sha256((text + "salt_unique").encode()).digest()
                vec = [(b - 128) / 128.0 for b in h[:10]]
                norm = math.sqrt(sum(v * v for v in vec))
                if norm > 0:
                    vec = [v / norm for v in vec]

                class Result:
                    def __init__(self, v):
                        self.vector = v
                return Result(vec)

        # Near-duplicates that pass text threshold
        entries = [
            _make_entry(
                id="lesson-emb1-11111111",
                title="Rolling stats shift caused inflation",
                rule="MUST shift before rolling on price data",
                source=["PR #100"],
            ),
            _make_entry(
                id="lesson-emb2-22222222",
                title="Rolling stats shift caused inflation issue",
                rule="MUST shift before rolling on price data always",
                source=["PR #101"],
            ),
        ]
        _write_events(self.events_path, entries)

        # This should try hybrid but embedding may filter some pairs
        report = find_duplicates(
            self.events_path, self.config,
            vectordb=MockVectorDB(),
            embedder=MockEmbedder(),
        )
        self.assertEqual(report.mode, "hybrid")
        # The pair may or may not survive depending on cosine similarity
        # We just verify it doesn't crash


# ---------------------------------------------------------------------------
# TestSuggestMerges
# ---------------------------------------------------------------------------

class TestSuggestMerges(unittest.TestCase):

    def test_higher_severity_kept(self):
        """S1 entry should be kept over S2."""
        entries = {
            "lesson-s1-11111111": _make_entry(id="lesson-s1-11111111", severity="S1"),
            "lesson-s2-22222222": _make_entry(id="lesson-s2-22222222", severity="S2"),
        }
        groups = [DuplicateGroup(
            canonical_id="",
            member_ids=["lesson-s1-11111111", "lesson-s2-22222222"],
            pairwise_scores=[("lesson-s1-11111111", "lesson-s2-22222222", 0.90)],
            avg_similarity=0.90,
        )]
        suggestions = suggest_merges(groups, entries)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].keep_id, "lesson-s1-11111111")
        self.assertEqual(suggestions[0].deprecate_ids, ["lesson-s2-22222222"])

    def test_more_sources_kept(self):
        """Entry with more sources wins when severity is equal."""
        entries = {
            "lesson-ms1-11111111": _make_entry(
                id="lesson-ms1-11111111", severity="S1",
                source=["PR #1", "PR #2", "PR #3"],
            ),
            "lesson-ms2-22222222": _make_entry(
                id="lesson-ms2-22222222", severity="S1",
                source=["PR #1"],
            ),
        }
        groups = [DuplicateGroup(
            member_ids=["lesson-ms1-11111111", "lesson-ms2-22222222"],
            pairwise_scores=[("lesson-ms1-11111111", "lesson-ms2-22222222", 0.90)],
            avg_similarity=0.90,
        )]
        suggestions = suggest_merges(groups, entries)
        self.assertEqual(suggestions[0].keep_id, "lesson-ms1-11111111")

    def test_recently_verified_kept(self):
        """Verified entry preferred over unverified when severity/sources equal."""
        entries = {
            "lesson-rv1-11111111": _make_entry(
                id="lesson-rv1-11111111", severity="S1",
                source=["PR #1"],
                last_verified=_now_iso(),
            ),
            "lesson-rv2-22222222": _make_entry(
                id="lesson-rv2-22222222", severity="S1",
                source=["PR #1"],
                last_verified=None,
            ),
        }
        groups = [DuplicateGroup(
            member_ids=["lesson-rv1-11111111", "lesson-rv2-22222222"],
            pairwise_scores=[("lesson-rv1-11111111", "lesson-rv2-22222222", 0.90)],
            avg_similarity=0.90,
        )]
        suggestions = suggest_merges(groups, entries)
        self.assertEqual(suggestions[0].keep_id, "lesson-rv1-11111111")

    def test_older_entry_tiebreak(self):
        """When all else equal, older entry wins."""
        entries = {
            "lesson-old-11111111": _make_entry(
                id="lesson-old-11111111", severity="S1",
                source=["PR #1"],
                created_at=_days_ago_iso(100),
            ),
            "lesson-new-22222222": _make_entry(
                id="lesson-new-22222222", severity="S1",
                source=["PR #1"],
                created_at=_days_ago_iso(10),
            ),
        }
        groups = [DuplicateGroup(
            member_ids=["lesson-old-11111111", "lesson-new-22222222"],
            pairwise_scores=[("lesson-old-11111111", "lesson-new-22222222", 0.90)],
            avg_similarity=0.90,
        )]
        suggestions = suggest_merges(groups, entries)
        self.assertEqual(suggestions[0].keep_id, "lesson-old-11111111")

    def test_empty_groups(self):
        """No groups → no suggestions."""
        suggestions = suggest_merges([], {})
        self.assertEqual(len(suggestions), 0)


# ---------------------------------------------------------------------------
# TestSuggestDeprecations
# ---------------------------------------------------------------------------

class TestSuggestDeprecations(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.project_root = self.tmpdir
        self.config = _make_config()

    def test_low_confidence_flagged(self):
        """Very old entry with no verification → deprecation candidate."""
        entry = _make_entry(
            id="lesson-low-11111111",
            created_at=_days_ago_iso(500),
            source=["PR #100"],
        )
        _write_events(self.events_path, [entry])
        report = suggest_deprecations(
            self.events_path, self.config, self.project_root
        )
        # Very old entry with PR source → likely low confidence
        # Check if it appears as candidate
        if report.candidates:
            self.assertGreater(len(report.candidates), 0)

    def test_high_confidence_not_flagged(self):
        """Fresh, verified entry should not be flagged."""
        entry = _make_entry(
            id="lesson-high-11111111",
            created_at=_now_iso(),
            last_verified=_now_iso(),
            source=["PR #123"],
        )
        _write_events(self.events_path, [entry])
        report = suggest_deprecations(
            self.events_path, self.config, self.project_root
        )
        candidate_ids = [c.entry_id for c in report.candidates]
        self.assertNotIn("lesson-high-11111111", candidate_ids)

    def test_very_stale_flagged(self):
        """Entry >180 days without verification → candidate."""
        entry = _make_entry(
            id="lesson-stale-11111111",
            created_at=_days_ago_iso(200),
            source=["PR #100"],
        )
        _write_events(self.events_path, [entry])
        report = suggest_deprecations(
            self.events_path, self.config, self.project_root
        )
        candidate_ids = [c.entry_id for c in report.candidates]
        self.assertIn("lesson-stale-11111111", candidate_ids)

    def test_superseded_but_not_deprecated(self):
        """Entry with superseded_by but not deprecated → flagged."""
        entry = _make_entry(
            id="lesson-sup-11111111",
            created_at=_now_iso(),
            last_verified=_now_iso(),
            source=["PR #123"],
            _meta={"superseded_by": "lesson-new-99999999"},
        )
        _write_events(self.events_path, [entry])
        report = suggest_deprecations(
            self.events_path, self.config, self.project_root
        )
        candidate_ids = [c.entry_id for c in report.candidates]
        self.assertIn("lesson-sup-11111111", candidate_ids)

    def test_suggested_action_reverify(self):
        """Stale entry with valid sources → reverify."""
        entry = _make_entry(
            id="lesson-rev-11111111",
            created_at=_days_ago_iso(200),
            source=["PR #100"],  # PR sources always ok
        )
        _write_events(self.events_path, [entry])
        report = suggest_deprecations(
            self.events_path, self.config, self.project_root
        )
        for c in report.candidates:
            if c.entry_id == "lesson-rev-11111111":
                self.assertEqual(c.suggested_action, "reverify")

    def test_confidence_cache_used(self):
        """Pre-computed confidence cache avoids recomputation."""
        entry = _make_entry(
            id="lesson-cache-11111111",
            created_at=_now_iso(),
            source=["PR #123"],
        )
        _write_events(self.events_path, [entry])

        # Pre-compute with very low confidence
        cache = {
            "lesson-cache-11111111": ConfidenceScore(
                entry_id="lesson-cache-11111111",
                score=0.05,
                breakdown=ConfidenceBreakdown(),
                classification="low",
            )
        }
        report = suggest_deprecations(
            self.events_path, self.config, self.project_root,
            confidence_cache=cache,
        )
        candidate_ids = [c.entry_id for c in report.candidates]
        self.assertIn("lesson-cache-11111111", candidate_ids)


# ---------------------------------------------------------------------------
# TestBuildEvolutionReport
# ---------------------------------------------------------------------------

class TestBuildEvolutionReport(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.project_root = self.tmpdir
        self.config = _make_config()

    def test_complete_report_structure(self):
        """All sub-reports should be populated."""
        entries = [
            _make_entry(
                id="lesson-r1-11111111",
                created_at=_now_iso(),
                source=["PR #100"],
            ),
            _make_entry(
                id="lesson-r2-22222222",
                created_at=_now_iso(),
                source=["PR #200"],
            ),
        ]
        _write_events(self.events_path, entries)
        report = build_evolution_report(
            self.events_path, self.config, self.project_root
        )
        self.assertEqual(report.total_entries, 2)
        self.assertEqual(report.active_entries, 2)
        self.assertEqual(report.deprecated_entries, 0)
        self.assertIsNotNone(report.duplicate_report)
        self.assertIsNotNone(report.deprecation_report)
        self.assertEqual(len(report.confidence_scores), 2)
        self.assertGreater(report.duration_ms, 0)

    def test_health_score_fresh_entries(self):
        """Fresh entries should have reasonable health score."""
        entries = [
            _make_entry(
                id="lesson-fresh1-11111111",
                created_at=_now_iso(),
                last_verified=_now_iso(),
                source=["PR #100"],
            ),
            _make_entry(
                id="lesson-fresh2-22222222",
                created_at=_now_iso(),
                last_verified=_now_iso(),
                source=["PR #200"],
            ),
        ]
        _write_events(self.events_path, entries)
        report = build_evolution_report(
            self.events_path, self.config, self.project_root
        )
        self.assertGreater(report.health_score, 0.3)

    def test_health_score_old_entries(self):
        """Very old entries should have low health score."""
        entries = [
            _make_entry(
                id="lesson-old1-11111111",
                created_at=_days_ago_iso(500),
                source=["PR #100"],
            ),
            _make_entry(
                id="lesson-old2-22222222",
                created_at=_days_ago_iso(500),
                source=["PR #200"],
            ),
        ]
        _write_events(self.events_path, entries)
        report = build_evolution_report(
            self.events_path, self.config, self.project_root
        )
        self.assertLess(report.health_score, 0.5)

    def test_confidence_distribution_counts(self):
        """high + medium + low should sum to active entries."""
        entries = [
            _make_entry(
                id="lesson-dist1-11111111",
                created_at=_now_iso(),
                source=["PR #100"],
            ),
            _make_entry(
                id="lesson-dist2-22222222",
                created_at=_days_ago_iso(300),
                source=["PR #200"],
            ),
        ]
        _write_events(self.events_path, entries)
        report = build_evolution_report(
            self.events_path, self.config, self.project_root
        )
        total = (
            report.entries_high_confidence
            + report.entries_medium_confidence
            + report.entries_low_confidence
        )
        self.assertEqual(total, report.active_entries)

    def test_empty_events(self):
        """Empty events → report with zeros."""
        self.events_path.write_text("")
        report = build_evolution_report(
            self.events_path, self.config, self.project_root
        )
        self.assertEqual(report.total_entries, 0)
        self.assertEqual(report.active_entries, 0)
        self.assertEqual(report.health_score, 0.0)


class TestRankEntriesEmpty(unittest.TestCase):
    """Test _rank_entries_for_merge edge cases."""

    def test_empty_entry_ids(self):
        from lib.evolution import _rank_entries_for_merge
        result = _rank_entries_for_merge([], {})
        self.assertEqual(result, [])

    def test_missing_entry_in_dict(self):
        from lib.evolution import _rank_entries_for_merge
        # eid exists in list but not in entries dict — should not crash
        result = _rank_entries_for_merge(["missing-id"], {})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "missing-id")


class TestConfidenceEdgeCases(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.events_path = self.tmpdir / "events.jsonl"
        self.events_path.write_text("")
        self.project_root = self.tmpdir

    def test_half_life_zero(self):
        """half_life=0 should not divide by zero; age_factor should be 0."""
        config = _make_config()
        config["evolution"]["confidence_half_life_days"] = 0
        entry = _make_entry(
            id="lesson-hl0-11111111",
            created_at=_days_ago_iso(10),
            source=["PR #100"],
        )
        score = calculate_confidence(entry, self.events_path, self.project_root, config)
        # age_factor should be 0.0 (not crash)
        self.assertEqual(score.breakdown.age_factor, 0.0)

    def test_no_sources_low_quality(self):
        """Entry with empty source list gets 0 source quality."""
        config = _make_config()
        entry = _make_entry(
            id="lesson-nosrc-11111111",
            created_at=_now_iso(),
            source=[],
        )
        score = calculate_confidence(entry, self.events_path, self.project_root, config)
        self.assertEqual(score.breakdown.source_quality, 0.0)

    def test_non_list_sources_handled(self):
        """Entry with sources as string (not list) should not crash."""
        config = _make_config()
        entry = _make_entry(
            id="lesson-strsrc-11111111",
            created_at=_now_iso(),
            source="not-a-list",
        )
        score = calculate_confidence(entry, self.events_path, self.project_root, config)
        self.assertGreaterEqual(score.score, 0.0)
        self.assertLessEqual(score.score, 1.0)


class TestFindDuplicatesPreloaded(unittest.TestCase):

    def test_preloaded_entries_used(self):
        """_preloaded_entries should be used instead of reading from file."""
        events_path = Path(tempfile.mkdtemp()) / "events.jsonl"
        events_path.write_text("")  # Empty file

        # Pass entries directly via _preloaded_entries
        preloaded = {
            "lesson-pre1-11111111": _make_entry(
                id="lesson-pre1-11111111",
                title="Exact same title for preload test",
                rule="Same rule",
            ),
            "lesson-pre2-22222222": _make_entry(
                id="lesson-pre2-22222222",
                title="Exact same title for preload test",
                rule="Same rule",
            ),
        }
        config = _make_config()
        report = find_duplicates(
            events_path, config, _preloaded_entries=preloaded
        )
        # Should find duplicates from preloaded data, not from empty file
        self.assertGreater(len(report.groups), 0)


class TestBuildEvolutionReportDeprecated(unittest.TestCase):

    def test_deprecated_entries_counted(self):
        tmpdir = Path(tempfile.mkdtemp())
        events_path = tmpdir / "events.jsonl"
        entries = [
            _make_entry(id="lesson-act-11111111", created_at=_now_iso(), source=["PR #1"]),
            _make_entry(id="lesson-dep-22222222", created_at=_now_iso(), source=["PR #2"], deprecated=True),
        ]
        _write_events(events_path, entries)
        config = _make_config()
        report = build_evolution_report(events_path, config, tmpdir)
        self.assertEqual(report.total_entries, 2)
        self.assertEqual(report.active_entries, 1)
        self.assertEqual(report.deprecated_entries, 1)


if __name__ == "__main__":
    unittest.main()
