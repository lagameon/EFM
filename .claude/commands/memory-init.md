# /memory-init — Initialize EF Memory auto-startup

## Quick Start

```bash
python3 .memory/scripts/init_cli.py --preset standard
```

Presets: `minimal` (try EFM, human review on) | `standard` (most projects, auto-harvest on) | `full` (requires API keys for embeddings + LLM reasoning)

## Purpose

Generate or update the auto-startup files that make every Claude Code session
aware of the EF Memory system. Run this once when first adding EF Memory to a
project, or re-run to update after configuration changes.

---

## What it generates

| File | Purpose | Existing file handling |
|------|---------|----------------------|
| `CLAUDE.md` | Tier 1 auto-load — session awareness | Appends EF Memory section (preserves existing content) |
| `.claude/rules/ef-memory-startup.md` | Tier 2 auto-load — brief rule | Creates or overwrites (EFM-owned) |
| `.claude/hooks.json` | Pre-compact reminder (legacy) | Merges EF Memory hook (preserves other hooks) |
| `.claude/settings.local.json` | Permissions + **all 5 hooks** (SessionStart, PreToolUse:Edit\|Write, PreToolUse:EnterPlanMode, Stop, PreCompact) | Merges EFM permissions and hooks (preserves existing) |

---

## Usage

Run the init CLI:

```bash
# Standard init (current project)
python3 .memory/scripts/init_cli.py

# Preview without writing files
python3 .memory/scripts/init_cli.py --dry-run

# Force update existing EF Memory sections
python3 .memory/scripts/init_cli.py --force

# Init a different project
python3 .memory/scripts/init_cli.py --target /path/to/project
```

---

## Behavior

### For new projects (no existing files)
- Creates all 4 files from templates
- Interpolates entry count from `.memory/events.jsonl`
- Respects `automation.human_review_required` config

### For existing projects (files already present)
- **CLAUDE.md**: Appends EF Memory section at end with `---` separator.
  If EF Memory section already exists, skips (use `--force` to update).
- **hooks.json**: Reads existing hooks, adds EF Memory `pre-compact` message
  hook if not present. Never duplicates. (Legacy — main hooks are in settings.local.json.)
- **settings.local.json**: Merges EFM permissions (`Bash(python3:*)`, `Bash(bash:*)`)
  **and all 5 automation hooks** (SessionStart, PreToolUse:Edit|Write, PreToolUse:EnterPlanMode,
  Stop harvest/scan, PreCompact). Never removes existing permissions or hooks.
- **ef-memory-startup.md**: Always written (EFM-owned file).

### Post-init scan
After writing files, scans the project for advisory suggestions:
- Missing `.gitignore` entries for `.memory/working/` and `vectors.db`

### Auto-scan for existing projects

**When `scan.auto_scan_on_init` is `true` (default)** and the init report
shows suggestions about importable documents:

1. Automatically run the document scanner:
   ```bash
   python3 .memory/scripts/scan_cli.py discover --json
   ```

2. If documents are found, present the discovery table to the user
   (same format as `/memory-scan` Step 1):

   ```
   Found <N> importable documents:

     #  | Score | Path                              | Type   | Status
     ---|-------|-----------------------------------|--------|--------
     1  | 0.95  | docs/INCIDENTS.md                 | md     | New
     2  | 0.88  | docs/DECISIONS.md                 | md     | New
     ...

   Would you like to import memory from these documents?
   Enter numbers (e.g., "1,2,3"), "all", "new", or "skip":
   ```

3. If the user selects documents, proceed with the `/memory-scan`
   extraction workflow (Step 3–5 from memory-scan.md):
   - Read each selected document
   - Extract MEMORY ENTRY candidates using extraction rules
   - Convert to JSON, validate, dedup
   - Persist (respecting `human_review_required` config)

4. If the user says "skip", finish init without importing.

**When `scan.auto_scan_on_init` is `false`:**
- Only show advisory suggestions (old behavior):
  `> Found 12 documents in docs/ — consider /memory-import to extract knowledge`

### Auto-generate rules (always runs)

After init (and auto-scan if applicable), **always** run the pipeline
to generate Hard rules and sync search index:

```bash
python3 .memory/scripts/pipeline_cli.py
```

This ensures `.claude/rules/ef-memory/*.md` files are created from any
existing Hard entries in `events.jsonl`. Without this step, memory
entries are stored but invisible to Claude Code sessions.

If there are no entries in `events.jsonl`, the pipeline completes instantly
with nothing to generate.

---

## Re-running

Init is idempotent. Running it again:
- Skips files with existing EF Memory sections (unless `--force`)
- Merges safely into hooks.json and settings.local.json
- Auto-scan only triggers if new unimported documents are found

Use `--force` to refresh EF Memory sections (e.g., after config changes
or version upgrades).

---

## Output

```
EF Memory Init — /path/to/project

Created:
  + CLAUDE.md
  + .claude/rules/ef-memory-startup.md

Merged:
  ~ .claude/hooks.json
  ~ .claude/settings.local.json

Suggestions:
  > Consider adding to .gitignore: .memory/working/

Auto-scan: Found 12 importable documents (see table below)

Done (4 files processed, 15ms)
```
