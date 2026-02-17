# Changelog

All notable changes to EFM (Evidence-First Memory for Claude Code) will be documented in this file.

---

## 2026-02-17 — V3.2 Phase 6: Git Merge Safety (`/memory-repair`)

### New: Post-merge repair command for `events.jsonl`

When multiple branches append to `events.jsonl`, git merges can introduce conflict markers, duplicate entries, and orphan source references. The new `/memory-repair` command fixes all of these in one pass.

**New files (3):**
- `.memory/lib/repair.py` — Core repair logic: merge marker removal, ID-based dedup (newest `created_at` wins, file position tiebreak), chronological sort, orphan source detection, atomic rewrite with backup
- `.memory/scripts/repair_cli.py` — CLI entry point (`--dry-run`, `--no-backup`)
- `.claude/commands/memory-repair.md` — `/memory-repair` slash command (dry-run → confirm → repair → report)

**Modified files (3):**
- `.memory/lib/auto_sync.py` — Startup health check now detects merge conflict markers in `events.jsonl` and warns with `/memory-repair` suggestion
- `.memory/lib/init.py` — `scan_project()` now suggests `.gitattributes merge=union` for `events.jsonl` (prevents conflict markers on merge)
- `README.md` — New FAQ #11 (git merge conflicts) and version bump

**New tests:**
- `.memory/tests/test_repair.py` — 25+ tests covering merge marker detection, raw line parsing, newest-wins dedup, orphan source checks, integration (repair + backup + dry-run + sort), and startup hint integration

**Key design decisions:**
| Decision | Choice | Rationale |
|----------|--------|-----------|
| Conflict strategy | Remove marker lines, keep all valid JSON | Markers aren't valid JSON; both sides' entries are valuable |
| Dedup strategy | Newest `created_at` wins | More deterministic than file position after merge |
| Tiebreak | Later file position wins | Consistent with append-only latest-wins semantics |
| Orphan sources | Report only, don't delete | Entries may still be valuable; user decides |
| Prevention | `.gitattributes merge=union` | Git keeps both sides' lines without conflict markers |

---

## 2026-02-15 — V3.2 Phase 5: Hook Safety for Non-Git Directories

### Fix: Stop hook infinite loop in non-git subdirectories

**BUG: Hook commands fail in non-git directories**
All hook commands used `cd "$(git rev-parse --show-toplevel)" && ...` to find the project root. When Claude Code is opened from a directory that isn't inside a git repository (e.g. `infra/openclaw-veda/` inside a mono-repo), `git rev-parse` fails with exit code 128 and writes to stderr. For the Stop hook specifically, this error output is fed back into the conversation, causing Claude to respond, which triggers another Stop → **infinite loop**.

**Fix:** Changed the hook command prefix to:
```
_r="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; cd "$_r" && ...
```
This suppresses stderr (`2>/dev/null`) and silently exits the hook (`|| exit 0`) when not in a git repo. All 5 hooks (SessionStart, PreToolUse:Edit/Write, PreToolUse:EnterPlanMode, Stop, PreCompact) updated.

**Upgrade:** Run `/memory-init` to regenerate hooks with the safe prefix in existing installations.

**Modified files (2):**
- `.memory/lib/init.py` — `generate_hooks_settings()` safe prefix
- `.memory/tests/test_init.py` — updated test to assert stderr suppression and graceful exit

**Test count: 953** (unchanged)

---

## 2026-02-11 — V3.2 Phase 3: Intelligence Upgrades, Session Safety, Test Coverage

### 9 improvements across search intelligence, scanner safety, session health, DRY refactoring, and test coverage

**SMART: Confidence-aware search ranking**
Search scoring now incorporates `_meta.confidence` from entry metadata. A configurable `confidence_weight` (default 0.1) multiplied by the entry's confidence value is added to all 4 search modes (hybrid, vector, keyword, basic). Entries with higher confidence scores rank slightly higher. New `_compute_confidence_boost()` function and `SearchResult.confidence_boost` field for transparency.

**SAFETY: Scanner file size limits**
`_build_document_info()` now checks file size before reading. Files exceeding `scan.max_file_size_bytes` (default 5MB) are skipped with a log message. Line count safety cap at 100,000 prevents OOM on extremely long single-line files. `ScanReport.skipped_oversized` tracks count.

**SMART: Orphan session detection**
New `is_session_stale()` function in `working_memory.py` detects abandoned sessions by checking mtime of session files against `v3.session_timeout_hours` (default 48h). `SessionStatus` extended with `is_stale` and `age_hours` fields. Startup hint now warns about stale sessions with age display.

