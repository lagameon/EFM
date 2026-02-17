"""
EF Memory V3 — Project Init & Auto-Startup

Generates auto-startup files for a target project so that every
Claude Code session automatically knows about the EF Memory system.

Generated files:
  - CLAUDE.md (or appends to existing) — Tier 1 auto-load
  - .claude/rules/ef-memory-startup.md — Tier 2 auto-load
  - .claude/hooks.json (or merges into existing) — pre-compact reminder
  - .claude/settings.local.json (or merges into existing) — permissions

All operations are idempotent and non-destructive by default.
Use force=True to overwrite existing EF Memory sections.

No external dependencies — pure Python stdlib.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("efm.init")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class InitReport:
    """Summary of an init operation."""
    files_created: List[str] = field(default_factory=list)
    files_skipped: List[str] = field(default_factory=list)
    files_merged: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    dry_run: bool = False
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# EF Memory section marker (for detecting existing sections in CLAUDE.md)
# ---------------------------------------------------------------------------

_EFM_SECTION_START = "<!-- EF-MEMORY-START -->"
_EFM_SECTION_END = "<!-- EF-MEMORY-END -->"


# ---------------------------------------------------------------------------
# Template generators
# ---------------------------------------------------------------------------

def generate_ef_memory_section(config: dict, entry_count: int = 0) -> str:
    """
    Generate the EF Memory section for CLAUDE.md.

    This is the content that gets inserted/appended, wrapped in marker comments
    so it can be detected and updated on re-init.
    """
    human_review = config.get("automation", {}).get("human_review_required", True)
    review_status = "on (default)" if human_review else "off"

    # Preset display
    preset_name = config.get("preset")
    if preset_name:
        try:
            from .config_presets import describe_preset
            preset_line = f"- Active preset: **{preset_name}** ({describe_preset(preset_name)})"
        except ImportError:
            preset_line = f"- Active preset: **{preset_name}**"
    else:
        preset_line = "- Preset: none (custom config)"

    return f"""{_EFM_SECTION_START}

# Project Memory (EF Memory)

This project uses **EF Memory** for persistent engineering knowledge.
Memory store: `.memory/events.jsonl` ({entry_count} entries).

## Session Startup

1. Run `python3 .memory/scripts/pipeline_cli.py --startup` for a quick health check
2. If working on a specific domain, use `/memory-search <domain>` for relevant context
3. If a working memory session is active (`.memory/working/task_plan.md` exists), read it before making changes

## During Work

- Before modifying critical code, check: `/memory-search <relevant_topic>`
- After completing a significant task or fix, consider: `/memory-save` to capture lessons
- For complex multi-step tasks, use: `/memory-plan <description>` to start a working memory session
- Use `/memory-import <doc>` to extract knowledge from documents

## Core Commands (start here)

| Command | Purpose |
|---------|---------|
| `/memory-search <query>` | Find relevant project knowledge |
| `/memory-save` | Capture a lesson / decision / constraint |
| `/memory-plan` | Start / resume a working memory session |

## Advanced Commands

| Command | Purpose |
|---------|---------|
| `/memory-import <path>` | Extract from documents |
| `/memory-scan` | Batch discover + import |
| `/memory-verify` | Integrity check (read-only) |
| `/memory-evolve` | Health / evolution analysis |
| `/memory-reason` | LLM reasoning (needs API key) |
| `/memory-compact` | Compact + archive |
| `/memory-init` | Re-initialize startup files |

## Configuration

{preset_line}
- Memory config: `.memory/config.json`
- Human review: {review_status} (toggle via config or say "turn off/on memory review")
- Hard rules auto-injected from `.claude/rules/ef-memory/` when generated

{_EFM_SECTION_END}"""


def generate_claude_md(config: dict, entry_count: int = 0) -> str:
    """Generate a complete CLAUDE.md for projects without one."""
    return generate_ef_memory_section(config, entry_count).strip() + "\n"


def generate_startup_rule(config: dict, entry_count: int = 0) -> str:
    """
    Generate .claude/rules/ef-memory-startup.md content.

    Intentionally brief (<200 tokens) to minimize context overhead.
    Mentions only core commands to reduce cognitive load.
    """
    return f"""# EF Memory — Session Awareness

