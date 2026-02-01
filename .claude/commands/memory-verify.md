# /memory-verify — Memory Integrity Check (Read-only)

## Purpose

Verify that existing memory entries remain valid, trustworthy, and actionable over time.

**This command performs checks only. It NEVER modifies memory, writes files, or executes verify commands.**

---

## When to use

- Before major refactors (ensure constraints still apply)
- Periodically (monthly hygiene check)
- After incidents (verify related memories are current)
- Before citing memory in decisions (trust but verify)

---

## Input

| Format | Description |
|--------|-------------|
| `/memory-verify` | Verify all entries in `.memory/events.jsonl` (default) |
| `/memory-verify --id=<id>` | Verify single entry by ID |

---

## Output Modes

| Flag | Behavior |
|------|----------|
| (default) | Summary table: `OK` / `WARN` / `FAIL` per entry |
| `--full` | Expand each entry with check details + recommended actions |

---

## Verification Checks

### 1. Schema Sanity

Validates entry structure against `.memory/SCHEMA.md`:

| Check | Rule | Result |
|-------|------|--------|
| Required fields | `id`, `type`, `classification`, `title`, `content`, `source`, `created_at` must exist | FAIL if missing |
| ID format | Pattern `^[a-z]+-[a-z0-9_]+-[a-f0-9]{8}$` | FAIL if malformed |
| Type enum | `decision` \| `lesson` \| `constraint` \| `risk` \| `fact` | FAIL if invalid |
| Classification enum | `hard` \| `soft` | FAIL if invalid |
| Severity enum | `S1` \| `S2` \| `S3` \| `null` | WARN if invalid |
| Executable constraint | `rule != null OR implication != null` | FAIL if both null |
| Content length | 2-6 items in array | WARN if out of range |
| Title length | ≤120 characters | WARN if exceeded |

---

### 2. Source Resolvable

For each source in `source[]`, verify the reference exists:

| Source Type | Validation Steps | Files to Read |
|-------------|------------------|---------------|
| Markdown `path#anchor:L<s>-L<e>` | 1. File exists<br>2. Anchor heading exists<br>3. Line range valid | Read the target `.md` file |
| Code `path:L<s>-L<e>` | 1. File exists<br>2. Line range within bounds | Check file existence + line count |
| Function `path::func` | 1. File exists<br>2. Function definition exists | Read the target `.py` file |
| Commit `commit <hash>` | Hash exists in git history | Run `git cat-file -t <hash>` |
| PR `PR #<id>` | (Informational only, no validation) | — |

**Result levels:**

| Status | Meaning |
|--------|---------|
| ✅ `OK` | Source fully resolvable |
| ⚠️ `WARN` | Anchor exists but line numbers outdated (content drift) |
| ❌ `FAIL` | Source missing, file deleted, or anchor not found |

**Explicit requirement**: When validating source, MUST specify which file will be read. Example:
```
Checking source: docs/decisions/INCIDENTS.md#INC-036:L553-L761
→ Will read: docs/decisions/INCIDENTS.md
→ Looking for: heading "## INC-036" at or near lines 553-761
```

---

### 3. Verify Command — Static Safety Check

If `verify` field is non-null, perform **static analysis only** (NEVER execute):

| Check | Rule | Result |
|-------|------|--------|
| Syntax | Valid shell/Python command structure | WARN if malformed |
| Read-only | No write operations (`>`, `>>`, `rm`, `mv`, `sed -i`) | FAIL if destructive |
| Safe patterns | `grep`, `cat`, `find`, `python -c "...print..."` | OK |
| Dangerous patterns | `curl`, `wget`, `pip install`, `eval`, `exec` | FAIL |
| Path scope | References project-relative paths only | WARN if absolute paths outside project |

**Guardrails**: The verify command is NEVER executed by `/memory-verify`. It is only analyzed for syntax and safety.

---

### 4. Staleness Check

Calculate freshness based on timestamps:

| Field | Calculation |
|-------|-------------|
| `last_verified` | Days since last human verification |
| `created_at` | Days since creation (if never verified) |