**DRY: init.py `_read_raw_json()` helper**
Repeated `json.loads(path.read_text())` + try/except pattern in `_handle_hooks_json()` and `_handle_settings_json()` extracted to `_read_raw_json()` helper. Returns `Optional[dict]`, handles missing/corrupt files uniformly.

**TEST: Search internals coverage (+22 tests)**
Direct tests for `_search_hybrid()`, `_search_vector()`, `_search_keyword()`, `_get_search_weights()`, and confidence boost behavior across all search modes.

**TEST: prompts.py zero → full coverage (+21 tests)**
New `test_prompts.py` covering `_truncate()`, `entries_to_compact_text()`, all 4 prompt builders (correlation, contradiction, synthesis, risk), single-entry prompt, and default constants.

**TEST: Scanner helpers + file size limits (+9 tests)**
`_extract_snippet()` direct tests (heading+content, no heading, empty, truncation) and file size limit behavior (oversized skip, normal pass, discover count, default constant).

**TEST: Working memory session completeness + stale detection (+11 tests)**
`is_session_complete()` (4 tests), `is_session_stale()` (4 tests), and `SessionStatus` stale field behavior (3 tests).

**TEST: Auto-sync stale hints + init DRY helper (+7 tests)**
Stale session hint formatting (3 tests) and `_read_raw_json()` helper (4 tests).

**Modified files (5):**
- `.memory/lib/search.py` — confidence_weight, _compute_confidence_boost, SearchResult.confidence_boost
- `.memory/lib/scanner.py` — _MAX_FILE_SIZE_BYTES, _MAX_LINE_COUNT, ScanReport.skipped_oversized
- `.memory/lib/working_memory.py` — is_session_stale(), SessionStatus.is_stale/age_hours
- `.memory/lib/auto_sync.py` — StartupReport.session_stale/session_age_hours, stale hint
- `.memory/lib/init.py` — _read_raw_json() DRY helper

**Test count: 871 → 938** (+67 tests: 22 search + 21 prompts + 9 scanner + 11 working_memory + 3 auto_sync + 4 init — new file: test_prompts.py)

---

## 2026-02-11 — V3.2 Phase 2: Security Hardening, Test Coverage, Robustness

### 11 improvements across I/O performance, self-heal, error handling, test coverage, and startup architecture

**PERF: events_io.py byte-offset mode no longer re-reads entire file**
When using `byte_offset > 0` (incremental sync), the function no longer seeks back to position 0 to count total lines. This eliminates an O(n) full-file scan on every sync operation.

**TEST: Comprehensive events_io.py test coverage (18 tests)**
New `test_events_io.py` covering: empty/missing files, latest-wins semantics, start_line skipping, track_lines metadata, byte-offset incremental sync, invalid JSON handling, blank lines, missing IDs, OSError recovery, and large file line counting.

**SELF-HEAL: vectordb.py schema versioning infrastructure**
Added `SCHEMA_VERSION = 1` constant and `PRAGMA user_version` tracking. `ensure_schema()` now checks schema version on open, calls `_migrate()` framework for future upgrades, and warns when database is newer than code. `stats()` includes `schema_version`.

**TEST: _compute_extraction_confidence() full coverage (10 tests)**
All 7+ scoring branches tested: lesson/decision/constraint markers (0.9), must/never/always (0.8), warning/risk (0.75), error/fix (0.7), unknown (0.6), title length bonus, rule bonus, score capping at 1.0.

**LOGIC: Evolution verification boost thresholds now configurable**
`evolution.py` 30/90 day hardcoded thresholds replaced with `evo_config.verification_boost.{full_boost_days, partial_boost_days, partial_boost_value}` (defaults preserved).

**ERROR: 12 silent except:pass replaced with logging**
All hooks (stop_harvest, plan_start, pre_edit_search) and auto_sync.py now log warnings on config load failure, scan failure, compaction failure, draft check failure, and session recovery failure. Search exceptions use logger.debug (normal when no events.jsonl).

**SIMPLIFY: check_startup() decomposed into 6 focused functions**
134-line monolith split into: `_check_drafts()`, `_load_and_count()`, `_check_compaction()`, `_check_version()`, `_check_staleness_and_sources()`, `_check_session_recovery()`. Pure mechanical extraction, behavior unchanged.

**SECURITY: stdin size validation in all hooks (10MB limit)**
`stop_harvest.py`, `plan_start.py`, `pre_edit_search.py` now cap stdin reads at 10MB, preventing potential DoS from oversized hook input.

**SECURITY: Atomic JSON writes in init.py**
`_stamp_efm_version()`, hooks.json, and settings.local.json writes now use `tempfile.mkstemp() + os.replace()` pattern. Crash during write cannot corrupt existing files.