<!-- Auto-generated by EF Memory init. Safe to regenerate with /memory-init. -->

This project has an EF Memory system at `.memory/`.

- Memory store: `.memory/events.jsonl` ({entry_count} entries)
- Core: `/memory-search` (find knowledge), `/memory-save` (capture lessons), `/memory-plan` (working sessions)
- Hard memory rules in `.claude/rules/ef-memory/` auto-load when present
- If `.memory/working/task_plan.md` exists, an active working session is in progress — read it first
- All commands: see CLAUDE.md Memory Commands section
"""


def generate_hooks_json(existing_hooks: Optional[dict] = None) -> dict:
    """
    Generate or merge .claude/hooks.json with EF Memory hooks.

    DEPRECATED: Prefer generate_hooks_settings() which produces hooks
    in the Claude Code settings.json format for settings.local.json.

    If existing_hooks is provided, merges EF Memory entries into it
    without duplicating (checks by message content prefix).
    """
    efm_hook = {
        "type": "message",
        "message": "[EF Memory] Before compacting: consider /memory-save if you discovered lessons during this session. Check .memory/working/ for active planning sessions."
    }

    if existing_hooks is None:
        return {
            "hooks": {
                "pre-compact": [efm_hook]
            }
        }

    hooks = json.loads(json.dumps(existing_hooks))  # deep copy
    if "hooks" not in hooks:
        hooks["hooks"] = {}

    pre_compact = hooks["hooks"].get("pre-compact", [])

    # Check if EF Memory hook already exists
    efm_prefix = "[EF Memory]"
    already_exists = any(
        isinstance(h, dict) and h.get("message", "").startswith(efm_prefix)
        for h in pre_compact
    )

    if not already_exists:
        pre_compact.append(efm_hook)
        hooks["hooks"]["pre-compact"] = pre_compact

    return hooks


def generate_hooks_settings() -> dict:
    """
    Generate EF Memory hooks in Claude Code settings.json format.

    Returns a dict with hook event names as keys, ready to merge
    into settings.local.json["hooks"].

    Hooks:
      - SessionStart: run startup health check
      - PreToolUse (Edit|Write): search memory for relevant entries
      - PreToolUse (EnterPlanMode): auto-start working memory session
      - Stop: auto-harvest working session (M9) OR scan conversation → drafts (M10)
      - PreCompact: remind to save before compacting
    """
    # Prefix hook commands with cd to git repo root so they work
    # regardless of the current working directory (e.g. when Claude
    # is editing files in a subdirectory like deployment/live_trading/).
    # The _root variable silently exits if not in a git repo, preventing
    # errors (and infinite Stop-hook loops) in non-git subdirectories.
    _root = '_r="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; cd "$_r" && '
    return {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{_root}python3 .memory/scripts/pipeline_cli.py --startup 2>/dev/null || true",
                        "timeout": 15,
                        "statusMessage": "EF Memory startup check",
                    }
                ]
            }
        ],
        "PreToolUse": [
            {
                "matcher": "Edit|Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{_root}python3 .memory/hooks/pre_edit_search.py",
                        "timeout": 5,
                        "statusMessage": "EF Memory search",
                    }
                ]
            },
            {
                "matcher": "EnterPlanMode",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{_root}python3 .memory/hooks/plan_start.py",
                        "timeout": 10,
                        "statusMessage": "EF Memory plan session",
                    }
                ]
            },
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{_root}python3 .memory/hooks/stop_harvest.py",
                        "timeout": 30,
                        "once": True,
                    }
                ]
            }
        ],
        "PreCompact": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{_root}python3 .memory/hooks/compact_harvest.py",
                        "timeout": 10,
                        "statusMessage": "EFM pre-compact harvest",
                    }
                ]
            }
        ],
    }


def merge_settings_json(
    existing: Optional[dict],
    memory_permissions: Optional[List[str]] = None,
    include_hooks: bool = True,
) -> dict:
    """
    Merge EF Memory permissions and hooks into settings.local.json.

    Default EF Memory permissions:
    - Bash(python3:*) — for running memory scripts
    - Bash(bash:*) — for running hook shell scripts

    When include_hooks is True, also merges EF Memory hooks
    (SessionStart, PreToolUse, Stop, PreCompact).
    """
    if memory_permissions is None:
        memory_permissions = [
            "Bash(python3:*)",
            "Bash(bash:*)",
        ]

    if existing is None:
        settings: dict = {
            "permissions": {
                "allow": memory_permissions
            }
        }
    else:
        settings = json.loads(json.dumps(existing))  # deep copy
        if "permissions" not in settings:
            settings["permissions"] = {}
        if "allow" not in settings["permissions"]:
            settings["permissions"]["allow"] = []

        current = settings["permissions"]["allow"]
        for perm in memory_permissions:
            if perm not in current:
                current.append(perm)

    # Merge hooks
    if include_hooks:
        efm_hooks = generate_hooks_settings()
        if "hooks" not in settings:
            settings["hooks"] = {}

        for event_name, hook_groups in efm_hooks.items():
            if event_name not in settings["hooks"]:
                settings["hooks"][event_name] = hook_groups
            else:
                # Check if EF Memory hooks already exist
                # Match by .memory/ path OR [EF Memory] marker in command
                # Remove old EFM hooks and replace with new ones (handles
                # upgrades, e.g. relative→absolute path prefix, bash→python)
                non_efm_groups = []
                for group in settings["hooks"][event_name]:
                    is_efm = False
                    for h in group.get("hooks", []):
                        cmd = h.get("command", "")
                        msg = h.get("statusMessage", "")
                        if (".memory/hooks/" in cmd
                                or ".memory/scripts/" in cmd
                                or "[EF Memory]" in cmd
                                or "EF Memory" in msg):
                            is_efm = True
                            break
                    if not is_efm:
                        non_efm_groups.append(group)

                settings["hooks"][event_name] = non_efm_groups + hook_groups

    return settings


# ---------------------------------------------------------------------------
# Project scanner (advisory suggestions)
# ---------------------------------------------------------------------------

def scan_project(project_root: Path) -> List[str]:
    """
    Scan a project for advisory suggestions after init.

    Returns a list of human-readable suggestion strings.
    Does NOT modify any files.
    """
    suggestions = []

    # Check for docs that could be imported
    doc_count = 0
    for ext in ("*.md", "*.rst", "*.txt"):
        doc_count += len(list(project_root.glob(f"docs/**/{ext}")))
    if doc_count > 0:
        suggestions.append(
            f"Found {doc_count} documents in docs/ — consider `/memory-import` to extract knowledge"
        )

    # Check for existing INCIDENTS.md or similar
    for name in ("INCIDENTS.md", "ADR", "decisions"):
        path = project_root / "docs" / name
        if path.exists():
            suggestions.append(
                f"Found {path.relative_to(project_root)} — high-value import target"
            )

    # Check .gitignore for memory artifacts.
    # These are derived/session-scoped files that MUST NOT be committed:
    #   - vectors.db: SQLite binary, corrupts on branch switch, unresolvable merge
    #   - working/: session-scoped PWF files
    #   - archive/: compacted history, regenerable
    #   - drafts/*.json: review queue, transient
    #   - .claude/rules/ef-memory/: auto-generated from events.jsonl
    _required_ignores = {
        ".memory/vectors.db": [".memory/vectors.db", "vectors.db"],
        ".memory/working/": [".memory/working/"],
        ".memory/archive/": [".memory/archive/"],
        ".memory/drafts/*.json": [".memory/drafts/", "drafts/*.json"],
        ".claude/rules/ef-memory/": [".claude/rules/ef-memory/"],
    }
    gitignore = project_root / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        missing = [
            pattern
            for pattern, variants in _required_ignores.items()
            if not any(v in content for v in variants)
        ]
        if missing:
            suggestions.append(
                f"⚠️ Add to .gitignore (prevents merge conflicts): {', '.join(missing)}"
            )
    else:
        suggestions.append(
            "⚠️ No .gitignore found — create one with: "
            + ", ".join(_required_ignores.keys())
        )

    return suggestions


# ---------------------------------------------------------------------------
# Count entries helper
# ---------------------------------------------------------------------------

def _count_entries(events_path: Path) -> int:
    """Count non-empty lines in events.jsonl."""
    if not events_path.exists():
        return 0
    count = 0
    try:
        with open(events_path, "r") as f:
            for line in f:
                if line.strip():
                    count += 1
    except Exception:
        pass
    return count


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file + os.replace."""
    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as tmp_f:
            tmp_f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_raw_json(path: Path) -> Optional[dict]:
    """Read a JSON file, returning None on missing/corrupt files."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Version stamping
# ---------------------------------------------------------------------------

def _stamp_efm_version(config_path: Path) -> None:
    """Write the current EFM_VERSION into config.json's efm_version field.

    Called after run_init() and run_upgrade() to track installed version.
    """
    try:
        from .config_presets import EFM_VERSION
    except ImportError:
        return

    try:
        if config_path.exists():
            raw = json.loads(config_path.read_text())
        else:
            raw = {}
        raw["efm_version"] = EFM_VERSION
        _atomic_write_json(config_path, raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not stamp efm_version: %s", exc)


# ---------------------------------------------------------------------------
# Main init orchestrator
# ---------------------------------------------------------------------------

def run_init(
    project_root: Path,
    config: dict,
    force: bool = False,
    dry_run: bool = False,
) -> InitReport:
    """
    Initialize EF Memory auto-startup for a target project.

    Generates:
    1. CLAUDE.md (or appends EF Memory section to existing)
    2. .claude/rules/ef-memory-startup.md
    3. .claude/hooks.json (or merges)
    4. .claude/settings.local.json (or merges)

    Args:
        project_root: Path to the target project root
        config: EF Memory config dict (from .memory/config.json)
        force: If True, overwrite existing EF Memory sections
        dry_run: If True, report what would happen without writing files

    Returns:
        InitReport with details of what was created/skipped/merged
    """
    report = InitReport(dry_run=dry_run)
    start_time = time.monotonic()

    # Count entries for template interpolation
    events_path = project_root / ".memory" / "events.jsonl"
    entry_count = _count_entries(events_path)

    # Ensure .claude/ directory exists
    claude_dir = project_root / ".claude"
    rules_dir = claude_dir / "rules"

    if not dry_run:
        claude_dir.mkdir(parents=True, exist_ok=True)
        rules_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. CLAUDE.md ---
    _handle_claude_md(project_root, config, entry_count, force, dry_run, report)

    # --- 2. .claude/rules/ef-memory-startup.md ---
    _handle_startup_rule(rules_dir, config, entry_count, force, dry_run, report)

    # --- 3. .claude/hooks.json ---
    _handle_hooks_json(claude_dir, dry_run, report)

    # --- 4. .claude/settings.local.json ---
    _handle_settings_json(claude_dir, dry_run, report)

    # --- 5. Project scan (advisory) ---
    report.suggestions = scan_project(project_root)

    # Stamp version
    if not dry_run:
        _stamp_efm_version(project_root / ".memory" / "config.json")

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report


def _handle_claude_md(
    project_root: Path,
    config: dict,
    entry_count: int,
    force: bool,
    dry_run: bool,
    report: InitReport,
) -> None:
    """Handle CLAUDE.md creation or append."""
    claude_md_path = project_root / "CLAUDE.md"
    rel_path = "CLAUDE.md"

    if claude_md_path.exists():
        existing = claude_md_path.read_text()

        if _EFM_SECTION_START in existing:
            if force:
                # Replace existing EF Memory section
                new_content = _replace_efm_section(
                    existing, generate_ef_memory_section(config, entry_count)
                )
                if not dry_run:
                    claude_md_path.write_text(new_content)
                report.files_merged.append(rel_path)
                logger.info("Updated EF Memory section in CLAUDE.md (force)")
            else:
                report.files_skipped.append(rel_path)
                logger.info("CLAUDE.md already has EF Memory section, skipping")
        else:
            # Append EF Memory section
            efm_section = generate_ef_memory_section(config, entry_count)
            new_content = existing.rstrip() + "\n\n---\n\n" + efm_section + "\n"
            if not dry_run:
                claude_md_path.write_text(new_content)
            report.files_merged.append(rel_path)
            logger.info("Appended EF Memory section to existing CLAUDE.md")
    else:
        # Create new CLAUDE.md
        content = generate_claude_md(config, entry_count)
        if not dry_run:
            claude_md_path.write_text(content)
        report.files_created.append(rel_path)
        logger.info("Created CLAUDE.md")


def _handle_startup_rule(
    rules_dir: Path,
    config: dict,
    entry_count: int,
    force: bool,
    dry_run: bool,
    report: InitReport,
) -> None:
    """Handle .claude/rules/ef-memory-startup.md."""
    rule_path = rules_dir / "ef-memory-startup.md"
    rel_path = ".claude/rules/ef-memory-startup.md"

    if rule_path.exists() and not force:
        report.files_skipped.append(rel_path)
        logger.info("ef-memory-startup.md exists, skipping (use --force to overwrite)")
        return

    content = generate_startup_rule(config, entry_count)
    action = "Updated" if rule_path.exists() else "Created"

    if not dry_run:
        rule_path.write_text(content)

    if rule_path.exists() and force:
        report.files_merged.append(rel_path)
    else:
        report.files_created.append(rel_path)
    logger.info(f"{action} ef-memory-startup.md")


def _handle_hooks_json(
    claude_dir: Path,
    dry_run: bool,
    report: InitReport,
) -> None:
    """Handle .claude/hooks.json creation or merge."""
    hooks_path = claude_dir / "hooks.json"
    rel_path = ".claude/hooks.json"

    existing_hooks = _read_raw_json(hooks_path)
    if existing_hooks is None and hooks_path.exists():
        report.warnings.append(f"Could not parse existing hooks.json")

    merged = generate_hooks_json(existing_hooks)

    if existing_hooks is not None:
        # Check if anything changed
        if merged == existing_hooks:
            report.files_skipped.append(rel_path)
            logger.info("hooks.json already has EF Memory hook, skipping")
            return
        if not dry_run:
            _atomic_write_json(hooks_path, merged)
        report.files_merged.append(rel_path)
        logger.info("Merged EF Memory hook into hooks.json")
    else:
        if not dry_run:
            _atomic_write_json(hooks_path, merged)
        report.files_created.append(rel_path)
        logger.info("Created hooks.json")


def _handle_settings_json(
    claude_dir: Path,
    dry_run: bool,
    report: InitReport,
) -> None:
    """Handle .claude/settings.local.json merge."""
    settings_path = claude_dir / "settings.local.json"
    rel_path = ".claude/settings.local.json"

    existing = _read_raw_json(settings_path)
    if existing is None and settings_path.exists():
        report.warnings.append(f"Could not parse existing settings.local.json")

    merged = merge_settings_json(existing)

    if existing is not None:
        if merged == existing:
            report.files_skipped.append(rel_path)
            logger.info("settings.local.json already has EF Memory permissions, skipping")
            return
        if not dry_run:
            _atomic_write_json(settings_path, merged)
        report.files_merged.append(rel_path)
        logger.info("Merged EF Memory permissions into settings.local.json")
    else:
        if not dry_run:
            _atomic_write_json(settings_path, merged)
        report.files_created.append(rel_path)
        logger.info("Created settings.local.json")


def run_upgrade(
    project_root: Path,
    config: dict,
    dry_run: bool = False,
) -> InitReport:
    """
    Upgrade an existing EFM installation without overwriting user content.
    
    Updates:
    - .claude/rules/ef-memory-startup.md (force regenerate)
    - CLAUDE.md EFM section only (preserve user content)
    - .claude/settings.local.json (merge hooks + permissions)
    - .claude/hooks.json (merge)
    
    Does NOT touch:
    - events.jsonl
    - config.json content (except version stamp)
    - working/ directory
    - drafts/
    """
    report = InitReport(dry_run=dry_run)
    start_time = time.monotonic()

    events_path = project_root / ".memory" / "events.jsonl"
    entry_count = _count_entries(events_path)

    claude_dir = project_root / ".claude"
    rules_dir = claude_dir / "rules"

    if not dry_run:
        claude_dir.mkdir(parents=True, exist_ok=True)
        rules_dir.mkdir(parents=True, exist_ok=True)

    # 1. Force-update startup rule
    _handle_startup_rule(rules_dir, config, entry_count, force=True, dry_run=dry_run, report=report)

    # 2. Upgrade CLAUDE.md EFM section only
    _handle_claude_md_upgrade(project_root, config, entry_count, dry_run, report)

    # 3. Merge settings.local.json
    _handle_settings_json(claude_dir, dry_run, report)

    # 4. Merge hooks.json
    _handle_hooks_json(claude_dir, dry_run, report)

    # 5. Check CLAUDE.md content quality
    _check_claude_md_content(project_root, report)

    # 6. Project scan (advisory)
    report.suggestions = scan_project(project_root)

    # Stamp version
    if not dry_run:
        _stamp_efm_version(project_root / ".memory" / "config.json")

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report


def _handle_claude_md_upgrade(
    project_root: Path,
    config: dict,
    entry_count: int,
    dry_run: bool,
    report: InitReport,
) -> None:
    """Upgrade CLAUDE.md: replace only EFM section, preserve all user content."""
    claude_md_path = project_root / "CLAUDE.md"
    rel_path = "CLAUDE.md"
    new_section = generate_ef_memory_section(config, entry_count)

    if claude_md_path.exists():
        existing = claude_md_path.read_text()

        if _EFM_SECTION_START in existing:
            # Replace just the EFM section
            new_content = _replace_efm_section(existing, new_section)
            if not dry_run:
                claude_md_path.write_text(new_content)
            report.files_merged.append(rel_path)
            logger.info("Upgraded EFM section in CLAUDE.md (preserved user content)")
        else:
            # No EFM section — append
            new_content = existing.rstrip() + "\n\n---\n\n" + new_section + "\n"
            if not dry_run:
                claude_md_path.write_text(new_content)
            report.files_merged.append(rel_path)
            logger.info("Appended EFM section to existing CLAUDE.md")
    else:
        # No CLAUDE.md — create with just EFM section + warning
        content = generate_claude_md(config, entry_count)
        if not dry_run:
            claude_md_path.write_text(content)
        report.files_created.append(rel_path)
        report.warnings.append(
            "Created CLAUDE.md with only EFM section. "
            "Add project architecture, commands, and rules above the EFM section."
        )
        logger.info("Created CLAUDE.md (EFM only — needs project context)")


def _check_claude_md_content(project_root: Path, report: InitReport) -> None:
    """Check if CLAUDE.md has meaningful project content above the EFM section.
    
    Warns if there are fewer than 10 non-empty lines before the EFM markers,
    suggesting the user add project architecture, commands, and rules.
    """
    claude_md_path = project_root / "CLAUDE.md"
    if not claude_md_path.exists():
        return

    content = claude_md_path.read_text()
    start_idx = content.find(_EFM_SECTION_START)
    
    if start_idx == -1:
        return  # No EFM section — nothing to check

    # Count non-empty lines before the EFM section
    before_section = content[:start_idx]
    non_empty_lines = [
        line for line in before_section.splitlines()
        if line.strip() and not line.strip().startswith("---")
    ]

    if len(non_empty_lines) < 10:
        report.warnings.append(
            f"CLAUDE.md has only {len(non_empty_lines)} lines of project context before "
            f"the EFM section. Consider adding: project overview, build commands, "
            f"architecture description, and coding rules."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _replace_efm_section(text: str, new_section: str) -> str:
    """Replace the EF Memory section in a text, preserving everything else."""
    start_idx = text.find(_EFM_SECTION_START)
    end_idx = text.find(_EFM_SECTION_END)

    if start_idx == -1 or end_idx == -1:
        return text

    end_idx += len(_EFM_SECTION_END)
    # Include trailing newline if present
    if end_idx < len(text) and text[end_idx] == "\n":
        end_idx += 1

    return text[:start_idx] + new_section + text[end_idx:]
