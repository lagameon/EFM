# EF Memory vs Claude Native Memory: Deep Comparison

> Last updated: 2026-02-09

This document provides a comprehensive comparison between **EF Memory for Claude (EFM)** and **Claude's built-in memory systems** (Claude.ai Chat Memory, Claude Code Auto Memory, and the Memory Tool API).

---

## Table of Contents

- [1. System Positioning](#1-system-positioning)
- [2. Claude's Native Memory Landscape](#2-claudes-native-memory-landscape)
- [3. Storage Architecture](#3-storage-architecture)
- [4. Memory Writing & Capture](#4-memory-writing--capture)
- [5. Memory Retrieval](#5-memory-retrieval)
- [6. Memory Lifecycle Management](#6-memory-lifecycle-management)
- [7. Integration & Automation](#7-integration--automation)
- [8. Limitations Compared](#8-limitations-compared)
- [9. Complementary Usage Model](#9-complementary-usage-model)
- [10. Feature Matrix](#10-feature-matrix)

---

## 1. System Positioning

| Dimension | Claude Native Memory | EF Memory (EFM) |
|-----------|---------------------|------------------|
| **Purpose** | General recall (preferences, style, project overview) | **Engineering knowledge base** (rules, lessons, constraints, risks) |
| **Target user** | All Claude users | Teams iterating long-term on complex codebases |
| **Core philosophy** | "Remember who you are" | **"Remember what you got wrong, with evidence to prove it"** |
| **Design paradigm** | Conversational memory | Evidence-first structured memory |

---

## 2. Claude's Native Memory Landscape

Claude's memory ecosystem comprises **three separate systems**, each serving different use cases.

### 2a. Claude.ai Chat Memory (Consumer / Team / Enterprise)

- **Writer**: Claude auto-synthesizes past conversations (~24h cycle); users can explicitly say "remember X" for immediate effect.
- **Storage**: Server-side (Anthropic infrastructure). Follows organizational data retention policies.
- **Format**: Structured summary categorized into domains (Role & Work, Current Projects, Communication Preferences, etc.).
- **Loading**: Memories live _outside_ the context window. Claude searches them on demand per conversation, pulling in only what is relevant.
- **User control**: View/edit (natural language or direct), Pause (keep but don't use), Reset (irreversible full wipe), Incognito mode (exclude conversations).
- **Availability**: Pro, Max, Team, and Enterprise plans. Team/Enterprise get project-scoped memory with confidentiality isolation.

### 2b. Claude Code Auto Memory (Developer CLI)

Two sub-layers:

**CLAUDE.md files** (user-authored instructions):

| Level | Location | Scope |
|-------|----------|-------|
| Managed policy | `/Library/Application Support/ClaudeCode/CLAUDE.md` (macOS) | Organization-wide |
| Project memory | `./CLAUDE.md` or `./.claude/CLAUDE.md` | Team-shared (git-tracked) |
| Project rules | `./.claude/rules/*.md` | Modular, topic-specific |
| User memory | `~/.claude/CLAUDE.md` | Personal, all projects |
| Local memory | `./CLAUDE.local.md` | Personal, single project (gitignored) |

- Loaded in full at session start (parent directories); on-demand for child directories.
- Supports `@path/to/import` syntax and YAML frontmatter glob matching in rules.

**Auto Memory** (Claude-authored notes):

- Location: `~/.claude/projects/<project>/memory/`
- `MEMORY.md`: Concise index — **first 200 lines loaded into every session**; content beyond 200 lines is not auto-loaded.
- Topic files (`debugging.md`, `api-conventions.md`, etc.): read on demand, not at startup.
- Claude reads and writes these files in real time during sessions.
- Managed via `/memory` command or verbal instructions.
- Disable: `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.

### 2c. Memory Tool API (Beta, for Custom Agents)

- Tool type: `memory_20250818`, beta header: `context-management-2025-06-27`.
- Client-side storage — developer controls infrastructure.
- Operations: `view`, `create`, `str_replace`, `insert`, `delete`, `rename` on a `/memories` directory.
- Claude checks memory directory before starting tasks.
- Combined with context editing: 39% performance improvement, 84% token reduction.

---

## 3. Storage Architecture

| Dimension | Claude Native | EF Memory |
|-----------|--------------|-----------|
| **Storage location** | Server-side (Anthropic) + local `~/.claude/projects/` | Local `.memory/` directory (git-tracked) |
| **Data format** | Markdown free-text | **Structured JSONL** (v1.1 schema, 14 validation rules) |
| **Type system** | None | 5 types (`decision`/`lesson`/`constraint`/`risk`/`fact`) x 2 classifications (`hard`/`soft`) x 3 severities (`S1`/`S2`/`S3`) |
| **Data integrity** | No schema validation | **7 schema checks + 2 source verifications + static analysis** |
| **Version history** | None (Reset is irreversible) | **Append-only + quarterly archive**, full audit trail |
| **Vector index** | Unknown (server-side) | SQLite WAL + FTS5 + multi-dimensional vectors (768/1536/3072d) |
| **Team sharing** | Team/Enterprise project memory | Git-tracked by design — natural team sharing |

### EFM Storage Stack (3 layers + archive)

```
Layer 1: events.jsonl (source of truth)
  ├── Append-only, one entry per line
  ├── Latest-wins resolution by entry ID
  └── Git-tracked for full audit history

Layer 2: vectors.db (SQLite)
  ├── vectors table: entry_id → float32 embeddings (packed binary)
  ├── fts_entries: FTS5 virtual table for BM25 keyword search
  ├── sync_state: incremental byte-offset cursor
  └── Indexes on deprecated flag for fast filtering

Layer 3: .claude/rules/ef-memory/*.md (derived)
  ├── Auto-generated from Hard entries
  ├── Scoped by domain (feature-engine, labels, protocols)
  └── Auto-injected by Claude Code when editing matching files

Archive: .memory/archive/events_YYYYQN.jsonl
  ├── Time-sharded by quarter
  ├── compaction_log.jsonl audit trail
  └── Triggered when waste_ratio >= 2.0
```

### EFM Entry Schema Example (Hard / S1)

```json
{
  "id": "lesson-inc036-a3f8c2d1",
  "type": "lesson",
  "classification": "hard",
  "severity": "S1",
  "title": "Rolling statistics without shift(1) caused 999x backtest inflation",
  "content": [
    "42 rolling/ewm/pct_change calls missing shift(1)",
    "Model learned to explain past, not predict future",
    "IC with T-5 returns (-0.115) > IC with T+1 returns (0.018)"
  ],
  "rule": "shift(1) MUST precede any rolling(), ewm(), pct_change() on price data",
  "implication": "Backtest returns inflated 100-1000x; predictions encode future info",
  "verify": "grep -rn 'rolling|ewm|pct_change' src/features/*.py | grep -v 'shift(1)'",
  "source": ["docs/decisions/INCIDENTS.md#INC-036:L553-L699"],
  "tags": ["leakage", "feature-engine", "shift", "rolling"],
  "created_at": "2026-02-01T14:30:00Z",
  "deprecated": false
}
```

**Key contrast**: Claude's Auto Memory is `MEMORY.md` free-text — Claude writes whatever it deems important, with no schema constraints. EFM entries must pass 14 validation rules, must have a traceable `source` path, and must include an executable `rule` or `implication`.

---

## 4. Memory Writing & Capture

| Dimension | Claude Native | EF Memory |
|-----------|--------------|-----------|
| **Write mechanism** | Claude decides what to write | **6 extraction patterns** + confidence-based routing |
| **Approval workflow** | None (Claude auto-writes) | Confidence routing: high → auto-persist, low → draft queue |
| **Source traceability** | None | Required `source` field (file:line, commit, PR) |
| **Duplicate detection** | None | 0.85 threshold text similarity + semantic dedup |
| **Auto-capture trigger** | Chat: ~24h synthesis; Code: real-time | Stop hook scans conversation -> generates Drafts |

### EFM's 6 Extraction Patterns

| Pattern | Trigger | Example |
|---------|---------|---------|
| LESSON | `LESSON: ...` marker | "LESSON: shift(1) must precede rolling" |
| CONSTRAINT | `CONSTRAINT:` / `INVARIANT:` | "CONSTRAINT: all features must be shift-aware" |
| DECISION | `DECISION:` / `Decided:` | "DECISION: use LightGBM over XGBoost" |
| WARNING | `WARNING:` / `RISK:` | "RISK: FTS5 not available on all SQLite builds" |
| MUST/NEVER | `MUST ...` / `NEVER ...` | "MUST validate schema before persist" |
| Error-Fix | Error message + subsequent fix | Exception trace -> resolution pattern |

### Capture Flow Comparison

**Claude Native:**
```
Conversation happens → Claude auto-summarizes → MEMORY.md updated
                       (no review, no schema, no source tracking)
```

**EFM:**
```
Conversation happens → Stop hook scans → Candidates extracted
  → Schema validation (14 rules) → Source verification
  → Dedup check (feedback on skips) → Confidence scoring
  → High confidence → events.jsonl (auto-persist)
  → Low confidence → Draft queue → Human approval → events.jsonl
```

**Core difference**: Claude native memory is a diary — the AI summarizes what it thinks matters. EFM is a **controlled evidence chain** — every memory must have provenance, a rule, and verification. Confidence-based routing balances automation with safety.

---

## 5. Memory Retrieval

| Dimension | Claude Native | EF Memory |
|-----------|--------------|-----------|
| **Search engine** | Server-side black box | **4-level degradation**: Hybrid -> Vector -> Keyword -> Basic |
| **Algorithm** | Undisclosed | BM25 (0.4) + Vector (0.6) + Classification Boost |
| **Context injection** | Chat: on-demand search; Code: first 200 lines bulk-loaded | **Pre-edit hook auto-injects** + manual `/memory-search` |
| **Degradation strategy** | N/A | 4-level graceful degradation (100% availability) |
| **Priority weighting** | None | Hard+S1: +0.15, S2: +0.10, S3: +0.05 boost |
| **Zero-dependency fallback** | N/A | Basic token-overlap search (pure stdlib) |

### EFM 4-Level Search Degradation

```
Level 1: Hybrid (BM25 + Vector + Re-rank)
  Requirements: Embedder + FTS5
  Score = 0.4 x BM25 + 0.6 x Vector + Classification Boost

Level 2: Vector (Pure Semantic)
  Requirements: Embedder only
  Fallback when FTS5 unavailable

Level 3: Keyword (Pure BM25)
  Requirements: FTS5 only
  Fallback when no embedding provider configured

Level 4: Basic (Token Overlap)
  Requirements: None (pure Python stdlib)
  100% reliability fallback — used by pre_edit_search hook
```

### Proactive Protection

EFM's pre-edit hook automatically searches relevant memories when you edit a file. For example, editing `feature_engine.py` triggers:

```
PreToolUse:Edit fires → extract "feature_engine features" as query
  → search_memory() → max 3 results
  → Injects: "[EF Memory] [Hard|S1] shift(1) MUST precede rolling()..."
```

Claude native memory has no equivalent proactive guard — it relies on Claude voluntarily recalling relevant information.

---

## 6. Memory Lifecycle Management

| Dimension | Claude Native | EF Memory |
|-----------|--------------|-----------|
| **Freshness management** | None | 90-day staleness detection, 120-day half-life decay |
| **Confidence scoring** | None | 4-factor model (source quality x age decay x verification history x source validity) |
| **Compaction / archival** | Chat: auto-overwrite synthesis; Code: manual | Auto-compact at waste_ratio >= 2.0 -> quarterly archive |
| **Deprecation suggestions** | None | Confidence < 0.3 -> auto-suggest deprecation |
| **Merge recommendations** | None | Similarity > 0.95 -> suggest consolidation |
| **Contradiction detection** | None | MUST/NEVER opposition detection + LLM semantic analysis |
| **Cross-entry reasoning** | None | Heuristic correlation + LLM enrichment (optional) |

### EFM Memory Evolution

This is EFM's most fundamental differentiator — **memories are not static; they evolve**:

1. **Age decay**: Confidence decreases exponentially (120-day half-life, configurable)
2. **Contradiction detection**: MUST/NEVER pairs flagged; LLM validates semantic conflicts
3. **Duplicate clustering**: Near-identical entries identified for merge
4. **Deprecation flow**: Low-confidence entries flagged -> human review -> soft-delete
5. **Synthesis suggestions**: Related entries grouped for higher-level principle extraction
6. **Risk assessment**: Staleness + confidence + severity -> actionable risk annotations

Claude native memory has none of these capabilities. Old memories persist indefinitely without review, contradictions go undetected, and duplicates accumulate silently.

---

## 7. Integration & Automation

| Dimension | Claude Native | EF Memory |
|-----------|--------------|-----------|
| **Hook integration** | None | 5 Claude Code Hooks (SessionStart, PreEdit, PlanMode, Stop, PreCompact) |
| **Rule injection** | CLAUDE.md bulk-loaded | `.claude/rules/ef-memory/` domain-scoped auto-injection |
| **Working memory** | None | Working Memory V3 (task_plan + findings + progress) |
| **Reasoning engine** | None | Dual-stage: Heuristic (zero cost) + LLM enrichment (optional) |
| **Multi-model support** | Claude only | Gemini / OpenAI / Ollama for embeddings + LLM reasoning |
| **Pipeline automation** | None | 4-step pipeline with retry + exponential backoff + state tracking |

### EFM Hook System

| Hook | Event | Action |
|------|-------|--------|
| SessionStart | Session begins | Health check, pending drafts, stale entries, session status |
| PreToolUse:Edit/Write | Before file edit | Search memory -> inject relevant entries as context |
| PreToolUse:EnterPlanMode | Plan mode entry | Auto-start working memory, prefill findings |
| Stop | Response complete | Auto-harvest session / scan conversation -> drafts; auto-compact |
| PreCompact | Context running low | Reminder to preserve session state |

All hooks are idempotent, never block user actions, and communicate via `additionalContext` injection.

---

## 8. Limitations Compared

| Limitation | Claude Native | EF Memory |
|------------|--------------|-----------|
| **Capacity** | Auto Memory: 200 lines effective; Chat: undisclosed cap | Theoretically unlimited (compaction + archive + incremental sync) |
| **Cross-project** | Chat/Code memory don't sync | Per-project isolation (but structured data is portable) |
| **Initial setup** | Zero configuration | One-command init with preset profiles (`--preset standard`) |
| **Maintenance** | Zero | Low (fully automated pipeline with retry + checkpointing) |
| **Learning curve** | None | Gentle (3 core commands to start, 7 advanced when needed; preset configs) |
| **Cost** | Included in plan | Free (optional API costs for embedding/LLM providers) |
| **Cross-product sync** | Chat and Code memory are completely separate | N/A (Code-only system) |

---

## 9. Complementary Usage Model

EFM and Claude's native memory are **complementary, not competing**. They address fundamentally different needs.

```
+-----------------------------------------------------------+
|              Claude Native Memory                          |
|  Preferences  |  Work style  |  Project overview           |
|  "I prefer TypeScript"  "We use pnpm"  "Concise style"    |
|  -> Best for: personal preferences, communication style,  |
|     general project context, team conventions              |
+-----------------------------------------------------------+
                          +
+-----------------------------------------------------------+
|              EF Memory (EFM)                               |
|  Rules  |  Lessons  |  Constraints  |  Risks  |  Facts    |
|  "shift(1) MUST precede rolling()" [Hard|S1]              |
|  -> Best for: critical rules, historical lessons,          |
|     architecture constraints, risk management,             |
|     compliance requirements                                |
+-----------------------------------------------------------+
```

### Recommended Division of Responsibility

| What to remember | Use | Why |
|------------------|-----|-----|
| "I prefer functional style" | Claude Native | Personal preference, applies everywhere |
| "We use pnpm, not npm" | Claude Native or CLAUDE.md | Convention, simple text |
| "shift(1) MUST precede rolling() on price data" | **EFM** | S1 critical rule with source evidence, needs enforcement |
| "INC-036: 42 features leaked future data" | **EFM** | Historical lesson with traceable incident, needs verification |
| "API rate limit is 100 req/min" | **EFM** | Constraint with source, may become stale |
| "Decided LightGBM over XGBoost for latency" | **EFM** | Architecture decision with rationale, needs lifecycle |
| "User prefers Chinese communication" | Claude Native | Personal preference |
| "FTS5 may not be available on all SQLite builds" | **EFM** | Risk with severity classification, needs monitoring |

### One-Line Summary

> **Claude Native Memory** is a short-term memo pad — it remembers who you are and how you work.
> **EF Memory** is an engineering knowledge base — it prevents teams from repeating mistakes, with structured evidence chains and self-evolving lifecycle management.

---

## 10. Feature Matrix

| Feature | Claude.ai Chat | Claude Code Auto | Memory Tool API | EF Memory |
|---------|---------------|-----------------|----------------|-----------|
| Schema validation | - | - | - | **14 rules** |
| Source traceability | - | - | - | **Required** |
| Type system | - | - | - | **5 types x 2 classes x 3 severities** |
| Append-only audit trail | - | - | - | **Yes** |
| Human review gate | - | - | - | **Configurable** |
| Hybrid search (BM25+Vector) | ? | - | - | **4-level degradation** |
| Pre-edit context injection | - | - | - | **Hook-driven** |
| Classification-weighted ranking | - | - | - | **Yes** |
| Confidence scoring | - | - | - | **4-factor model + extraction confidence routing** |
| Staleness detection | - | - | - | **90-day threshold** |
| Contradiction detection | - | - | - | **Heuristic + LLM** |
| Duplicate clustering | - | - | - | **Text + semantic (with feedback)** |
| Auto-compaction & archive | - | - | - | **Quarterly sharding** |
| Config presets | - | - | - | **3 profiles (minimal/standard/full)** |
| Pipeline resilience | - | - | - | **Retry + exponential backoff + checkpoint** |
| Evolution checkpoint | - | - | - | **Hash-based incremental (skips O(n²))** |
| Session-complete signal | - | - | - | **Task plan phase tracking** |
| Working memory (session) | - | - | - | **V3 (plan+findings+progress)** |
| LLM reasoning layer | - | - | - | **Dual-stage** |
| Multi-provider embeddings | - | - | - | **Gemini/OpenAI/Ollama** |
| Rule auto-injection | CLAUDE.md | CLAUDE.md | - | **Domain-scoped rules** |
| Git-tracked | - | CLAUDE.md only | - | **Full system** |
| Zero-config setup | **Yes** | **Yes** | Moderate | Requires init |
| Cross-conversation persistence | **Yes** | **Yes** | **Yes** | **Yes** |
| Team sharing | Enterprise | CLAUDE.md | Developer choice | **Git-native** |
| Auto-capture from conversation | **~24h cycle** | Real-time | - | **Stop hook (real-time)** |

---

## Appendix: Architecture Diagram

```
Claude Native Memory                    EF Memory (EFM)
==================                    ================

  Conversation                          Conversation
      |                                     |
      v                                     v
  Auto-synthesize                    Stop Hook: 6 extraction patterns
  (black box, ~24h)                  (LESSON/CONSTRAINT/DECISION/
      |                               WARNING/MUST-NEVER/Error-Fix)
      v                                     |
  MEMORY.md                                 v
  (free text,                        Schema Validation (14 rules)
   200 lines cap)                    Source Verification
      |                              Dedup Check (0.85 threshold)
      v                                     |
  Bulk-load at                              v
  session start                      Draft Queue (human review)
      |                                     |
      v                                     v
  Claude reads                       events.jsonl (append-only)
  when relevant                             |
                                    +-------+-------+
                                    |       |       |
                                    v       v       v
                                  Sync   Rules   Evolution
                                (vectors) (inject) (decay)
                                    |       |       |
                                    v       v       v
                                 Search  Auto-   Reasoning
                                (4-level) inject  (2-stage)
                                    |       |       |
                                    v       v       v
                                 Pre-edit  .claude/ Correlations
                                  hook    rules/   Contradictions
                                           |       Synthesis
                                           v
                                    +------+------+
                                    |             |
                                    v             v
                                 Compaction    Archive
                                (waste>=2.0)  (quarterly)
```

---

*This document is part of the EF Memory for Claude project. For implementation details, see [SCHEMA.md](../.memory/SCHEMA.md) and [README.md](../README.md).*
