"""
EF Memory V2 — Memory Evolution (M5)

Duplicate detection, confidence scoring, deprecation suggestions,
and merge recommendations for memory health lifecycle.

Human-in-the-loop invariant: this module NEVER modifies events.jsonl.
All functions return advisory reports only.

No external dependencies — pure Python stdlib + internal M1-M4 modules.
"""

import difflib
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .auto_verify import (
    _load_entries_latest_wins,
    _parse_iso8601,
    _parse_source_ref,
    check_staleness,
    verify_source,
)
from .text_builder import build_dedup_text

logger = logging.getLogger("efm.evolution")


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

_DEFAULT_HALF_LIFE_DAYS = 120
_DEFAULT_DEPRECATION_THRESHOLD = 0.3

_DEFAULT_SOURCE_QUALITY = {
    "code": 1.0,
    "function": 1.0,
    "markdown": 0.7,
    "commit": 0.6,
    "pr": 0.5,
    "unknown": 0.3,
}

_DEFAULT_CONFIDENCE_WEIGHTS = {
    "source_quality": 0.30,
    "age_factor": 0.30,
    "verification_boost": 0.15,
    "source_validity": 0.25,
}

_SEVERITY_RANK = {"S1": 3, "S2": 2, "S3": 1}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DuplicateGroup:
    """A cluster of entries that are similar to each other."""
    canonical_id: str = ""
    member_ids: List[str] = field(default_factory=list)
    pairwise_scores: List[Tuple[str, str, float]] = field(default_factory=list)
    avg_similarity: float = 0.0


@dataclass
class DuplicateReport:
    """Report from find_duplicates()."""
    total_entries: int = 0
    entries_checked: int = 0
    groups: List[DuplicateGroup] = field(default_factory=list)
    mode: str = "text"          # "text" | "hybrid"
    text_threshold: float = 0.85
    embedding_threshold: float = 0.92
    duration_ms: float = 0.0


@dataclass
class ConfidenceBreakdown:
    """Breakdown of the confidence score factors."""
    source_quality: float = 0.0
    age_factor: float = 0.0
    verification_boost: float = 0.0
    source_validity: float = 0.0


@dataclass
class ConfidenceScore:
    """Result from calculate_confidence()."""
    entry_id: str = ""
    score: float = 0.0
    breakdown: ConfidenceBreakdown = field(default_factory=ConfidenceBreakdown)
    classification: str = ""    # "high" | "medium" | "low"


@dataclass
class DeprecationCandidate:
    """A single entry recommended for deprecation/action."""
    entry_id: str = ""
    title: str = ""
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)
    suggested_action: str = ""  # "deprecate" | "reverify" | "merge-into:<id>"


@dataclass
class DeprecationReport:
    """Report from suggest_deprecations()."""
    total_entries: int = 0
    candidates: List[DeprecationCandidate] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class MergeSuggestion:
    """Suggestion for merging duplicate entries."""
    keep_id: str = ""
    deprecate_ids: List[str] = field(default_factory=list)
    merge_reason: str = ""
    group_similarity: float = 0.0


@dataclass
class EvolutionReport:
    """Comprehensive evolution report from build_evolution_report()."""
    total_entries: int = 0
    active_entries: int = 0
    deprecated_entries: int = 0
    health_score: float = 0.0
    avg_confidence: float = 0.0
    duplicate_report: Optional[DuplicateReport] = None
    deprecation_report: Optional[DeprecationReport] = None
    merge_suggestions: List[MergeSuggestion] = field(default_factory=list)
    confidence_scores: List[ConfidenceScore] = field(default_factory=list)
    entries_high_confidence: int = 0
    entries_medium_confidence: int = 0
    entries_low_confidence: int = 0
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Union-Find for duplicate clustering
# ---------------------------------------------------------------------------

class _UnionFind:
    """Simple union-find for clustering duplicate groups."""

    def __init__(self, items: List[str]):
        self._parent: Dict[str, str] = {item: item for item in items}

    def find(self, x: str) -> str:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]  # path compression
            x = self._parent[x]
        return x

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self._parent[rx] = ry

    def groups(self) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        for item in self._parent:
            root = self.find(item)
            result.setdefault(root, []).append(item)
        return {r: members for r, members in result.items() if len(members) > 1}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_evolution_config(config: dict) -> dict:
    """Extract evolution section from config with defaults."""
    return config.get("evolution", {})


