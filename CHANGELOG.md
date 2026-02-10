# Changelog

All notable changes to EF Memory for Claude will be documented in this file.

---

## 2026-02-10 — V3.1: Quality Gate, Session Dedup, --upgrade, Version Tracking

### Five improvements to harvest quality, deployment safety, and observability

**Step 1: Harvest Quality Gate**
Auto-harvest now filters low-quality candidates before they reach `events.jsonl`:
- `_clean_markdown_artifacts()` — strips pipes, bold, backticks, horizontal rules from extracted text
- `_is_viable_candidate()` — rejects short titles (<15 chars), boilerplate-only, title-repeating content
- Confidence penalties: no rule+implication (-0.15), short title (-0.05), repeated content (-0.10)
- Configurable `automation.min_content_length` (default 15, range 5–100)
- Fixed bug: `vr.checks` → `vr.errors` (nonexistent attribute on ValidationResult)

**Step 2: Session-Level Dedup**
Prevents duplicate writes when Stop hook fires multiple times per conversation:
- `conversation_id` extracted from hook input and passed to `auto_harvest_and_persist()`
- Session-scoped tracking set prevents re-writing entries already persisted this session
- `conversation_id` stored in `_meta` for audit trail

**Step 3: Init `--upgrade` Mode**
Safe in-place upgrade for existing EFM installations:
- `--upgrade` flag added to init_cli.py (mutually exclusive with `--force`)
- `run_upgrade()` replaces EFM section markers in CLAUDE.md only, preserving all user content
- Force-updates startup rule, merges settings + hooks
- Thin-CLAUDE.md detection warns when <10 lines of project context

**Step 4: Version Tracking**
- `EFM_VERSION = "3.1.0"` constant in `config_presets.py`
- `_stamp_efm_version()` writes version to `config.json` on init/upgrade
- Startup health check compares installed vs current version
- Hint suggests `run /memory-init --upgrade` when mismatch detected

**Step 5: Waste Ratio Enhancement**
- `waste_lines` field added to StartupReport
- Compact hint now shows specific count (e.g., "42 obsolete lines")

**Modified files (7):**
- `.memory/lib/working_memory.py` — Quality gate functions + session dedup logic + bug fix
- `.memory/hooks/stop_harvest.py` — Extract conversation_id, pass to auto_harvest
- `.memory/lib/init.py` — `run_upgrade()`, `_handle_claude_md_upgrade()`, `_check_claude_md_content()`, `_stamp_efm_version()`
- `.memory/scripts/init_cli.py` — `--upgrade` flag, mutual exclusion with `--force`
- `.memory/lib/auto_sync.py` — Version check fields, waste_lines, startup hints
- `.memory/lib/config_presets.py` — `EFM_VERSION = "3.1.0"` constant
- `.memory/config.schema.json` — Added `min_content_length`, `efm_version` properties

**Test count: 765 → 804** (+39 tests: 21 quality/dedup + 11 upgrade + 5 version + 3 waste - 1 renamed)

---

## 2026-02-08 — Draft Auto-Expire

### Stale Draft Auto-Cleanup

Drafts from conversation scanning (M10) now auto-expire during startup. Stale drafts older than `v3.draft_auto_expire_days` (default 7) are deleted automatically. The startup hint now shows draft age and expiry info with `/memory-save` review suggestion.

**Modified files (4):**
- `.memory/lib/auto_capture.py` — Added `expire_stale_drafts()` function
- `.memory/lib/auto_sync.py` — Enhanced `check_startup()` with draft expiry + age tracking; enhanced `_format_hint()` with expiry reporting and `/memory-save` suggestion
- `.memory/config.json` — Added `v3.draft_auto_expire_days: 7`
- `.memory/config.schema.json` — Added `draft_auto_expire_days` field schema

**Test count: 713 → 731** (+18 tests: 9 expire + 4 hint format + 5 startup integration)

---

## 2026-02-08 — V3 M11: events.jsonl Compaction + Time-Sharded Archive

### M11: Hot/Archive Compaction

`events.jsonl` is append-only and accumulates superseded versions and deprecated entries over time. M11 adds compaction to resolve the hot file and archive removed lines by quarter.

**New files (3):**
- `.memory/lib/compaction.py` — Core compaction engine: `compact()`, `get_compaction_stats()`, atomic rewrite, quarterly archive partitioning, sync cursor reset, audit logging
- `.memory/scripts/compact_cli.py` — CLI with `--stats`, `--dry-run`, and run modes
- `.claude/commands/memory-compact.md` — `/memory-compact` slash command

