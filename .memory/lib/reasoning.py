"""
EF Memory V2 — LLM Reasoning Engine (M6)

Cross-memory correlation, contradiction detection, knowledge synthesis,
and context-aware risk assessment using LLM analysis.

Human-in-the-loop invariant: this module NEVER modifies events.jsonl.
All functions return advisory reports only.

Graceful degradation: all functions work without LLM provider
(heuristic-only mode). LLM enrichment is additive.

No external dependencies beyond internal M1-M5 modules.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .auto_verify import _load_entries_latest_wins, _parse_iso8601
from .llm_provider import LLMProvider, LLMResponse
from .prompts import (
    _entries_to_compact_text,
    correlation_prompt,
    contradiction_prompt,
    synthesis_prompt,
    risk_prompt,
)

logger = logging.getLogger("efm.reasoning")


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

_DEFAULT_CORRELATION_THRESHOLD = 2   # Min tag overlap to correlate
_DEFAULT_SYNTHESIS_MIN_GROUP = 3     # Min entries for synthesis suggestion
_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_TOKEN_BUDGET = 16000

# Opposing keyword pairs for heuristic contradiction detection
_OPPOSING_KEYWORDS = [
    ("MUST", "NEVER"),
    ("ALWAYS", "NEVER"),
    ("MUST", "MUST NOT"),
    ("before", "after"),
]


# ---------------------------------------------------------------------------
# Data types — Correlation
# ---------------------------------------------------------------------------

@dataclass
class CorrelationGroup:
    """A group of related entries found by correlation analysis."""
    entry_ids: List[str] = field(default_factory=list)
    relationship: str = ""
    explanation: str = ""
    strength: float = 0.0


@dataclass
class CorrelationReport:
    """Summary of correlation analysis."""
    total_entries: int = 0
    groups: List[CorrelationGroup] = field(default_factory=list)
    mode: str = "heuristic"
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Data types — Contradiction
# ---------------------------------------------------------------------------

@dataclass
class ContradictionPair:
    """A pair of potentially conflicting entries."""
    entry_id_a: str = ""
    entry_id_b: str = ""
    type: str = ""           # "rule_conflict" | "severity_mismatch" | "semantic"
    explanation: str = ""
    confidence: float = 0.0


@dataclass
class ContradictionReport:
    """Summary of contradiction detection."""
    total_entries: int = 0
    pairs: List[ContradictionPair] = field(default_factory=list)
    mode: str = "heuristic"
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Data types — Synthesis
# ---------------------------------------------------------------------------

@dataclass
class SynthesisSuggestion:
    """A suggestion to consolidate multiple entries into one principle."""
    source_entry_ids: List[str] = field(default_factory=list)
    proposed_title: str = ""
    proposed_principle: str = ""
    rationale: str = ""


@dataclass
class SynthesisReport:
    """Summary of knowledge synthesis."""
    total_entries: int = 0
    suggestions: List[SynthesisSuggestion] = field(default_factory=list)
    mode: str = "heuristic"
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Data types — Risk
# ---------------------------------------------------------------------------

@dataclass
class RiskAnnotation:
    """A risk annotation for a search result entry."""
    entry_id: str = ""
    risk_level: str = ""     # "high" | "medium" | "low" | "info"
    annotation: str = ""
    related_entry_ids: List[str] = field(default_factory=list)


@dataclass
class RiskReport:
    """Summary of risk assessment."""
    query: str = ""
    annotations: List[RiskAnnotation] = field(default_factory=list)
    mode: str = "heuristic"
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Data types — Comprehensive
# ---------------------------------------------------------------------------

@dataclass
class ReasoningReport:
    """Comprehensive reasoning report combining all analyses."""
    total_entries: int = 0
    correlation_report: Optional[CorrelationReport] = None
    contradiction_report: Optional[ContradictionReport] = None
    synthesis_report: Optional[SynthesisReport] = None
    mode: str = "heuristic"
    llm_calls: int = 0
    llm_tokens_used: int = 0
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------

def _get_reasoning_config(config: dict) -> dict:
    """Extract reasoning config with defaults."""
    rc = config.get("reasoning", {})
    return {
        "correlation_threshold": rc.get("correlation_threshold", _DEFAULT_CORRELATION_THRESHOLD),
        "synthesis_min_group_size": rc.get("synthesis_min_group_size", _DEFAULT_SYNTHESIS_MIN_GROUP),
        "max_tokens": rc.get("max_tokens", _DEFAULT_MAX_TOKENS),
        "token_budget": rc.get("token_budget", _DEFAULT_TOKEN_BUDGET),
        "contradiction_detection": rc.get("contradiction_detection", True),
    }


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _safe_llm_call(
    llm_provider: LLMProvider,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
) -> Optional[LLMResponse]:
    """Wrap LLM call in try/except. Returns None on failure."""
    try:
        return llm_provider.complete(system_prompt, user_prompt, max_tokens)
    except Exception as e:
        logger.warning(f"LLM call failed: {e}")
        return None


def _parse_llm_json(response_text: str) -> Optional[dict]:
    """
    Safely parse JSON from LLM response.

    Handles:
    - Raw JSON
    - Markdown code blocks (```json ... ```)
    - Partial/malformed JSON
    """
    if not response_text:
        return None

    text = response_text.strip()

    # Try raw JSON first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if md_match:
        try:
            return json.loads(md_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    logger.warning(f"Failed to parse LLM JSON response: {text[:200]}")
    return None


# ---------------------------------------------------------------------------
# Core function 1: find_correlations
# ---------------------------------------------------------------------------

def find_correlations(
    entries: Dict[str, dict],
    config: dict,
    llm_provider: Optional[LLMProvider] = None,
) -> CorrelationReport:
    """
    Find meaningful correlations between memory entries.

    Stage 1 — Heuristic: tag overlap, source overlap, temporal proximity.
    Stage 2 — LLM enrichment (optional): discover hidden connections.

    Args:
        entries: {entry_id: entry_dict} — active, non-deprecated entries.
        config: Full config dict.
        llm_provider: Optional LLM provider for enrichment.

    Returns:
        CorrelationReport with correlation groups.
    """
    t0 = time.monotonic()
    rc = _get_reasoning_config(config)
    threshold = rc["correlation_threshold"]

    report = CorrelationReport(total_entries=len(entries))

    if len(entries) < 2:
        report.duration_ms = (time.monotonic() - t0) * 1000
        return report

    entry_list = list(entries.items())
    seen_groups: Dict[str, set] = {}  # group_key -> set of entry_ids

    # --- Stage 1: Heuristic pre-filter ---

    # Tag overlap
    for i in range(len(entry_list)):
        eid_a, entry_a = entry_list[i]
        tags_a = set(entry_a.get("tags", []))
        for j in range(i + 1, len(entry_list)):
            eid_b, entry_b = entry_list[j]
            tags_b = set(entry_b.get("tags", []))
            overlap = tags_a & tags_b
            if len(overlap) >= threshold:
                key = f"tag:{','.join(sorted(overlap))}"
                if key not in seen_groups:
                    seen_groups[key] = set()
                seen_groups[key].add(eid_a)
                seen_groups[key].add(eid_b)

    # Source file overlap (extract file path from source strings)
    source_files: Dict[str, List[str]] = {}
    for eid, entry in entry_list:
        for src in entry.get("source", []):
            # Extract file path (before :L or # or ::)
            file_path = re.split(r"[::#]", src)[0]
            if file_path:
                source_files.setdefault(file_path, []).append(eid)

    for file_path, eids in source_files.items():
        if len(eids) >= 2:
            key = f"source:{file_path}"
            seen_groups[key] = set(eids)

    # Temporal proximity (within 24h)
    entry_times: List[Tuple[str, datetime]] = []
    for eid, entry in entry_list:
        try:
            ts = _parse_iso8601(entry.get("created_at", ""))
        except (ValueError, TypeError):
            ts = None
        if ts:
            entry_times.append((eid, ts))
    entry_times.sort(key=lambda x: x[1])

    for i in range(len(entry_times)):
        for j in range(i + 1, len(entry_times)):
            diff = abs((entry_times[j][1] - entry_times[i][1]).total_seconds())
            if diff <= 86400:  # 24 hours
                key = f"temporal:{entry_times[i][0]},{entry_times[j][0]}"
                seen_groups[key] = {entry_times[i][0], entry_times[j][0]}
            else:
                break  # Sorted, so no need to check further

    # Convert to CorrelationGroup objects (merge overlapping groups by entry set)
    unique_sets: List[Tuple[frozenset, str]] = []
    for key, id_set in seen_groups.items():
        rel_type = key.split(":")[0]
        # Check if this set is a subset of an existing group
        merged = False
        for idx, (existing_set, existing_rel) in enumerate(unique_sets):
            if id_set & existing_set:
                unique_sets[idx] = (existing_set | frozenset(id_set), f"{existing_rel}+{rel_type}")
                merged = True
                break
        if not merged:
            unique_sets.append((frozenset(id_set), rel_type))

    for id_set, rel_type in unique_sets:
        report.groups.append(CorrelationGroup(
            entry_ids=sorted(id_set),
            relationship=rel_type,
            explanation=f"Heuristic: {rel_type} overlap",
            strength=min(1.0, len(id_set) / len(entries)),
        ))

    # --- Stage 2: LLM enrichment ---
    llm_calls = 0
    if llm_provider and entries:
        entries_text = _entries_to_compact_text(
            list(entries.values()),
            max_chars=rc["token_budget"] // 2,
        )
        groups_text = "\n".join(
            f"Group: {g.entry_ids} — {g.relationship}"
            for g in report.groups
        ) or "None found"

        sys_prompt, user_prompt = correlation_prompt(
            entries_text, groups_text,
            max_input_chars=rc["token_budget"],
        )
        response = _safe_llm_call(
            llm_provider, sys_prompt, user_prompt,
            max_tokens=rc["max_tokens"],
        )
        llm_calls += 1

        if response:
            parsed = _parse_llm_json(response.text)
            if parsed and "groups" in parsed:
                for g in parsed["groups"]:
                    if isinstance(g, dict) and "entry_ids" in g:
                        # Validate entry_ids exist
                        valid_ids = [eid for eid in g["entry_ids"] if eid in entries]
                        if len(valid_ids) >= 2:
                            report.groups.append(CorrelationGroup(
                                entry_ids=valid_ids,
                                relationship=g.get("relationship", "llm_discovered"),
                                explanation=g.get("relationship", "LLM-discovered correlation"),
                                strength=float(g.get("strength", 0.7)),
                            ))
                report.mode = "llm_enriched"

    report.duration_ms = (time.monotonic() - t0) * 1000
    return report


# ---------------------------------------------------------------------------
# Core function 2: detect_contradictions
# ---------------------------------------------------------------------------

def detect_contradictions(
    entries: Dict[str, dict],
    config: dict,
    llm_provider: Optional[LLMProvider] = None,
) -> ContradictionReport:
    """
    Detect contradictory rules or lessons in memory entries.

    Stage 1 — Heuristic: keyword opposition, severity mismatch.
    Stage 2 — LLM enrichment (optional): semantic contradiction detection.

    Args:
        entries: {entry_id: entry_dict}
        config: Full config dict.
        llm_provider: Optional LLM provider for enrichment.

    Returns:
        ContradictionReport with contradiction pairs.
    """
    t0 = time.monotonic()
    rc = _get_reasoning_config(config)

    report = ContradictionReport(total_entries=len(entries))

    if len(entries) < 2 or not rc["contradiction_detection"]:
        report.duration_ms = (time.monotonic() - t0) * 1000
        return report

    entry_list = list(entries.items())
    candidate_pairs: List[ContradictionPair] = []

    # --- Stage 1: Heuristic pre-filter ---
    for i in range(len(entry_list)):
        eid_a, entry_a = entry_list[i]
        rule_a = (entry_a.get("rule") or "").strip()
        tags_a = set(entry_a.get("tags", []))

        for j in range(i + 1, len(entry_list)):
            eid_b, entry_b = entry_list[j]
            rule_b = (entry_b.get("rule") or "").strip()
            tags_b = set(entry_b.get("tags", []))

            # Must share at least one tag to be comparable
            if not (tags_a & tags_b):
                continue

            # Check for opposing keywords in rules
            if rule_a and rule_b:
                for kw_pos, kw_neg in _OPPOSING_KEYWORDS:
                    a_has_pos = kw_pos.lower() in rule_a.lower()
                    a_has_neg = kw_neg.lower() in rule_a.lower()
                    b_has_pos = kw_pos.lower() in rule_b.lower()
                    b_has_neg = kw_neg.lower() in rule_b.lower()

                    if (a_has_pos and b_has_neg) or (a_has_neg and b_has_pos):
                        candidate_pairs.append(ContradictionPair(
                            entry_id_a=eid_a,
                            entry_id_b=eid_b,
                            type="rule_conflict",
                            explanation=(
                                f"Opposing keywords: '{kw_pos}'/'{kw_neg}' "
                                f"in rules of entries sharing tags {sorted(tags_a & tags_b)}"
                            ),
                            confidence=0.6,
                        ))
                        break  # One conflict per pair is enough

            # Severity mismatch on same topic
            sev_a = entry_a.get("severity", "")
            sev_b = entry_b.get("severity", "")
            tag_overlap = len(tags_a & tags_b)
            if sev_a and sev_b and sev_a != sev_b and tag_overlap >= 2:
                # Don't duplicate if already found as rule_conflict
                existing_pair = any(
                    p.entry_id_a == eid_a and p.entry_id_b == eid_b
                    for p in candidate_pairs
                )
                if not existing_pair:
                    candidate_pairs.append(ContradictionPair(
                        entry_id_a=eid_a,
                        entry_id_b=eid_b,
                        type="severity_mismatch",
                        explanation=(
                            f"Different severity ({sev_a} vs {sev_b}) for "
                            f"entries sharing tags {sorted(tags_a & tags_b)}"
                        ),
                        confidence=0.4,
                    ))

    report.pairs = candidate_pairs

    # --- Stage 2: LLM enrichment ---
    if llm_provider and candidate_pairs:
        pairs_text = "\n".join(
            f"Pair: [{p.entry_id_a}] vs [{p.entry_id_b}]\n"
            f"  Entry A rule: {entries.get(p.entry_id_a, {}).get('rule', 'N/A')}\n"
            f"  Entry B rule: {entries.get(p.entry_id_b, {}).get('rule', 'N/A')}\n"
            f"  Shared tags: {sorted(set(entries.get(p.entry_id_a, {}).get('tags', [])) & set(entries.get(p.entry_id_b, {}).get('tags', [])))}\n"
            f"  Heuristic type: {p.type}\n"
            for p in candidate_pairs
        )

        sys_prompt, user_prompt = contradiction_prompt(
            pairs_text, max_input_chars=rc["token_budget"],
        )
        response = _safe_llm_call(
            llm_provider, sys_prompt, user_prompt,
            max_tokens=rc["max_tokens"],
        )

        if response:
            parsed = _parse_llm_json(response.text)
            if parsed and "contradictions" in parsed:
                llm_pairs = []
                for c in parsed["contradictions"]:
                    if isinstance(c, dict):
                        eid_a = c.get("entry_id_a", "")
                        eid_b = c.get("entry_id_b", "")
                        if eid_a in entries and eid_b in entries:
                            llm_pairs.append(ContradictionPair(
                                entry_id_a=eid_a,
                                entry_id_b=eid_b,
                                type=c.get("type", "semantic"),
                                explanation=c.get("explanation", ""),
                                confidence=float(c.get("confidence", 0.7)),
                            ))
                if llm_pairs:
                    # Merge: keep heuristic pairs not covered by LLM
                    llm_keys = {(p.entry_id_a, p.entry_id_b) for p in llm_pairs}
                    merged = [p for p in report.pairs
                              if (p.entry_id_a, p.entry_id_b) not in llm_keys]
                    merged.extend(llm_pairs)
                    report.pairs = merged
                    report.mode = "llm_enriched"

    report.duration_ms = (time.monotonic() - t0) * 1000
    return report


# ---------------------------------------------------------------------------
# Core function 3: suggest_syntheses
# ---------------------------------------------------------------------------

def suggest_syntheses(
    entries: Dict[str, dict],
    config: dict,
    llm_provider: Optional[LLMProvider] = None,
) -> SynthesisReport:
    """
    Suggest consolidation of related entries into principles.

    Stage 1 — Heuristic: tag-based clustering.
    Stage 2 — LLM enrichment (optional): generate principle text.

    Args:
        entries: {entry_id: entry_dict}
        config: Full config dict.
        llm_provider: Optional LLM provider for enrichment.

    Returns:
        SynthesisReport with synthesis suggestions.
    """
    t0 = time.monotonic()
    rc = _get_reasoning_config(config)
    min_group = rc["synthesis_min_group_size"]

    report = SynthesisReport(total_entries=len(entries))

    if len(entries) < min_group:
        report.duration_ms = (time.monotonic() - t0) * 1000
        return report

    # --- Stage 1: Heuristic clustering by tag overlap ---
    # Group entries by their most common shared tags
    tag_to_entries: Dict[str, List[str]] = {}
    for eid, entry in entries.items():
        for tag in entry.get("tags", []):
            tag_to_entries.setdefault(tag, []).append(eid)

    # Find tag groups with enough entries
    clusters: List[Tuple[str, List[str]]] = []
    seen_eids: set = set()
    for tag, eids in sorted(tag_to_entries.items(), key=lambda x: -len(x[1])):
        unique_eids = [e for e in eids if e not in seen_eids]
        if len(unique_eids) >= min_group:
            clusters.append((tag, unique_eids))
            seen_eids.update(unique_eids)

    # Build suggestions from clusters
    heuristic_suggestions: List[SynthesisSuggestion] = []
    for tag, eids in clusters:
        heuristic_suggestions.append(SynthesisSuggestion(
            source_entry_ids=eids,
            proposed_title="",
            proposed_principle="",
            rationale=f"Cluster of {len(eids)} entries sharing tag '{tag}'",
        ))

    report.suggestions = heuristic_suggestions

    # --- Stage 2: LLM enrichment ---
    if llm_provider and heuristic_suggestions:
        cluster_text = ""
        for idx, sugg in enumerate(heuristic_suggestions):
            cluster_entries = [entries[eid] for eid in sugg.source_entry_ids if eid in entries]
            cluster_text += f"Cluster {idx + 1} ({sugg.rationale}):\n"
            cluster_text += _entries_to_compact_text(
                cluster_entries,
                max_chars=rc["token_budget"] // max(len(heuristic_suggestions), 1),
            )
            cluster_text += "\n"

        sys_prompt, user_prompt = synthesis_prompt(
            cluster_text, max_input_chars=rc["token_budget"],
        )
        response = _safe_llm_call(
            llm_provider, sys_prompt, user_prompt,
            max_tokens=rc["max_tokens"],
        )

        if response:
            parsed = _parse_llm_json(response.text)
            if parsed and "syntheses" in parsed:
                llm_suggestions = []
                for s in parsed["syntheses"]:
                    if isinstance(s, dict):
                        source_ids = s.get("source_entry_ids", [])
                        valid_ids = [eid for eid in source_ids if eid in entries]
                        if valid_ids:
                            llm_suggestions.append(SynthesisSuggestion(
                                source_entry_ids=valid_ids,
                                proposed_title=s.get("proposed_title", ""),
                                proposed_principle=s.get("proposed_principle", ""),
                                rationale=s.get("rationale", ""),
                            ))
                if llm_suggestions:
                    # Merge: keep heuristic suggestions not covered by LLM
                    llm_keys = {frozenset(s.source_entry_ids) for s in llm_suggestions}
                    merged = [s for s in report.suggestions
                              if frozenset(s.source_entry_ids) not in llm_keys]
                    merged.extend(llm_suggestions)
                    report.suggestions = merged
                    report.mode = "llm_enriched"

    report.duration_ms = (time.monotonic() - t0) * 1000
    return report


# ---------------------------------------------------------------------------
# Core function 4: assess_risks
# ---------------------------------------------------------------------------

def assess_risks(
    query: str,
    search_results: list,
    entries: Dict[str, dict],
    config: dict,
    llm_provider: Optional[LLMProvider] = None,
) -> RiskReport:
    """
    Assess risks for search results in context of the query.

    Stage 1 — Heuristic: confidence-based and source-based annotations.
    Stage 2 — LLM enrichment (optional): context-aware risk explanations.

    Args:
        query: Original search query.
        search_results: List of SearchResult-like objects with entry_id.
        entries: {entry_id: entry_dict}
        config: Full config dict.
        llm_provider: Optional LLM provider for enrichment.

    Returns:
        RiskReport with risk annotations.
    """
    t0 = time.monotonic()
    rc = _get_reasoning_config(config)

    report = RiskReport(query=query)

    if not search_results:
        report.duration_ms = (time.monotonic() - t0) * 1000
        return report

    # --- Stage 1: Heuristic annotations ---
    for result in search_results:
        eid = result.entry_id if hasattr(result, "entry_id") else result.get("entry_id", "")
        entry = entries.get(eid, {})
        if not entry:
            continue

        # Check for stale/old entries
        try:
            created_at = _parse_iso8601(entry.get("created_at", ""))
        except (ValueError, TypeError):
            created_at = None
        try:
            lv = entry.get("last_verified")
            last_verified = _parse_iso8601(lv) if lv else None
        except (ValueError, TypeError):
            last_verified = None
        ref_time = last_verified or created_at

        risk_level = "info"
        annotation = ""

        if ref_time:
            now = datetime.now(timezone.utc)
            days_old = (now - ref_time).days
            if days_old > 180:
                risk_level = "medium"
                annotation = f"Entry is {days_old} days old without recent verification"
            elif days_old > 90:
                risk_level = "low"
                annotation = f"Entry has not been verified in {days_old} days"

        # Check for superseded entries
        meta = entry.get("_meta", {})
        if meta and isinstance(meta, dict) and meta.get("superseded_by"):
            risk_level = "high"
            annotation = f"Entry superseded by {meta['superseded_by']} but not deprecated"

        # Check classification
        if entry.get("classification") == "hard" and entry.get("severity") == "S1":
            if risk_level == "info":
                risk_level = "info"
                annotation = "Critical rule (Hard/S1) — ensure compliance"

        if annotation:
            report.annotations.append(RiskAnnotation(
                entry_id=eid,
                risk_level=risk_level,
                annotation=annotation,
            ))

    # --- Stage 2: LLM enrichment ---
    if llm_provider and report.annotations:
        results_text = "\n".join(
            f"[{a.entry_id}] ({a.risk_level}) {a.annotation}"
            for a in report.annotations
        )

        sys_prompt, user_prompt = risk_prompt(
            query, results_text, "general context",
            max_input_chars=rc["token_budget"],
        )
        response = _safe_llm_call(
            llm_provider, sys_prompt, user_prompt,
            max_tokens=rc["max_tokens"],
        )

        if response:
            parsed = _parse_llm_json(response.text)
            if parsed and "annotations" in parsed:
                llm_annotations = []
                for a in parsed["annotations"]:
                    if isinstance(a, dict) and a.get("entry_id") in entries:
                        llm_annotations.append(RiskAnnotation(
                            entry_id=a["entry_id"],
                            risk_level=a.get("risk_level", "info"),
                            annotation=a.get("annotation", ""),
                            related_entry_ids=a.get("related_entry_ids", []),
                        ))
                if llm_annotations:
                    # Merge: keep heuristic annotations not covered by LLM
                    llm_keys = {a.entry_id for a in llm_annotations}
                    merged = [a for a in report.annotations
                              if a.entry_id not in llm_keys]
                    merged.extend(llm_annotations)
                    report.annotations = merged
                    report.mode = "llm_enriched"

    report.duration_ms = (time.monotonic() - t0) * 1000
    return report


# ---------------------------------------------------------------------------
# Core function 5: build_reasoning_report
# ---------------------------------------------------------------------------

def build_reasoning_report(
    events_path: Path,
    config: dict,
    project_root: Path,
    llm_provider: Optional[LLMProvider] = None,
    skip_correlations: bool = False,
    skip_contradictions: bool = False,
    skip_syntheses: bool = False,
) -> ReasoningReport:
    """
    Build a comprehensive reasoning report.

    Orchestrates all reasoning functions and aggregates results.

    Args:
        events_path: Path to events.jsonl.
        config: Full config dict.
        project_root: Project root for source verification.
        llm_provider: Optional LLM provider.
        skip_correlations: Skip correlation analysis.
        skip_contradictions: Skip contradiction detection.
        skip_syntheses: Skip synthesis suggestions.

    Returns:
        ReasoningReport with all sub-reports.
    """
    t0 = time.monotonic()

    # Load entries
    entries = _load_entries_latest_wins(events_path)
    # Filter deprecated
    active_entries = {
        eid: e for eid, e in entries.items()
        if not e.get("deprecated", False)
    }

    report = ReasoningReport(total_entries=len(active_entries))
    total_llm_calls = 0
    total_tokens = 0

    # Correlation
    if not skip_correlations:
        corr = find_correlations(active_entries, config, llm_provider)
        report.correlation_report = corr
        if corr.mode == "llm_enriched":
            total_llm_calls += 1

    # Contradiction
    if not skip_contradictions:
        contr = detect_contradictions(active_entries, config, llm_provider)
        report.contradiction_report = contr
        if contr.mode == "llm_enriched":
            total_llm_calls += 1

    # Synthesis
    if not skip_syntheses:
        synth = suggest_syntheses(active_entries, config, llm_provider)
        report.synthesis_report = synth
        if synth.mode == "llm_enriched":
            total_llm_calls += 1

    # Determine overall mode
    if any(
        r and r.mode == "llm_enriched"
        for r in [report.correlation_report, report.contradiction_report, report.synthesis_report]
    ):
        report.mode = "llm_enriched"

    report.llm_calls = total_llm_calls
    report.llm_tokens_used = total_tokens
    report.duration_ms = (time.monotonic() - t0) * 1000

    return report


# ---------------------------------------------------------------------------
# Search integration
# ---------------------------------------------------------------------------

def annotate_search_results(
    search_results: list,
    entries: Dict[str, dict],
    config: dict,
    llm_provider: Optional[LLMProvider] = None,
    query: str = "",
) -> List[dict]:
    """
    Generate risk annotations for search results.

    This is the interface for search.py integration.
    Returns a list of annotation dicts.

    Args:
        search_results: List of SearchResult objects.
        entries: {entry_id: entry_dict}
        config: Full config dict.
        llm_provider: Optional LLM provider.
        query: Original search query.

    Returns:
        List of annotation dicts: [{entry_id, risk_level, annotation}]
    """
    risk_report = assess_risks(query, search_results, entries, config, llm_provider)
    return [
        {
            "entry_id": a.entry_id,
            "risk_level": a.risk_level,
            "annotation": a.annotation,
            "related_entry_ids": a.related_entry_ids,
        }
        for a in risk_report.annotations
    ]
