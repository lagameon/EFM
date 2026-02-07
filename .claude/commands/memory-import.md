# /memory-import — Semi-automatic memory extraction from project documents

## Purpose

Extract memory candidates from **any structured project document** — incidents, decisions, architecture records, runbooks, retrospectives, README sections, code comments, and more.

- **Input**: Any markdown document, code file, or structured text with extractable knowledge
- **Output**: MEMORY ENTRY blocks in `/memory-save` format

---

## Human Review Mode

**Check `.memory/config.json` → `automation.human_review_required`:**

- **`true` (default)**: Display candidates in response only. Do NOT write to `events.jsonl`. User must explicitly review and persist via `/memory-save`.
- **`false`**: After extracting and validating candidates, directly append all valid entries to `events.jsonl` and run the automation pipeline. Still validates schema and requires valid sources — just skips the manual approval step.

**Users can toggle this at any time:**
- To disable review: set `"human_review_required": false` in config, or tell Claude "turn off memory review"
- To re-enable: set `"human_review_required": true` in config, or tell Claude "turn on memory review"

---

## What /memory-import Does

1. User provides (or points to) a document section or file
2. Claude identifies extractable knowledge (rules, lessons, constraints, decisions, risks, facts)
3. Each candidate MUST have Rule or Implication (otherwise rejected)
4. Source is normalized to the document's path + heading/line reference
5. **When `human_review_required: true`**: Display candidates for human review → persist via `/memory-save`
6. **When `human_review_required: false`**: Validate and directly append to `events.jsonl` → run pipeline

## What /memory-import Does NOT Do (regardless of review mode)

- ❌ Maintain approval state or pending lists
- ❌ Guarantee exact line numbers (best-effort only)
- ❌ Deduplicate against existing memory (check `/memory-search` first)
- ❌ Create entries without valid sources or without Rule/Implication

---

## Supported Source Documents

### Any structured document is supported

| Document Type | Examples | Expected Content |
|---------------|----------|-----------------|
| **Incident records** | `INCIDENTS.md`, `postmortems/` | Root cause, fix, lessons learned |
| **Decision records** | `DECISIONS.md`, `ADR/`, `RFC/` | Context, decision, rationale, consequences |
| **Architecture docs** | `ARCHITECTURE.md`, `design/` | Constraints, invariants, design rules |
| **Runbooks / SOPs** | `runbooks/`, `playbooks/` | Procedures, checks, safety rules |
| **Retrospectives** | `retros/`, `RETROSPECTIVE.md` | Action items, learned patterns |
| **READMEs** | `README.md`, `CLAUDE.md` | Project constraints, conventions, rules |
| **Code comments** | `*.py`, `*.ts`, `*.go` | `# LESSON:`, `// CONSTRAINT:`, `TODO(critical)` |
| **Config / standards** | `.eslintrc`, `pyproject.toml` | Enforcement rules, style constraints |
| **Changelogs** | `CHANGELOG.md` | Breaking changes, migration rules |
| **Any markdown** | `docs/**/*.md` | Anything with extractable rules or knowledge |

**The system is document-type agnostic.** If a document contains actionable knowledge with a verifiable source, it can be imported.

---

## Extraction Rules

### MUST Extract

| Content Pattern | Maps To | Required Fields |
|----------------|---------|-----------------|
| **Root Cause** / error analysis | `Content` + `Implication` | What went wrong, why it matters |
| **Fix** / solution / resolution | `Rule` | MUST/NEVER statement derived from fix |
| **Decision + Rationale** | `Rule` + `Content` | What was decided and why |
| **Constraint / Invariant** | `Rule` + `Implication` | What must hold true and what breaks |
| **Regression Check** / verification | `Verify` | One-line command or observable check |
| **Lessons Learned** / takeaways | `Content` | Key actionable points (max 4) |
| **Breaking Change** / migration | `Rule` + `Implication` | What changed and what breaks |

### MUST NOT Extract

| Content Type | Reason |
|--------------|--------|
| Timeline / chronology | No reuse value; context-specific |
| Raw logs / stack traces | Noise; not actionable |
| File listings (unless constraint) | Volatile; likely outdated |
| Estimated time / effort | Not a rule or fact |
| Intermediate discussion | Not a conclusion |
| Agent handoff notes | Session-specific |
| Opinions without evidence | Violates evidence-first principle |

### Extraction Heuristics

```
1. If "Fix", "Solution", "Resolution" section → derive Rule
2. If "Root Cause", "Why", "Analysis" section → derive Content + Implication
3. If "Verification", "Regression", "Test" section → derive Verify
4. If "Decision", "Chosen approach", "We decided" → derive Rule + Implication
5. If "Constraint", "MUST", "NEVER", "Invariant" → derive Rule
6. If "Risk", "Warning", "Caveat" → derive Implication
7. If error caused production impact → Severity = S1, Classification = Hard
8. If architectural constraint → Classification = Hard
9. If best practice / preference → Classification = Soft
```

---

## Usage

```
# Import from any document
/memory-import docs/decisions/INCIDENTS.md#INC-036
/memory-import docs/decisions/DECISIONS.md#DEC-057
/memory-import docs/architecture/ARCHITECTURE.md
/memory-import docs/runbooks/deployment-checklist.md
/memory-import README.md#Error-Handling
/memory-import CLAUDE.md#Protocol-Section
/memory-import src/core/auth.py           # Extract from code comments
```