def _get_source_quality_weights(config: dict) -> Dict[str, float]:
    """Get source quality weights from config or defaults."""
    evo = _get_evolution_config(config)
    return evo.get("source_quality_weights", _DEFAULT_SOURCE_QUALITY)


def _get_confidence_weights(config: dict) -> Dict[str, float]:
    """Get confidence component weights from config or defaults."""
    evo = _get_evolution_config(config)
    return evo.get("confidence_weights", _DEFAULT_CONFIDENCE_WEIGHTS)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def calculate_confidence(
    entry: dict,
    events_path: Path,
    project_root: Path,
    config: dict,
) -> ConfidenceScore:
    """
    Calculate a composite confidence score (0.0-1.0) for a single entry.

    Components (weighted):
    1. Source quality (30%): Based on source type hierarchy
       code/function > markdown > commit > pr > unknown
    2. Age factor (30%): Exponential decay from last_verified or created_at
       Uses configurable half-life (default 120 days)
    3. Verification boost (15%): Recently verified entries get boost
    4. Source validity (25%): Fraction of sources that still exist on disk

    Returns ConfidenceScore with breakdown and classification.
    """
    entry_id = entry.get("id", "")
    evo_config = _get_evolution_config(config)
    weights = _get_confidence_weights(config)
    sq_weights = _get_source_quality_weights(config)

    # --- 1. Source quality ---
    sources = entry.get("source", [])
    if not isinstance(sources, list):
        sources = []

    source_quality = 0.0
    if sources:
        qualities = []
        for src in sources:
            try:
                src_type, _, _, _ = _parse_source_ref(str(src))
                qualities.append(sq_weights.get(src_type, sq_weights.get("unknown", 0.3)))
            except Exception:
                qualities.append(sq_weights.get("unknown", 0.3))
        source_quality = max(qualities) if qualities else 0.0
    # No sources → 0.0

    # --- 2. Age factor ---
    half_life = evo_config.get("confidence_half_life_days", _DEFAULT_HALF_LIFE_DAYS)
    age_factor = 0.0

    # Use last_verified if available, otherwise created_at
    ref_date = None
    last_verified = entry.get("last_verified")
    created_at = entry.get("created_at")

    if last_verified:
        try:
            ref_date = _parse_iso8601(str(last_verified))
        except Exception:
            pass

    if ref_date is None and created_at:
        try:
            ref_date = _parse_iso8601(str(created_at))
        except Exception:
            pass

    if ref_date is not None:
        now = datetime.now(timezone.utc)
        days_old = max(0, (now - ref_date).days)
        # Exponential decay: 2^(-days/half_life)
        if half_life > 0:
            age_factor = math.pow(2, -days_old / half_life)
        else:
            age_factor = 0.0
    # No parseable date → 0.0

    # --- 3. Verification boost ---
    verification_boost = 0.0
    if last_verified:
        try:
            verified_dt = _parse_iso8601(str(last_verified))
            days_since_verified = max(0, (datetime.now(timezone.utc) - verified_dt).days)
            if days_since_verified <= 30:
                verification_boost = 1.0
            elif days_since_verified <= 90:
                verification_boost = 0.67
            else:
                verification_boost = 0.0
        except Exception:
            pass

    # --- 4. Source validity ---
    source_validity = 0.0
    if sources:
        validity_scores = []
        for src in sources:
            try:
                src_type, _, _, _ = _parse_source_ref(str(src))
                if src_type in ("pr", "commit"):
                    # Informational sources — count as 0.5
                    validity_scores.append(0.5)
                else:
                    # File-based sources — check existence
                    result = verify_source(str(src), project_root)
                    if result.status == "OK":
                        validity_scores.append(1.0)
                    elif result.status == "WARN":
                        validity_scores.append(0.5)
                    else:
                        validity_scores.append(0.0)
            except Exception:
                validity_scores.append(0.0)
        source_validity = sum(validity_scores) / len(validity_scores) if validity_scores else 0.0
    else:
        source_validity = 0.0

    # --- Composite score ---
    w_sq = weights.get("source_quality", 0.30)
    w_age = weights.get("age_factor", 0.30)
    w_vb = weights.get("verification_boost", 0.15)
    w_sv = weights.get("source_validity", 0.25)

    score = (
        w_sq * source_quality
        + w_age * age_factor
        + w_vb * verification_boost
        + w_sv * source_validity
    )
    # Clamp to [0, 1]
    score = max(0.0, min(1.0, score))

    # Classification
    if score >= 0.7:
        classification = "high"
    elif score >= 0.4:
        classification = "medium"
    else:
        classification = "low"

    return ConfidenceScore(
        entry_id=entry_id,
        score=score,
        breakdown=ConfidenceBreakdown(
            source_quality=source_quality,
            age_factor=age_factor,
            verification_boost=verification_boost,
            source_validity=source_validity,
        ),
        classification=classification,
    )


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def find_duplicates(
    events_path: Path,
    config: dict,
    vectordb=None,
    embedder=None,
) -> DuplicateReport:
    """
    Find clusters of duplicate or near-duplicate entries.

    Two-stage approach:
    1. Text-based: difflib.SequenceMatcher on build_dedup_text()
       Threshold from config["automation"]["dedup_threshold"] (default 0.85)
    2. Optional embedding refinement: cosine similarity on vectors
       Threshold from config["embedding"]["dedup_threshold"] (default 0.92)

    Stage 2 only runs if vectordb and embedder are both provided.
    Entries are grouped into clusters using union-find.

    Skips deprecated entries.
    """
    t0 = time.time()

    # Thresholds
    auto_config = config.get("automation", {})
    embed_config = config.get("embedding", {})
    text_threshold = auto_config.get("dedup_threshold", 0.85)
    embedding_threshold = embed_config.get("dedup_threshold", 0.92)

    # Load entries
    all_entries = _load_entries_latest_wins(events_path)
    total_entries = len(all_entries)

    # Filter active (non-deprecated)
    active: Dict[str, dict] = {
        eid: e for eid, e in all_entries.items()
        if not e.get("deprecated", False)
    }
    entries_checked = len(active)

    if entries_checked < 2:
        return DuplicateReport(
            total_entries=total_entries,
            entries_checked=entries_checked,
            groups=[],
            mode="text",
            text_threshold=text_threshold,
            embedding_threshold=embedding_threshold,
            duration_ms=(time.time() - t0) * 1000,
        )

    # Build dedup texts
    texts: Dict[str, str] = {
        eid: build_dedup_text(e) for eid, e in active.items()
    }
    entry_ids = list(active.keys())

    # Stage 1: Text similarity (O(n^2))
    candidate_pairs: List[Tuple[str, str, float]] = []
    for i in range(len(entry_ids)):
        for j in range(i + 1, len(entry_ids)):
            id_a, id_b = entry_ids[i], entry_ids[j]
            ratio = difflib.SequenceMatcher(
                None, texts[id_a], texts[id_b]
            ).ratio()
            if ratio >= text_threshold:
                candidate_pairs.append((id_a, id_b, ratio))

    # Stage 2: Embedding refinement (optional)
    use_hybrid = vectordb is not None and embedder is not None
    mode = "hybrid" if use_hybrid else "text"

    confirmed_pairs: List[Tuple[str, str, float]] = []

    if use_hybrid and candidate_pairs:
        for id_a, id_b, text_score in candidate_pairs:
            try:
                emb_score = _get_embedding_similarity(
                    id_a, id_b, active, vectordb, embedder
                )
                if emb_score >= embedding_threshold:
                    # Use embedding score as final score in hybrid mode
                    confirmed_pairs.append((id_a, id_b, emb_score))
            except Exception as exc:
                logger.warning("Embedding similarity failed for %s/%s: %s", id_a, id_b, exc)
                # Fall back to text score for this pair
                confirmed_pairs.append((id_a, id_b, text_score))
    else:
        confirmed_pairs = candidate_pairs

    if not confirmed_pairs:
        return DuplicateReport(
            total_entries=total_entries,
            entries_checked=entries_checked,
            groups=[],
            mode=mode,
            text_threshold=text_threshold,
            embedding_threshold=embedding_threshold,
            duration_ms=(time.time() - t0) * 1000,
        )

    # Cluster with union-find
    all_involved = set()
    for id_a, id_b, _ in confirmed_pairs:
        all_involved.add(id_a)
        all_involved.add(id_b)

    uf = _UnionFind(list(all_involved))
    for id_a, id_b, _ in confirmed_pairs:
        uf.union(id_a, id_b)

    raw_groups = uf.groups()

    # Build DuplicateGroup objects
    groups: List[DuplicateGroup] = []
    for _, members in raw_groups.items():
        # Collect pairwise scores for this group
        group_pairs = [
            (a, b, s) for a, b, s in confirmed_pairs
            if a in members and b in members
        ]
        avg_sim = (
            sum(s for _, _, s in group_pairs) / len(group_pairs)
            if group_pairs else 0.0
        )

        # Select canonical
        canonical = _rank_entries_for_merge(members, active)[0]

        groups.append(DuplicateGroup(
            canonical_id=canonical,
            member_ids=sorted(members),
            pairwise_scores=group_pairs,
            avg_similarity=avg_sim,
        ))

    # Sort groups by size descending
    groups.sort(key=lambda g: len(g.member_ids), reverse=True)

    return DuplicateReport(
        total_entries=total_entries,
        entries_checked=entries_checked,
        groups=groups,
        mode=mode,
        text_threshold=text_threshold,
        embedding_threshold=embedding_threshold,
        duration_ms=(time.time() - t0) * 1000,
    )


