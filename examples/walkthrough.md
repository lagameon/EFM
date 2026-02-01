# EF Memory Walkthrough

This guide walks you through the complete workflow:
**Import → Review → Save → Search**

Time: ~10 minutes

---

## Prerequisites

- Claude Code CLI installed
- This repository cloned or copied to your project

---

## Step 1: Import from INCIDENTS.sample.md

Run the import command:

```
/memory-import examples/INCIDENTS.sample.md
```

Claude will scan the document and output MEMORY ENTRY candidates:

```
========================================
IMPORT CANDIDATE #1 (from INC-001)
========================================

MEMORY ENTRY
Type: lesson
Recommended: Hard
Severity: S1
Title: Missing validation caused data corruption
Content:
- Input validation was skipped for performance reasons
- Malformed data propagated through the pipeline
- Downstream systems produced incorrect results for 3 days
Rule: All external inputs MUST be validated before processing
Implication: Data corruption propagates silently; recovery requires full reprocessing
Verify: Check all API endpoints have input validation middleware
Source:
- examples/INCIDENTS.sample.md#INC-001
Tags: validation, data-quality

---

[... more candidates ...]

========================================
IMPORT SUMMARY
========================================
Candidates extracted: 3
  - Hard/S1: 1
  - Hard/S2: 2

⚠️ REVIEW REQUIRED
No files have been modified.
```

**Key point**: Nothing is written. This is a dry-run.

---

## Step 2: Review the candidates

For each candidate, verify:

- [ ] Title accurately summarizes the lesson
- [ ] Rule is actionable (MUST/NEVER/ALWAYS)
- [ ] Implication explains real consequence
- [ ] Source is correct
- [ ] Content is concrete (no fluff)

Edit if needed by copying and modifying the entry.

---

## Step 3: Save approved entries

For entries you approve, use `/memory-save` to confirm format:

```
/memory-save
```

Then provide the entry content. Claude will display it in the standard format.

**To persist**: Explicitly request the write:

```
Please append this entry to .memory/events.jsonl
```

Claude will write the entry (with your permission).

---

## Step 4: Search your memory

Now that you have entries, search them:

```
/memory-search validation
```

Output:

```
/memory-search validation

Found 1 entry:

[Hard] [S1] lesson
Title: Missing validation caused data corruption
Rule: All external inputs MUST be validated before processing
Implication: Data corruption propagates silently; recovery requires full reprocessing
Source: examples/INCIDENTS.sample.md#INC-001
---

Tips:
- Use `--full` to see Content and Verify fields
```

---

## Step 5: Verify memory integrity (optional)

Periodically check that your memory is still valid:

```
/memory-verify lesson-sample-a1b2c3d4
```

Output:

```
Status: ✅ OK

Checks:
- Source: OK (file exists, anchor valid)
- Rule: OK
- Verify: OK
- Last verified: 2026-01-01 (30 days ago)
```

---

## Complete workflow summary

```
┌─────────────────┐
│ INCIDENTS.md    │  (your project documents)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ /memory-import  │  (extract candidates, dry-run)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Human Review    │  (verify, edit, approve)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ /memory-save    │  (format and persist)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ events.jsonl    │  (append-only storage)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ /memory-search  │  (query when needed)
└─────────────────┘
```

---

## Tips

1. **Start small**: Import 3-5 high-impact incidents first
2. **Review carefully**: Quality > quantity
3. **Use before changes**: Run `/memory-search` before modifying critical code
4. **Verify periodically**: Run `/memory-verify` monthly or before refactors

---

## What's next?

- Import your real `INCIDENTS.md` or `DECISIONS.md`
- Add project-specific constraints
- Integrate into your development workflow
