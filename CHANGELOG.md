# Changelog

All notable changes to EF Memory for Claude will be documented in this file.

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
