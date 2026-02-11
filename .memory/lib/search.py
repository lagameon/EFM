"""
EF Memory V2 — Hybrid Search Engine

Four-level degradation search:
  Level 1: Hybrid   — BM25 + Vector + Re-rank (embedder + FTS5)
  Level 2: Vector   — Pure semantic search     (embedder, no FTS5)
  Level 3: Keyword  — Pure BM25 full-text      (FTS5, no embedder)
  Level 4: Basic    — Token overlap on JSONL    (zero dependencies)

Score formula (hybrid mode):
  final_score = bm25_weight × bm25_norm + vector_weight × vector_norm + boost

Boost table (from config):
  hard + S1: +0.15    hard + S2: +0.10    hard + S3: +0.05
  soft:      +0.00

No external dependencies — pure Python stdlib + M1 lib modules.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

# Lazy imports: embedder/vectordb/text_builder are heavy modules (sqlite3, HTTP
# clients, etc.) that are NOT needed for basic search mode.  By deferring them
# to the functions that actually use them, the pre_edit_search hook (which always
# degrades to basic mode) avoids ~20-40 ms of unnecessary import overhead.
if TYPE_CHECKING:
    from .embedder import EmbeddingProvider
    from .text_builder import build_query_text  # noqa: F401
    from .vectordb import VectorDB

logger = logging.getLogger("efm.search")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """A single search result with scoring breakdown."""
    entry_id: str
    entry: dict               # Full entry data from events.jsonl
    score: float              # Final composite score
    bm25_score: float = 0.0   # BM25 score (0 if unavailable)
    vector_score: float = 0.0 # Vector similarity (0 if unavailable)
    boost: float = 0.0        # Classification + severity boost
    confidence_boost: float = 0.0  # Confidence-based boost
    search_mode: str = ""     # "hybrid" | "vector" | "keyword" | "basic"


@dataclass
class SearchReport:
    """Summary of a search operation."""
    query: str
    mode: str                 # Actual search mode used
    total_found: int = 0
    results: List[SearchResult] = field(default_factory=list)
    degraded: bool = False    # Whether degradation occurred
    degradation_reason: str = ""
    duration_ms: float = 0.0
    reasoning_annotations: Optional[List[dict]] = None  # M6 risk annotations


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_search_weights(config: dict) -> dict:
    """Extract search weights from config, with defaults."""
    embedding_search = config.get("embedding", {}).get("search", {})
    return {
        "bm25_weight": embedding_search.get("bm25_weight", 0.4),
        "vector_weight": embedding_search.get("vector_weight", 0.6),
        "hard_s1_boost": embedding_search.get("hard_s1_boost", 0.15),
        "hard_s2_boost": embedding_search.get("hard_s2_boost", 0.10),
        "hard_s3_boost": embedding_search.get("hard_s3_boost", 0.05),
        "min_score": embedding_search.get("min_score", 0.1),
        "confidence_weight": embedding_search.get("confidence_weight", 0.1),
    }


def _compute_boost(entry: dict, weights: dict) -> float:
    """Compute classification + severity boost for an entry."""
    classification = entry.get("classification", "")
    severity = entry.get("severity", "")

    if classification.lower() != "hard":
        return 0.0

    boost_map = {
        "S1": weights["hard_s1_boost"],
        "S2": weights["hard_s2_boost"],
        "S3": weights["hard_s3_boost"],
    }
    return boost_map.get(severity, 0.0)


def _compute_confidence_boost(entry: dict, weights: dict) -> float:
    """Compute confidence-based boost from entry metadata."""
    confidence = entry.get("_meta", {}).get("confidence", 0.5)
    return weights.get("confidence_weight", 0.1) * confidence


# ---------------------------------------------------------------------------
# Entry loader (events.jsonl → dict by entry_id)
# ---------------------------------------------------------------------------

def _load_entries(events_path: Path) -> Dict[str, dict]:
    """
    Load all entries from events.jsonl, resolving latest-wins.

    Returns {entry_id: latest_entry_dict}, excluding deprecated entries.
    Thin wrapper around :func:`events_io.load_events_latest_wins`.
    """
    from .events_io import load_events_latest_wins
    entries, _total, _offset = load_events_latest_wins(events_path)
    # Filter out deprecated entries
    return {
        eid: e for eid, e in entries.items()
        if not e.get("deprecated", False)
    }


# ---------------------------------------------------------------------------
# Search modes
# ---------------------------------------------------------------------------

def _search_hybrid(
    query: str,
    vectordb: "VectorDB",
    embedder: "EmbeddingProvider",
    entries: Dict[str, dict],
    weights: dict,
    context: Optional[dict],
    max_results: int,
) -> List[SearchResult]:
    """
    Level 1: Hybrid search — BM25 + Vector + Re-rank.

    Combines FTS5 keyword matching with semantic vector similarity.
    """
    from .text_builder import build_query_text

    fetch_limit = max_results * 3  # Over-fetch for merging

    # BM25 scores (already normalized relative to result set by search_fts)
    bm25_raw = vectordb.search_fts(query, limit=fetch_limit)
    bm25_map: Dict[str, float] = {eid: score for eid, score in bm25_raw}

    # Vector scores
    query_text = build_query_text(query, context)
    query_result = embedder.embed_query(query_text)
    vec_raw = vectordb.search_vectors(
        query_result.vector, limit=fetch_limit, exclude_deprecated=True,
    )
    # Normalize vector scores to [0, 1] relative to result set
    vec_map: Dict[str, float] = {}
    if vec_raw:
        raw_sims = [(eid, (sim + 1.0) / 2.0) for eid, sim in vec_raw]
        max_vec = max(s for _, s in raw_sims) if raw_sims else 1.0
        for eid, s in raw_sims:
            vec_map[eid] = s / max_vec if max_vec > 0 else 0.0

    # Union of candidate IDs
    candidate_ids = set(bm25_map.keys()) | set(vec_map.keys())

    # Compute composite scores
    results: List[SearchResult] = []
    for eid in candidate_ids:
        if eid not in entries:
            continue  # Skip if entry not in current JSONL (or deprecated)

        entry = entries[eid]
        bm25_s = bm25_map.get(eid, 0.0)
        vec_s = vec_map.get(eid, 0.0)
        boost = _compute_boost(entry, weights)
        conf_boost = _compute_confidence_boost(entry, weights)

        score = (
            weights["bm25_weight"] * bm25_s
            + weights["vector_weight"] * vec_s
            + boost
            + conf_boost
        )

        results.append(SearchResult(
            entry_id=eid,
            entry=entry,
            score=score,
            bm25_score=bm25_s,
            vector_score=vec_s,
            boost=boost,
            confidence_boost=conf_boost,
            search_mode="hybrid",
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:max_results]


def _search_vector(
    query: str,
    vectordb: "VectorDB",
    embedder: "EmbeddingProvider",
    entries: Dict[str, dict],
    weights: dict,
    context: Optional[dict],
    max_results: int,
) -> List[SearchResult]:
    """
    Level 2: Pure vector search (no FTS5 available).
    """
    from .text_builder import build_query_text

    fetch_limit = max_results * 3

    query_text = build_query_text(query, context)
    query_result = embedder.embed_query(query_text)
    vec_raw = vectordb.search_vectors(
        query_result.vector, limit=fetch_limit, exclude_deprecated=True,
    )

    results: List[SearchResult] = []
    for eid, sim in vec_raw:
        if eid not in entries:
            continue

        entry = entries[eid]
        vec_s = (sim + 1.0) / 2.0  # Normalize to [0, 1]
        boost = _compute_boost(entry, weights)
        conf_boost = _compute_confidence_boost(entry, weights)
        score = vec_s + boost + conf_boost

        results.append(SearchResult(
            entry_id=eid,
            entry=entry,
            score=score,
            vector_score=vec_s,
            boost=boost,
            confidence_boost=conf_boost,
            search_mode="vector",
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:max_results]


def _search_keyword(
    query: str,
    vectordb: "VectorDB",
    entries: Dict[str, dict],
    weights: dict,
    max_results: int,
) -> List[SearchResult]:
    """
    Level 3: Pure BM25 keyword search (no embedder available).
    """
    fetch_limit = max_results * 3

    bm25_raw = vectordb.search_fts(query, limit=fetch_limit)

    results: List[SearchResult] = []
    for eid, bm25_s in bm25_raw:
        if eid not in entries:
            continue

        entry = entries[eid]
        boost = _compute_boost(entry, weights)
        conf_boost = _compute_confidence_boost(entry, weights)
        score = bm25_s + boost + conf_boost

        results.append(SearchResult(
            entry_id=eid,
            entry=entry,
            score=score,
            bm25_score=bm25_s,
            boost=boost,
            confidence_boost=conf_boost,
            search_mode="keyword",
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:max_results]


def _search_basic(
    query: str,
    entries: Dict[str, dict],
    weights: dict,
    max_results: int,
) -> List[SearchResult]:
    """
    Level 4: Basic token-overlap search on in-memory entries.

    Zero external dependencies. Fallback when both embedder and FTS5
    are unavailable.
    """
    if not query.strip():
        return []

    # Tokenize query (lowercase, split on non-word characters)
    query_tokens = set(re.findall(r'\w+', query.lower()))

    results: List[SearchResult] = []
    for eid, entry in entries.items():
        # Build searchable text from entry fields
        text_parts = []
        title = entry.get("title", "")
        if title:
            text_parts.append(title)
        rule = entry.get("rule")
        if rule:
            text_parts.append(rule)
        implication = entry.get("implication")
        if implication:
            text_parts.append(implication)
        content = entry.get("content", [])
        if isinstance(content, list):
            text_parts.extend(item for item in content if item)
        tags = entry.get("tags", [])
        if isinstance(tags, list):
            text_parts.extend(tags)

        full_text = " ".join(text_parts).lower()
        entry_tokens = set(re.findall(r'\w+', full_text))

        # Compute overlap ratio
        if not entry_tokens:
            continue

        overlap = query_tokens & entry_tokens
        if not overlap:
            continue

        # Score: weighted overlap (matching more query tokens = higher)
        # Jaccard-inspired: |overlap| / |query_tokens|
        overlap_ratio = len(overlap) / len(query_tokens)

        # Bonus: title match is worth more
        title_lower = title.lower()
        title_bonus = 0.0
        for token in query_tokens:
            if token in title_lower:
                title_bonus += 0.1

        boost = _compute_boost(entry, weights)
        conf_boost = _compute_confidence_boost(entry, weights)
        score = overlap_ratio + title_bonus + boost + conf_boost

        results.append(SearchResult(
            entry_id=eid,
            entry=entry,
            score=score,
            boost=boost,
            confidence_boost=conf_boost,
            search_mode="basic",
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:max_results]


# ---------------------------------------------------------------------------
# Main search function
# ---------------------------------------------------------------------------

def _determine_mode(
    vectordb: Optional["VectorDB"],
    embedder: Optional["EmbeddingProvider"],
    force_mode: Optional[str] = None,
) -> tuple[str, bool, str]:
    """
    Determine the best available search mode.

    Returns: (mode, degraded, reason)
    """
    if force_mode:
        # Validate that required components are available for the forced mode
        has_embedder = embedder is not None
        has_vectordb = vectordb is not None
        if force_mode == "hybrid" and (not has_embedder or not has_vectordb):
            return "basic", True, f"Cannot use hybrid mode: missing {'embedder' if not has_embedder else 'vectordb'}"
        if force_mode == "vector" and (not has_embedder or not has_vectordb):
            return "basic", True, f"Cannot use vector mode: missing {'embedder' if not has_embedder else 'vectordb'}"
        if force_mode == "keyword" and not has_vectordb:
            return "basic", True, "Cannot use keyword mode: missing vectordb"
        return force_mode, False, ""

    has_embedder = embedder is not None
    has_fts = (
        vectordb is not None
        and hasattr(vectordb, "_fts5_available")
        and vectordb._fts5_available
    )
    has_vectordb = vectordb is not None

    if has_embedder and has_fts and has_vectordb:
        return "hybrid", False, ""
    elif has_embedder and has_vectordb:
        return "vector", True, "FTS5 not available; using vector-only search"
    elif has_fts and has_vectordb:
        return "keyword", True, "No embedding provider; using keyword search"
    else:
        return "basic", True, "No vector DB or embedding provider; using basic text match"


def search_memory(
    query: str,
    events_path: Path,
    vectordb: Optional["VectorDB"] = None,
    embedder: Optional["EmbeddingProvider"] = None,
    config: Optional[dict] = None,
    context: Optional[dict] = None,
    max_results: Optional[int] = None,
    force_mode: Optional[str] = None,
) -> SearchReport:
    """
    Search project memory with four-level degradation.

    Args:
        query: Search query string.
        events_path: Path to events.jsonl.
        vectordb: Open VectorDB instance (None = basic mode).
        embedder: Embedding provider (None = keyword/basic mode).
        config: Full config dict (for search weights).
        context: Optional context dict {current_file, tags}.
        max_results: Maximum results to return.
        force_mode: Force a specific mode ("hybrid", "vector", "keyword", "basic").

    Returns:
        SearchReport with results and metadata.
    """
    start_time = time.monotonic()
    config = config or {}
    weights = _get_search_weights(config)

    # Determine search mode
    mode, degraded, reason = _determine_mode(vectordb, embedder, force_mode)

    report = SearchReport(
        query=query,
        mode=mode,
        degraded=degraded,
        degradation_reason=reason,
    )

    # Load entries from events.jsonl
    entries = _load_entries(events_path)
    if not entries:
        report.duration_ms = (time.monotonic() - start_time) * 1000
        return report

    # Apply max_results: caller override > config > default 5
    config_max = config.get("search", {}).get("max_results", 5)
    effective_max = max_results if max_results is not None else config_max

    # Execute search based on mode
    try:
        if mode == "hybrid":
            results = _search_hybrid(
                query, vectordb, embedder, entries, weights, context, effective_max,
            )
        elif mode == "vector":
            results = _search_vector(
                query, vectordb, embedder, entries, weights, context, effective_max,
            )
        elif mode == "keyword":
            results = _search_keyword(
                query, vectordb, entries, weights, effective_max,
            )
        else:  # basic
            results = _search_basic(
                query, entries, weights, effective_max,
            )
    except Exception as e:
        logger.error(f"Search failed in {mode} mode: {e}")
        # Fall back to basic if any mode fails
        if mode != "basic":
            logger.info("Falling back to basic search mode")
            results = _search_basic(query, entries, weights, effective_max)
            report.mode = "basic"
            report.degraded = True
            report.degradation_reason = f"{mode} search failed: {e}; fell back to basic"
        else:
            results = []

    # Apply min_score filter
    min_score = weights["min_score"]
    results = [r for r in results if r.score >= min_score]

    report.results = results
    report.total_found = len(results)
    report.duration_ms = (time.monotonic() - start_time) * 1000

    return report