**New test file (1):**
- `.memory/tests/test_compaction.py` — 28 tests (quarter key parsing, compaction algorithm, stats calculation, startup hint integration, auto-compact on stop)

**Modified files (4):**
- `.memory/hooks/stop_harvest.py` — Auto-compact after session harvest when waste ratio ≥ threshold
- `.memory/lib/auto_sync.py` — Startup hint shows compaction suggestion when waste ratio exceeds threshold
- `.memory/config.json` — Added `compaction` section (threshold, archive_dir, sort_output)
- `.memory/config.schema.json` — Added `compaction` object schema

**Key design:**
- **Zero consumer changes**: All 8+ loaders continue reading `events.jsonl` unchanged — it's just smaller
- **Hot/Archive split**: `events.jsonl` = clean (one line per active entry), `archive/events_YYYYQN.jsonl` = quarterly history
- **Atomic rewrite**: `os.replace(tmp, events.jsonl)` — POSIX crash-safe
- **Auto-trigger**: Stop hook runs compaction when waste ratio ≥ 2.0× (configurable)
- **Manual trigger**: `/memory-compact` or `python3 .memory/scripts/compact_cli.py`

**Test count: 683 → 711** (+28 tests)

---

## 2026-02-07 — V3 M10: Conversation Context Auto-Save

### M10: Conversation Transcript Scanning → Draft Queue

Normal conversations (without Plan Mode) now get scanned for memory-worthy patterns. When Claude stops, the Stop hook reads the conversation transcript and creates draft entries for human review.

**New files (2):**
- `.memory/lib/transcript_scanner.py` — Reads Claude Code transcript JSONL, extracts assistant messages, scans with 6 harvest patterns, creates drafts
- `.memory/tests/test_transcript_scanner.py` — 18 tests (transcript reading, pattern matching, draft creation, dedup, source attribution)

**Modified files (3):**
- `.memory/hooks/stop_harvest.py` — Added conversation scanning branch: when no working memory session exists, reads `transcript_path` from hook stdin, scans for patterns, writes drafts to `.memory/drafts/`
- `.memory/config.json` — Added `v3.auto_draft_from_conversation: true`
- `.memory/config.schema.json` — Added `auto_draft_from_conversation` field schema

**Key features:**
- **Transcript reading**: Parses Claude Code JSONL transcript, extracts assistant text blocks
- **Pattern reuse**: Same 6 harvest patterns as working memory (LESSON/CONSTRAINT/DECISION/WARNING/MUST-NEVER/Error-Fix)
- **Draft queue**: Candidates go to `.memory/drafts/` — never directly to `events.jsonl`
- **Human-in-the-loop**: User reviews drafts via `/memory-save`
- **Safety**: Never blocks stopping, graceful degradation, 10MB transcript limit, config toggle
- **Source attribution**: Draft entries tagged with `conversation:{session_id}` source

**Test count: 652 → 670** (+18 tests), later +13 dedup/rules-echo tests → 683

---

## 2026-02-07 — V3 Full Automation + Deep Memory Integration

### Plan Session Full Closed-Loop Automation

Complete automation of the working memory lifecycle via Claude Code hooks — no manual steps required.

**New files (2):**
- `.memory/hooks/plan_start.py` — PreToolUse hook: auto-starts working memory session on EnterPlanMode
- `.claude/commands/memory-evolve.md` — `/memory-evolve` slash command
- `.claude/commands/memory-reason.md` — `/memory-reason` slash command

**Modified files (7):**
- `.memory/lib/working_memory.py` — Added `auto_harvest_and_persist()`, `_convert_candidate_to_entry()`, `_hash8()`, `_sanitize_anchor()`, `_extract_tags()`
- `.memory/hooks/stop_harvest.py` — Changed from block+remind to auto-harvest+persist+pipeline+clear
- `.memory/lib/init.py` — Added EnterPlanMode hook in `generate_hooks_settings()`; Stop hook timeout 5→30s
- `.memory/config.json` — Added `v3.auto_start_on_plan`, `v3.auto_harvest_on_stop`; pipeline_steps now includes `evolution_check` and `reasoning_check`; removed `INCIDENTS_FILE` (use `doc_roots` instead)
- `.memory/config.schema.json` — Added new v3 field schemas; updated INCIDENTS_FILE description
- `.memory/tests/test_working_memory.py` — +23 tests (hash, sanitize, convert, auto-harvest, extract_tags)
- `.memory/tests/test_init.py` — Updated hook tests for EnterPlanMode

