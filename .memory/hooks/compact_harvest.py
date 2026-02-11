#!/usr/bin/env python3
"""
EFM — PreCompact Hook (Emergency Harvest)

Fires when Claude Code is about to compact context. This is the last
chance to capture memories before context is lost.

Strategy:
  1. If active working memory session → auto_harvest_and_persist (skip pipeline)
  2. If no session → scan conversation transcript → drafts
  3. Write marker file to prevent double-harvest if Stop fires after

Designed to be fast. Skips pipeline (sync/rules/evolution) — it runs
on next SessionStart anyway.
"""

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger("efm.compact_harvest")

_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
_PROJECT_ROOT = _MEMORY_DIR.parent

MAX_STDIN_SIZE = 10 * 1024 * 1024  # 10 MB

ECHO_REMINDER = (
    "[EFM] Before compacting: consider /memory-save if you discovered "
    "lessons. Check .memory/working/ for active sessions."
)


def main():
    # --- Parse stdin ---
    try:
        raw_input = sys.stdin.read(MAX_STDIN_SIZE + 1)
        if len(raw_input) > MAX_STDIN_SIZE:
            sys.exit(0)
        input_data = json.loads(raw_input)
    except (json.JSONDecodeError, OSError):
        input_data = {}

    # --- Load config ---
    config_path = _MEMORY_DIR / "config.json"
    try:
        sys.path.insert(0, str(_MEMORY_DIR))
        from lib.config_presets import load_config
        config = load_config(config_path)
    except Exception as e:
        logger.warning("Config load failed, using defaults: %s", e)
        config = {}

    v3_config = config.get("v3", {})

    # Check if harvest_on_compact is enabled
    if not v3_config.get("harvest_on_compact", True):
        # Disabled: fall back to echo reminder
        print(json.dumps({"additionalContext": ECHO_REMINDER}))
        sys.exit(0)

    working_dir_rel = v3_config.get("working_memory_dir", ".memory/working")
    working_dir = _PROJECT_ROOT / working_dir_rel
    findings_path = working_dir / "findings.md"
    progress_path = working_dir / "progress.md"

    has_session = findings_path.exists() or progress_path.exists()

    # Ensure working dir exists (for marker file)
    working_dir.mkdir(parents=True, exist_ok=True)
    compact_marker = working_dir / ".compact_harvested"

    # Already harvested in this compaction cycle? Skip.
    if compact_marker.exists():
        sys.exit(0)

    output_parts = []

    if has_session:
        # --- Path A: Active working memory session ---
        try:
            from lib.working_memory import auto_harvest_and_persist, is_session_complete

            session_complete = is_session_complete(working_dir)
            require_complete = v3_config.get("require_complete_for_harvest", False)
            conversation_id = input_data.get("conversation_id")
            events_path = _MEMORY_DIR / "events.jsonl"

            report = auto_harvest_and_persist(
                working_dir=working_dir,
                events_path=events_path,
                project_root=_PROJECT_ROOT,
                config=config,
                run_pipeline_after=False,  # Skip pipeline for speed
                draft_only=require_complete and not session_complete,
                conversation_id=conversation_id,
            )

            status_label = "complete" if session_complete else "partial"
            lines = [f"[EFM] Pre-compact harvest ({status_label}):"]
            lines.append(f"  Candidates: {report['candidates_found']}")
            lines.append(f"  Written: {report['entries_written']}")
            if report.get("entries_drafted", 0):
                lines.append(f"  Drafted (low confidence): {report['entries_drafted']}")
            lines.append("  Pipeline: deferred to next session start")

            output_parts.append("\n".join(lines))

        except Exception as e:
            output_parts.append(
                f"[EFM] Pre-compact harvest error: {e}. "
                f"Review .memory/working/ manually if needed."
            )
    else:
        # --- Path B: No session — scan conversation transcript ---
        transcript_path = input_data.get("transcript_path")
        auto_draft = v3_config.get("auto_draft_from_conversation", True)

        if auto_draft and transcript_path:
            try:
                from lib.transcript_scanner import scan_conversation_for_drafts

                drafts_dir = _MEMORY_DIR / "drafts"
                report = scan_conversation_for_drafts(
                    Path(transcript_path).expanduser(),
                    drafts_dir,
                    _PROJECT_ROOT,
                    config,
                )
                if report["drafts_created"] > 0:
                    type_summary = ", ".join(
                        f"{t} ({n})" for t, n in report["draft_types"].items()
                    )
                    lines = [
                        f"[EFM] Pre-compact conversation scan: "
                        f"{report['candidates_found']} candidates "
                        f"→ {report['drafts_created']} drafts.",
                        f"  Types: {type_summary}",
                        f"  Review with: /memory-save",
                    ]
                    output_parts.append("\n".join(lines))
            except Exception as e:
                logger.warning("Pre-compact transcript scan failed: %s", e)

    # --- Write marker file ---
    try:
        compact_marker.touch()
    except OSError:
        pass

    # --- Output ---
    if output_parts:
        print(json.dumps({"additionalContext": "\n".join(output_parts)}))
    else:
        # Nothing harvested, still show reminder
        print(json.dumps({"additionalContext": ECHO_REMINDER}))

    sys.exit(0)


if __name__ == "__main__":
    main()
