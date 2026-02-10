#!/usr/bin/env python3
"""
EF Memory — Stop Hook (Session Harvest + Conversation Scan)

When Claude finishes responding:

1. If an active working memory session exists:
   - v3.auto_harvest_on_stop = true: auto-harvest → events.jsonl → clear
   - v3.auto_harvest_on_stop = false: block with reminder

2. If NO session exists (normal conversation):
   - v3.auto_draft_from_conversation = true: scan transcript for patterns
     → create drafts in .memory/drafts/ → remind user to review
   - v3.auto_draft_from_conversation = false: exit silently

Runs only once per session (via the 'once' hook config flag).
Checks stop_hook_active to prevent infinite loops.
"""

import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
_PROJECT_ROOT = _MEMORY_DIR.parent


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    # Prevent infinite loops: if this hook already fired, let Claude stop
    if input_data.get("stop_hook_active", False):
        sys.exit(0)

    # Load config (with preset resolution)
    config_path = _MEMORY_DIR / "config.json"
    try:
        sys.path.insert(0, str(_MEMORY_DIR))
        from lib.config_presets import load_config
        config = load_config(config_path)
    except Exception:
        config = {}

    v3_config = config.get("v3", {})
    working_dir_rel = v3_config.get("working_memory_dir", ".memory/working")
    working_dir = _PROJECT_ROOT / working_dir_rel
    findings_path = working_dir / "findings.md"
    progress_path = working_dir / "progress.md"

    has_session = findings_path.exists() or progress_path.exists()
    if not has_session:
        # No active working memory session — try scanning conversation
        auto_draft = v3_config.get("auto_draft_from_conversation", True)
        transcript_path = input_data.get("transcript_path")
        if not auto_draft or not transcript_path:
            sys.exit(0)

        try:
            sys.path.insert(0, str(_MEMORY_DIR))
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
                result = {
                    "additionalContext": (
                        f"[EF Memory] Conversation scan found "
                        f"{report['candidates_found']} memory candidates "
                        f"→ saved {report['drafts_created']} drafts.\n"
                        f"  Types: {type_summary}\n"
                        f"  Review with: /memory-save (review pending drafts)\n"
                        f"  Location: .memory/drafts/\n"
                        f"No files were modified (events.jsonl unchanged). "
                        f"Drafts require your approval."
                    )
                }
                print(json.dumps(result))
        except Exception:
            pass  # Never block stopping on scan failure

        sys.exit(0)

    # --- Active session path: collect all output parts, print once at end ---
    output_parts = []
    decision = None  # Only set for non-auto-harvest block

    auto_harvest = v3_config.get("auto_harvest_on_stop", True)

    if auto_harvest:
        # Full automation: harvest → convert → write → pipeline → clear
        try:
            sys.path.insert(0, str(_MEMORY_DIR))
            from lib.working_memory import auto_harvest_and_persist, is_session_complete

            # Check session completeness
            session_complete = is_session_complete(working_dir)
            require_complete = v3_config.get("require_complete_for_harvest", False)

            # Extract conversation ID for session-level dedup
            conversation_id = input_data.get("conversation_id")

            events_path = _MEMORY_DIR / "events.jsonl"
            report = auto_harvest_and_persist(
                working_dir=working_dir,
                events_path=events_path,
                project_root=_PROJECT_ROOT,
                config=config,
                run_pipeline_after=True,
                draft_only=require_complete and not session_complete,
                conversation_id=conversation_id,
            )

            status_label = "complete" if session_complete else "partial"
            lines = [f"[EF Memory] Auto-harvested working session ({status_label}):"]
            lines.append(f"  Candidates found: {report['candidates_found']}")
            lines.append(f"  Entries written: {report['entries_written']}")
            if report["entries_skipped"]:
                lines.append(f"  Entries skipped: {report['entries_skipped']}")
            dedup_skipped = report.get("dedup_skipped", [])
            if dedup_skipped:
                lines.append("  Duplicates skipped:")
                for ds in dedup_skipped[:3]:
                    title = ds["title"][:50]
                    lines.append(
                        f"    - \"{title}\" "
                        f"(~{ds['similarity']:.0%} similar to {ds['similar_to']})"
                    )
                if len(dedup_skipped) > 3:
                    lines.append(f"    ... and {len(dedup_skipped) - 3} more")
            drafted = report.get("entries_drafted", 0)
            if drafted:
                lines.append(f"  Entries routed to drafts (low confidence): {drafted}")
            lines.append(f"  Pipeline run: {'yes' if report['pipeline_run'] else 'no'}")
            lines.append(f"  Session cleared: {'yes' if report['session_cleared'] else 'no'}")
            if report["errors"]:
                lines.append(f"  Errors: {'; '.join(report['errors'])}")

            output_parts.append("\n".join(lines))

        except Exception as e:
            # Never block stopping due to harvest failure
            output_parts.append(
                f"[EF Memory] Auto-harvest error: {e}. "
                f"Review .memory/working/ manually if needed."
            )
    else:
        # Old behavior: block + remind
        decision = {
            "decision": "block",
            "reason": (
                "[EF Memory] Active working session detected. Before stopping, consider: "
                "(1) /memory-save if you discovered lessons or made important decisions, "
                "(2) check .memory/working/findings.md for unharvested insights. "
                "Say 'done' to stop without saving."
            ),
        }

    # Auto-compact if waste ratio exceeds threshold
    try:
        if _MEMORY_DIR not in [Path(p) for p in sys.path]:
            sys.path.insert(0, str(_MEMORY_DIR))
        from lib.compaction import get_compaction_stats, compact

        compact_config = config.get("compaction", {})
        threshold = compact_config.get("auto_suggest_threshold", 2.0)
        archive_rel = compact_config.get("archive_dir", ".memory/archive")
        archive_dir = _PROJECT_ROOT / archive_rel
        events_path = _MEMORY_DIR / "events.jsonl"

        stats = get_compaction_stats(events_path, threshold=threshold)
        if stats.suggest_compact:
            compact_report = compact(events_path, archive_dir, config)
            if compact_report.lines_archived > 0:
                output_parts.append(
                    f"[EF Memory] Auto-compacted events.jsonl: "
                    f"{compact_report.lines_before} → {compact_report.lines_after} lines, "
                    f"{compact_report.lines_archived} archived to {compact_report.quarters_touched}"
                )
    except Exception:
        pass  # Never block stopping on compact failure

    # --- Unified output: single JSON to stdout ---
    if decision:
        # Block path (auto_harvest=false): output the block decision
        print(json.dumps(decision))
    elif output_parts:
        # Non-blocking: merge all parts into one additionalContext
        result = {"additionalContext": "\n".join(output_parts)}
        print(json.dumps(result))

    sys.exit(0)


if __name__ == "__main__":
    main()