**Key features:**
- **Auto-start**: EnterPlanMode hook starts working memory session with prefill
- **Auto-harvest**: Stop hook extracts LESSON/CONSTRAINT/DECISION/WARNING markers
- **Auto-persist**: Converts HarvestCandidate → full EFM schema entry → appends to events.jsonl
- **Auto-pipeline**: Runs sync + rules + evolution + reasoning after writing
- **Auto-clear**: Cleans session files after successful harvest
- **Graceful fallback**: If auto-harvest fails, falls back to block+remind behavior

### Deep Memory Integration

Evolution (M5) and reasoning (M6) now run automatically as pipeline steps and have dedicated slash commands.

- `evolution_check` and `reasoning_check` added to default `pipeline_steps`
- `/memory-evolve` — Memory health analysis: confidence, duplicates, deprecations, merges
- `/memory-reason` — Cross-memory reasoning: correlations, contradictions, synthesis, risks

### Documentation Generalization

- Removed hardcoded `INCIDENTS_FILE` from config — system now works with any `*.md` document via `doc_roots` and `supported_sources`
- Updated README.md with full automation section, hook architecture, pipeline steps
- Updated directory structure with hooks, new commands (9 total), 652 tests

**Test count: 571 → 652** (+81 tests)

---

## 2026-02-07 — v3.0.0 M7-M9: Auto-Startup + Working Memory + Lifecycle

### M7: Project Init & Auto-Startup

One-command initialization that makes every Claude Code session aware of EF Memory. Handles existing projects safely via append/merge strategies.

**New files (4):**
- `.memory/lib/init.py` — Core init logic (templates, merge, project scan)
- `.memory/scripts/init_cli.py` — CLI (`--dry-run`, `--force`, `--target`)
- `.claude/commands/memory-init.md` — `/memory-init` command
- `.memory/tests/test_init.py` — 65 tests

**Modified files (3):**
- `.memory/config.json` — v1.4 → v1.5, added `v3` section
- `.memory/config.schema.json` — Added `v3` schema definition
- `.memory/lib/__init__.py` — Version 2.0.0 → 3.0.0

**Key features:**
- Generates CLAUDE.md (Tier 1), .claude/rules/ef-memory-startup.md (Tier 2), hooks.json, settings.local.json
- Non-destructive merging: CLAUDE.md (append with `<!-- EF-MEMORY-START/END -->` markers), hooks.json (merge), settings.local.json (merge permissions)
- Idempotent re-runs — skips existing sections unless `--force`
- Project scanner with advisory suggestions (docs import, gitignore entries)

**Test count: 407 → 472** (+65 tests)

### M8: Working Memory (PWF Integration)

Short-term working memory for multi-step tasks, inspired by Planning with Files. Maintains session files (task_plan.md, findings.md, progress.md) in `.memory/working/`.

**New files (4):**
- `.memory/lib/working_memory.py` — Session lifecycle + harvest extraction
- `.memory/scripts/working_memory_cli.py` — CLI (start/resume/status/harvest/clear/read-plan)
- `.claude/commands/memory-plan.md` — `/memory-plan` command
- `.memory/tests/test_working_memory.py` — 72 tests

**Modified files (1):**
- `.gitignore` — Added `.memory/working/`

**Key features:**
- Auto-prefill: On session start, searches EF Memory and injects relevant entries into findings.md
- Pattern-based harvest: 6 extraction patterns (LESSON/CONSTRAINT/DECISION/WARNING markers, MUST/NEVER statements, Error/Fix)
- Session lifecycle: start → work → harvest → /memory-save → clear
- Deduplication in harvest candidates

**Test count: 472 → 544** (+72 tests)

### M9: Memory Lifecycle Automation

Closed-loop lifecycle: harvest pipeline step + session recovery at startup.

**New files (1):**
- `.memory/tests/test_lifecycle.py` — 22 tests

**Modified files (4):**
- `.memory/lib/auto_sync.py` — Added `harvest_check` pipeline step, session recovery in `check_startup`
- `.memory/scripts/pipeline_cli.py` — Added `--harvest-only` flag
- `.memory/config.json` — Added `session_recovery` toggle
- `.memory/config.schema.json` — Added `harvest_check` to pipeline_steps enum