**Staleness thresholds:**

| Days | Status | Meaning |
|------|--------|---------|
| ≤30 | ✅ Fresh | Recently verified or created |
| 31-90 | ⚠️ Review | Consider re-verification |
| >90 | ⚠️ Stale | Likely needs review before trusting |
| Never verified | ⚠️ Unverified | `last_verified` is null |

**Note**: Staleness is informational only. It does NOT trigger automatic deprecation.

---

### 5. Supersession Check (Informational)

If `_meta.superseded_by` exists:

| Check | Rule |
|-------|------|
| Target exists | Referenced ID must exist in events.jsonl |
| Deprecated flag | Entry should have `deprecated: true` |
| Chain integrity | No circular references |

---

## Summary Table Format (Default)

```
/memory-verify

========================================
MEMORY VERIFICATION REPORT
========================================
Storage: .memory/events.jsonl
Entries: 3
Date: 2026-02-01

| ID                       | Schema | Source | Verify | Stale  | Status |
|--------------------------|--------|--------|--------|--------|--------|
| lesson-inc034-d1760930   | OK     | OK     | OK     | 90d ⚠️ | WARN   |
| lesson-inc035-800ae2e3   | OK     | OK     | OK     | 90d ⚠️ | WARN   |
| lesson-inc036-e3f13b37   | OK     | OK     | OK     | 90d ⚠️ | WARN   |

========================================
SUMMARY
========================================
✅ PASS: 0
⚠️ WARN: 3
❌ FAIL: 0

All warnings are due to staleness (never verified).

No files were modified. This is a read-only report.
```

---

## Full Mode Format (--full)

```
/memory-verify --id=lesson-inc036-e3f13b37 --full

========================================
SINGLE ENTRY VERIFICATION
========================================
ID: lesson-inc036-e3f13b37
Title: Rolling statistics without shift(1) caused 999x backtest inflation

---

[1/4] SCHEMA SANITY
Status: ✅ OK
- id: valid format (lesson-inc036-e3f13b37)
- type: lesson ✓
- classification: hard ✓
- severity: S1 ✓
- title: 73 chars (≤120) ✓
- content: 4 items (2-6 range) ✓
- rule: present ✓
- implication: present ✓
- source: 1 reference ✓
- created_at: 2026-02-01T16:00:00Z ✓

---

[2/4] SOURCE RESOLVABLE
Source: docs/decisions/INCIDENTS.md#INC-036:L553-L761

⚠️ Requires file read to verify.
→ File to read: docs/decisions/INCIDENTS.md
→ Looking for: anchor "## INC-036" near lines 553-761

[Verification performed]
- File exists: ✅ Yes
- Anchor found: ✅ "## INC-036" found at line 554
- Line range: ⚠️ Content extends to line 775 (declared L761)

Status: ⚠️ WARN (line range drift detected, anchor valid)

---

[3/4] VERIFY COMMAND SAFETY
Command: grep -rn '\.rolling\|\.ewm\|\.pct_change' src/features/*.py | grep -v 'shift(1)'

Static analysis (NOT executed):
- Pattern: grep with pipe ✓
- Read-only: yes (no write operators) ✓
- Scope: project-relative path (src/features/*.py) ✓
- Dangerous patterns: none detected ✓

Status: ✅ OK (safe read-only command)

---

[4/4] STALENESS CHECK
created_at: 2026-02-01T16:00:00Z
last_verified: null

Days since creation: ~0 days
Days since verification: NEVER

Status: ⚠️ WARN (never verified)

---

========================================
OVERALL STATUS: ⚠️ WARN
========================================

Issues found:
1. Source line range slightly outdated (761 → 775)
2. Entry has never been verified

Recommended actions:
1. Read docs/decisions/INCIDENTS.md#INC-036 to confirm line range
2. Update source to L553-L775 if confirmed
3. Set last_verified to current date after review

No files were modified. Use /memory-save to update if needed.
```

---

## Guardrails (Mandatory)

