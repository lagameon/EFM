# EF Memory for Claude

> Evidence-first project memory skills for Claude Code — [OpenClaw](https://github.com/pinkpixel-dev/OpenClaw)/moltbot-style structured memory with multi-layer retrieval, auto-verification, and lifecycle management.

A **safe, auditable memory skill system** that turns project incidents, constraints, and hard-earned lessons into reusable engineering knowledge. Inspired by the workspace memory architecture of [OpenClaw](https://github.com/pinkpixel-dev/OpenClaw) (moltbot) — but built specifically for Claude Code's skill system with evidence-first guarantees.

**This is not chat history. This is project memory.**

---

## Inspired By: OpenClaw / moltbot Memory

This project shares the same philosophy as [OpenClaw](https://github.com/pinkpixel-dev/OpenClaw) (moltbot)'s memory system — persistent, workspace-integrated, embedding-powered agent memory — but takes a different approach for Claude Code:

| | OpenClaw / moltbot | EF Memory |
|---|---|---|
| **Interface** | CLI commands (`openclaw memory ...`) | Claude Code skills (`/memory-save`, `/memory-search`, ...) |
| **Storage** | Markdown files + workspace | Structured JSONL + SQLite vector DB |
| **Retrieval** | Embedding search | 4-level degradation (Hybrid → Vector → Keyword → Basic) |
| **Safety** | Auto memory flush | Human-in-the-loop for all writes |
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
- **Human-in-the-loop control** — no silent writes
- **Zero side effects by default**

---

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │            EF Memory V2 Runtime               │
                    │                                              │
  Event Sources      │   Layer 3: LLM Reasoning                    │
  ─────────────     │   ├── Cross-memory correlation               │
  · file edit       │   ├── Contradiction detection                │
  · test fail/pass  │   ├── Knowledge synthesis                    │
  · git commit      │   └── Context-aware risk assessment          │
  · manual /cmd     │                                              │
       │            │   Layer 2: Semantic Retrieval                │
       │            │   ├── Embedding (Gemini / OpenAI / Ollama)   │
       │            │   ├── BM25 full-text search (FTS5)           │
       │            │   └── Hybrid search + Re-rank                │
       ▼            │                                              │
  ┌─────────┐       │   Layer 1: Structured Rules                  │
  │ Drafts  │──────▶│   ├── .claude/rules/ Bridge (auto-inject)    │
  │ (queue) │       │   └── Hard entries → domain rule files       │
  └─────────┘       │                                              │
       │            │   Storage: events.jsonl (source of truth)    │
       │ human      │   Index:   vectors.db (SQLite, derived)      │
       │ approval   │   Cache:   .claude/rules/ef-memory/ (derived)│
       ▼            └──────────────────────────────────────────────┘
  events.jsonl
                    Automation Engine:
                    ├── Auto-Verify:  schema + source drift detection
                    ├── Auto-Capture: event → draft queue → approval
                    ├── Auto-Sync:    events.jsonl → vectors.db + FTS
                    ├── Auto-Evolve:  dedup / confidence decay / deprecation
                    └── Auto-Reason:  LLM correlation / contradiction / synthesis
```

### Three-Layer Retrieval (4-Level Degradation)

| Level | Mode | Requirements | Score Formula |
|-------|------|-------------|---------------|
| 1 | Hybrid | Embedder + FTS5 | `bm25×0.4 + vector×0.6 + boost` |
| 2 | Vector | Embedder only | `vector×1.0 + boost` |
| 3 | Keyword | FTS5 only | `bm25×1.0 + boost` |
| 4 | Basic | None (zero deps) | token overlap on JSONL |

Hard+S1 entries get a +0.15 re-rank boost; the system always returns results regardless of available infrastructure.

---

## Security Boundaries

### When `human_review_required: true` (default)

```
This system will NEVER:
  - Write files without explicit human request
  - Execute verify commands (static analysis only)
  - Dump all memory entries (max 5 per search)
  - Auto-persist imported entries
  - Modify existing entries (append-only)
  - Auto-promote drafts to events.jsonl

This system will ALWAYS:
  - Require human approval for persistence
  - Report "No files were modified" after read-only operations
  - Distinguish between "checked" and "assumed" results
```

### When `human_review_required: false`

```
/memory-save and /memory-import will:
  - Validate schema and source before writing
  - Directly append valid entries to events.jsonl
  - Run the automation pipeline after writing
  - Still NEVER invent sources or skip validation

All other boundaries remain unchanged:
  - /memory-verify still never executes commands
  - /memory-search still returns max 5 entries
  - Entries are still append-only
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
│ Any Document    │  (incidents, decisions, architecture, runbooks, code...)
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
| `/memory-import` | Extract candidates from any document | **Never** |
| `/memory-save` | Format and display entry | **Never** |
| `/memory-search` | Query existing memory | **Never** |
| `/memory-verify` | Check integrity | **Never** |

---

## V2 Capabilities

EF Memory V2 adds six milestones of infrastructure on top of the core template:

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
  },
  "import": {
    "supported_sources": ["*.md", "*.py", "*.ts", "*.js", "*.go"],
    "doc_roots": ["docs/", "CLAUDE.md", "README.md"]
  }
}
```

The `import` section defines which file types `/memory-import` can extract from. By default it supports all common document and code formats.

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
  "version": "1.4",

  "paths": {
    "CODE_ROOTS": ["src/"],
    "DOCS_ROOT": "docs/",
    "INCIDENTS_FILE": "docs/INCIDENTS.md"
  },

  "import": {
    "supported_sources": ["*.md", "*.py", "*.ts", "*.js", "*.go"],
    "doc_roots": ["docs/", "CLAUDE.md", "README.md"]
  },

  "embedding": {
    "enabled": false,
    "provider": "gemini",
    "dedup_threshold": 0.92
  },

  "verify": {
    "rulesets": [".memory/rules/verify-core.rules.json"],
    "fail_on_missing_path": false,
    "staleness_threshold_days": 90
  },

  "automation": {
    "human_review_required": true,
    "startup_check": true,
    "pipeline_steps": ["sync_embeddings", "generate_rules"],
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
├── config.json            # Project configuration (v1.4)
├── config.schema.json     # JSON Schema for config
├── events.jsonl           # Memory storage (append-only)
├── vectors.db             # Vector + FTS5 index (derived, gitignored)
├── drafts/                # Draft queue (pending human approval)
├── rules/
│   └── verify-core.rules.json   # Core verification rules
├── lib/                   # Python library modules (V2)
│   ├── text_builder.py    #   Text construction for embedding/dedup
│   ├── embedder.py        #   Multi-provider embedding (Gemini/OpenAI/Ollama)
│   ├── vectordb.py        #   SQLite vector storage + FTS5
│   ├── sync.py            #   Incremental sync engine
│   ├── search.py          #   Hybrid search engine (4-level degradation)
│   ├── generate_rules.py  #   Hard entry → .claude/rules/ bridge
│   ├── auto_verify.py     #   Schema/source/staleness/dedup validation
│   ├── auto_capture.py    #   Draft queue management
│   ├── auto_sync.py       #   Pipeline orchestration
│   ├── evolution.py       #   Memory health & lifecycle (M5)
│   ├── llm_provider.py    #   Multi-provider LLM abstraction (M6)
│   ├── prompts.py         #   LLM prompt templates (M6)
│   └── reasoning.py       #   LLM reasoning engine (M6)
├── scripts/               # CLI entry points
│   ├── sync_embeddings.py
│   ├── search_cli.py
│   ├── generate_rules_cli.py
│   ├── verify_cli.py
│   ├── capture_cli.py
│   ├── pipeline_cli.py
│   ├── evolution_cli.py
│   └── reasoning_cli.py
└── tests/                 # 407 unit tests
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
    └── test_reasoning.py

.claude/commands/
├── memory-save.md         # Entry creation workflow
├── memory-search.md       # Query workflow
├── memory-import.md       # Import workflow (dry-run)
└── memory-verify.md       # Integrity check workflow
```

---

## FAQ

### 1. Will this system write files automatically?

**No.** All four commands are read-only by default. File writes only happen when you explicitly request them (e.g., "append this to events.jsonl"). The automation engine produces drafts that require human approval.

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

### 9. Can I use this without Claude Code CLI?

The memory format (JSONL + SCHEMA.md) is tool-agnostic. The `.claude/commands/` files are specific to Claude Code CLI but the principles apply anywhere. The Python library modules can be used independently.

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
| Config | 1.4 |
| Commands | 1.1 |
| V2 Engine | M6 (407 tests) |
