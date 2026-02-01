# .memory/SCHEMA.md — Memory Events Schema v1.0

---

## Overview

This document defines the schema for `.memory/events.jsonl`, the persistent storage format for project memory entries created by `/memory-save` and queried by `/memory-search`.

**Design principles**:
- Evidence-first: `source` is mandatory and machine-parseable
- Executable: at least one of `rule` or `implication` must be present
- Minimal: only fields with clear semantics; no speculative extensions
- Forward-compatible: unknown fields are preserved, not rejected

---

## Field Definitions

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | string | **YES** | — | Unique identifier (see ID generation rules) |
| `type` | enum | **YES** | — | `decision` \| `lesson` \| `constraint` \| `risk` \| `fact` |
| `classification` | enum | **YES** | — | `hard` \| `soft` |
| `severity` | enum | NO | `null` | `S1` \| `S2` \| `S3` \| `null` |
| `title` | string | **YES** | — | One-sentence summary (max 120 chars) |
| `content` | string[] | **YES** | — | 2-6 bullet points, concrete and unambiguous |
| `rule` | string \| null | NO* | `null` | MUST/NEVER statement (actionable check) |
| `implication` | string \| null | NO* | `null` | What breaks if violated |
| `verify` | string \| null | NO | `null` | One-line regression check or command |
| `source` | string[] | **YES** | — | Normalized source references (see format below) |
| `tags` | string[] | NO | `[]` | Categorization keywords for search |
| `created_at` | string | **YES** | — | ISO 8601 timestamp (e.g., `2026-02-01T14:30:00Z`) |
| `last_verified` | string \| null | NO | `null` | ISO 8601 timestamp of last verification |
| `deprecated` | boolean | NO | `false` | If `true`, entry is hidden from default search |
| `_meta` | object | NO | `{}` | Reserved for future extensions (see compatibility) |

**\* Constraint**: At least one of `rule` or `implication` MUST be non-null. Entries without either are invalid.

### Field Naming Note

`classification` corresponds to the `/memory-save` output field `Recommended: Hard | Soft`. The schema uses `classification` internally for clarity (hard/soft is a classification, not a recommendation), while the user-facing command uses "Recommended" to indicate the suggested injection behavior.

---

## Source Format (Normalized)

Sources must follow these patterns for machine parsing:

| Type | Pattern | Example |
|------|---------|---------|
| Code | `<path>:L<start>-L<end>` | `src/features/feature_engine_EMV3.py:L553-L699` |
| Markdown | `<path>#<heading>:L<start>-L<end>` | `docs/decisions/INCIDENTS.md#INC-036:L553-L699` |
| Commit | `commit <hash>` | `commit 7874956` |
| PR | `PR #<id>` | `PR #123` |
| Function | `<path>::<function>` | `src/labels/risk_adjusted_labels.py::create_return_drawdown_label` |

---

## ID Generation Rules

**Format**: `<type>-<source_anchor>-<hash8>`

**Components**:
1. `type`: entry type (e.g., `lesson`, `constraint`)
2. `source_anchor`: primary source identifier, normalized
   - For INC: `inc034`, `inc036`
   - For DEC: `dec057`
   - For code: `feature_engine_emv3`
   - For CLAUDE.md: `claudemd_protocola`
3. `hash8`: first 8 chars of SHA-256 of `title + source[0]`

**Examples**:
```
lesson-inc036-a3f8c2d1
lesson-inc035-7b2e4f9a
constraint-claudemd_protocola-e1d4b8c7
fact-risk_adjusted_labels-9c3a1e5f
```

**Properties**:
- **Stable**: Same entry always generates same ID
- **Dedup-safe**: Identical title+source produces same hash
- **Human-readable prefix**: Type and source visible without parsing

---

## Example Entries

### Example 1: Hard / S1 (Lesson)

```json
{"id":"lesson-inc036-a3f8c2d1","type":"lesson","classification":"hard","severity":"S1","title":"Rolling statistics without shift(1) caused 999x backtest inflation","content":["42 rolling/ewm/pct_change calls missing shift(1) in feature engine","Model learned to explain past, not predict future","IC with T-5 returns (-0.115) > IC with T+1 returns (0.018)","Backtest showed 49,979% return; after fix only 52%"],"rule":"shift(1) MUST precede any rolling(), ewm(), pct_change() on price-derived data","implication":"Backtest returns inflated 100-1000x; predictions structurally encode future information","verify":"grep -rn 'rolling\\|ewm\\|pct_change' src/features/*.py | grep -v 'shift(1)'","source":["docs/decisions/INCIDENTS.md#INC-036:L553-L699"],"tags":["leakage","feature-engine","shift","rolling"],"created_at":"2026-02-01T14:30:00Z","last_verified":null,"deprecated":false,"_meta":{}}
```

