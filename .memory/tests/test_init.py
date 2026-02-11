"""
Tests for EF Memory V3 — Project Init & Auto-Startup

Covers: generate_ef_memory_section, generate_claude_md, generate_startup_rule,
        generate_hooks_json, merge_settings_json, scan_project,
        run_init (full orchestrator), _replace_efm_section, _count_entries
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Import path setup
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.init import (
    InitReport,
    _EFM_SECTION_END,
    _EFM_SECTION_START,
    _atomic_write_json,
    _count_entries,
    _replace_efm_section,
    generate_claude_md,
    generate_ef_memory_section,
    generate_hooks_json,
    generate_hooks_settings,
    generate_startup_rule,
    merge_settings_json,
    run_init,
    run_upgrade,
    scan_project,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> dict:
    """Create a minimal config for testing."""
    config = {
        "automation": {"human_review_required": True},
    }
    config.update(overrides)
    return config


def _write_events(path: Path, count: int) -> None:
    """Write `count` dummy entries to events.jsonl."""
    with open(path, "w") as f:
        for i in range(count):
            f.write(json.dumps({"id": f"test-entry-{i:08x}"}) + "\n")


# ===========================================================================
# Test: generate_ef_memory_section
# ===========================================================================

class TestGenerateEfMemorySection(unittest.TestCase):

    def test_contains_markers(self):
        section = generate_ef_memory_section(_make_config())
        self.assertIn(_EFM_SECTION_START, section)
        self.assertIn(_EFM_SECTION_END, section)

    def test_contains_entry_count(self):
        section = generate_ef_memory_section(_make_config(), entry_count=42)
        self.assertIn("42 entries", section)

    def test_zero_entries(self):
        section = generate_ef_memory_section(_make_config(), entry_count=0)
        self.assertIn("0 entries", section)

    def test_human_review_on(self):
        config = _make_config()
        section = generate_ef_memory_section(config)
        self.assertIn("on (default)", section)

    def test_human_review_off(self):
        config = _make_config(automation={"human_review_required": False})
        section = generate_ef_memory_section(config)
        self.assertIn("off", section)

    def test_contains_core_commands(self):
        section = generate_ef_memory_section(_make_config())
        self.assertIn("/memory-search", section)
        self.assertIn("/memory-save", section)
        self.assertIn("/memory-plan", section)
        self.assertIn("Core Commands", section)

    def test_contains_advanced_commands(self):
        section = generate_ef_memory_section(_make_config())
        self.assertIn("/memory-init", section)
        self.assertIn("/memory-import", section)
        self.assertIn("/memory-scan", section)
        self.assertIn("/memory-verify", section)
        self.assertIn("/memory-evolve", section)
        self.assertIn("/memory-reason", section)
        self.assertIn("/memory-compact", section)
        self.assertIn("Advanced Commands", section)

    def test_contains_startup_instructions(self):
        section = generate_ef_memory_section(_make_config())
        self.assertIn("pipeline_cli.py --startup", section)

    def test_empty_config(self):
        """Empty config should still work (defaults to review on)."""
        section = generate_ef_memory_section({})
        self.assertIn(_EFM_SECTION_START, section)
        self.assertIn("on (default)", section)

    def test_preset_displayed_when_present(self):
        """Section should show active preset name."""
        config = _make_config(preset="standard")
        section = generate_ef_memory_section(config)
        self.assertIn("standard", section)
        self.assertIn("Active preset", section)

    def test_no_preset_shows_custom(self):
        """Section should show 'custom config' when no preset."""
        section = generate_ef_memory_section(_make_config())
        self.assertIn("custom config", section)

    def test_all_ten_commands_present(self):
        """All 10 slash commands should be listed somewhere in the section."""
        section = generate_ef_memory_section(_make_config())
        all_commands = [
            "/memory-search", "/memory-save", "/memory-plan",
            "/memory-import", "/memory-scan", "/memory-verify",
            "/memory-evolve", "/memory-reason", "/memory-compact",
            "/memory-init",
        ]
        for cmd in all_commands:
            self.assertIn(cmd, section, f"Missing command: {cmd}")


# ===========================================================================
# Test: generate_claude_md
# ===========================================================================

class TestGenerateClaudeMd(unittest.TestCase):

    def test_starts_with_marker(self):
        content = generate_claude_md(_make_config())
        self.assertTrue(content.strip().startswith(_EFM_SECTION_START))

    def test_ends_with_newline(self):
        content = generate_claude_md(_make_config())
        self.assertTrue(content.endswith("\n"))

    def test_entry_count_interpolation(self):
        content = generate_claude_md(_make_config(), entry_count=10)
        self.assertIn("10 entries", content)


# ===========================================================================
# Test: generate_startup_rule
# ===========================================================================

class TestGenerateStartupRule(unittest.TestCase):

    def test_contains_awareness_header(self):
        content = generate_startup_rule(_make_config())
        self.assertIn("Session Awareness", content)

    def test_entry_count(self):
        content = generate_startup_rule(_make_config(), entry_count=5)
        self.assertIn("5 entries", content)

    def test_brief_length(self):
        """Startup rule should be concise (<200 tokens ~ <1000 chars)."""
        content = generate_startup_rule(_make_config())
        self.assertLess(len(content), 1000)

    def test_contains_core_commands(self):
        content = generate_startup_rule(_make_config())
        self.assertIn("/memory-search", content)
        self.assertIn("/memory-save", content)
        self.assertIn("/memory-plan", content)

    def test_contains_all_commands_reference(self):
        """Startup rule should reference CLAUDE.md for full command list."""
        content = generate_startup_rule(_make_config())
        self.assertIn("All commands", content)


# ===========================================================================
# Test: generate_hooks_json
# ===========================================================================

class TestGenerateHooksJson(unittest.TestCase):

    def test_new_hooks(self):
        result = generate_hooks_json(None)
        self.assertIn("hooks", result)
        self.assertIn("pre-compact", result["hooks"])
        self.assertEqual(len(result["hooks"]["pre-compact"]), 1)

    def test_hook_has_message_type(self):
        result = generate_hooks_json(None)
        hook = result["hooks"]["pre-compact"][0]
        self.assertEqual(hook["type"], "message")

    def test_hook_message_prefix(self):
        result = generate_hooks_json(None)
        hook = result["hooks"]["pre-compact"][0]
        self.assertTrue(hook["message"].startswith("[EF Memory]"))

    def test_merge_with_existing_empty(self):
        existing = {"hooks": {}}
        result = generate_hooks_json(existing)
        self.assertEqual(len(result["hooks"]["pre-compact"]), 1)

    def test_merge_with_existing_hooks(self):
        existing = {
            "hooks": {
                "pre-compact": [
                    {"type": "message", "message": "Some other hook"}
                ],
                "post-edit": [
                    {"type": "message", "message": "After edit"}
                ],
            }
        }
        result = generate_hooks_json(existing)
        # Should have 2 pre-compact hooks
        self.assertEqual(len(result["hooks"]["pre-compact"]), 2)
        # Should preserve post-edit
        self.assertIn("post-edit", result["hooks"])

    def test_no_duplicate_on_rerun(self):
        """Running merge twice should not duplicate the EF Memory hook."""
        result1 = generate_hooks_json(None)
        result2 = generate_hooks_json(result1)
        self.assertEqual(len(result2["hooks"]["pre-compact"]), 1)

    def test_merge_preserves_existing_unchanged(self):
        existing = {
            "hooks": {
                "post-edit": [{"type": "message", "message": "Custom"}]
            }
        }
        result = generate_hooks_json(existing)
        self.assertEqual(
            result["hooks"]["post-edit"],
            existing["hooks"]["post-edit"],
        )

    def test_existing_without_hooks_key(self):
        """Handle existing file with no 'hooks' key."""
        existing = {"some_other_key": True}
        result = generate_hooks_json(existing)
        self.assertIn("hooks", result)
        self.assertEqual(len(result["hooks"]["pre-compact"]), 1)
        # Preserve other keys
        self.assertTrue(result.get("some_other_key"))


# ===========================================================================
# Test: generate_hooks_settings
# ===========================================================================

class TestGenerateHooksSettings(unittest.TestCase):

    def test_returns_all_event_types(self):
        hooks = generate_hooks_settings()
        self.assertIn("SessionStart", hooks)
        self.assertIn("PreToolUse", hooks)
        self.assertIn("Stop", hooks)
        self.assertIn("PreCompact", hooks)

    def test_session_start_hook(self):
        hooks = generate_hooks_settings()
        group = hooks["SessionStart"][0]
        self.assertEqual(group["matcher"], "")
        hook = group["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertIn("pipeline_cli.py --startup", hook["command"])

    def test_pre_tool_use_edit_write_matcher(self):
        hooks = generate_hooks_settings()
        group = hooks["PreToolUse"][0]
        self.assertEqual(group["matcher"], "Edit|Write")

    def test_pre_tool_use_enter_plan_mode_hook(self):
        hooks = generate_hooks_settings()
        # Second PreToolUse group is for EnterPlanMode
        self.assertEqual(len(hooks["PreToolUse"]), 2)
        group = hooks["PreToolUse"][1]
        self.assertEqual(group["matcher"], "EnterPlanMode")
        hook = group["hooks"][0]
        self.assertIn("plan_start.py", hook["command"])

    def test_stop_hook_has_once(self):
        hooks = generate_hooks_settings()
        group = hooks["Stop"][0]
        hook = group["hooks"][0]
        self.assertTrue(hook.get("once"))

    def test_pre_compact_uses_python_hook(self):
        hooks = generate_hooks_settings()
        group = hooks["PreCompact"][0]
        hook = group["hooks"][0]
        self.assertIn("compact_harvest.py", hook["command"])
        self.assertEqual(hook["timeout"], 10)
        self.assertIn("statusMessage", hook)

    def test_all_hooks_have_timeout(self):
        hooks = generate_hooks_settings()
        for event_name, groups in hooks.items():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertIn("timeout", hook, f"{event_name} hook missing timeout")


# ===========================================================================
# Test: merge_settings_json
# ===========================================================================

class TestMergeSettingsJson(unittest.TestCase):

    def test_new_settings(self):
        result = merge_settings_json(None)
        self.assertIn("permissions", result)
        self.assertIn("allow", result["permissions"])
        self.assertIn("Bash(python3:*)", result["permissions"]["allow"])
        self.assertIn("Bash(bash:*)", result["permissions"]["allow"])

    def test_merge_with_existing(self):
        existing = {
            "permissions": {
                "allow": ["Bash(git:*)"]
            }
        }
        result = merge_settings_json(existing)
        self.assertIn("Bash(git:*)", result["permissions"]["allow"])
        self.assertIn("Bash(python3:*)", result["permissions"]["allow"])
        self.assertIn("Bash(bash:*)", result["permissions"]["allow"])

    def test_no_duplicate_on_rerun(self):
        result1 = merge_settings_json(None)
        result2 = merge_settings_json(result1)
        count = result2["permissions"]["allow"].count("Bash(python3:*)")
        self.assertEqual(count, 1)
        count_bash = result2["permissions"]["allow"].count("Bash(bash:*)")
        self.assertEqual(count_bash, 1)

    def test_custom_permissions(self):
        result = merge_settings_json(None, memory_permissions=["Bash(custom:*)"])
        self.assertIn("Bash(custom:*)", result["permissions"]["allow"])

    def test_existing_no_permissions_key(self):
        existing = {"some_key": True}
        result = merge_settings_json(existing)
        self.assertIn("permissions", result)
        self.assertIn("Bash(python3:*)", result["permissions"]["allow"])

    def test_existing_no_allow_key(self):
        existing = {"permissions": {"deny": ["Bash(rm:*)"]}}
        result = merge_settings_json(existing)
        self.assertIn("allow", result["permissions"])
        # Preserve deny
        self.assertIn("deny", result["permissions"])

    def test_does_not_mutate_original(self):
        existing = {"permissions": {"allow": ["Bash(git:*)"]}}
        result = merge_settings_json(existing)
        # Original should be unchanged
        self.assertEqual(len(existing["permissions"]["allow"]), 1)
        self.assertGreater(len(result["permissions"]["allow"]), 1)

    def test_includes_hooks_by_default(self):
        result = merge_settings_json(None)
        self.assertIn("hooks", result)
        self.assertIn("SessionStart", result["hooks"])
        self.assertIn("PreToolUse", result["hooks"])
        self.assertIn("Stop", result["hooks"])
        self.assertIn("PreCompact", result["hooks"])

    def test_hooks_disabled(self):
        result = merge_settings_json(None, include_hooks=False)
        self.assertNotIn("hooks", result)

    def test_hooks_idempotent_on_rerun(self):
        """Running merge twice should not duplicate any hooks."""
        result1 = merge_settings_json(None)
        result2 = merge_settings_json(result1)
        for event_name in ("SessionStart", "PreToolUse", "Stop", "PreCompact"):
            self.assertEqual(
                len(result2["hooks"][event_name]),
                len(result1["hooks"][event_name]),
                f"{event_name} hook groups duplicated on second merge",
            )

    def test_hooks_merge_preserves_non_efm_hooks(self):
        """Non-EFM hooks in the same event should be preserved."""
        existing = {
            "permissions": {"allow": []},
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "my-custom-linter"}],
                    }
                ]
            },
        }
        result = merge_settings_json(existing)
        # Should have custom hook + 2 EFM hooks (Edit|Write + EnterPlanMode)
        self.assertEqual(len(result["hooks"]["PreToolUse"]), 3)

    def test_hooks_precompact_not_duplicated(self):
        """PreCompact hook (echo command) must not duplicate on rerun."""
        result1 = merge_settings_json(None)
        result2 = merge_settings_json(result1)
        self.assertEqual(len(result2["hooks"]["PreCompact"]), 1)

    def test_hooks_commands_have_cd_prefix(self):
        """All .memory/hooks/ commands should cd to git root first."""
        hooks = generate_hooks_settings()
        for event_name, groups in hooks.items():
            for group in groups:
                for hook in group["hooks"]:
                    cmd = hook["command"]
                    if ".memory/hooks/" in cmd:
                        self.assertIn(
                            'cd "$(git rev-parse --show-toplevel)"',
                            cmd,
                            f"{event_name} hook missing cd-to-root prefix",
                        )

    def test_hooks_upgrade_replaces_old_relative_paths(self):
        """Merging over old relative-path hooks should replace, not duplicate."""
        # Simulate settings from a pre-fix installation (relative paths)
        old_settings = {
            "permissions": {"allow": []},
            "hooks": {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 .memory/hooks/stop_harvest.py",
                                "timeout": 30,
                                "once": True,
                            }
                        ],
                    }
                ],
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "bash .memory/hooks/session_start.sh",
                                "timeout": 15,
                            }
                        ],
                    }
                ],
            },
        }
        result = merge_settings_json(old_settings)
        # Should have exactly 1 Stop group (replaced, not appended)
        self.assertEqual(len(result["hooks"]["Stop"]), 1)
        stop_cmd = result["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertIn("git rev-parse", stop_cmd)
        # Should have exactly 1 SessionStart group (replaced)
        self.assertEqual(len(result["hooks"]["SessionStart"]), 1)
        start_cmd = result["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertIn("git rev-parse", start_cmd)


# ===========================================================================
# Test: scan_project
# ===========================================================================

class TestScanProject(unittest.TestCase):

    def test_empty_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            suggestions = scan_project(Path(tmp))
            # Should suggest creating gitignore
            self.assertTrue(any(".gitignore" in s for s in suggestions))

    def test_docs_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "docs"
            docs.mkdir()
            (docs / "README.md").write_text("# Docs")
            (docs / "guide.md").write_text("# Guide")
            suggestions = scan_project(Path(tmp))
            self.assertTrue(any("documents" in s.lower() or "docs" in s.lower() for s in suggestions))

    def test_incidents_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "docs"
            docs.mkdir()
            (docs / "INCIDENTS.md").write_text("# Incidents")
            suggestions = scan_project(Path(tmp))
            self.assertTrue(any("INCIDENTS" in s for s in suggestions))

    def test_gitignore_missing_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".gitignore").write_text("node_modules/\n")
            suggestions = scan_project(Path(tmp))
            self.assertTrue(any(".memory/working/" in s for s in suggestions))

    def test_gitignore_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".gitignore").write_text(
                ".memory/working/\nvectors.db\n"
            )
            suggestions = scan_project(Path(tmp))
            # Should NOT suggest gitignore additions
            gitignore_suggestions = [s for s in suggestions if ".gitignore" in s.lower() or "gitignore" in s.lower()]
            self.assertEqual(len(gitignore_suggestions), 0)


# ===========================================================================
# Test: _count_entries
# ===========================================================================

class TestCountEntries(unittest.TestCase):

    def test_nonexistent_file(self):
        self.assertEqual(_count_entries(Path("/nonexistent/events.jsonl")), 0)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("")
            f.flush()
            try:
                self.assertEqual(_count_entries(Path(f.name)), 0)
            finally:
                os.unlink(f.name)

    def test_file_with_entries(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"id": "a"}\n{"id": "b"}\n{"id": "c"}\n')
            f.flush()
            try:
                self.assertEqual(_count_entries(Path(f.name)), 3)
            finally:
                os.unlink(f.name)

    def test_file_with_blank_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"id": "a"}\n\n{"id": "b"}\n\n')
            f.flush()
            try:
                self.assertEqual(_count_entries(Path(f.name)), 2)
            finally:
                os.unlink(f.name)


# ===========================================================================
# Test: _replace_efm_section
# ===========================================================================

class TestReplaceEfmSection(unittest.TestCase):

    def test_replaces_section(self):
        text = f"Before\n{_EFM_SECTION_START}\nOld content\n{_EFM_SECTION_END}\nAfter"
        new_section = f"{_EFM_SECTION_START}\nNew content\n{_EFM_SECTION_END}"
        result = _replace_efm_section(text, new_section)
        self.assertIn("New content", result)
        self.assertNotIn("Old content", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_no_markers_returns_unchanged(self):
        text = "No markers here"
        result = _replace_efm_section(text, "replacement")
        self.assertEqual(result, text)

    def test_preserves_surrounding_content(self):
        before = "# My Project\n\nSome docs here.\n\n"
        after = "\n## Other Section\n"
        # Note: _replace_efm_section consumes one trailing \n after END marker
        text = f"{before}{_EFM_SECTION_START}\nOld\n{_EFM_SECTION_END}\n{after}"
        new_section = f"{_EFM_SECTION_START}\nNew\n{_EFM_SECTION_END}"
        result = _replace_efm_section(text, new_section)
        self.assertTrue(result.startswith(before))
        self.assertIn("## Other Section", result)


# ===========================================================================
# Test: run_init — Full Orchestrator
# ===========================================================================

class TestRunInit(unittest.TestCase):
    """Integration tests for run_init."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_root = Path(self.tmpdir)
        # Create .memory/events.jsonl
        memory_dir = self.project_root / ".memory"
        memory_dir.mkdir()
        _write_events(memory_dir / "events.jsonl", 5)
        self.config = _make_config()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_creates_all_files_on_fresh_project(self):
        report = run_init(self.project_root, self.config)
        self.assertIn("CLAUDE.md", report.files_created)
        self.assertIn(".claude/rules/ef-memory-startup.md", report.files_created)
        self.assertIn(".claude/hooks.json", report.files_created)
        self.assertIn(".claude/settings.local.json", report.files_created)

    def test_claude_md_exists(self):
        run_init(self.project_root, self.config)
        self.assertTrue((self.project_root / "CLAUDE.md").exists())

    def test_startup_rule_exists(self):
        run_init(self.project_root, self.config)
        self.assertTrue(
            (self.project_root / ".claude" / "rules" / "ef-memory-startup.md").exists()
        )

    def test_hooks_json_exists(self):
        run_init(self.project_root, self.config)
        path = self.project_root / ".claude" / "hooks.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertIn("hooks", data)

    def test_settings_json_exists(self):
        run_init(self.project_root, self.config)
        path = self.project_root / ".claude" / "settings.local.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertIn("Bash(python3:*)", data["permissions"]["allow"])
        self.assertIn("Bash(bash:*)", data["permissions"]["allow"])
        # Should also have hooks
        self.assertIn("hooks", data)
        self.assertIn("SessionStart", data["hooks"])

    def test_entry_count_interpolated(self):
        run_init(self.project_root, self.config)
        content = (self.project_root / "CLAUDE.md").read_text()
        self.assertIn("5 entries", content)

    def test_idempotent_second_run_skips(self):
        run_init(self.project_root, self.config)
        report2 = run_init(self.project_root, self.config)
        # CLAUDE.md should be skipped (has EFM section)
        self.assertIn("CLAUDE.md", report2.files_skipped)
        # hooks.json should be skipped (already has EFM hook)
        self.assertIn(".claude/hooks.json", report2.files_skipped)
        # settings should be skipped (already has EFM perms)
        self.assertIn(".claude/settings.local.json", report2.files_skipped)

    def test_force_updates_claude_md(self):
        run_init(self.project_root, self.config)
        report2 = run_init(self.project_root, self.config, force=True)
        self.assertIn("CLAUDE.md", report2.files_merged)

    def test_dry_run_no_files_created(self):
        report = run_init(self.project_root, self.config, dry_run=True)
        self.assertTrue(report.dry_run)
        # No actual files should exist
        self.assertFalse((self.project_root / "CLAUDE.md").exists())
        self.assertFalse(
            (self.project_root / ".claude" / "hooks.json").exists()
        )

    def test_dry_run_reports_what_would_happen(self):
        report = run_init(self.project_root, self.config, dry_run=True)
        # Should still report files_created
        self.assertGreater(len(report.files_created), 0)

    def test_append_to_existing_claude_md(self):
        """If CLAUDE.md exists without EFM section, append."""
        existing_content = "# My Project\n\nSome existing content.\n"
        (self.project_root / "CLAUDE.md").write_text(existing_content)

        report = run_init(self.project_root, self.config)
        self.assertIn("CLAUDE.md", report.files_merged)

        content = (self.project_root / "CLAUDE.md").read_text()
        # Original content preserved
        self.assertIn("My Project", content)
        self.assertIn("Some existing content", content)
        # EFM section added
        self.assertIn(_EFM_SECTION_START, content)
        # Separator
        self.assertIn("---", content)

    def test_skip_existing_claude_md_with_efm_section(self):
        """If CLAUDE.md already has EFM section, skip without force."""
        section = generate_ef_memory_section(self.config, entry_count=5)
        (self.project_root / "CLAUDE.md").write_text(f"# Proj\n\n{section}\n")

        report = run_init(self.project_root, self.config)
        self.assertIn("CLAUDE.md", report.files_skipped)

    def test_force_replaces_efm_section_in_claude_md(self):
        """With force=True, replace existing EFM section."""
        old_section = generate_ef_memory_section(self.config, entry_count=0)
        original = f"# Proj\n\n{old_section}\n\n# Other\n"
        (self.project_root / "CLAUDE.md").write_text(original)

        # Add more entries
        _write_events(self.project_root / ".memory" / "events.jsonl", 10)
        report = run_init(self.project_root, self.config, force=True)
        self.assertIn("CLAUDE.md", report.files_merged)

        content = (self.project_root / "CLAUDE.md").read_text()
        self.assertIn("10 entries", content)
        # Surrounding content preserved
        self.assertIn("# Proj", content)
        self.assertIn("# Other", content)

    def test_merge_hooks_with_existing(self):
        """Merge EFM hook into existing hooks.json."""
        claude_dir = self.project_root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        existing = {
            "hooks": {
                "post-edit": [{"type": "message", "message": "Custom hook"}]
            }
        }
        (claude_dir / "hooks.json").write_text(json.dumps(existing))

        report = run_init(self.project_root, self.config)
        self.assertIn(".claude/hooks.json", report.files_merged)

        data = json.loads((claude_dir / "hooks.json").read_text())
        self.assertIn("post-edit", data["hooks"])
        self.assertIn("pre-compact", data["hooks"])

    def test_merge_settings_with_existing(self):
        """Merge EFM permissions into existing settings."""
        claude_dir = self.project_root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        existing = {
            "permissions": {
                "allow": ["Bash(git:*)"]
            }
        }
        (claude_dir / "settings.local.json").write_text(json.dumps(existing))

        report = run_init(self.project_root, self.config)
        self.assertIn(".claude/settings.local.json", report.files_merged)

        data = json.loads((claude_dir / "settings.local.json").read_text())
        self.assertIn("Bash(git:*)", data["permissions"]["allow"])
        self.assertIn("Bash(python3:*)", data["permissions"]["allow"])

    def test_corrupt_hooks_json_warning(self):
        """Corrupt hooks.json should produce a warning, then create fresh."""
        claude_dir = self.project_root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "hooks.json").write_text("not valid json{{{")

        report = run_init(self.project_root, self.config)
        self.assertTrue(any("hooks.json" in w for w in report.warnings))
        # Should still create a valid hooks.json
        data = json.loads((claude_dir / "hooks.json").read_text())
        self.assertIn("hooks", data)

    def test_corrupt_settings_json_warning(self):
        """Corrupt settings.json should produce a warning, then create fresh."""
        claude_dir = self.project_root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.local.json").write_text("broken")

        report = run_init(self.project_root, self.config)
        self.assertTrue(any("settings.local.json" in w for w in report.warnings))

    def test_no_events_file(self):
        """Init should work even without events.jsonl (entry_count=0)."""
        os.unlink(self.project_root / ".memory" / "events.jsonl")
        report = run_init(self.project_root, self.config)
        content = (self.project_root / "CLAUDE.md").read_text()
        self.assertIn("0 entries", content)

    def test_duration_tracked(self):
        report = run_init(self.project_root, self.config)
        self.assertGreater(report.duration_ms, 0)

    def test_suggestions_populated(self):
        report = run_init(self.project_root, self.config)
        # At minimum should suggest gitignore items
        self.assertIsInstance(report.suggestions, list)

    def test_creates_claude_dir_structure(self):
        """Ensure .claude/ and .claude/rules/ are created."""
        run_init(self.project_root, self.config)
        self.assertTrue((self.project_root / ".claude").is_dir())
        self.assertTrue((self.project_root / ".claude" / "rules").is_dir())


