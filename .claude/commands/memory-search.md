# /memory-search — Retrieve project memory (evidence-first)

## Purpose

Retrieve reusable engineering knowledge from project memory, returning Rules, Lessons, and Constraints relevant to the current task.

- **Problem solved**: Avoid repeating historical mistakes; quickly access project constraints and best practices
- **Relationship with /memory-save**: save is the write entry point, search is the read exit; both share the same Schema
- **Core principle**: Evidence-first (every result must have Source); Context-safe (limits prevent pollution)

**This command does not write files. It only retrieves and displays memory entries.**

---

## Search Modes (V2)

The search engine supports four modes with automatic degradation:

| Mode | Indicator | Requirements | Description |
|------|-----------|-------------|-------------|
| **Hybrid** | `[hybrid]` | Embedder + FTS5 | BM25 + Vector similarity + Re-rank (best quality) |
| **Vector** | `[vector]` | Embedder only | Pure semantic similarity search |
| **Keyword** | `[keyword]` | FTS5 only | BM25 full-text keyword search |
| **Basic** | `[basic]` | None | Token overlap on events.jsonl (zero dependencies) |

**Automatic degradation**: The engine selects the best available mode. When degraded from the ideal mode, a warning indicator (`⚠`) is shown in the output header.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--full` | off | Show Content and Verify fields |
| `--debug` | off | Show BM25/Vector/Boost score breakdown |
| `--mode <mode>` | auto | Force a specific search mode |
| `--max-results <N>` | 5 | Maximum entries to return |

### CLI Usage

```bash
python3 .memory/scripts/search_cli.py "leakage shift"
python3 .memory/scripts/search_cli.py "rolling" --max-results 3
python3 .memory/scripts/search_cli.py "label" --mode keyword
python3 .memory/scripts/search_cli.py "shift" --full --debug
```

---

## Priority and Filtering Logic

### Return Order (strictly enforced)

```
1. Hard Memory + S1  ← Highest priority; return first when relevance ≥ threshold
2. Hard Memory + S2
3. Hard Memory + S3
4. Soft Notes + S1   ← Only supplement when Hard is insufficient
5. Soft Notes + S2/S3 ← Only when explicitly requested or Hard is empty
```

**Important**: If S1 entries have no clear semantic relevance to the query, do not force-return them.

### Filtering Rules

| Condition | Behavior |
|-----------|----------|
| Query matches Hard Memory | Prioritize Hard, max 5 entries |
| Query only matches Soft Notes | Return Soft with `[Soft]` label, max 3 entries |
| No match | Do not force an answer; prompt user to refine query or consider `/memory-save` |
| Too many matches (>10) | Return only S1+S2; prompt user to add filter terms |

### When NOT to return results, but ask instead

- Query too broad (e.g., `search feature`) → Prompt: "Try: `leakage`, `engine`, `gap`, `shift`?"
- Query has no match and appears to be a new concept → Prompt: "No match. Should this be captured with `/memory-save`?"
- Query matches entries marked `Deprecated` → Warn: "This memory may be outdated. Still want to view?"

---

## Output Format

### Single Entry Template

```
[Hard|Soft] [S1|S2|S3] <Type>
Title: <one sentence>
Rule: <MUST/NEVER statement>           # if present
Implication: <what breaks if violated> # if present
Source: <normalized source>
---
```

### Full Output Template

```
/memory-search <query>  [hybrid|vector|keyword|basic]

Found <N> entries (showing top <M>):

[Hard] [S1] lesson
Title: Rolling statistics without shift(1) caused 999x backtest inflation
Rule: shift(1) MUST precede any rolling(), ewm(), pct_change() on price-derived data
Implication: Backtest returns inflated 100-1000x; model predicts past, not future
Source: docs/decisions/INCIDENTS.md#INC-036:L553-L699
---

[Hard] [S1] lesson
Title: ...
...
---

[Soft] [S2] risk
Title: ...
...
---

Tips:
- Use `/memory-search <query> --full` to see Content and Verify fields
```

### Limits

| Parameter | Default | Notes |
|-----------|---------|-------|
| Max entries returned | 5 | Prevents context pollution |
| Max length per entry | 6 lines | Title + Rule + Implication + Source |
| `--full` mode | 10 lines/entry | Includes Content + Verify |

**`--all` mode is explicitly forbidden. Memory search MUST NEVER support full dump of all entries.**

---

## Examples

### Example 1: `/memory-search leakage`

```
/memory-search leakage

Found 4 entries (showing top 4):

[Hard] [S1] lesson
Title: Rolling statistics without shift(1) caused 999x backtest inflation
Rule: shift(1) MUST precede any rolling(), ewm(), pct_change() on price-derived data
Implication: Backtest returns inflated 100-1000x; predictions encode future info
Source: docs/decisions/INCIDENTS.md#INC-036:L553-L699
---

[Hard] [S1] lesson
Title: Walk-Forward labels on full data caused 191x performance inflation
Rule: Labels MUST be generated inside each WF training window, then drop tail MAX_HORIZON rows
Implication: All WF predictions invalid; model trained on future information
Source: docs/decisions/INCIDENTS.md#INC-035:L407-L498
---