def _get_embedding_similarity(
    id_a: str,
    id_b: str,
    entries: Dict[str, dict],
    vectordb,
    embedder,
) -> float:
    """Get cosine similarity between two entries via embeddings."""
    from .vectordb import cosine_similarity

    text_a = build_dedup_text(entries[id_a])
    text_b = build_dedup_text(entries[id_b])

    # Try to get vectors from DB first
    vec_a = vectordb.get_vector(id_a) if hasattr(vectordb, "get_vector") else None
    vec_b = vectordb.get_vector(id_b) if hasattr(vectordb, "get_vector") else None

    # Embed if not in DB
    if vec_a is None:
        result = embedder.embed_query(text_a)
        vec_a = result.vector
    if vec_b is None:
        result = embedder.embed_query(text_b)
        vec_b = result.vector

    return cosine_similarity(vec_a, vec_b)


# ---------------------------------------------------------------------------
# Merge suggestions
# ---------------------------------------------------------------------------

def _rank_entries_for_merge(
    entry_ids: List[str],
    entries: Dict[str, dict],
) -> List[str]:
    """
    Rank entries for merge selection (best first).

    Priority:
    1. Higher severity (S1 > S2 > S3 > None)
    2. More sources
    3. More recently verified
    4. Older entry (established knowledge)
    """
    def sort_key(eid: str):
        e = entries.get(eid, {})
        severity = e.get("severity")
        sev_rank = _SEVERITY_RANK.get(severity, 0) if severity else 0
        sources = e.get("source", [])
        num_sources = len(sources) if isinstance(sources, list) else 0

        # Verification recency (higher = more recent)
        verified_ts = 0.0
        lv = e.get("last_verified")
        if lv:
            try:
                verified_ts = _parse_iso8601(str(lv)).timestamp()
            except Exception:
                pass

        # Created at (lower = older = better for tiebreak)
        created_ts = float("inf")
        ca = e.get("created_at")
        if ca:
            try:
                created_ts = _parse_iso8601(str(ca)).timestamp()
            except Exception:
                pass

        return (-sev_rank, -num_sources, -verified_ts, created_ts)

    return sorted(entry_ids, key=sort_key)


