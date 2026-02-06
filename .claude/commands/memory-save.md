# /memory-save — Save project memory (evidence-first)

## Purpose

Persist decisions, lessons, constraints, or risks from the current task into project memory.
All memory entries must be traceable to a concrete source and actionable.

**This command does not write files by default unless explicitly allowed by Guardrails.**

---

## When to use

- After finishing a task, investigation, or fix
- After resolving an incident or identifying root cause
- After making or revising a technical/architectural decision
- When discovering a non-obvious constraint or pitfall worth remembering

---

## What to extract

Extract only high-signal items. Prefer quality over quantity.

**Valid memory types:**

| Type | Description |
|------|-------------|
| `decision` | a chosen approach or rule (why A not B) |
| `lesson` | what went wrong / what to avoid |
| `constraint` | limitations, invariants, forbidden actions |
| `risk` | known fragile areas or failure modes |
| `fact` | stable project knowledge (paths, invariants, assumptions) |

**Avoid:**

- raw logs
- timelines
- speculative ideas
- temporary discussion without conclusion

---

## Required structure (MUST follow)

For each memory entry, produce:

1. **Type**: one of `decision` | `lesson` | `constraint` | `risk` | `fact`
2. **Recommended**: `Hard` | `Soft` (see classification below)
3. **Title**: one concise sentence
4. **Content**: 2–6 lines, concrete and unambiguous
5. **Rule or Implication**: at least one MUST be present (see below)
6. **Source (MANDATORY)**: normalized format (see below)

**If a valid source cannot be identified, do not create the entry.**
**If neither Rule nor Implication can be stated, do not create the entry.**

---

## Source format (MANDATORY, normalized)

Sources must be machine-parseable. Use these formats:

| Type | Format | Example |
|------|--------|---------|
| **Code** | `path:L<start>-L<end>` | `deployment/live_trading/config/settings.py:L291-L317` |
| **Markdown** | `file.md#Heading:L<start>-L<end>` | `docs/decisions/INCIDENTS.md#INC-036:L553-L699` |
| **Commit** | `commit <hash>` | `commit 7874956` |
| **PR** | `PR #<id>` | `PR #123` |
| **Fallback** | `path::function_name` or `path::config_key` | `src/features/feature_engine_EMV3.py::calculate_gap_features` |

**Fallback (function/config anchor) is acceptable only when exact lines are unknown.**

---

## Executable memory requirements

Each entry MUST include at least one of:

- **Rule**: A MUST/NEVER requirement that can be checked
  - Example: `Rule: shift(1) MUST precede any rolling() on price-derived data`

- **Implication**: What breaks if violated
  - Example: `Implication: Backtest returns inflated 100-1000x; model learns to explain past, not predict future`

**If neither can be clearly stated, the memory is not actionable — do not save it.**

---

## Hard vs Soft classification (MANDATORY)

| Classification | Criteria | Injection behavior |
|----------------|----------|-------------------|
| **Hard** | Constraints, frozen decisions, recurrent high-impact lessons | Auto-inject on relevant triggers |
| **Soft** | Context-dependent risks, low-impact facts, exploratory notes | Index only, retrieve on search |

**Guidelines:**
- `constraint` → almost always Hard
- `lesson` with S1 severity → Hard
- `decision` with 🔒 frozen status → Hard
- `fact` → usually Soft (unless it's an invariant)
- `risk` → Hard if production-impacting, Soft otherwise

---

## Optional fields (recommended)

### Severity

| Level | Criteria |
|-------|----------|
| `S1` | Can invalidate backtests/models or cause production incident |
| `S2` | Significant risk or efficiency loss |
| `S3` | Minor guidance or best practice |

### Verify

One-line regression check or observable symptom:
- Example: `Verify: Check IC(pred, Ret_T-5) < IC(pred, Ret_T+1); if not, feature leakage exists`
- Example: `Verify: grep -n "rolling(" src/features/*.py | grep -v "shift(1)"`

### Tags

Optional categorization for search:
- Example: `Tags: leakage, feature-engine, rolling`

---

## Output format (response only)

Return the extracted entries in the response, using the following format:

```
MEMORY ENTRY
Type: <type>
Recommended: <Hard|Soft>
Severity: <S1|S2|S3>
Title: <one sentence>
Content:
- ...
- ...
Rule: <MUST/NEVER statement>
Implication: <what breaks if violated>
Verify: <regression check>
Source:
- <normalized source>
Tags: <optional>

---
```

**Notes:**
- `Rule` and `Implication`: include at least one, preferably both
- `Severity` and `Verify`: optional but recommended for S1/S2 entries
- **Do not write files unless explicitly instructed by the user.**

---

## Guardrails

- Obey `Side Effects & File Writes` rules from `CLAUDE.md`
- Never invent or guess a source
- Never merge multiple concepts into one entry
- If unsure whether something is worth saving, ask
- **If you cannot write a Rule or Implication, do not create the entry**

---

## Example

```
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
Implication: Backtest returns inflated 100-1000x; model predictions structurally encode future information
Verify: grep -rn "rolling\|ewm\|pct_change" src/features/*.py | grep -v "shift(1)"
Source:
- docs/decisions/INCIDENTS.md#INC-036:L553-L699
Tags: leakage, feature-engine, shift, rolling

---
```

---

## Post-Save: Rule Generation (V2)

After writing a **Hard** entry to `events.jsonl`, remind the user:

```
Memory saved. If this is a Hard entry, you can regenerate Claude Code rules:
  python3 .memory/scripts/generate_rules_cli.py

This will update .claude/rules/ef-memory/ with the latest Hard rules,
so Claude Code automatically applies them when editing relevant files.
```

To preview without writing: `python3 .memory/scripts/generate_rules_cli.py --dry-run`
