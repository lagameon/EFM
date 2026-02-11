# EFM Walkthrough

Complete workflow demonstration:
**Import (dry-run) → Human Review → Save → Generate JSONL → Append → Verify**

---

## Prerequisites

- Claude Code CLI installed
- EFM template copied to your project

---

## Quick Start (Copy & Run)

```bash
# 1. Copy template to your project
cp -r /path/to/EFM/.memory /your/project/
cp -r /path/to/EFM/.claude /your/project/

# 2. Edit config (set your paths)
# Edit .memory/config.json:
#   "CODE_ROOTS": ["src/", "lib/"],
#   "DOCS_ROOT": "docs/"

# 3. Verify setup (in Claude Code)
/memory-verify
```

Expected first-run output (empty memory):
```
MEMORY VERIFICATION REPORT
Storage: .memory/events.jsonl
Entries: 0

No entries to verify.
No files were modified.
```

---

## Archetype Selection (Optional)

If your project matches an archetype, merge its rules:

```bash
# Example: quant archetype
cat archetypes/quant/memory.config.patch.json
```

**Merge instructions** (do NOT replace entire config):
1. Copy `paths_override` values into your `.memory/config.json` `paths` section
2. Append archetype ruleset to `verify.rulesets` array:
   ```json
   "rulesets": [
     ".memory/rules/verify-core.rules.json",
     "archetypes/quant/rules/verify-quant.rules.json"
   ]
   ```

---

## Step 1: Import from INCIDENTS.md (Dry-Run)

Run the import command:

```
/memory-import docs/INCIDENTS.md#INC-001
```

Claude extracts MEMORY ENTRY candidates:

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
Verify: grep -rn 'validate' src/ | head -20 || echo 'WARN: no validation found'
Source:
- docs/INCIDENTS.md#INC-001
Tags: validation, data-quality

---

========================================
IMPORT SUMMARY
========================================
Candidates extracted: 1
  - Hard/S1: 1

⚠️ REVIEW REQUIRED
No files have been modified.
```

**Key point**: `/memory-import` NEVER writes files. This is a dry-run.

---

## Step 2: Human Review

For each candidate, verify:

- [ ] Title accurately summarizes the lesson (not just the incident)
- [ ] Rule is actionable (MUST/NEVER/ALWAYS)
- [ ] Verify field is an executable command (not prose)
- [ ] Source points to correct location
- [ ] Content has 2-6 concrete points

Edit if needed by modifying the entry in your response.

---

## Step 3: Save Entry Format

Run `/memory-save` to confirm the entry format:

```
/memory-save
```

Provide the reviewed entry. Claude displays it in standard format.

**Note**: `/memory-save` also NEVER writes files. It only formats for confirmation.

---

## Step 4: Generate Single-Line JSONL

Use Python to create a valid single-line JSON:

```bash
cat << 'EOF' | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), ensure_ascii=False))'
{
  "id": "lesson-inc001-a1b2c3d4",
  "type": "lesson",
  "classification": "hard",
  "severity": "S1",
  "title": "Missing validation caused data corruption",
  "content": [
    "Input validation was skipped for performance reasons",
    "Malformed data propagated through the pipeline",
    "Downstream systems produced incorrect results for 3 days"
  ],
  "rule": "All external inputs MUST be validated before processing",
  "implication": "Data corruption propagates silently; recovery requires full reprocessing",
  "verify": "grep -rn 'validate' src/ | head -20 || echo 'WARN: no validation found'",
  "source": ["docs/INCIDENTS.md#INC-001"],
  "tags": ["validation", "data-quality"],
  "created_at": "2026-01-15T10:00:00Z",
  "last_verified": "2026-01-15T10:00:00Z",
  "deprecated": false,
  "_meta": {"import_source": "memory-import v1.0"}
}
EOF
```

Output (single line):
```json
{"id":"lesson-inc001-a1b2c3d4","type":"lesson","classification":"hard","severity":"S1","title":"Missing validation caused data corruption","content":["Input validation was skipped for performance reasons","Malformed data propagated through the pipeline","Downstream systems produced incorrect results for 3 days"],"rule":"All external inputs MUST be validated before processing","implication":"Data corruption propagates silently; recovery requires full reprocessing","verify":"grep -rn 'validate' src/ | head -20 || echo 'WARN: no validation found'","source":["docs/INCIDENTS.md#INC-001"],"tags":["validation","data-quality"],"created_at":"2026-01-15T10:00:00Z","last_verified":"2026-01-15T10:00:00Z","deprecated":false,"_meta":{"import_source":"memory-import v1.0"}}
```

---

## Step 5: Append to events.jsonl

Append the single-line JSON:

```bash
echo '{"id":"lesson-inc001-a1b2c3d4",...}' >> .memory/events.jsonl
```

Or use heredoc for safety:

```bash
cat >> .memory/events.jsonl << 'JSONL'
{"id":"lesson-inc001-a1b2c3d4","type":"lesson",...}
JSONL
```

---

## Step 6: Validate JSONL Format

```bash
python3 -c "
import json
with open('.memory/events.jsonl', 'r') as f:
    for i, line in enumerate(f, 1):
        line = line.strip()
        if line:
            try:
                json.loads(line)
                print(f'Line {i}: OK')
            except json.JSONDecodeError as e:
                print(f'Line {i}: FAIL - {e}')
"
```

---

## Step 7: Verify Memory Integrity

Run `/memory-verify` to check all entries:

```
/memory-verify
```

Expected output:

```
========================================
MEMORY VERIFICATION REPORT
========================================
Storage: .memory/events.jsonl
Entries: 1

| ID                       | Schema | Source | Verify | Stale  | Overall |
|--------------------------|--------|--------|--------|--------|---------|
| lesson-inc001-a1b2c3d4   | ✅     | ✅     | ✅     | ✅     | PASS    |

========================================
SUMMARY
========================================
✅ PASS: 1
⚠️ WARN: 0
❌ FAIL: 0

No files were modified. This is a read-only report.
========================================
```

### Understanding Verification Results

| Status | Meaning | Action Required |
|--------|---------|-----------------|
| ✅ PASS | All checks passed | None |
| ⚠️ WARN | Non-critical issue | Review recommended |
| ❌ FAIL | Critical issue | Must fix before trusting |

**Common WARN scenarios:**
- `Stale: 90d ⚠️` — Entry not verified in >90 days
- `Source: ⚠️` — Line numbers may have drifted (anchor still valid)
- `Verify: N/A` — No verify command defined (optional field)

**Common FAIL scenarios:**
- `Schema: ❌` — Missing required field (id, type, source, etc.)
- `Source: ❌` — Source file deleted or anchor not found
- `Verify: ❌` — Verify command uses dangerous patterns (rm, >, etc.)

---

## Complete Workflow Summary

```
┌─────────────────┐
│ INCIDENTS.md    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ /memory-import  │ → Dry-run, extracts candidates
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Human Review    │ → Verify title, rule, verify field
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ /memory-save    │ → Format confirmation (no write)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Python JSON     │ → Generate single-line JSONL
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ echo >> append  │ → Explicit file write
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Validate JSONL  │ → Ensure format is correct
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ /memory-verify  │ → Read-only integrity check
└─────────────────┘
```

---

## Tips

1. **Start small**: Import 3-5 high-impact incidents first
2. **Review carefully**: Quality > quantity
3. **Use before changes**: Run `/memory-search` before modifying critical code
4. **Verify periodically**: Run `/memory-verify` monthly or before refactors
5. **Always single-line**: JSONL requires one JSON object per line