Or provide the document content directly in the conversation.

---

## Source Normalization

### Two Levels (both valid)

| Level | Format | When to Use |
|-------|--------|-------------|
| **A (Ideal)** | `docs/DECISIONS.md#DEC-057:L12-L45` | When line numbers are provided or verifiable |
| **B (Acceptable)** | `docs/DECISIONS.md#DEC-057` | When exact lines cannot be determined |
| **C (Minimum)** | `docs/DECISIONS.md` | When no heading anchor is available |

### Best-Effort Line Numbers

If exact line numbers cannot be determined:
- Output the stable anchor only (`#DEC-057`, `#Error-Handling`)
- Annotate with `[Line numbers needed]` if precision is important
- Human or future tooling (`/memory-verify`) can add line numbers later

**Do NOT invent line numbers. An anchor without lines is better than wrong lines.**

---

## Output Format

Output follows `/memory-save` MEMORY ENTRY format exactly:

```
/memory-import docs/architecture/ARCHITECTURE.md#Database-Rules

Scanning: Database-Rules section

========================================
IMPORT CANDIDATE #1
========================================

MEMORY ENTRY
Type: constraint
Recommended: Hard
Severity: S2
Title: All database migrations must be backward-compatible for zero-downtime deploys
Content:
- Migrations run while old code is still serving traffic
- Column drops require a 2-release deprecation cycle
- New NOT NULL columns must have defaults
Rule: Database migrations MUST be backward-compatible; NEVER drop columns in the same release they become unused
Implication: Zero-downtime deployment fails; old pods crash on missing columns
Verify: Review migration files for DROP COLUMN without prior deprecation release
Source:
- docs/architecture/ARCHITECTURE.md#Database-Rules
Tags: database, migration, deployment, zero-downtime

---

========================================
IMPORT SUMMARY
========================================
Candidates extracted: 1
  - Hard/S2: 1

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

## Document-Specific Guidance

### Incident Records (INCIDENTS.md, postmortems/)

Focus on: Root cause → Rule, Fix → Verify, Lessons → Content
```
Section "Root Cause" → Content + Implication
Section "Fix" → Rule (derive MUST/NEVER)
Section "Regression" → Verify
```

### Decision Records (DECISIONS.md, ADR/)

Focus on: Decision → Rule, Rationale → Content, Consequences → Implication
```
Section "Decision" → Rule
Section "Context" + "Rationale" → Content
Section "Consequences" → Implication
Status "Accepted" / "Superseded" → Classification guidance
```

### Architecture Docs

Focus on: Constraints → Rule, Invariants → Rule + Implication
```
"MUST" / "NEVER" / "ALWAYS" statements → Rule
Diagrams with labeled constraints → Content
"If violated..." patterns → Implication
```

### Runbooks / SOPs

Focus on: Critical steps → Rule, Failure modes → Implication
```
"Before deploying..." → Rule (MUST check)
"If X happens..." → Risk entry
"Never do Y in production" → Constraint entry
```

### Code Comments

Focus on: `# LESSON:`, `# CONSTRAINT:`, `# WARNING:`, `# INVARIANT:`
```
# LESSON: → lesson entry
# CONSTRAINT: or # INVARIANT: → constraint entry
# WARNING: or # DANGER: → risk entry
# DECISION: or # WHY: → decision entry
```

---

## Human Review Workflow

### Review Checklist

Before persisting any entry, human MUST verify:

```
□ Title accurately summarizes the knowledge (not just the document)
□ Rule is actionable and checkable (MUST/NEVER/ALWAYS)
□ Implication explains real-world consequence
□ Source points to correct document section (verify anchor exists)
□ Content has 2-6 concrete points (no fluff)
□ Severity matches impact (S1 = invalidates results or production incident)
□ No timeline, logs, or speculative content included
□ Entry type matches the knowledge type (decision vs lesson vs constraint)
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
REJECT if: No Rule AND no Implication can be derived
REJECT if: No actionable content exists (purely descriptive)
REJECT if: Source cannot be identified or verified
```

### Warnings

```
WARN if: Content exceeds 6 bullet points (likely too verbose)
WARN if: Source section exceeds 200 lines (may need to narrow scope)
WARN if: No verification method can be suggested
WARN if: Entry duplicates existing memory (suggest checking /memory-search first)
```

---

## Guardrails

### Hard Constraints (always apply, regardless of review mode)

```
- NEVER claim to maintain approval state or pending lists
- NEVER invent line numbers; use anchor-only format if uncertain
- NEVER create entries without valid source and Rule/Implication
- ALWAYS validate schema before persisting
```

### When `human_review_required: true` (default)

```
- NEVER write to events.jsonl or any file
- ALWAYS display "No files have been modified" at end of output
- ALWAYS require human to explicitly request persistence
```

### When `human_review_required: false`

```
- Validate all entries, then directly append to events.jsonl
- Display summary of what was written (entry count, titles)
- Run automation pipeline after writing (sync + rules)
- Still NEVER invent sources or create entries without Rule/Implication
```

---

## Version

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-02-01 | Initial design, INCIDENTS.md support, read-only |
| 1.1 | 2026-02-07 | Universal document support, any markdown/code/config |
| 1.2 | 2026-02-07 | Human review toggle (`human_review_required` config flag) |