# ===========================================================================
# Test: InitReport dataclass
# ===========================================================================

class TestInitReport(unittest.TestCase):

    def test_default_values(self):
        report = InitReport()
        self.assertEqual(report.files_created, [])
        self.assertEqual(report.files_skipped, [])
        self.assertEqual(report.files_merged, [])
        self.assertEqual(report.warnings, [])
        self.assertEqual(report.suggestions, [])
        self.assertFalse(report.dry_run)
        self.assertEqual(report.duration_ms, 0.0)

    def test_custom_values(self):
        report = InitReport(
            files_created=["a.md"],
            dry_run=True,
            duration_ms=42.5,
        )
        self.assertEqual(report.files_created, ["a.md"])
        self.assertTrue(report.dry_run)
        self.assertEqual(report.duration_ms, 42.5)



# ===========================================================================
# Test: run_upgrade (Step 3)
# ===========================================================================

class TestRunUpgrade(unittest.TestCase):
    """Tests for the --upgrade init mode."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_root = Path(self.tmpdir)
        memory_dir = self.project_root / ".memory"
        memory_dir.mkdir()
        _write_events(memory_dir / "events.jsonl", 5)
        self.config = _make_config()
        # Do initial init
        run_init(self.project_root, self.config)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_upgrade_updates_startup_rule(self):
        # Modify startup rule to simulate old version
        rule_path = self.project_root / ".claude" / "rules" / "ef-memory-startup.md"
        rule_path.write_text("# Old startup rule content\n")

        report = run_upgrade(self.project_root, self.config)
        content = rule_path.read_text()
        self.assertIn("Session Awareness", content)
        self.assertIn(".claude/rules/ef-memory-startup.md", report.files_merged)

    def test_upgrade_preserves_user_claude_md(self):
        # Add user content above EFM section
        claude_md = self.project_root / "CLAUDE.md"
        existing = claude_md.read_text()
        user_content = "# My Project\n\nThis is my project description.\n\n## Build Commands\n\n```bash\nnpm install\n```\n\n---\n\n"
        claude_md.write_text(user_content + existing)

        report = run_upgrade(self.project_root, self.config)
        new_content = claude_md.read_text()
        self.assertIn("My Project", new_content)
        self.assertIn("npm install", new_content)

    def test_upgrade_updates_efm_section(self):
        # Add more events to change entry count
        _write_events(self.project_root / ".memory" / "events.jsonl", 15)

        report = run_upgrade(self.project_root, self.config)
        content = (self.project_root / "CLAUDE.md").read_text()
        self.assertIn("15 entries", content)

    def test_upgrade_does_not_touch_events(self):
        events_path = self.project_root / ".memory" / "events.jsonl"
        original = events_path.read_text()

        run_upgrade(self.project_root, self.config)
        self.assertEqual(events_path.read_text(), original)

    def test_upgrade_stamps_efm_version_in_config(self):
        """Upgrade should stamp efm_version but preserve other config content."""
        config_path = self.project_root / ".memory" / "config.json"
        config_path.write_text('{"version": 3, "preset": "standard"}\n')

        run_upgrade(self.project_root, self.config)
        updated = json.loads(config_path.read_text())
        # Original fields preserved
        self.assertEqual(updated["version"], 3)
        self.assertEqual(updated["preset"], "standard")
        # Version stamp added
        from lib.config_presets import EFM_VERSION
        self.assertEqual(updated["efm_version"], EFM_VERSION)

    def test_upgrade_merges_settings(self):
        settings_path = self.project_root / ".claude" / "settings.local.json"
        existing = json.loads(settings_path.read_text())
        existing["permissions"]["allow"].append("Bash(custom:*)")
        settings_path.write_text(json.dumps(existing))

        run_upgrade(self.project_root, self.config)
        updated = json.loads(settings_path.read_text())
        self.assertIn("Bash(custom:*)", updated["permissions"]["allow"])
        self.assertIn("Bash(python3:*)", updated["permissions"]["allow"])

    def test_upgrade_warns_thin_claude_md(self):
        # CLAUDE.md with only EFM section (no project context)
        report = run_upgrade(self.project_root, self.config)
        thin_warnings = [w for w in report.warnings if "project context" in w.lower()]
        self.assertGreater(len(thin_warnings), 0)

    def test_upgrade_no_warn_rich_claude_md(self):
        # Add rich content
        claude_md = self.project_root / "CLAUDE.md"
        lines = [f"Line {i}: Important project information" for i in range(15)]
        rich_content = "\n".join(lines) + "\n\n---\n\n"
        existing_efm = generate_ef_memory_section(self.config, 5)
        claude_md.write_text(rich_content + existing_efm + "\n")

        report = run_upgrade(self.project_root, self.config)
        thin_warnings = [w for w in report.warnings if "project context" in w.lower()]
        self.assertEqual(len(thin_warnings), 0)

    def test_upgrade_dry_run(self):
        # Record current state
        rule_path = self.project_root / ".claude" / "rules" / "ef-memory-startup.md"
        original_rule = rule_path.read_text()

        report = run_upgrade(self.project_root, self.config, dry_run=True)
        self.assertTrue(report.dry_run)
        # Files should not be modified
        self.assertEqual(rule_path.read_text(), original_rule)

    def test_upgrade_appends_efm_to_non_efm_project(self):
        # Remove EFM markers from CLAUDE.md
        claude_md = self.project_root / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nNo EFM section here.\n")

        report = run_upgrade(self.project_root, self.config)
        content = claude_md.read_text()
        self.assertIn(_EFM_SECTION_START, content)
        self.assertIn("My Project", content)


# ===========================================================================
# Test: _atomic_write_json (C4)
# ===========================================================================

class TestAtomicWriteJson(unittest.TestCase):

    def test_atomic_write_creates_file(self):
        """Writes valid JSON atomically to a new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            data = {"key": "value", "number": 42}
            _atomic_write_json(path, data)
            self.assertTrue(path.exists())
            loaded = json.loads(path.read_text())
            self.assertEqual(loaded["key"], "value")
            self.assertEqual(loaded["number"], 42)

    def test_atomic_write_overwrites_existing(self):
        """Existing file is updated atomically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            path.write_text('{"old": true}\n')
            _atomic_write_json(path, {"new": True})
            loaded = json.loads(path.read_text())
            self.assertTrue(loaded["new"])
            self.assertNotIn("old", loaded)

    def test_atomic_write_no_corruption_on_error(self):
        """If write fails, original file is preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            original_data = {"preserved": True}
            path.write_text(json.dumps(original_data) + "\n")

            # Create an object that will fail JSON serialization
            class BadObj:
                pass

            try:
                _atomic_write_json(path, {"bad": BadObj()})
            except (TypeError, Exception):
                pass  # Expected to fail

            # Original should still be intact
            loaded = json.loads(path.read_text())
            self.assertTrue(loaded["preserved"])


# ===========================================================================
# Test: _read_raw_json (C2)
# ===========================================================================

class TestReadRawJson(unittest.TestCase):

    def test_reads_valid_json(self):
        from lib.init import _read_raw_json
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            path.write_text('{"key": "value"}\n')
            result = _read_raw_json(path)
            self.assertEqual(result["key"], "value")

    def test_missing_file_returns_none(self):
        from lib.init import _read_raw_json
        result = _read_raw_json(Path("/nonexistent/file.json"))
        self.assertIsNone(result)

    def test_corrupt_json_returns_none(self):
        from lib.init import _read_raw_json
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.json"
            path.write_text("not valid json{{{")
            result = _read_raw_json(path)
            self.assertIsNone(result)

    def test_empty_file_returns_none(self):
        from lib.init import _read_raw_json
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.json"
            path.write_text("")
            result = _read_raw_json(path)
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
