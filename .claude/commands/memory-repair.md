# /memory-repair — Repair events.jsonl After Git Merge

## Purpose

Repair `events.jsonl` after git merge conflicts: strip conflict markers, deduplicate entries, sort, and detect orphan sources.

**Use this after merging branches that both modified memory.**

---

## When to use

- After `git merge` produces conflict markers in `events.jsonl`
- After startup shows "merge conflict markers detected"
- After merging a branch where both sides ran `/memory-save` or `/memory-compact`
- When you suspect duplicate or out-of-order entries

---

## Input

| Format | Description |
|--------|-------------|
| `/memory-repair` | Run repair (default) |
| `/memory-repair --dry-run` | Preview issues without modifying files |

---

## Workflow

### Step 1: Dry-run first

Run: `python3 .memory/scripts/repair_cli.py --dry-run`

Report to the user:
- Merge markers found
- Duplicate IDs to resolve
- Entries before → after
- Orphan sources (missing files)

### Step 2: Run repair (if needed)

If dry-run shows issues, ask user to confirm, then run:

`python3 .memory/scripts/repair_cli.py`

### Step 3: Report results

Show:
- Markers removed
- Duplicates resolved (which entry version was kept: newest `created_at`)
- Entries before → after
- Backup location (`.jsonl.bak`)
- Orphan sources (suggest `/memory-verify` for full check)

---

## What it does

1. Reads all lines from `events.jsonl`
2. Removes git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
3. Deduplicates by entry ID (newest `created_at` wins; file position tiebreaks)
4. Sorts all entries by `created_at`
5. Checks source references for missing files
6. Creates backup (`.memory/events.jsonl.bak`) before writing
7. Atomically rewrites `events.jsonl`
8. Resets vectordb sync cursor (forces re-index)

## Safety

- **Backup first**: Creates `.jsonl.bak` before any modification
- **Atomic write**: Uses `os.replace()` — crash-safe
- **Dry-run mode**: Always preview before modifying
- **No data loss**: All valid JSON entries are preserved; only markers and older duplicates removed

## Prevention

To avoid merge conflicts in the future, add to `.gitattributes`:

```
.memory/events.jsonl merge=union
```

This tells git to keep both sides' lines on merge (no conflict markers). Then run `/memory-repair` to deduplicate.