[Hard] [S1] lesson
Title: ATR calculation error caused 0% positive rate, 3K model useless
Rule: ATR MUST use True Range formula: max(H-L, |H-prevC|, |L-prevC|).ewm(span=14)
Implication: Label threshold unreachable; model outputs meaningless
Source: docs/decisions/INCIDENTS.md#INC-034:L81-L145
---

[Hard] [S1] constraint
Title: Protocol A: Feature Firewall requires leakage audit before commit
Rule: Any modification to src/features/ MUST pass static_leakage_linter.py with NO CRITICAL/HIGH
Implication: Unaudited feature code may introduce look-ahead bias
Source: CLAUDE.md#Protocol-A:L10-L19
---

Tips:
- All 4 results are Hard + S1 (high-confidence; suitable for downstream injection by orchestration logic)
- Use `--full` to see Verify commands for regression testing
```

### Example 2: `/memory-search label`

```
/memory-search label

Found 3 entries (showing top 3):

[Hard] [S1] lesson
Title: ATR calculation error caused 0% positive rate, 3K model useless
Rule: ATR MUST use True Range formula; check positive rate in [0.2, 0.5]
Implication: Label threshold unreachable; all predictions invalid
Source: docs/decisions/INCIDENTS.md#INC-034:L81-L145
---

[Hard] [S1] lesson
Title: Walk-Forward labels on full data caused 191x performance inflation
Rule: Labels MUST be generated inside each WF window, drop tail MAX_HORIZON rows
Implication: Training leaks prediction period prices
Source: docs/decisions/INCIDENTS.md#INC-035:L407-L498
---

[Soft] [S3] fact
Title: 3K label uses dual-condition (return + drawdown), not just ATR breakout
Rule: —
Implication: Stricter than CLAUDE.md description; actual impl in risk_adjusted_labels.py
Source: src/labels/risk_adjusted_labels.py:L93-L144
---

Tips:
- 2 Hard + 1 Soft returned
- The Soft entry provides context but is not eligible for auto-injection
```

---

## Guardrails

### When NOT to force an answer

| Situation | Behavior |
|-----------|----------|
| Query has no match | Reply: "No matching memories found. Consider `/memory-save` if this is a new lesson." |
| Query too broad | Reply: "Query too broad. Try: `leakage`, `shift`, `percentile`, `live-backtest`?" |
| All matches are Soft + S3 | Reply: "Only low-confidence notes found. Proceed with caution." |

### When to suggest /memory-save

- User describes a newly discovered issue, but search returns no match
- User asks "has this error happened before?" and the answer is "no record"
- User just completed an incident fix but hasn't run `/memory-save`

**Suggested phrasing**:
```
No existing memory matches this issue.
If this is a new lesson worth remembering, consider running `/memory-save` to capture it.
```

### Avoiding stale or context-specific memory injection

| Risk | Mitigation |
|------|------------|
| Stale memory | If `last_verified` > 90 days, annotate with `[Stale?]` |
| Context-specific | Soft Notes are never auto-injected; display only in search results |
| Version mismatch | If Source file has been deleted/refactored, annotate `[Source may be outdated]` |
| Misleading memory | Only entries with both Rule + Source can be classified as Hard |

### Hard Constraints

```
- NEVER dump entire memory store
- NEVER return more than 5 entries without user confirmation
- NEVER auto-inject Soft Notes into current task context
- ALWAYS show Source for every returned entry
- ALWAYS prefer Rule over Content when space is limited
- Search retrieves and displays; it does NOT make injection decisions
```

---

## Startup Health Check (V2)

Run at session start for a quick health summary:

```bash
python3 .memory/scripts/pipeline_cli.py --startup
```

This shows:
- Pending drafts awaiting approval
- Stale entries (not verified within 90 days)
- Source warnings (referenced files no longer exist)

Example output:
```
EF Memory Startup Check
  Total entries:    15
  Pending drafts:   3
  Stale entries:    2
  Source warnings:  1

  发现 3 条待审记忆 / 1 条 source 告警 / 2 条过期 (>90天)
```

---

## Draft Management (V2)

Review and manage captured memory drafts:

```bash
python3 .memory/scripts/capture_cli.py list      # List pending drafts
python3 .memory/scripts/capture_cli.py review     # Review with verification
python3 .memory/scripts/capture_cli.py approve <filename>  # Approve to events.jsonl
python3 .memory/scripts/capture_cli.py reject <filename>   # Delete draft
```

Verify entries against schema and sources:

```bash
python3 .memory/scripts/verify_cli.py             # Verify all entries
python3 .memory/scripts/verify_cli.py --drafts     # Verify drafts
python3 .memory/scripts/verify_cli.py --schema-only # Schema checks only
```

---

## Future Extensions

The following features are intentionally deferred:

- `--type=<constraint|lesson|...>` filtering
- `--severity=<S1|S2|S3>` filtering
- `--since=<date>` time-based filtering
- Hard logic for `last_verified` expiration
- Context-aware auto-search (triggered by file path matching)

These will be added as memory volume grows or M5+ milestones are implemented.