**Key features:**
- `harvest_check` pipeline step scans working memory for candidates
- Session recovery at startup detects stale `.memory/working/` sessions
- Active session info included in startup hint string

**Test count: 544 → 566** (+22 tests)

---

## 2026-02-07 — Human Review Toggle

### `human_review_required` config flag

New toggle to control whether `/memory-save` and `/memory-import` require explicit human approval before writing to `events.jsonl`.

**Modified files (5):**
- `.memory/config.json` — Added `automation.human_review_required: true` (default)
- `.memory/config.schema.json` — Added `human_review_required` schema definition
- `.claude/commands/memory-save.md` — Added Human Review Mode section + conditional guardrails
- `.claude/commands/memory-import.md` — Added Human Review Mode section + conditional guardrails (v1.2)
- `README.md` — Updated Security Boundaries, config example, added FAQ

**Behavior:**
- `true` (default): Same as before — display only, require explicit user request to write
- `false`: Validate schema + source, then directly append to `events.jsonl` and run pipeline
- Toggle via config or natural language: "turn off memory review" / "turn on memory review"

---

## 2026-02-07 — Universal Document Import

### `/memory-import` expanded to support any document type

Previously, `/memory-import` only supported `INCIDENTS.md`. Now it supports **any structured document**: incidents, decisions, architecture docs, ADRs, RFCs, runbooks, retrospectives, READMEs, code comments, changelogs, and more.

**Modified files (4):**
- `.claude/commands/memory-import.md` — Rewritten for universal document support (v1.0 → v1.1)
- `.memory/config.json` — Import section: `["INCIDENTS.md"]` → `["*.md", "*.py", "*.ts", "*.js", "*.go"]` + `doc_roots`
- `.memory/config.schema.json` — Added `doc_roots` property, updated descriptions
- `README.md` — Updated workflow diagram, config examples, FAQ

**Key changes:**
- Supported sources expanded from single file to glob patterns (`*.md`, `*.py`, `*.ts`, `*.js`, `*.go`)
- New `doc_roots` config for default scan directories
- Document-specific extraction guidance (incidents, decisions, architecture, runbooks, code comments)
- Three-level source normalization (ideal/acceptable/minimum)
- New extraction heuristics for decisions, constraints, risks, and breaking changes

---

## 2026-02-07 — v1.4 M6: LLM Reasoning Layer

### M6: Layer 3 LLM Reasoning Engine

Cross-memory correlation, contradiction detection, knowledge synthesis, and context-aware risk assessment — powered by multi-provider LLM abstraction with automatic heuristic fallback.

**New files (6):**
- `.memory/lib/llm_provider.py` — Multi-provider LLM abstraction (Anthropic/OpenAI/Gemini/Ollama)
- `.memory/lib/prompts.py` — Centralized LLM prompt templates
- `.memory/lib/reasoning.py` — Core reasoning engine (5 analysis functions)
- `.memory/scripts/reasoning_cli.py` — CLI for reasoning analysis
- `.memory/tests/test_llm_provider.py` — 21 tests
- `.memory/tests/test_reasoning.py` — 61 tests

**Modified files (7):**
- `.memory/tests/conftest.py` — Added MockLLMProvider + SAMPLE_ENTRIES_EXTENDED
- `.memory/config.json` — Added `reasoning` section (v1.3 → v1.4)
- `.memory/config.schema.json` — Added reasoning schema + `reasoning_check` pipeline step
- `.memory/lib/search.py` — Added `reasoning_annotations` field to SearchReport
- `.memory/scripts/search_cli.py` — Added `--annotate` flag for risk annotations
- `.memory/lib/auto_sync.py` — Added `reasoning_check` pipeline step
- `.memory/tests/test_search.py` + `.memory/tests/test_auto_sync.py` — 10 new integration tests

**Key features:**
- Two-stage architecture: heuristic pre-filter (zero LLM cost) + optional LLM enrichment
- Four providers: Anthropic Claude, OpenAI GPT, Google Gemini, Ollama (all optional, lazy import)
- Fallback chain with graceful degradation (LLM unavailable → heuristic-only mode)
- Cross-memory correlation (tag overlap, source overlap, temporal proximity)
- Contradiction detection (opposing keywords MUST/NEVER, severity mismatch)
- Knowledge synthesis (tag-based clustering → principle generation)
- Risk assessment (staleness, superseded entries, confidence-based annotations)
- Search integration via `--annotate` flag
- Pipeline integration via `reasoning_check` step