def suggest_merges(
    duplicate_groups: List[DuplicateGroup],
    entries: Dict[str, dict],
) -> List[MergeSuggestion]:
    """
    From duplicate groups, suggest which entry to keep as canonical.

    Ranking criteria (in order of priority):
    1. Higher severity wins (S1 > S2 > S3 > None)
    2. More sources wins (more evidence)
    3. More recently verified wins
    4. Older entry wins (established knowledge)

    Returns one MergeSuggestion per group.
    """
    suggestions: List[MergeSuggestion] = []

    for group in duplicate_groups:
        if len(group.member_ids) < 2:
            continue

        ranked = _rank_entries_for_merge(group.member_ids, entries)
        keep_id = ranked[0]
        deprecate_ids = ranked[1:]

        # Build reason
        keep_entry = entries.get(keep_id, {})
        reason_parts = []
        severity = keep_entry.get("severity")
        if severity:
            reason_parts.append(f"highest severity ({severity})")
        sources = keep_entry.get("source", [])
        if isinstance(sources, list) and len(sources) > 1:
            reason_parts.append(f"most sources ({len(sources)})")
        if keep_entry.get("last_verified"):
            reason_parts.append("recently verified")

        if not reason_parts:
            reason_parts.append("oldest entry")

        merge_reason = f"Keep {keep_id}: {', '.join(reason_parts)}"

        suggestions.append(MergeSuggestion(
            keep_id=keep_id,
            deprecate_ids=deprecate_ids,
            merge_reason=merge_reason,
            group_similarity=group.avg_similarity,
        ))

    return suggestions