**SELF-HEAL: Compaction audit logging for corrupted JSON**
`compaction.py` now logs warnings for each corrupted JSON line skipped, with line number and error details. Summary count logged after compaction.

**Modified files (12):**
- `.memory/lib/events_io.py` — byte-offset total_lines optimization
- `.memory/lib/vectordb.py` — SCHEMA_VERSION, PRAGMA user_version, _migrate framework
- `.memory/lib/evolution.py` — Configurable verification boost thresholds
- `.memory/lib/auto_sync.py` — check_startup decomposition, error logging
- `.memory/lib/init.py` — _atomic_write_json helper, atomic writes for config/hooks/settings
- `.memory/lib/compaction.py` — Corrupted JSON audit logging
- `.memory/hooks/stop_harvest.py` — Error logging, stdin size limit
- `.memory/hooks/plan_start.py` — Error logging, stdin size limit
- `.memory/hooks/pre_edit_search.py` — Error logging, stdin size limit

**Test count: 824 → 871** (+47 tests: 18 events_io + 10 confidence + 6 vectordb + 4 evolution + 4 auto_sync + 3 init + 2 compaction)

---

## 2026-02-11 — V3.2: Critical Bug Fixes, Performance, Intelligence

### 10 fixes across hooks, pipeline, reasoning, compaction, evolution, and presets

**Critical: pre_edit_search hook completely fixed (H-2)**
The pre-edit memory search hook was non-functional since creation — `search_memory()` returns a `SearchReport` dataclass, but the code iterated it as a list, causing a silent TypeError swallowed by `except: pass`. Now correctly uses `report.results` → `SearchResult.entry` dict access.

**Hook output format standardized (UX-1)**
`pre_edit_search.py` now outputs JSON `{"additionalContext": ...}` matching `plan_start.py` and `stop_harvest.py` protocol.

**Smart query enrichment for pre-edit search (SMART-1)**
Edit/Write hooks now extract identifiers from `old_string`/`content` (up to 6 terms) to enrich the search query beyond just the filename.

**Pipeline config preset loading (H-1)**
`pipeline_cli.py` now uses `load_config()` instead of raw `json.loads()`, ensuring preset defaults are applied consistently with stop hooks.

**Reasoning timestamp crash fix (LOGIC-02)**
`_parse_iso8601("")` in `reasoning.py` no longer crashes on empty/missing `created_at` — wrapped in try/except with graceful fallback.

**Auto-sync single-read optimization (H-4/PERF-05)**
`check_startup()` now reads `events.jsonl` once via `load_events_latest_wins()` instead of twice (once for entries, once for line count).

**RuntimeError control flow replaced (H-5)**
`auto_sync.py` no longer uses `raise RuntimeError` for flow control when `session_recovery` is disabled — replaced with clean if/else.

**Evolution checkpoint reset on compaction (INTEGRATION-4)**
`compact()` now deletes `evolution_checkpoint.json` after rewriting events, preventing stale cached analysis results.

**Content-aware evolution hash (SMART-3)**
`_compute_entry_ids_hash()` now includes `created_at` and `last_verified` in its fingerprint, so content changes invalidate the cache.

**Precompiled regex in working_memory (PERF-06)**
6 regex patterns in `_clean_markdown_artifacts()` moved to module-level precompiled constants.

**Dynamic plan session description (SMART-6)**
`plan_start.py` now extracts task description from hook input instead of hardcoding "Plan session".

**Preset compaction config (INTEGRATION-5)**
All 3 presets now include compaction thresholds: minimal=1.5, standard=2.0, full=3.0.

**Version bump: 3.1.0 → 3.2.0**

**Modified files (10):**
- `.memory/hooks/pre_edit_search.py` — SearchReport iteration fix, JSON output, query enrichment
- `.memory/hooks/plan_start.py` — Dynamic task description extraction
- `.memory/scripts/pipeline_cli.py` — load_config() for preset resolution
- `.memory/lib/reasoning.py` — Safe timestamp parsing
- `.memory/lib/auto_sync.py` — Single-read optimization, clean control flow
- `.memory/lib/compaction.py` — Evolution checkpoint reset
- `.memory/lib/evolution.py` — Content-aware entry hash
- `.memory/lib/working_memory.py` — Precompiled regex patterns
- `.memory/lib/config_presets.py` — Preset compaction config, version bump to 3.2.0

**Test count: 804 → 824** (+20 tests: 8 pre_edit_search + 5 auto_sync/reasoning + 7 compaction/evolution/presets)

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

### EFM v1.0

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
