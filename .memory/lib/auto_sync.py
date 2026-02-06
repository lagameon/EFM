"""
EF Memory V2 — Auto-Sync (Pipeline Orchestration)

Orchestrates the automation pipeline:
  - run_pipeline: sync embeddings + generate rules (steps are isolated)
  - check_startup: lightweight health check for session start

Reuses existing modules:
  - sync.sync_embeddings (M1)
  - generate_rules.generate_rule_files (M3)
  - auto_verify.check_staleness, verify_source (M4.1)
  - auto_capture.list_drafts (M4.2)

No external dependencies — pure Python stdlib + internal modules.
"""

import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .auto_capture import list_drafts
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
    step: str = ""               # "sync_embeddings" | "generate_rules"
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
    stale_entries: int = 0
    source_warnings: int = 0
    total_entries: int = 0
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
        "sync_embeddings" — sync events.jsonl -> vectors.db (FTS + optional vectors)
        "generate_rules"  — regenerate .claude/rules/ef-memory/*.md from Hard entries

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
                embedder = create_embedder(config)
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

        db.close()

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
    4. Format startup hint string
    """
    report = StartupReport()
    start_time = time.monotonic()

    # 1. Pending drafts
    try:
        drafts = list_drafts(drafts_dir)
        report.pending_drafts = len(drafts)
    except Exception:
        report.pending_drafts = 0

    # 2. Load entries and count stale
    entries = _load_entries_latest_wins(events_path)
    active_entries = {
        eid: e for eid, e in entries.items()
        if not e.get("deprecated", False)
    }
    report.total_entries = len(active_entries)

    threshold = config.get("verify", {}).get("staleness_threshold_days", 90)
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

    # 4. Format hint
    report.hint = _format_hint(report)

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report


def _format_hint(report: StartupReport) -> str:
    """Format the startup hint string."""
    parts = []

    if report.pending_drafts > 0:
        parts.append(f"{report.pending_drafts} \u6761\u5f85\u5ba1\u8bb0\u5fc6")

    if report.source_warnings > 0:
        parts.append(f"{report.source_warnings} \u6761 source \u544a\u8b66")

    if report.stale_entries > 0:
        parts.append(f"{report.stale_entries} \u6761\u8fc7\u671f (>90\u5929)")

    if parts:
        return f"\u53d1\u73b0 {' / '.join(parts)}"
    else:
        return f"EF Memory: {report.total_entries} entries, all healthy"