**Test count: 315 → 407** (+92 new tests, 0 failures)

---

## 2026-02-05 — Comprehensive Audit

### P0-P3 Four-Layer Audit

Systematic audit of M1-M5 codebase, fixing 36 bugs across 4 priority layers.

- **P0 (Critical)**: 8 fixes — data loss prevention, crash fixes, import/config errors
- **P1 (Correctness)**: 12 fixes — logic bugs, scoring errors, boundary conditions
- **P2 (Robustness)**: 10 fixes — error handling, edge cases, graceful degradation
- **P3 (Quality)**: 6 fixes — code style, test coverage, minor improvements

**Test count: 256 → 315** (+59 tests from audit hardening)

---

## 2026-02-04 — v1.3 M5: Memory Evolution

### M5: Memory Health & Lifecycle Management

- Duplicate detection with Union-Find clustering (text + optional embedding hybrid)
- Confidence scoring model (source quality × age decay × verification × validity)
- Deprecation suggestions based on confidence threshold
- Merge recommendations from duplicate groups
- Evolution CLI with `--duplicates`, `--confidence`, `--deprecations`, `--merges`
- Pipeline integration via `evolution_check` step
- Config v1.2 → v1.3

**Test count: 216 → 256** (+40 tests)

---

## 2026-02-03 — v1.2 M4: Automation Engine

### M4: Auto-Verify + Auto-Capture + Auto-Sync

- Schema validation (core-001 to core-014 rules, programmatic)
- Source verification (file existence, line ranges, git commits, markdown anchors)
- Draft queue with human-in-the-loop approval workflow
- Pipeline orchestration (sync + rules steps, isolated failure handling)
- Startup health check (<100ms, source spot-checking)
- Three CLI scripts: verify_cli.py, capture_cli.py, pipeline_cli.py

**Test count: 102 → 216** (+114 tests)

---

## 2026-02-02 — v1.1 M1-M3: Foundation + Search + Auto-Inject

### M1: Embedding Layer
- Multi-provider embedding (Gemini, OpenAI, Ollama) with factory + fallback
- SQLite vector storage + FTS5 full-text index
- Incremental sync engine with text-hash change detection

### M2: Hybrid Search Engine
- BM25 + Vector fusion with 4-level graceful degradation
- Classification/severity re-rank boost
- Search CLI with `--debug`, `--full`, `--mode` options

### M3: Layer 1 Auto-Inject
- Hard entries → `.claude/rules/ef-memory/*.md` automatic generation
- Domain extraction from source paths
- Dry-run and clean modes

**Test count: 0 → 102** (38 + 32 + 32)

---

## 2026-02-01 — v1.1 Template Release

### Template Repository

- Restructured as GitHub Template Repo
- Added `.memory/config.json` with path variables
- Added `.memory/config.schema.json` for validation
- Added `.memory/rules/verify-core.rules.json` (Layer 0 rules)
- Added 3 archetypes: `quant`, `ml`, `web`
- Updated `examples/walkthrough.md` with complete workflow
- Added `examples/events.sample.jsonl` (sample entries)
- Empty `.memory/events.jsonl` for template use

### `/memory-verify` upgraded to v1.1

**Changes:**
- Schema sanity checks expanded (8 validation rules)
- Source verification now requires "File to read" declaration
- Verify command static safety analysis added (safe/dangerous patterns)
- Staleness grading refined (≤30 / 31-90 / >90 / never)
- Guardrails strengthened to 8 hard constraints
- Examples expanded with single-entry and full-verification outputs

### Archetypes

| Archetype | Rules | Focus |
|-----------|-------|-------|
| `quant` | 3 | Leakage, shift, train-live sync |
| `ml` | 3 | Data split, scaling, drift |
| `web` | 3 | Validation, auth, error handling |

---

## 2026-02-01 — v1.0 Initial Release

### EF Memory for Claude v1.0

- `/memory-save` — Create memory entries (manual, evidence-first)
- `/memory-search` — Query existing memory safely
- `/memory-import` — Extract memory candidates from documents (dry-run)
- `/memory-verify` — Verify memory integrity (read-only)
- `.memory/SCHEMA.md` — Storage contract v1.0
- `.memory/events.jsonl` — Append-only memory store

### Core Principles

1. Memory is project-level, not session-level
2. No memory without evidence
3. No persistence without human intent
4. No silent enforcement
5. Append-only > mutable truth
