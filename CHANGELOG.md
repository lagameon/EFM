# Changelog

All notable changes to EF Memory for Claude will be documented in this file.

---

## 2026-02-01

### `/memory-verify` upgraded to v1.1

**Changes:**

- Schema sanity checks expanded (8 validation rules)
- Source verification now requires "File to read" declaration
- Verify command static safety analysis added (safe/dangerous patterns)
- Staleness grading refined (≤30 / 31-90 / >90 / never)
- Guardrails strengthened to 8 hard constraints
- Examples expanded with single-entry and full-verification outputs

**Storage schema**: `SCHEMA v1.0` unchanged — this is a specification upgrade, not a breaking change.

---

## 2026-02-01 (Initial Release)

### EF Memory for Claude v1.0

- `/memory-save` — Create memory entries (manual, evidence-first)
- `/memory-search` — Query existing memory safely
- `/memory-import` — Extract memory candidates from documents (dry-run)
- `/memory-verify` — Verify memory integrity (read-only)
- `.memory/SCHEMA.md` — Storage contract v1.0
- `.memory/events.jsonl` — Append-only memory store
