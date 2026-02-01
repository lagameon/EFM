# EF Memory for Claude

> Evidence-first project memory for Claude Code CLI

A **safe, auditable memory system** that turns project incidents, constraints, and hard-earned lessons into reusable engineering knowledge.

**This is not chat history. This is project memory.**

---

## Purpose

Most teams lose critical knowledge over time:
- Incidents are repeated
- Constraints are forgotten
- AI assistants confidently reintroduce old mistakes

This system solves that by enforcing:
- **Evidence-first memory** — every entry has a verifiable source
- **Executable rules** — what MUST / MUST NOT be done
- **Human-in-the-loop control** — no silent writes
- **Zero side effects by default**

---

## Security Boundaries

```
This system will NEVER:
  - Write files without explicit human request
  - Execute verify commands (static analysis only)
  - Dump all memory entries (max 5 per search)
  - Auto-persist imported entries
  - Modify existing entries (append-only)

This system will ALWAYS:
  - Require human approval for persistence
  - Report "No files were modified" after read-only operations
  - Distinguish between "checked" and "assumed" results
```

**Critical Security Guarantee**: The `/memory-verify` command performs **static analysis only** on verify fields. It checks whether the command *looks* safe (read-only patterns, no dangerous commands) but **NEVER actually executes** the verify command. This is a hard security boundary that cannot be overridden.

---

## Command Workflow

```
┌─────────────────┐
│ INCIDENTS.md    │  (your project documents)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ /memory-import  │  (extract candidates, DRY-RUN only)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Human Review    │  (verify, edit, approve)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ /memory-save    │  (format entry, output only)
└────────┬────────┘
         │
         ▼ (explicit append request)
┌─────────────────┐
│ events.jsonl    │  (append-only storage)
└────────┬────────┘
         │
         ├──────────────────┐
         ▼                  ▼
┌─────────────────┐  ┌─────────────────┐
│ /memory-search  │  │ /memory-verify  │
│ (query memory)  │  │ (integrity check)│
└─────────────────┘  └─────────────────┘
```

| Command | Purpose | Writes Files? |
|---------|---------|---------------|
| `/memory-import` | Extract candidates from documents | **Never** |
| `/memory-save` | Format and display entry | **Never** |
| `/memory-search` | Query existing memory | **Never** |
| `/memory-verify` | Check integrity | **Never** |

---

## Quick Start (3 Steps)

### Step 1: Copy to your project

```bash
# Clone template
git clone https://github.com/anthropics/ef-memory-for-claude.git
cd ef-memory-for-claude

# Copy to your project
cp -r .memory /path/to/your-project/
cp -r .claude /path/to/your-project/
```

Or use this repo as a **GitHub Template**.

### Step 2: Configure paths

Edit `.memory/config.json`:

```json
{
  "paths": {
    "CODE_ROOTS": ["src/", "lib/"],
    "DOCS_ROOT": "docs/",
    "INCIDENTS_FILE": "docs/INCIDENTS.md"
  }
}
```

### Step 3: Choose an archetype (optional)

```bash
# For quant projects
cp archetypes/quant/memory.config.patch.json .memory/
# Then manually merge paths_override and rulesets into config.json
```

---

## Archetype Selection

| Archetype | Best For | Additional Checks |
|-----------|----------|-------------------|
| **quant** | Trading systems, backtesting | Leakage, shift, rolling, train-live sync |
| **ml** | ML pipelines, model training | Data split, feature scaling, drift |
| **web** | Web apps, API services | Validation, auth, injection |
| *(none)* | General projects | Core schema + source checks only |

---

## Configuration

### `.memory/config.json`

```json
{
  "$schema": "./config.schema.json",
  "version": "1.0",

  "paths": {
    "CODE_ROOTS": ["src/"],
    "DOCS_ROOT": "docs/",
    "INCIDENTS_FILE": "docs/INCIDENTS.md"
  },

  "verify": {
    "rulesets": [".memory/rules/verify-core.rules.json"],
    "fail_on_missing_path": false,
    "staleness_threshold_days": 90
  },

  "search": {
    "max_results": 5,
    "priority": ["hard", "soft"],
    "severity_order": ["S1", "S2", "S3"]
  }
}
```

### Path Variables

Rules reference paths using `${paths.CODE_ROOTS}` syntax:

| Variable | Default | Description |
|----------|---------|-------------|
| `${paths.CODE_ROOTS}` | `["src/"]` | Main source directories |
| `${paths.DOCS_ROOT}` | `"docs/"` | Documentation root |
| `${paths.INCIDENTS_FILE}` | `"docs/INCIDENTS.md"` | Incident log location |

---

## Directory Structure

```
.memory/
├── SCHEMA.md              # Storage contract (v1.0)
├── config.json            # Project configuration
├── config.schema.json     # JSON Schema for config
├── events.jsonl           # Memory storage (append-only)
└── rules/
    └── verify-core.rules.json   # Core verification rules

.claude/commands/
├── memory-save.md         # Entry creation workflow
├── memory-search.md       # Query workflow
├── memory-import.md       # Import workflow (dry-run)
└── memory-verify.md       # Integrity check workflow
```

---

## FAQ

### 1. Will this system write files automatically?

**No.** All four commands are read-only by default. File writes only happen when you explicitly request them (e.g., "append this to events.jsonl").

### 2. What happens if I run `/memory-search --all`?

It's forbidden. The system returns a maximum of 5 entries to prevent context overflow and accidental full dumps.

### 3. Can `/memory-verify` execute the verify commands in my entries?

**No.** Verify commands are analyzed statically for safety but never executed. The system only uses `grep`, `find`, and similar read-only tools to check patterns.

### 4. What if my source file moves or changes?

Run `/memory-verify` periodically. It will detect:
- Missing files (FAIL)
- Anchor drift (WARN)
- Stale entries >90 days (WARN)

Then manually update entries via `/memory-save` + append.

### 5. How do I update an existing entry?

Append a new version with the same `id`. The system uses append-only semantics — latest entry wins. Old versions remain for audit trail.

### 6. What's the difference between Hard and Soft memory?

| Type | Meaning | When to Use |
|------|---------|-------------|
| **Hard** | Violations invalidate results or cause incidents | Production bugs, data leakage, security issues |
| **Soft** | Contextual knowledge, not strictly enforced | Best practices, preferences, non-critical patterns |

### 7. Can I use this without Claude Code CLI?

The memory format (JSONL + SCHEMA.md) is tool-agnostic. The `.claude/commands/` files are specific to Claude Code CLI but the principles apply anywhere.

---

## Storage Format

All memory lives in `.memory/events.jsonl`:

- **One JSON object per line** (strict JSONL)
- **Append-only** — never modify existing lines
- **Git-tracked** — full audit history

See `.memory/SCHEMA.md` for field definitions.

---

## Hard vs Soft Memory

| Classification | Meaning | Severity |
|----------------|---------|----------|
| **Hard** | Violations invalidate results or cause incidents | S1, S2 |
| **Soft** | Contextual knowledge | S2, S3 |

Severity scale:
- **S1** — Invalidates all results / production incident
- **S2** — Significant impact / degraded quality
- **S3** — Minor issue / good to know

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

**Key rule**: Any change that violates the security boundaries will be rejected.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Version

| Component | Version |
|-----------|---------|
| Schema | 1.0 |
| Config | 1.0 |
| Commands | 1.1 |