# ---------------------------------------------------------------------------
# Deprecation suggestions
# ---------------------------------------------------------------------------

def suggest_deprecations(
    events_path: Path,
    config: dict,
    project_root: Path,
    confidence_cache: Optional[Dict[str, ConfidenceScore]] = None,
) -> DeprecationReport:
    """
    Identify entries that should be deprecated or re-verified.

    Candidates:
    1. Confidence < threshold (default 0.3)
    2. All file-based sources invalid (FAIL status)
    3. Stale beyond 2x staleness threshold
    4. Entry with superseded_by set but not yet deprecated

    Suggested actions:
    - "deprecate": low confidence AND sources invalid
    - "reverify": stale but sources may still exist
    """
    t0 = time.time()
    evo_config = _get_evolution_config(config)
    dep_threshold = evo_config.get(
        "deprecation_confidence_threshold", _DEFAULT_DEPRECATION_THRESHOLD
    )
    staleness_threshold = config.get("verify", {}).get("staleness_threshold_days", 90)

    entries = _load_entries_latest_wins(events_path)
    active = {
        eid: e for eid, e in entries.items()
        if not e.get("deprecated", False)
    }
    total_entries = len(active)

    candidates: List[DeprecationCandidate] = []

    for eid, entry in active.items():
        reasons: List[str] = []
        suggested_action = ""

        # Get confidence
        if confidence_cache and eid in confidence_cache:
            conf = confidence_cache[eid]
        else:
            conf = calculate_confidence(entry, events_path, project_root, config)

        # Rule 1: Low confidence
        if conf.score < dep_threshold:
            reasons.append(
                f"Low confidence ({conf.score:.2f} < {dep_threshold})"
            )

        # Rule 2: All file-based sources invalid
        sources = entry.get("source", [])
        if isinstance(sources, list) and sources:
            file_sources_checked = 0
            file_sources_invalid = 0
            for src in sources:
                try:
                    src_type, _, _, _ = _parse_source_ref(str(src))
                    if src_type in ("code", "markdown", "function"):
                        file_sources_checked += 1
                        result = verify_source(str(src), project_root)
                        if result.status == "FAIL":
                            file_sources_invalid += 1
                except Exception:
                    pass

            if file_sources_checked > 0 and file_sources_invalid == file_sources_checked:
                reasons.append("All file-based sources invalid")

        # Rule 3: Very stale (2x threshold)
        staleness = check_staleness(entry, staleness_threshold)
        effective_days = (
            staleness.days_since_verified
            if staleness.days_since_verified is not None
            else staleness.days_since_created
        )
        if effective_days > staleness_threshold * 2:
            reasons.append(
                f"Very stale ({effective_days}d > {staleness_threshold * 2}d)"
            )

        # Rule 4: Superseded but not deprecated
        meta = entry.get("_meta", {})
        if isinstance(meta, dict) and meta.get("superseded_by"):
            reasons.append(
                f"Superseded by {meta['superseded_by']} but not deprecated"
            )

        if not reasons:
            continue

        # Determine suggested action
        all_sources_invalid = "All file-based sources invalid" in reasons
        low_confidence = any("Low confidence" in r for r in reasons)

        if low_confidence and all_sources_invalid:
            suggested_action = "deprecate"
        elif "Superseded by" in " ".join(reasons):
            suggested_action = "deprecate"
        else:
            suggested_action = "reverify"

        candidates.append(DeprecationCandidate(
            entry_id=eid,
            title=entry.get("title", ""),
            confidence=conf.score,
            reasons=reasons,
            suggested_action=suggested_action,
        ))

    # Sort by confidence ascending (worst first)
    candidates.sort(key=lambda c: c.confidence)

    return DeprecationReport(
        total_entries=total_entries,
        candidates=candidates,
        duration_ms=(time.time() - t0) * 1000,
    )


