# /memory-import — Semi-automatic memory extraction from project documents

## Purpose

Extract Hard Memory candidates from structured project documents (primarily `docs/decisions/INCIDENTS.md`) for human review.

- **Input**: Markdown documents with structured incident/decision records
- **Output**: MEMORY ENTRY blocks in `/memory-save` format (displayed in response only)
- **Guarantee**: This command NEVER writes files. Persistence is always a separate, explicit workflow.

---

## What /memory-import Does

1. User provides (or points to) an INC-xxx section from INCIDENTS.md
2. Claude extracts 1-N MEMORY ENTRY candidates
3. Each candidate MUST have Rule or Implication (otherwise rejected)
4. Source is normalized to at least `#INC-xxx` (line numbers are best-effort)
5. Human reviews, edits, and decides what to persist via `/memory-save`

## What /memory-import Does NOT Do

- ❌ Write to `events.jsonl` or any file
- ❌ Maintain approval state or pending lists
- ❌ Guarantee exact line numbers (best-effort only)
- ❌ Deduplicate against existing memory
- ❌ Auto-persist anything

---

## Supported Source Documents

| Document | Content Type | Expected Sections |
|----------|--------------|-------------------|
| `docs/decisions/INCIDENTS.md` | Incident records | Root Cause, Fix, Regression, Lessons |

*DECISIONS.md support planned for Phase 2.*

---

## Extraction Rules

### MUST Extract

| Section | Maps To | Required Fields |
|---------|---------|-----------------|
| **Root Cause** / 错误原因 | `Content` + `Implication` | What went wrong, why it matters |
| **Fix** / 修复方案 | `Rule` | MUST/NEVER statement derived from fix |
| **Regression Check** / 验证 | `Verify` | One-line command or observable check |
| **Lessons Learned** / 经验教训 | `Content` | Key takeaways (max 4 points) |

### MUST NOT Extract

| Content Type | Reason |
|--------------|--------|
| Timeline / 时间线 | No reuse value; context-specific |
| Raw logs / 原始日志 | Noise; not actionable |
| File listings (unless constraint) | Volatile; likely outdated |
| Estimated time / 预估时间 | Not a rule or fact |
| Intermediate discussion | Not a conclusion |
| Agent handoff notes | Session-specific |

### Extraction Heuristics

```
1. If "✅ 修复" or "Fix" section exists → derive Rule
2. If "🔍 错误详情" or "Root Cause" exists → derive Content + Implication
3. If "验证" or "Regression" checklist exists → derive Verify
4. If error caused >10x metric distortion → Severity = S1
5. If error affected production/live trading → Classification = Hard
6. If error is calculation/logic bug → Tags include "leakage" or "calculation"
```

---

## Usage

```
/memory-import docs/decisions/INCIDENTS.md#INC-036
```

Or provide the incident content directly in the conversation.

---

## Source Normalization

### Two Levels (both valid)

| Level | Format | When to Use |
|-------|--------|-------------|
| **A (Ideal)** | `docs/decisions/INCIDENTS.md#INC-036:L553-L699` | When line numbers are provided or verifiable |
| **B (Acceptable)** | `docs/decisions/INCIDENTS.md#INC-036` | When exact lines cannot be determined |

### Best-Effort Line Numbers

If exact line numbers cannot be determined:
- Output the stable anchor only (`#INC-036`)
- Annotate with `[Line numbers needed]` if precision is important
- Human or future tooling (`/memory-verify`) can add line numbers later

**Do NOT invent line numbers. An anchor without lines is better than wrong lines.**

---

## Output Format

Output follows `/memory-save` MEMORY ENTRY format exactly:

```
/memory-import docs/decisions/INCIDENTS.md#INC-036

Scanning: INC-036 section

========================================
IMPORT CANDIDATE #1
========================================

MEMORY ENTRY
Type: lesson
Recommended: Hard
Severity: S1
Title: Rolling statistics without shift(1) caused 999x backtest inflation
Content:
- 42 rolling/ewm/pct_change calls missing shift(1) in feature engine
- Model learned to "explain past" not "predict future"
- IC with T-5 returns (-0.115) > IC with T+1 returns (0.018)
- Backtest showed 49,979% return; after fix only 52%
Rule: shift(1) MUST precede any rolling(), ewm(), pct_change() on price-derived data
Implication: Backtest returns inflated 100-1000x; predictions structurally encode future information
Verify: grep -rn "rolling\|ewm\|pct_change" src/features/*.py | grep -v "shift(1)"
Source:
- docs/decisions/INCIDENTS.md#INC-036
Tags: leakage, feature-engine, shift, rolling

---

========================================
IMPORT SUMMARY
========================================
Candidates extracted: 1
  - Hard/S1: 1

⚠️ REVIEW REQUIRED

This is a dry-run output. No files have been modified.

To persist this entry:
1. Review and edit the MEMORY ENTRY above as needed
2. Copy the final version
3. Use /memory-save workflow to display for confirmation
4. Explicitly request file write if Guardrails allow

/memory-import never writes files.
```

---

## Human Review Workflow

### Review Checklist

Before persisting any entry, human MUST verify:

```
□ Title accurately summarizes the lesson (not just the incident)
□ Rule is actionable and checkable (MUST/NEVER/ALWAYS)
□ Implication explains real-world consequence
□ Source points to correct incident (verify anchor exists)
□ Content has 2-6 concrete points (no fluff)
□ Severity matches impact (S1 = invalidates results or production incident)
□ No timeline, logs, or speculative content included
```

### How to Persist (Human-Driven)

1. **Review** the MEMORY ENTRY candidate displayed above
2. **Edit** by copying and modifying in your response if needed
3. **Run** `/memory-save` to confirm the entry format
4. **Request** explicit file write (e.g., "Write this entry to events.jsonl")

**There is no "approve" command.** Persistence is always a manual, explicit action.

---

## Quality Gates

### Automatic Rejection

```
REJECT if: No Rule AND no Implication can be derived from incident
REJECT if: Incident has no "Fix" or "修复" section (not actionable)
```

### Warnings

```
WARN if: Content exceeds 6 bullet points (likely too verbose)
WARN if: Source section exceeds 200 lines (may need to narrow scope)
WARN if: No "验证" or "Regression" section (Verify field will be empty)
```

---

## Guardrails

### Hard Constraints

```
- NEVER write to events.jsonl or any file
- NEVER claim to maintain approval state or pending lists
- NEVER invent line numbers; use anchor-only format if uncertain
- NEVER auto-persist extracted entries
- ALWAYS display "No files have been modified" at end of output
- ALWAYS require human to explicitly request persistence
```

---

## Limitations (v1.0)

| Not Supported | Reason | Planned |
|---------------|--------|---------|
| Automatic persistence | Violates Guardrails | Never |
| Duplicate detection | Requires reading events.jsonl | Phase 2 |
| DECISIONS.md import | Different structure | Phase 2 |
| Batch approval commands | No persistent state | Never |
| Guaranteed line numbers | Claude cannot reliably determine | Best-effort only |

---

## Future Extensions (Phase 2)

- Import from `DECISIONS.md` (DEC-xxx entries)
- Duplicate detection against existing memory
- Supersession workflow (mark old entry deprecated)
- Import from code comments (`# LESSON:` markers)
- `/memory-verify` to validate and enrich sources

---

## Version

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-02-01 | Initial design, INCIDENTS.md support, read-only |
