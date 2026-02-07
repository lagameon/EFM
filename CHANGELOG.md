# Changelog

All notable changes to EF Memory for Claude will be documented in this file.

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
