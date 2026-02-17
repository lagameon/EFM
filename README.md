# EFM — Evidence-First Memory for Claude Code

> Structured JSONL storage, hybrid search (embedding + FTS5), auto-harvest from conversations & plan sessions, 10 slash commands, compaction + time-sharded archive, fully automated via hooks.

A **safe, auditable memory skill system** that turns project incidents, constraints, and hard-earned lessons into reusable engineering knowledge. Inspired by the workspace memory architecture of [OpenClaw](https://github.com/pinkpixel-dev/OpenClaw) (moltbot) — but built specifically for Claude Code's skill system with evidence-first guarantees.

**This is not chat history. This is project memory.**

---

## Inspired By: OpenClaw / moltbot Memory

This project shares the same philosophy as [OpenClaw](https://github.com/pinkpixel-dev/OpenClaw) (moltbot)'s memory system — persistent, workspace-integrated, embedding-powered agent memory — but takes a different approach for Claude Code:

| | OpenClaw / moltbot | EFM |
|---|---|---|
| **Interface** | CLI commands (`openclaw memory ...`) | Claude Code skills (`/memory-save`, `/memory-search`, ...) |
| **Storage** | Markdown files + workspace | Structured JSONL + SQLite vector DB |
| **Retrieval** | Embedding search | 4-level degradation (Hybrid → Vector → Keyword → Basic) |
| **Safety** | Auto memory flush | Configurable human-in-the-loop (`human_review_required`) |
| **Typing** | Free-form | Schema-enforced (type, severity, source, verify) |
| **Lifecycle** | Manual | Auto-verify, confidence decay, dedup clustering |
| **Injection** | Plugin-based | Auto-inject Hard rules to `.claude/rules/` |

Both systems believe that **AI agents need durable project memory** — not just conversation history, but structured knowledge that survives across sessions.

---

## Purpose

Most teams lose critical knowledge over time:
- Incidents are repeated
- Constraints are forgotten
- AI assistants confidently reintroduce old mistakes

This system solves that by enforcing:
- **Evidence-first memory** — every entry has a verifiable source
- **Executable rules** — what MUST / MUST NOT be done
- **Configurable safety** — human review by default, auto-persist option for trusted workflows
- **Schema validation** — all writes pass structural checks regardless of mode

---

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │               EFM V3 Runtime                    │
                    │                                              │
  Event Sources      │   Layer 4: Working Memory (V3)               │
  ─────────────     │   ├── Session files (task_plan / findings)   │
  · file edit       │   ├── Auto-prefill from long-term memory     │
  · test fail/pass  │   └── Harvest extraction → /memory-save      │
  · git commit      │                                              │
  · manual /cmd     │   Layer 3: LLM Reasoning                    │
  · /memory-plan    │   ├── Cross-memory correlation               │
       │            │   ├── Contradiction detection                │
       │            │   ├── Knowledge synthesis                    │
       │            │   └── Context-aware risk assessment          │
       │            │                                              │
       │            │   Layer 2: Semantic Retrieval                │
       │            │   ├── Embedding (Gemini / OpenAI / Ollama)   │
       │            │   ├── BM25 full-text search (FTS5)           │
       ▼            │   └── Hybrid search + Re-rank                │
  ┌─────────┐       │                                              │
  │ Drafts  │──────▶│   Layer 1: Structured Rules                  │
  │ (queue) │       │   ├── .claude/rules/ Bridge (auto-inject)    │
  └─────────┘       │   └── Hard entries → domain rule files       │
       │            │                                              │
       │ human      │   Storage: events.jsonl (source of truth)    │
       │ approval   │   Index:   vectors.db (SQLite, derived)      │
       ▼            │   Cache:   .claude/rules/ef-memory/ (derived)│
  events.jsonl      └──────────────────────────────────────────────┘

                    Automation Engine:
                    ├── Auto-Verify:  schema + source drift detection
                    ├── Auto-Capture: event → draft queue → approval
                    ├── Auto-Sync:    events.jsonl → vectors.db + FTS
                    ├── Auto-Evolve:  dedup / confidence decay / deprecation
                    ├── Auto-Reason:  LLM correlation / contradiction / synthesis
                    ├── Auto-Harvest: working memory → memory candidates (V3)
                    └── Auto-Compact: hot/archive split + quarterly sharding (V3)
```

### Three-Layer Retrieval (4-Level Degradation)

| Level | Mode | Requirements | Score Formula |
|-------|------|-------------|---------------|
| 1 | Hybrid | Embedder + FTS5 | `bm25×0.4 + vector×0.6 + boost + confidence` |
| 2 | Vector | Embedder only | `vector×1.0 + boost + confidence` |
| 3 | Keyword | FTS5 only | `bm25×1.0 + boost + confidence` |
| 4 | Basic | None (zero deps) | token overlap + confidence on JSONL |

Hard+S1 entries get a +0.15 re-rank boost; entries with higher `_meta.confidence` get an additional configurable boost (default weight 0.1). The system always returns results regardless of available infrastructure.

---

## Security Boundaries

### When `human_review_required: true` (template default)

```
/memory-save, /memory-import, /memory-scan will:
  - Display candidates but NOT write to events.jsonl
  - Require explicit human approval before persisting
  - Report "No files were modified" after read-only operations

This system will NEVER:
  - Execute verify commands (static analysis only)
  - Dump all memory entries (max 5 per search)
  - Modify existing entries (append-only)
  - Auto-promote drafts to events.jsonl
```

### When `human_review_required: false`

```
/memory-save, /memory-import, /memory-scan will:
  - Validate schema and source before writing
  - Directly append valid entries to events.jsonl
  - Run the automation pipeline after writing
  - Still NEVER invent sources or skip validation

All other boundaries remain unchanged:
  - /memory-verify still never executes commands
  - /memory-search still returns max 5 entries
  - Entries are still append-only
  - Read-only commands (/memory-evolve, /memory-reason) never write
```

### How to toggle

```json
// In .memory/config.json → automation section:
"human_review_required": true    // default — manual approval required
"human_review_required": false   // auto-persist after validation
```

Or tell Claude: **"turn off memory review"** / **"turn on memory review"**

**Critical Security Guarantee**: The `/memory-verify` command performs **static analysis only** on verify fields. It checks whether the command *looks* safe (read-only patterns, no dangerous commands) but **NEVER actually executes** the verify command. This is a hard security boundary that cannot be overridden.

---

## Command Workflow

```
┌─────────────────┐
│ Any Document    │  (*.md, *.py, *.ts — any structured document)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ /memory-import  │  (extract candidates from document)
│ /memory-scan    │  (batch scan multiple documents)
└────────┬────────┘
         │
         ▼
┌─────────────────┐   human_review_required: true
│ Human Review    │──────────────────────────────┐
│ (verify, edit)  │                              │ false: auto-persist
└────────┬────────┘                              │
         │ approved                              │
         ▼                                       ▼
┌─────────────────┐                    ┌─────────────────┐
│ /memory-save    │  (validate +       │ Auto-persist     │
│                 │   persist entry)   │ (validate + write)│
└────────┬────────┘                    └────────┬────────┘
         │                                      │
         └──────────────┬───────────────────────┘
                        ▼
               ┌─────────────────┐
               │ events.jsonl    │  (append-only storage)
               └────────┬────────┘
                        │
                        ├── pipeline (sync + rules + evolution + reasoning)
                        │
                ┌───────┴───────┐
                ▼               ▼
       ┌─────────────────┐  ┌─────────────────┐
       │ /memory-search  │  │ /memory-verify  │
       │ (query memory)  │  │ (integrity check)│
       └─────────────────┘  └─────────────────┘
```

| Command | Purpose | Writes Files? |
|---------|---------|---------------|
| `/memory-save` | Validate and persist memory entry | `events.jsonl` + pipeline outputs (after approval or auto when `human_review_required=false`) |
| `/memory-import` | Extract candidates from any document | `events.jsonl` when `human_review_required=false`; otherwise display only |
| `/memory-scan` | Batch document scanning and extraction | `events.jsonl` when `human_review_required=false`; otherwise display only |
| `/memory-search` | Query existing memory | Never |
| `/memory-verify` | Check integrity (static analysis) | Never |
| `/memory-plan` | Working memory session management (V3) | `.memory/working/` session files |
| `/memory-init` | Initialize auto-startup files (V3); `--upgrade` for safe in-place upgrade | `CLAUDE.md`, `.claude/rules/`, `settings.local.json` (permissions + 5 hooks), `hooks.json` (legacy) |
| `/memory-evolve` | Memory health & evolution analysis (V3) | Never (read-only report) |
| `/memory-reason` | Cross-memory reasoning analysis (V3) | Never (read-only report) |
| `/memory-compact` | Compact events.jsonl + archive history (V3) | `events.jsonl` (rewrite), `archive/` (append) |

---

## V2 Capabilities

EFM V2 adds six milestones of infrastructure on top of the core template:

### M1: Embedding Layer
Multi-provider embedding support (Gemini, OpenAI, Ollama) with SQLite vector storage, FTS5 full-text index, and incremental sync engine.

```bash
python3 .memory/scripts/sync_embeddings.py          # Sync events → vectors.db
python3 .memory/scripts/sync_embeddings.py --full    # Full rebuild
```

### M2: Hybrid Search Engine
BM25 + Vector fusion search with 4-level graceful degradation. Works without any embedding provider (falls back to keyword then basic mode).

```bash
python3 .memory/scripts/search_cli.py "leakage shift"       # Search
python3 .memory/scripts/search_cli.py --debug "shift"        # Show score breakdown
```

### M3: Layer 1 Auto-Inject
Hard memory entries automatically generate `.claude/rules/ef-memory/*.md` files, so Claude Code loads relevant rules when editing matching files.

```bash
python3 .memory/scripts/generate_rules_cli.py                # Generate rule files
python3 .memory/scripts/generate_rules_cli.py --dry-run       # Preview only
python3 .memory/scripts/generate_rules_cli.py --clean          # Remove generated files
```

### M4: Automation Engine
Three automation subsystems: schema/source verification, draft queue with human-in-the-loop approval, and pipeline orchestration.

```bash
python3 .memory/scripts/verify_cli.py                         # Verify all entries
python3 .memory/scripts/verify_cli.py --id=<id>               # Verify single entry
python3 .memory/scripts/capture_cli.py list                    # List pending drafts
python3 .memory/scripts/capture_cli.py approve <filename>      # Approve draft → events.jsonl
python3 .memory/scripts/pipeline_cli.py                        # Run full pipeline
python3 .memory/scripts/pipeline_cli.py --startup              # Startup health check
```

### M5: Memory Evolution
Memory health and lifecycle management: duplicate clustering, confidence scoring with exponential decay, deprecation suggestions, and merge recommendations.

```bash
python3 .memory/scripts/evolution_cli.py                       # Full evolution report
python3 .memory/scripts/evolution_cli.py --duplicates          # Find duplicate clusters
python3 .memory/scripts/evolution_cli.py --confidence          # Score all entries
python3 .memory/scripts/evolution_cli.py --deprecations        # Suggest deprecations
python3 .memory/scripts/evolution_cli.py --merges              # Suggest merges
python3 .memory/scripts/evolution_cli.py --id=<id>             # Single entry confidence
```

**Confidence scoring model** (0.0–1.0):
```
score = 0.30 × source_quality + 0.30 × age_decay + 0.15 × verification_boost + 0.25 × source_validity
```

All evolution functions are **advisory only** — they never modify events.jsonl.

### M6: LLM Reasoning Layer
Cross-memory correlation, contradiction detection, knowledge synthesis, and context-aware risk assessment. Multi-provider LLM support (Anthropic Claude, OpenAI GPT, Google Gemini, Ollama) with automatic heuristic fallback.

```bash
python3 .memory/scripts/reasoning_cli.py                    # Full reasoning report
python3 .memory/scripts/reasoning_cli.py --correlations      # Cross-memory correlations
python3 .memory/scripts/reasoning_cli.py --contradictions    # Contradiction detection
python3 .memory/scripts/reasoning_cli.py --syntheses         # Knowledge synthesis
python3 .memory/scripts/reasoning_cli.py --risks "query"     # Context-aware risk assessment
python3 .memory/scripts/reasoning_cli.py --no-llm            # Force heuristic-only mode
python3 .memory/scripts/search_cli.py --annotate "leakage"   # Search with risk annotations
```

**Two-stage architecture:**
```
Stage 1 — Heuristic (zero LLM cost):
  ├── Tag overlap → correlation groups
  ├── MUST/NEVER keyword opposition → contradiction candidates
  ├── Tag clustering → synthesis candidates
  └── Staleness/confidence → risk annotations

Stage 2 — LLM Enrichment (optional):
  ├── Semantic correlation discovery
  ├── Deep contradiction analysis
  ├── Principle text generation
  └── Context-aware risk explanation
```

All reasoning functions are **advisory only** — they never modify events.jsonl. Without LLM provider SDKs, the system automatically degrades to heuristic-only mode.

---

## V3 Capabilities

EFM V3 adds automatic startup, working memory, and lifecycle automation:

### M7: Project Init & Auto-Startup
One-command initialization generates all Claude Code integration files. Safe merge for existing projects — CLAUDE.md is appended (not overwritten), settings.local.json merges permissions and hooks.

```bash
python3 .memory/scripts/init_cli.py                    # Init current project
python3 .memory/scripts/init_cli.py --dry-run           # Preview changes
python3 .memory/scripts/init_cli.py --force             # Overwrite existing files
python3 .memory/scripts/init_cli.py --upgrade            # Safe in-place upgrade (preserves user content)
python3 .memory/scripts/init_cli.py --target /path/to   # Init another project
```

Generated files: `CLAUDE.md` (EFM section), `.claude/rules/ef-memory-startup.md`, `.claude/settings.local.json` (permissions + 5 automation hooks), `.claude/hooks.json` (legacy pre-compact).

**`--upgrade` mode** (V3.1): Safely updates an existing EFM installation. Replaces only the EFM section markers in CLAUDE.md (preserves all user content), force-updates the startup rule, merges hooks and settings. Does NOT touch `events.jsonl`, `config.json` content, `working/`, or `drafts/`. Stamps the current EFM version. Warns if CLAUDE.md has thin project context (<10 lines before EFM section).

### M8: Working Memory (PWF Integration)
Session-scoped working memory inspired by Planning with Files. Three markdown files (`task_plan.md`, `findings.md`, `progress.md`) in `.memory/working/` act as short-term RAM while EFM serves as long-term disk.

```bash
python3 .memory/scripts/working_memory_cli.py start "refactor auth module"  # Start session
python3 .memory/scripts/working_memory_cli.py status                        # Check progress
python3 .memory/scripts/working_memory_cli.py resume                        # Resume session
python3 .memory/scripts/working_memory_cli.py harvest                       # Extract memory candidates
python3 .memory/scripts/working_memory_cli.py clear                         # End session
```

Or use the `/memory-plan` command for the full workflow.

**Auto-prefill**: On session start, EFM is searched and relevant entries are injected into `findings.md`.

**Harvest patterns**: LESSON, CONSTRAINT, DECISION, WARNING markers, MUST/NEVER statements, and Error→Fix pairs are automatically extracted as `/memory-save` candidates.

### M9: Memory Lifecycle Automation
Full closed-loop lifecycle automation via Claude Code hooks. No manual steps required.

```bash
python3 .memory/scripts/pipeline_cli.py --harvest-only   # Run harvest step
python3 .memory/scripts/pipeline_cli.py --startup         # Shows active session info
```

**Automatic lifecycle flow**:
```
Enter Plan Mode
  ↓ (EnterPlanMode hook)
  auto start_session() + prefill findings.md
  ↓
Claude works (edits trigger memory search via Edit|Write hook)
  ↓
Session ends (Stop hook)
  ↓ auto_harvest_and_persist()
  harvest candidates → convert to entries → write events.jsonl
  → run pipeline (sync + rules + evolution + reasoning)
  → clear session
  ↓
Next session (SessionStart hook)
  → startup health check
  → next plan mode prefills from newly saved entries
```

### M10: Conversation Context Auto-Save
Normal conversations (without Plan Mode) are also scanned for memory-worthy patterns. On session stop, the Stop hook reads the conversation transcript and creates draft entries for human review.

**How it works**:
```
Normal conversation (no Plan Mode)
  ↓
Session ends (Stop hook)
  ↓ No working memory session detected
  ↓ Read transcript_path from hook stdin
  scan_conversation_for_drafts()
  → extract assistant messages from JSONL transcript
  → apply 6 harvest patterns (LESSON/CONSTRAINT/DECISION/WARNING/MUST-NEVER/Error-Fix)
  → create drafts in .memory/drafts/ (NOT events.jsonl)
  → remind user to review via /memory-save
```

**Key safety properties**:
- Drafts only — never writes directly to `events.jsonl`
- Never blocks — only returns `additionalContext` (informational)
- Configurable via `v3.auto_draft_from_conversation` toggle

### M11: events.jsonl Compaction + Time-Sharded Archive
`events.jsonl` is append-only and accumulates superseded versions and deprecated entries. M11 adds compaction to keep the hot file clean and archive history by quarter.

**How it works**:
```
events.jsonl grows over time (superseded lines, deprecated entries)
  ↓
Waste ratio exceeds threshold (default 2.0×)
  ↓ Auto-trigger on session stop OR manual /memory-compact
compact(events_path, archive_dir, config)
  → resolve latest-wins (one line per ID)
  → partition: KEEP (active) vs ARCHIVE (superseded + deprecated)
  → archive by quarter → .memory/archive/events_YYYYQN.jsonl
  → atomic rewrite events.jsonl (os.replace)
  → reset vectordb sync cursor
  → log to compaction_log.jsonl
```

**Key design properties**:
- Zero consumer changes — all loaders read `events.jsonl` as before, just smaller
- Atomic rewrite — crash-safe via `os.replace()`
- Auto + manual — Stop hook auto-compacts above threshold; `/memory-compact` for manual control

---

## V3.1 Quality & Lifecycle Improvements

Five improvements to harvest quality, deployment safety, and observability:

### Harvest Quality Gate (Step 1)
Auto-harvest now filters low-quality candidates before writing to `events.jsonl`. Two new functions guard the pipeline:

- **`_clean_markdown_artifacts()`** — strips pipe chars, bold markers, backticks, horizontal rules from extracted text
- **`_is_viable_candidate()`** — rejects entries with short titles (<15 chars), boilerplate-only content, or title-repeating content
- **Confidence penalties** — entries without `rule` AND `implication` get -0.15; short titles get -0.05; content that repeats the title gets -0.10

Configurable: `automation.min_content_length` (default 15, range 5–100) in `config.json`.

### Session-Level Dedup (Step 2)
Prevents duplicate entries when the Stop hook fires multiple times in the same conversation. Uses `conversation_id` from hook input to track which entries have already been written in the current session. The conversation ID is stored in `_meta.conversation_id` for audit.

### Init `--upgrade` Mode (Step 3)
Safe upgrade path for existing EFM installations:

```bash
python3 .memory/scripts/init_cli.py --upgrade           # Upgrade in-place
python3 .memory/scripts/init_cli.py --upgrade --dry-run  # Preview upgrade
```

- Replaces only the EFM section markers in CLAUDE.md (preserves user content)
- Force-updates `.claude/rules/ef-memory-startup.md`
- Merges settings and hooks (never removes existing)
- Stamps `efm_version` in `config.json`
- Warns on thin CLAUDE.md (<10 lines of project context)
- Mutually exclusive with `--force`

### Version Tracking (Step 4)
EFM now tracks its installed version (`EFM_VERSION` in `config_presets.py`):

- `init` and `--upgrade` stamp `efm_version` into `config.json`
- Startup health check compares installed vs. current version
- When a mismatch is detected, the startup hint suggests: `run /memory-init --upgrade`

### Waste Ratio Enhancement (Step 5)
Startup hint now shows specific waste line counts when suggesting compaction. Instead of a generic message, users see exactly how many obsolete lines exist (e.g., "42 obsolete lines"), making it easier to decide whether to compact.

---

## Automation & Hooks

EFM uses **Claude Code hooks** for event-driven automation — no background daemons or cron jobs.

### Hook Architecture

| Hook | Event | Script | What it does |
|------|-------|--------|-------------|
| **SessionStart** | Session begins | `session_start.sh` | Startup health check (<100ms) |
| **PreToolUse: Edit\|Write** | Before file edit | `pre_edit_search.py` | Search memory for relevant entries |
| **PreToolUse: EnterPlanMode** | Plan mode entry | `plan_start.py` | Auto-start working memory session |
| **Stop** | Response complete | `stop_harvest.py` | Auto-harvest session (with quality gate + session dedup) OR scan conversation → drafts; auto-compact if waste ratio ≥ threshold |
| **PreCompact** | Context compaction | echo | Reminder to preserve session state |

### Pipeline Steps

The automation pipeline runs automatically on session stop (and can be invoked manually):

```
sync_embeddings  → Update FTS5 index (+ vector embeddings if enabled)
generate_rules   → Regenerate .claude/rules/ef-memory/*.md from Hard entries
evolution_check  → Duplicate detection, confidence scoring, deprecation suggestions
reasoning_check  → Cross-memory correlation, contradiction detection, synthesis
```

### Automation Config Toggles

```json
{
  "v3": {
    "auto_startup": true,                  // SessionStart hook health check
    "auto_start_on_plan": true,            // Auto-start session on plan mode
    "auto_harvest_on_stop": true,          // Auto-harvest + persist on stop
    "auto_draft_from_conversation": true,  // Scan conversation → drafts on stop
    "draft_auto_expire_days": 7,           // Auto-delete drafts older than N days (0=never)
    "session_recovery": true,              // Detect stale sessions at startup
    "prefill_on_plan_start": true,         // Prefill findings with EFM
    "max_prefill_entries": 5
  },
  "automation": {
    "human_review_required": false,  // Auto-persist after validation
    "pipeline_steps": ["sync_embeddings", "generate_rules", "evolution_check", "reasoning_check"],
    "min_content_length": 15         // Quality gate: minimum title length for auto-harvest
  },
  "compaction": {
    "auto_suggest_threshold": 2.0,   // Waste ratio to trigger auto-compact
    "archive_dir": ".memory/archive",
    "sort_output": true
  },
  "embedding": { "enabled": false },  // Set true + API key for vector search
  "reasoning": { "enabled": false }   // Set true + API key for LLM reasoning
}
```

All automation is idempotent and uses graceful degradation — embeddings fall back to FTS, reasoning falls back to heuristics, pipeline continues if a step fails.

### Enabling Embedding (Vector Search)

By default, embedding is disabled and search uses keyword-only mode (FTS5). To enable hybrid vector + keyword search:

**1. Install a provider SDK** (pick one):

```bash
pip install google-genai    # Gemini (recommended — free tier, 3072d)
pip install openai          # OpenAI (1536d)
pip install ollama          # Ollama (local, free, requires ollama server)
```

**2. Set API key** as environment variable:

<details>
<summary><strong>macOS / Linux</strong></summary>

```bash
# Temporary (current session only)
export GOOGLE_API_KEY="your-key"    # For Gemini
export OPENAI_API_KEY="your-key"    # For OpenAI

# Persistent — add to your shell profile
echo 'export GOOGLE_API_KEY="your-key"' >> ~/.zshrc    # macOS (zsh)
echo 'export GOOGLE_API_KEY="your-key"' >> ~/.bashrc   # Linux (bash)
source ~/.zshrc  # or ~/.bashrc — reload
```
</details>

<details>
<summary><strong>Windows (PowerShell)</strong></summary>

```powershell
# Temporary (current session only)
$env:GOOGLE_API_KEY = "your-key"
$env:OPENAI_API_KEY = "your-key"

# Persistent (user-level, survives reboot)
[Environment]::SetEnvironmentVariable("GOOGLE_API_KEY", "your-key", "User")
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "your-key", "User")
# Restart terminal to take effect
```
</details>

<details>
<summary><strong>Windows (CMD)</strong></summary>

```cmd
:: Temporary (current session only)
set GOOGLE_API_KEY=your-key

:: Persistent (user-level, survives reboot)
setx GOOGLE_API_KEY "your-key"
:: Restart terminal to take effect
```
</details>

<details>
<summary><strong>Ollama (local, no API key needed)</strong></summary>

```bash
# Install Ollama from https://ollama.com, then:
ollama pull nomic-embed-text
```
</details>

**3. Enable in config** — set `embedding.enabled` to `true` in `.memory/config.json`:

```json
"embedding": {
  "enabled": true,
  "provider": "gemini",
  "fallback": ["openai"],
  "providers": {
    "gemini":  { "model": "gemini-embedding-001",   "dimensions": 3072, "api_key_env": "GOOGLE_API_KEY" },
    "openai":  { "model": "text-embedding-3-small",  "dimensions": 1536, "api_key_env": "OPENAI_API_KEY" },
    "ollama":  { "model": "nomic-embed-text",         "dimensions": 768,  "host": "http://localhost:11434" }
  }
}
```

Each provider has its own native dimensions. The system reads `api_key_env` to resolve the environment variable automatically. You only need to configure the provider(s) you plan to use.

**4. Sync** to build the vector index:

```bash
python3 .memory/scripts/pipeline_cli.py --sync-only
# Verify: python3 .memory/scripts/search_cli.py --debug "test query"
# Output should show mode: "hybrid" instead of "keyword"
```

| Provider | SDK | API Key Env | Model | Dimensions | Cost |
|----------|-----|-------------|-------|------------|------|
| **Gemini** | `google-genai` | `GOOGLE_API_KEY` | `gemini-embedding-001` | 3072 | Free tier available |
| **OpenAI** | `openai` | `OPENAI_API_KEY` | `text-embedding-3-small` | 1536 | Paid |
| **Ollama** | `ollama` | (none) | `nomic-embed-text` | 768 | Free (local) |

> Gemini supports Matryoshka dimensionality reduction — set `"dimensions": 768` or `1536` in config to reduce storage at slight quality cost.

The system tries the primary provider first, then walks the fallback chain. If all providers fail, it silently degrades to keyword-only mode.

### Enabling LLM Reasoning

To enable LLM-enriched reasoning (semantic correlations, deep contradiction analysis, principle synthesis):

Set the API key for your chosen provider (same method as above):

| Provider | Env Variable | SDK |
|----------|-------------|-----|
| Anthropic Claude | `ANTHROPIC_API_KEY` | `pip install anthropic` |
| OpenAI GPT | `OPENAI_API_KEY` | `pip install openai` |
| Google Gemini | `GOOGLE_API_KEY` | `pip install google-genai` |
| Ollama (local) | (none) | `pip install ollama` |

Then enable in `.memory/config.json`:

```json
"reasoning": {
  "enabled": true,
  "provider": "anthropic",
  "model": "claude-sonnet-4-20250514",
  "fallback": ["openai", "gemini"],
  "max_tokens": 4096,
  "token_budget": 16000
}
```

Without LLM, reasoning runs in heuristic-only mode (tag overlap, keyword opposition) at zero cost.

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
    "DOCS_ROOT": "docs/"
  },
  "import": {
    "supported_sources": ["*.md", "*.py", "*.ts", "*.js", "*.go"],
    "doc_roots": ["docs/", "CLAUDE.md", "README.md"]
  }
}
```

The `import` section defines which file types `/memory-import` and `/memory-scan` can extract from. By default it supports all common document and code formats — any `*.md`, `*.py`, `*.ts`, `*.js`, `*.go` file.

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
  "version": "1.5",

  "paths": {
    "CODE_ROOTS": ["src/"],
    "DOCS_ROOT": "docs/"
  },

  "import": {
    "supported_sources": ["*.md", "*.py", "*.ts", "*.js", "*.go"],
    "doc_roots": ["docs/", "CLAUDE.md", "README.md"]
  },

  "embedding": {
    "enabled": false,
    "provider": "gemini",
    "fallback": ["openai"],
    "providers": {
      "gemini":  { "model": "gemini-embedding-001",   "dimensions": 3072, "api_key_env": "GOOGLE_API_KEY" },
      "openai":  { "model": "text-embedding-3-small",  "dimensions": 1536, "api_key_env": "OPENAI_API_KEY" },
      "ollama":  { "model": "nomic-embed-text",         "dimensions": 768,  "host": "http://localhost:11434" }
    },
    "dedup_threshold": 0.92
  },

  "verify": {
    "rulesets": [".memory/rules/verify-core.rules.json"],
    "fail_on_missing_path": false,
    "staleness_threshold_days": 90
  },

  "automation": {
    "human_review_required": false,
    "startup_check": true,
    "pipeline_steps": ["sync_embeddings", "generate_rules", "evolution_check", "reasoning_check"],
    "dedup_threshold": 0.85
  },

  "evolution": {
    "confidence_half_life_days": 120,
    "deprecation_confidence_threshold": 0.3
  },

  "reasoning": {
    "enabled": false,
    "provider": "anthropic",
    "fallback": ["openai", "gemini"],
    "max_tokens": 4096,
    "token_budget": 16000
  },

  "compaction": {
    "auto_suggest_threshold": 2.0,
    "archive_dir": ".memory/archive",
    "sort_output": true
  },

  "v3": {
    "auto_startup": true,
    "auto_start_on_plan": true,
    "auto_harvest_on_stop": true,
    "auto_draft_from_conversation": true,
    "working_memory_dir": ".memory/working",
    "prefill_on_plan_start": true,
    "max_prefill_entries": 5,
    "session_recovery": true
  }
}
```

### Path Variables

Rules reference paths using `${paths.CODE_ROOTS}` syntax:

| Variable | Default | Description |
|----------|---------|-------------|
| `${paths.CODE_ROOTS}` | `["src/"]` | Main source directories |
| `${paths.DOCS_ROOT}` | `"docs/"` | Documentation root |

Additional path keys (e.g., `FEATURE_ROOTS`, `DEPLOYMENT_ROOTS`) can be added for project-specific rule scoping. All paths support globs and arrays.

---

## Directory Structure

```
.memory/
├── SCHEMA.md              # Storage contract (v1.1)
├── config.json            # Project configuration (v1.5)
├── config.schema.json     # JSON Schema for config
├── events.jsonl           # Memory storage (append-only)
├── vectors.db             # Vector + FTS5 index (derived, gitignored)
├── drafts/                # Draft queue (pending human approval)
├── working/               # Working memory session files (V3, gitignored)
├── archive/               # Compacted history by quarter (gitignored)
│   ├── events_YYYYQN.jsonl    # Quarterly archive shards
│   └── compaction_log.jsonl   # Compaction audit log
├── rules/
│   └── verify-core.rules.json   # Core verification rules
├── hooks/                 # Claude Code hook scripts (V3)
│   ├── session_start.sh   #   SessionStart → startup health check
│   ├── pre_edit_search.py #   PreToolUse:Edit|Write → memory search
│   ├── plan_start.py      #   PreToolUse:EnterPlanMode → auto-start session
│   └── stop_harvest.py    #   Stop → auto-harvest session OR scan conversation → drafts
├── lib/                   # Python library modules
│   ├── text_builder.py    #   Text construction for embedding/dedup
│   ├── embedder.py        #   Multi-provider embedding (Gemini/OpenAI/Ollama)
│   ├── vectordb.py        #   SQLite vector storage + FTS5
│   ├── sync.py            #   Incremental sync engine
│   ├── search.py          #   Hybrid search engine (4-level degradation)
│   ├── generate_rules.py  #   Hard entry → .claude/rules/ bridge
│   ├── auto_verify.py     #   Schema/source/staleness/dedup validation
│   ├── auto_capture.py    #   Draft queue management
│   ├── auto_sync.py       #   Pipeline orchestration + harvest + version check
│   ├── evolution.py       #   Memory health & lifecycle (M5)
│   ├── llm_provider.py    #   Multi-provider LLM abstraction (M6)
│   ├── prompts.py         #   LLM prompt templates (M6)
│   ├── reasoning.py       #   LLM reasoning engine (M6)
│   ├── scanner.py         #   Batch document scanner
│   ├── config_presets.py   #   Configuration presets + EFM_VERSION constant
│   ├── init.py            #   Project init, --upgrade & auto-startup (V3 M7)
│   ├── working_memory.py  #   Working memory + auto-harvest (V3 M8-M9)
│   ├── transcript_scanner.py # Conversation transcript scan → drafts (V3 M10)
│   └── compaction.py      #   events.jsonl compaction + archive (V3 M11)
├── scripts/               # CLI entry points
│   ├── sync_embeddings.py
│   ├── search_cli.py
│   ├── generate_rules_cli.py
│   ├── verify_cli.py
│   ├── capture_cli.py
│   ├── pipeline_cli.py
│   ├── evolution_cli.py
│   ├── reasoning_cli.py
│   ├── scan_cli.py        #   Batch document scanner CLI
│   ├── init_cli.py        #   V3: project init CLI
│   ├── working_memory_cli.py  #  V3: working memory CLI
│   └── compact_cli.py     #   V3: compaction CLI (--stats, --dry-run)
└── tests/                 # 938 unit tests
    ├── conftest.py
    ├── test_text_builder.py
    ├── test_vectordb.py
    ├── test_sync.py
    ├── test_search.py
    ├── test_generate_rules.py
    ├── test_auto_verify.py
    ├── test_auto_capture.py
    ├── test_auto_sync.py
    ├── test_evolution.py
    ├── test_llm_provider.py
    ├── test_reasoning.py
    ├── test_scanner.py     #   Batch scanner tests
    ├── test_config_presets.py #  Config presets + version tests
    ├── test_init.py        #   V3: init + upgrade + hooks tests
    ├── test_working_memory.py  #  V3: working memory + auto-harvest tests
    ├── test_lifecycle.py   #   V3: lifecycle automation tests
    ├── test_transcript_scanner.py # V3: conversation scan → drafts tests
    ├── test_compaction.py  #   V3: compaction + archive tests
    ├── test_events_io.py   #   V3: events I/O + incremental sync tests
    ├── test_pre_edit_search.py # V3: pre-edit memory search hook tests
    └── test_prompts.py     #   LLM prompt template tests

.claude/commands/
├── memory-save.md         # Entry creation workflow
├── memory-search.md       # Query workflow
├── memory-import.md       # Import workflow (dry-run, any *.md/*.py/*.ts)
├── memory-verify.md       # Integrity check workflow
├── memory-scan.md         # Batch document scanning
├── memory-plan.md         # Working memory session management (V3)
├── memory-init.md         # Project init command (V3)
├── memory-evolve.md       # Memory health & evolution analysis (V3)
├── memory-reason.md       # Cross-memory reasoning analysis (V3)
└── memory-compact.md      # Compaction + archive management (V3)
```

---

## FAQ

### 1. Will this system write files automatically?

**It depends on your configuration and which commands you use.**

- **`human_review_required: true`** (template default): `/memory-save`, `/memory-import`, and `/memory-scan` display candidates but require explicit approval before writing to `events.jsonl`.
- **`human_review_required: false`**: These commands auto-persist after schema validation.
- **Always writes** (regardless of setting): `/memory-init` creates config/hook files, `/memory-plan` creates session files in `.memory/working/`.
- **V3 hooks**: The Stop hook auto-harvests working memory sessions → `events.jsonl`, scans conversations → `.memory/drafts/` (drafts always require manual approval), and auto-compacts when waste ratio exceeds threshold.
- **Never writes**: `/memory-search`, `/memory-verify`, `/memory-evolve`, `/memory-reason` are always read-only.

### 2. What happens if I run `/memory-search --all`?

It's forbidden. The system returns a maximum of 5 entries to prevent context overflow and accidental full dumps.

### 3. Can `/memory-verify` execute the verify commands in my entries?

**No.** Verify commands are analyzed statically for safety but never executed. The system only uses `grep`, `find`, and similar read-only tools to check patterns.

### 4. What if my source file moves or changes?

Run `/memory-verify` periodically. It will detect:
- Missing files (FAIL)
- Anchor drift (WARN)
- Stale entries >90 days (WARN)

Then manually update entries via `/memory-save` + append. The evolution CLI can also identify entries with broken sources and suggest deprecation.

### 5. How do I update an existing entry?

Append a new version with the same `id`. The system uses append-only semantics — latest entry wins. Old versions remain for audit trail.

### 6. What's the difference between Hard and Soft memory?

| Type | Meaning | When to Use |
|------|---------|-------------|
| **Hard** | Violations invalidate results or cause incidents | Production bugs, data leakage, security issues |
| **Soft** | Contextual knowledge, not strictly enforced | Best practices, preferences, non-critical patterns |

Hard entries are automatically injected into `.claude/rules/ef-memory/` (M3) so Claude loads them when editing relevant files.

### 7. Can I skip the manual review step?

**Yes.** Set `"human_review_required": false` in `.memory/config.json` under the `automation` section. When disabled, `/memory-save` and `/memory-import` will validate entries and write directly to `events.jsonl` without asking for confirmation. Schema validation and source checks still apply — only the human approval step is skipped. You can also tell Claude "turn off memory review" to toggle it.

### 8. What documents can I import from?

**Any structured document.** The system is document-type agnostic. You can import from incidents, decisions, architecture docs, runbooks, retrospectives, READMEs, code comments, changelogs, and any markdown file. Use `/memory-import <path>` with any file that contains extractable knowledge (rules, lessons, constraints, decisions, risks, or facts).

### 9. Hooks error in non-git subdirectories (infinite Stop loop)?

If you open Claude Code from a directory that isn't inside a git repository (e.g. a subfolder of a mono-repo that isn't itself a git root), the hooks will fail silently and skip. Before V3.2-P5 this caused `git rev-parse` to write to stderr, and the Stop hook could enter an infinite loop. **Fix:** run `/memory-init` to regenerate hooks with the safe prefix, or update to V3.2-P5+.

### 10. What should `.gitignore` include for EFM?

**Critical:** When deploying EFM to a new project, add these entries to its `.gitignore`:

```gitignore
# EF Memory (derived artifacts, session-scoped)
.memory/archive/
.memory/vectors.db
.memory/drafts/*.json
.memory/working/
.claude/rules/ef-memory/
```

**Why this matters:**
- `vectors.db` is a SQLite file. Git cannot merge binary files — switching branches corrupts it, and merge conflicts are unresolvable. If already tracked, run `git rm --cached .memory/vectors.db` to untrack it (the file stays on disk and is auto-rebuilt by `/memory-search`).
- `drafts/*.json` and `working/` are session-scoped transient files that should not persist across branches.
- `archive/` is user-specific compaction history, regenerable from `events.jsonl`.
- `rules/ef-memory/` is derived from `events.jsonl` entries and auto-regenerated.

**Files that SHOULD be committed:** `events.jsonl`, `config.json`, `SCHEMA.md`, `.memory/lib/`, `.memory/hooks/`, `.memory/scripts/`, `.memory/tests/`.

### 11. Git merge conflicts in `events.jsonl`?

**Prevention:** Add to your `.gitattributes`:
```
.memory/events.jsonl merge=union
```
This tells git to keep both sides' lines on merge (no conflict markers). EFM handles dedup automatically.

**Recovery:** If you already have merge conflicts (or duplicate entries after merge), run `/memory-repair`:
1. It removes git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
2. Deduplicates entries by ID (newest `created_at` wins)
3. Sorts entries chronologically
4. Reports orphan sources (entries referencing deleted files)
5. Creates a `.bak` backup before modifying

Use `--dry-run` to preview without changes: `python3 .memory/scripts/repair_cli.py --dry-run`

### 12. Can I use this without Claude Code CLI?

The memory format (JSONL + SCHEMA.md) is tool-agnostic. The `.claude/commands/` files are specific to Claude Code CLI but the principles apply anywhere. The Python library modules can be used independently.

---

## Storage Format

All memory lives in `.memory/events.jsonl`:

- **One JSON object per line** (strict JSONL)
- **Append-only** — new entries appended, updates append new version with same `id` (latest wins)
- **Git-tracked** — full audit history
- **Compactable** — `/memory-compact` resolves to one line per active entry, archives history to `.memory/archive/events_YYYYQN.jsonl`

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
| Schema | 1.1 |
| Config | 1.5 |
| EFM Version | 3.2.0 |
| Commands | 1.4 (11 slash commands) |
| V3 Engine | M12 (938+ tests) |