### Example 2: Soft / S3 (Fact)

```json
{"id":"fact-risk_adjusted_labels-9c3a1e5f","type":"fact","classification":"soft","severity":"S3","title":"3K label uses dual-condition (return + drawdown), not just ATR breakout","content":["CLAUDE.md describes 3K as: close[t+3]/close[t] - 1 > ATR_14/close[t]","Actual implementation uses create_return_drawdown_label(horizon=3)","Dual conditions: future_return > 0.1% AND max_drawdown < 0.5%"],"rule":null,"implication":"Stricter than documented; may affect threshold tuning expectations","verify":null,"source":["src/labels/risk_adjusted_labels.py:L93-L144"],"tags":["label","3k","documentation"],"created_at":"2026-02-01T15:00:00Z","last_verified":null,"deprecated":false,"_meta":{}}
```

---

## Validation Rules

### Required Field Checks

```
1. id: non-empty string, matches pattern ^[a-z]+-[a-z0-9_]+-[a-f0-9]{8}$
2. type: one of [decision, lesson, constraint, risk, fact]
3. classification: one of [hard, soft]
4. title: non-empty string, max 120 chars
5. content: array with 2-6 non-empty strings
6. source: non-empty array, each element matches normalized format
7. created_at: valid ISO 8601 timestamp
```

### Executable Memory Check

```
INVALID if: rule == null AND implication == null
```

### Severity Consistency

```
WARN if: classification == "hard" AND severity == null
WARN if: classification == "hard" AND severity == "S3"
```

---

## Deprecation and Supersession

### Relationship between `deprecated` and `superseded_by`

If an entry is superseded by a newer one:
- `deprecated` **MUST** be set to `true`
- `_meta.superseded_by` **SHOULD** reference the new entry's `id`

This enables:
- `/memory-search` to indicate "replaced by newer entry"
- Validation to detect dangling `superseded_by` references
- Archive tools to preserve supersession chains

**Example**:
```json
{
  "id": "lesson-inc034-old12345",
  "deprecated": true,
  "_meta": {
    "superseded_by": "lesson-inc034-a3f8c2d1"
  }
}
```

### Deprecation without Supersession

Entries may be deprecated without a replacement:
- Outdated context (project pivot, deprecated feature)
- Proven incorrect (new evidence contradicts)

In these cases, `deprecated: true` alone is sufficient.

---

## Compatibility Strategy

### Forward Compatibility (reading newer files)

- **Unknown fields**: Preserve in memory, pass through on write
- **Unknown enum values**: Treat as `null` with warning, do not reject
- **Missing optional fields**: Use default values

### Backward Compatibility (writing for older readers)

- **New fields added to `_meta`**: Old readers ignore `_meta` contents
- **Schema version**: Not required for v1.x; add if breaking changes occur
- **Deprecation path**: Set `deprecated: true` before removal; never hard-delete

### Extension Guidelines

| Change Type | Allowed in v1.x | Notes |
|-------------|-----------------|-------|
| Add optional field | ✅ Yes | Must have sensible default |
| Add enum value | ✅ Yes | Old readers treat as unknown |
| Add required field | ❌ No | Requires v2.0 |
| Remove field | ❌ No | Use `deprecated` flag instead |
| Change field type | ❌ No | Requires v2.0 |

### Reserved `_meta` Keys

```json
{
  "_meta": {
    "schema_version": "1.0",      // Reserved for future
    "import_source": "...",       // If auto-imported (e.g., from INCIDENTS.md)
    "embedding_id": "...",        // If vector-indexed
    "superseded_by": "...",       // If replaced by newer entry (see above)
    "confidence": 0.95            // If ML-generated
  }
}
```

---

## File Organization

```
.memory/
├── SCHEMA.md           # This document
├── events.jsonl        # All memory entries (append-only)
├── index.json          # Optional: tag index for fast search
└── archive/            # Optional: deprecated entries backup
    └── events_2026Q1.jsonl
```

**Write behavior**:
- New entries: append to `events.jsonl`
- Updates: append new version with same `id` (latest wins)
- Deletions: set `deprecated: true`, do not remove line

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-01 | Initial schema |
