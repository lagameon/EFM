"""
EF Memory V3 — Auto-Sync (Pipeline Orchestration)

Orchestrates the automation pipeline:
  - run_pipeline: sync embeddings + generate rules + evolution + reasoning + harvest
  - check_startup: lightweight health check for session start (incl. session recovery)

Reuses existing modules:
  - sync.sync_embeddings (M1)
  - generate_rules.generate_rule_files (M3)
  - auto_verify.check_staleness, verify_source (M4.1)
  - auto_capture.list_drafts (M4.2)
  - evolution.build_evolution_report (M5)
  - reasoning.build_reasoning_report (M6)
  - working_memory.harvest_session, get_session_status (M8)

No external dependencies — pure Python stdlib + internal modules.
"""

import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from datetime import datetime, timezone

from .auto_capture import expire_stale_drafts, list_drafts
from .auto_verify import (
    _load_entries_latest_wins,
    _parse_source_ref,
    check_staleness,
)

logger = logging.getLogger("efm.auto_sync")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Result of a single pipeline step."""
    step: str = ""               # "sync_embeddings" | "generate_rules" | "evolution_check" | "reasoning_check" | "harvest_check"
    success: bool = True
    skipped: bool = False
    skip_reason: str = ""
    error: str = ""
    details: Dict = field(default_factory=dict)


@dataclass
class PipelineReport:
    """Report from running the full automation pipeline."""
    steps_run: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0
    steps_skipped: int = 0
    step_results: List[StepResult] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class StartupReport:
    """Report for the startup health check hint."""
    pending_drafts: int = 0
    drafts_expired: int = 0
    oldest_draft_age_days: int = 0
    stale_entries: int = 0
    source_warnings: int = 0
    total_entries: int = 0
    active_session: bool = False
    active_session_task: str = ""
    active_session_phases: str = ""       # e.g., "1/3 done"
    staleness_threshold_days: int = 90    # Used in hint formatting
    compaction_suggested: bool = False
    waste_ratio: float = 0.0
    hint: str = ""
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    events_path: Path,
    config: dict,
    project_root: Path,
    steps: Optional[List[str]] = None,
) -> PipelineReport:
    """
    Run the automation pipeline with specified steps.

    Available steps (in order):
        "sync_embeddings"  — sync events.jsonl -> vectors.db (FTS + optional vectors)
        "generate_rules"   — regenerate .claude/rules/ef-memory/*.md from Hard entries
        "evolution_check"  — run evolution report (duplicates, confidence, deprecations)
        "reasoning_check"  — run LLM reasoning report (correlations, contradictions, synthesis)
        "harvest_check"    — scan working memory for harvestable candidates (M9)

    Each step is isolated: failure in one doesn't block others.
    Embedding disabled → sync still updates FTS index.
    """
    report = PipelineReport()
    start_time = time.monotonic()

    # Resolve steps
    if steps is None:
        steps = config.get("automation", {}).get(
            "pipeline_steps",
            ["sync_embeddings", "generate_rules"],
        )

    for step_name in steps:
        report.steps_run += 1

        if step_name == "sync_embeddings":
            result = _run_sync_step(events_path, config)
        elif step_name == "generate_rules":
            result = _run_rules_step(events_path, config, project_root)
        elif step_name == "evolution_check":
            result = _run_evolution_step(events_path, config, project_root)
        elif step_name == "reasoning_check":
            result = _run_reasoning_step(events_path, config, project_root)
        elif step_name == "harvest_check":
            result = _run_harvest_step(events_path, config, project_root)
        else:
            result = StepResult(
                step=step_name,
                success=False,
                error=f"Unknown step: {step_name}",
            )

        report.step_results.append(result)

        if result.skipped:
            report.steps_skipped += 1
        elif result.success:
            report.steps_succeeded += 1
        else:
            report.steps_failed += 1

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report


def _run_sync_step(events_path: Path, config: dict) -> StepResult:
    """Run the sync_embeddings step."""
    result = StepResult(step="sync_embeddings")

    db = None
    try:
        from .embedder import create_embedder
        from .sync import sync_embeddings
        from .vectordb import VectorDB

        # Resolve DB path
        embedding_config = config.get("embedding", {})
        db_path_str = embedding_config.get("storage", {}).get(
            "db_path", ".memory/vectors.db"
        )
        # Resolve relative to events.jsonl parent (which is .memory/)
        memory_dir = events_path.parent
        db_path = memory_dir / Path(db_path_str).name

        # Open VectorDB
        db = VectorDB(db_path)
        db.open()
        db.ensure_schema()

        # Try to create embedder (None if disabled)
        embedder = None
        if embedding_config.get("enabled", False):
            try:
                embedder = create_embedder(embedding_config)
            except Exception as e:
                logger.warning(f"Embedder not available: {e}")

        # Run sync
        batch_size = embedding_config.get("sync", {}).get("batch_size", 20)
        sync_report = sync_embeddings(
            events_path=events_path,
            vectordb=db,
            embedder=embedder,
            batch_size=batch_size,
        )

        result.success = len(sync_report.errors) == 0
        result.details = {
            "mode": sync_report.mode,
            "scanned": sync_report.entries_scanned,
            "added": sync_report.entries_added,
            "updated": sync_report.entries_updated,
            "skipped": sync_report.entries_skipped,
            "fts_only": sync_report.entries_fts_only,
            "errors": sync_report.errors,
        }

    except Exception as e:
        result.success = False
        result.error = str(e)
        logger.error(f"sync_embeddings step failed: {e}")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    return result


def _run_rules_step(
    events_path: Path,
    config: dict,
    project_root: Path,
) -> StepResult:
    """Run the generate_rules step."""
    result = StepResult(step="generate_rules")

    try:
        from .generate_rules import generate_rule_files

        output_dir = project_root / ".claude" / "rules" / "ef-memory"

        gen_report = generate_rule_files(
            events_path=events_path,
            output_dir=output_dir,
            config=config,
        )

        result.success = True
        result.details = {
            "entries_scanned": gen_report.entries_scanned,
            "entries_hard": gen_report.entries_hard,
            "entries_injected": gen_report.entries_injected,
            "files_written": [str(f) for f in gen_report.files_written],
            "domains": dict(gen_report.domains),
        }

    except Exception as e:
        result.success = False
        result.error = str(e)
        logger.error(f"generate_rules step failed: {e}")

    return result


def _run_evolution_step(
    events_path: Path,
    config: dict,
    project_root: Path,
) -> StepResult:
    """Run the evolution_check step."""
    result = StepResult(step="evolution_check")

    try:
        from .evolution import build_evolution_report

        evo_report = build_evolution_report(
            events_path=events_path,
            config=config,
            project_root=project_root,
        )

        result.success = True
        result.details = {
            "total_entries": evo_report.total_entries,
            "active_entries": evo_report.active_entries,
            "health_score": round(evo_report.health_score, 3),
            "duplicate_groups": len(evo_report.duplicate_report.groups) if evo_report.duplicate_report else 0,
            "deprecation_candidates": len(evo_report.deprecation_report.candidates) if evo_report.deprecation_report else 0,
            "merge_suggestions": len(evo_report.merge_suggestions),
            "confidence_high": evo_report.entries_high_confidence,
            "confidence_medium": evo_report.entries_medium_confidence,
            "confidence_low": evo_report.entries_low_confidence,
        }

    except Exception as e:
        result.success = False
        result.error = str(e)
        logger.error(f"evolution_check step failed: {e}")

    return result


def _run_reasoning_step(
    events_path: Path,
    config: dict,
    project_root: Path,
) -> StepResult:
    """Run the reasoning_check step (M6 LLM reasoning analysis)."""
    result = StepResult(step="reasoning_check")

    try:
        from .reasoning import build_reasoning_report
        from .llm_provider import create_llm_provider

        # Optionally create LLM provider
        llm_provider = None
        reasoning_config = config.get("reasoning", {})
        if reasoning_config.get("enabled", False):
            try:
                llm_provider = create_llm_provider(reasoning_config)
            except Exception as e:
                logger.warning(f"LLM provider not available: {e}")

        reasoning_report = build_reasoning_report(
            events_path=events_path,
            config=config,
            project_root=project_root,
            llm_provider=llm_provider,
        )

        result.success = True
        result.details = {
            "total_entries": reasoning_report.total_entries,
            "mode": reasoning_report.mode,
            "llm_calls": reasoning_report.llm_calls,
            "llm_tokens_used": reasoning_report.llm_tokens_used,
            "correlation_groups": len(reasoning_report.correlation_report.groups) if reasoning_report.correlation_report else 0,
            "contradiction_pairs": len(reasoning_report.contradiction_report.pairs) if reasoning_report.contradiction_report else 0,
            "synthesis_suggestions": len(reasoning_report.synthesis_report.suggestions) if reasoning_report.synthesis_report else 0,
        }

    except Exception as e:
        result.success = False
        result.error = str(e)
        logger.error(f"reasoning_check step failed: {e}")

    return result


def _run_harvest_step(
    events_path: Path,
    config: dict,
    project_root: Path,
) -> StepResult:
    """Run the harvest_check step (M9 — scan working memory for candidates)."""
    result = StepResult(step="harvest_check")

    try:
        from .working_memory import harvest_session, get_session_status

        v3_config = config.get("v3", {})
        working_dir_rel = v3_config.get("working_memory_dir", ".memory/working")
        working_dir = project_root / working_dir_rel

        # Check if there's an active session
        status = get_session_status(working_dir)
        if not status.active:
            result.skipped = True
            result.skip_reason = "No active working memory session"
            return result

        # Run harvest
        harvest_report = harvest_session(working_dir, events_path, config)

        result.success = True
        result.details = {
            "candidates_found": len(harvest_report.candidates),
            "findings_scanned": harvest_report.findings_scanned,
            "progress_scanned": harvest_report.progress_scanned,
            "candidate_types": _count_candidate_types(harvest_report.candidates),
        }

    except Exception as e:
        result.success = False
        result.error = str(e)
        logger.error(f"harvest_check step failed: {e}")

    return result


def _count_candidate_types(candidates) -> dict:
    """Count candidates by type for reporting."""
    counts: Dict[str, int] = {}
    for c in candidates:
        counts[c.suggested_type] = counts.get(c.suggested_type, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Startup health check
# ---------------------------------------------------------------------------

def check_startup(
    events_path: Path,
    drafts_dir: Path,
    project_root: Path,
    config: dict,
) -> StartupReport:
    """
    Lightweight startup health check. Must be fast (<100ms).

    Checks:
    1. Count pending drafts
    2. Count stale entries (>threshold days)
    3. Spot-check source file existence on a sample of entries
    4. Detect active working memory session (session recovery)
    5. Format startup hint string
    """
    report = StartupReport()
    start_time = time.monotonic()

    # 1. Auto-expire stale drafts, then count remaining
    try:
        v3_config_drafts = config.get("v3", {})
        expire_days = v3_config_drafts.get("draft_auto_expire_days", 7)
        if expire_days > 0:
            expired = expire_stale_drafts(drafts_dir, expire_days)
            report.drafts_expired = len(expired)

        drafts = list_drafts(drafts_dir)
        report.pending_drafts = len(drafts)

        # Calculate oldest draft age
        if drafts:
            now = datetime.now(timezone.utc)
            oldest_ts = drafts[0].capture_timestamp  # sorted oldest-first
            if oldest_ts:
                try:
                    ts = datetime.fromisoformat(oldest_ts)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    report.oldest_draft_age_days = (now - ts).days
                except (ValueError, TypeError):
                    pass
    except Exception:
        report.pending_drafts = 0

    # 2. Load entries and count stale
    entries = _load_entries_latest_wins(events_path)
    active_entries = {
        eid: e for eid, e in entries.items()
        if not e.get("deprecated", False)
    }
    report.total_entries = len(active_entries)

    # Compaction stats (fast, read-only)
    try:
        from .compaction import get_compaction_stats
        compact_threshold = config.get("compaction", {}).get(
            "auto_suggest_threshold", 2.0
        )
        compact_stats = get_compaction_stats(events_path, threshold=compact_threshold)
        report.compaction_suggested = compact_stats.suggest_compact
        report.waste_ratio = compact_stats.waste_ratio
    except Exception:
        pass  # Compaction module unavailable — skip silently

    threshold = config.get("verify", {}).get("staleness_threshold_days", 90)
    report.staleness_threshold_days = threshold
    for entry in active_entries.values():
        staleness = check_staleness(entry, threshold)
        if staleness.stale:
            report.stale_entries += 1

    # 3. Spot-check source file existence (fast, no git)
    sample_size = config.get("automation", {}).get(
        "startup_source_sample_size", 10
    )
    entries_to_check = list(active_entries.values())
    if len(entries_to_check) > sample_size:
        entries_to_check = random.sample(entries_to_check, sample_size)

    source_issues = 0
    for entry in entries_to_check:
        for src in entry.get("source", []):
            src_type, file_path, _, _ = _parse_source_ref(src)
            if src_type in ("code", "markdown", "function") and file_path:
                full_path = project_root / file_path
                try:
                    if not full_path.exists():
                        source_issues += 1
                        break  # One issue per entry is enough
                except OSError:
                    pass

    report.source_warnings = source_issues

    # 4. Session recovery — detect active working memory session
    try:
        v3_config = config.get("v3", {})
        if not v3_config.get("session_recovery", True):
            raise RuntimeError("session_recovery disabled")  # Skip to except

        from .working_memory import get_session_status as _get_wm_status

        working_dir_rel = v3_config.get("working_memory_dir", ".memory/working")
        working_dir = project_root / working_dir_rel
        wm_status = _get_wm_status(working_dir)

        if wm_status.active:
            report.active_session = True
            report.active_session_task = wm_status.task_description
            report.active_session_phases = (
                f"{wm_status.phases_done}/{wm_status.phases_total} done"
            )
    except Exception:
        pass  # Working memory module not available — skip silently

    # 5. Format hint
    report.hint = _format_hint(report)

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report


def _format_hint(report: StartupReport) -> str:
    """Format the startup hint string."""
    parts = []

    if report.active_session:
        task_preview = report.active_session_task[:50]
        parts.append(f"active session: \"{task_preview}\" ({report.active_session_phases})")

    if report.drafts_expired > 0 and report.pending_drafts > 0:
        parts.append(
            f"auto-expired {report.drafts_expired} stale drafts, "
            f"{report.pending_drafts} pending (oldest: {report.oldest_draft_age_days}d, review: /memory-save)"
        )
    elif report.drafts_expired > 0:
        parts.append(f"auto-expired {report.drafts_expired} stale drafts")
    elif report.pending_drafts > 0:
        age_suffix = f" (oldest: {report.oldest_draft_age_days}d, review: /memory-save)" if report.oldest_draft_age_days > 0 else " (review: /memory-save)"
        parts.append(f"{report.pending_drafts} pending drafts{age_suffix}")

    if report.source_warnings > 0:
        parts.append(f"{report.source_warnings} source warnings")

    if report.stale_entries > 0:
        parts.append(f"{report.stale_entries} stale entries (>{report.staleness_threshold_days}d)")

    if report.compaction_suggested:
        parts.append(f"compact suggested ({report.waste_ratio:.1f}x waste)")

    if parts:
        return f"EF Memory: {' / '.join(parts)}"
    else:
        return f"EF Memory: {report.total_entries} entries, all healthy"