# ---------------------------------------------------------------------------
# Comprehensive evolution report
# ---------------------------------------------------------------------------

def build_evolution_report(
    events_path: Path,
    config: dict,
    project_root: Path,
    vectordb=None,
    embedder=None,
) -> EvolutionReport:
    """
    Build a comprehensive evolution report combining all checks.

    Orchestrates:
    1. find_duplicates() -> DuplicateReport
    2. calculate_confidence() for each active entry
    3. suggest_deprecations() with confidence cache
    4. suggest_merges() from duplicate groups
    5. Aggregate health score

    Health score = mean of all entry confidence scores.
    """
    t0 = time.time()

    # Load entries
    all_entries = _load_entries_latest_wins(events_path)
    total_entries = len(all_entries)

    active = {
        eid: e for eid, e in all_entries.items()
        if not e.get("deprecated", False)
    }
    active_count = len(active)
    deprecated_count = total_entries - active_count

    if active_count == 0:
        return EvolutionReport(
            total_entries=total_entries,
            active_entries=0,
            deprecated_entries=deprecated_count,
            duration_ms=(time.time() - t0) * 1000,
        )

    # 1. Duplicates
    dup_report = find_duplicates(events_path, config, vectordb, embedder)

    # 2. Confidence scores
    confidence_scores: List[ConfidenceScore] = []
    confidence_cache: Dict[str, ConfidenceScore] = {}
    for eid, entry in active.items():
        cs = calculate_confidence(entry, events_path, project_root, config)
        confidence_scores.append(cs)
        confidence_cache[eid] = cs

    # 3. Deprecation suggestions (pass cache to avoid recomputation)
    dep_report = suggest_deprecations(
        events_path, config, project_root, confidence_cache=confidence_cache
    )

    # 4. Merge suggestions
    merge_suggestions = suggest_merges(dup_report.groups, active)

    # 5. Aggregate health score
    total_score = sum(cs.score for cs in confidence_scores)
    avg_confidence = total_score / len(confidence_scores) if confidence_scores else 0.0
    health_score = avg_confidence  # Health = average confidence

    # Confidence distribution
    high = sum(1 for cs in confidence_scores if cs.classification == "high")
    medium = sum(1 for cs in confidence_scores if cs.classification == "medium")
    low = sum(1 for cs in confidence_scores if cs.classification == "low")

    return EvolutionReport(
        total_entries=total_entries,
        active_entries=active_count,
        deprecated_entries=deprecated_count,
        health_score=health_score,
        avg_confidence=avg_confidence,
        duplicate_report=dup_report,
        deprecation_report=dep_report,
        merge_suggestions=merge_suggestions,
        confidence_scores=confidence_scores,
        entries_high_confidence=high,
        entries_medium_confidence=medium,
        entries_low_confidence=low,
        duration_ms=(time.time() - t0) * 1000,
    )