```
┌────────────────────────────────────────────────────────────────────┐
│ HARD CONSTRAINTS — VIOLATION = COMMAND FAILURE                     │
├────────────────────────────────────────────────────────────────────┤
│ 1. NEVER write to events.jsonl or any file                         │
│ 2. NEVER execute the verify command (static analysis only)         │
│ 3. NEVER auto-fix or auto-deprecate entries                        │
│ 4. NEVER fabricate line numbers, anchors, or file contents         │
│ 5. NEVER guess if a source exists — must explicitly check          │
│ 6. ALWAYS state which file will be read before reading             │
│ 7. ALWAYS report "No files were modified" at end of output         │
│ 8. ALWAYS distinguish between "checked" and "assumed" results      │
└────────────────────────────────────────────────────────────────────┘
```

**Note on example outputs**: In examples, ⚠️ may indicate "not checked yet" or "needs review". Always distinguish checked vs assumed.

---

## Example Outputs

### Example 1: Single Entry Verification

```
/memory-verify --id=lesson-inc036-e3f13b37

========================================
SINGLE ENTRY VERIFICATION
========================================
ID: lesson-inc036-e3f13b37
Title: Rolling statistics without shift(1) caused 999x backtest inflation

| Check    | Status | Details                                          |
|----------|--------|--------------------------------------------------|
| Schema   | ✅ OK  | All required fields present, types valid         |
| Source   | ⚠️ WARN| Anchor valid; line range may have drifted        |
| Verify   | ✅ OK  | grep command is safe (read-only, project-scoped) |
| Staleness| ⚠️ WARN| Never verified (last_verified: null)             |

OVERALL: ⚠️ WARN

Files read during verification:
- docs/decisions/INCIDENTS.md (source anchor check)

Recommended actions:
- Confirm line range L553-L761 is still accurate
- Run verification and update last_verified

No files were modified.
```

### Example 2: Full Verification (Summary Table)

```
/memory-verify

========================================
MEMORY VERIFICATION REPORT
========================================
Storage: .memory/events.jsonl
Entries: 3
Date: 2026-02-01

| ID                       | Type   | Schema | Source | Verify | Stale  | Overall |
|--------------------------|--------|--------|--------|--------|--------|---------|
| lesson-inc034-d1760930   | lesson | ✅     | ⚠️     | ✅     | ⚠️     | ⚠️ WARN |
| lesson-inc035-800ae2e3   | lesson | ✅     | ⚠️     | N/A    | ⚠️     | ⚠️ WARN |
| lesson-inc036-e3f13b37   | lesson | ✅     | ⚠️     | ✅     | ⚠️     | ⚠️ WARN |

Legend:
- ✅ OK: Check passed
- ⚠️ WARN: Issue detected, needs review
- ❌ FAIL: Critical issue, entry may be invalid
- N/A: Field not present (verify is optional)

========================================
SUMMARY
========================================
✅ PASS: 0
⚠️ WARN: 3
❌ FAIL: 0

Common issues:
- [3 entries] Never verified (last_verified: null)
- [3 entries] Source line ranges need confirmation

Files read during verification:
- docs/decisions/INCIDENTS.md (all 3 sources reference this file)

Recommended actions:
1. Read INCIDENTS.md to confirm INC-034/035/036 line ranges
2. Update source fields if line numbers have drifted
3. Set last_verified after manual review

========================================
No files were modified. This is a read-only report.
========================================
```

---

## Limitations (v1.1)

| Not Supported | Reason | Future |
|---------------|--------|--------|
| Auto-fix | Violates read-only guarantee | Never |
| Execute verify commands | Security risk | Never |
| CI integration | Requires persistent state | Phase 2 |
| Batch line-number correction | Complex merge logic | Phase 2 |
| Embedding validation | No vector store in v1 | Phase 2 |

---

## Version

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-02-01 | Initial specification (read-only) |
| 1.1 | 2026-02-01 | Schema sanity checks, verify safety analysis, explicit file read declaration, detailed examples |
