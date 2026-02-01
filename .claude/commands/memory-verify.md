# /memory-verify — Memory Integrity Check (Read-only)

## Purpose

Verify that existing memory entries remain valid, trustworthy, and actionable over time.

**This command performs checks only. It NEVER modifies memory or writes files.**

---

## Why memory verification is necessary

Over time:

- Files move or get deleted
- Line numbers become outdated
- Rules become obsolete
- Decisions are superseded

**Unverified memory is worse than no memory.**

---

## Scope (v1)

`/memory-verify` operates on one or more memory entries and reports issues.

It does **not**:

- Auto-fix entries
- Deprecate entries automatically
- Rewrite sources

---

## Verification Checks

### 1. Source Integrity

For each source reference:

- File exists
- Anchor (heading / function) still present
- Line numbers (if provided) are within file bounds

**Results:**

- ✅ `OK`
- ⚠️ `Anchor exists but line numbers outdated`
- ❌ `Source missing or unresolvable`

---

### 2. Executable Validity

Checks that:

- At least one of `rule` or `implication` exists
- Rule still makes semantic sense given current codebase
- `verify` command (if present) is syntactically plausible

**Results:**

- ✅ `Executable`
- ⚠️ `Needs review`
- ❌ `No longer actionable`

---

### 3. Staleness Check

Based on:

- `last_verified` field
- File modification timestamps (informational)

**Heuristic only:**

- \>90 days without verification → mark `[Stale?]`
- No automatic action taken

---

### 4. Supersession Check (informational)

If `_meta.superseded_by` exists:

- Verify referenced entry exists
- Warn if chain is broken

---

## Output Format

**Example:**

```
/memory-verify lesson-inc036-e3f13b37

Status: ⚠️ Needs Review

Checks:
- Source: OK (anchor found, line numbers valid)
- Rule: OK
- Verify: OK (command is syntactically valid)
- Last verified: 2026-02-01 (90+ days ago) [Stale?]

Recommended action:
- Re-verify rule still applies after feature refactor
- Update last_verified timestamp if confirmed
```

---

## Usage

### Verify single entry

```
/memory-verify lesson-inc036-e3f13b37
```

### Verify all entries (summary)

```
/memory-verify --all
```

**Output:**

```
Memory Verification Summary

Total entries: 3
- ✅ OK: 2
- ⚠️ Needs review: 1
- ❌ Invalid: 0

Entries needing attention:
- lesson-inc034-d1760930: [Stale?] last_verified > 90 days
```

---

## Guardrails

```
- NEVER modify memory entries
- NEVER auto-deprecate
- NEVER guess missing information
- ALWAYS report uncertainty explicitly
- ALWAYS show source of each check result
```

---

## Expected Workflow

1. Run `/memory-verify` periodically (or before major refactors)
2. Review warnings
3. Manually update entries via `/memory-save`
4. Append updated versions to `events.jsonl`

---

## Non-goals (v1)

- Automatic fixes
- CI enforcement
- Batch auto-verification
- Confidence scoring

These belong to later phases.

---

## Version

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-02-01 | Initial specification (read-only) |
