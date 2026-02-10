#!/usr/bin/env python3
"""
EF Memory — Plan Start Hook (PreToolUse: EnterPlanMode)

Automatically starts a working memory session when Claude enters
plan mode. Injects prefilled EF Memory context as additionalContext.

Skips if a session already exists (idempotent).
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
        # Can't read input — don't block
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
    if not v3_config.get("auto_start_on_plan", True):
        sys.exit(0)

    working_dir_rel = v3_config.get("working_memory_dir", ".memory/working")
    working_dir = _PROJECT_ROOT / working_dir_rel
    events_path = _MEMORY_DIR / "events.jsonl"

    # Check if session already exists
    if (working_dir / "task_plan.md").exists():
        sys.exit(0)

    # Extract task description from hook input
    tool_input = input_data.get("tool_input", {})
    task_desc = (
        tool_input.get("description")
        or tool_input.get("task")
        or tool_input.get("title")
        or "Plan session"
    )

    # Start session
    try:
        sys.path.insert(0, str(_MEMORY_DIR))
        from lib.working_memory import start_session

        report = start_session(
            task_description=task_desc,
            events_path=events_path,
            working_dir=working_dir,
            config=config,
            project_root=_PROJECT_ROOT,
        )

        # Build context message
        lines = [
            "[EF Memory] Working memory session started automatically.",
            f"  Files: {', '.join(report.files_created)}",
            f"  Pre-filled: {report.prefill_count} relevant memory entries",
        ]

        if report.prefill_entries:
            lines.append("  Relevant context:")
            for pe in report.prefill_entries[:3]:
                sev = f" [{pe.severity}]" if pe.severity else ""
                lines.append(f"    - [{pe.classification}]{sev} {pe.title}")

        lines.append("")
        lines.append("Update .memory/working/findings.md with discoveries as you work.")

        result = {"additionalContext": "\n".join(lines)}
        print(json.dumps(result))

    except Exception as e:
        # Never block plan mode entry on failure
        result = {
            "additionalContext": f"[EF Memory] Auto-start session failed: {e}"
        }
        print(json.dumps(result))

    sys.exit(0)


if __name__ == "__main__":
    main()
