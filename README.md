# EF Memory for Claude

> Evidence-first project memory for Claude Code CLI

This repository provides a **safe, auditable memory system** for Claude Code that turns project incidents, constraints, and hard-earned lessons into reusable engineering knowledge.

**This is not chat history. This is project memory.**

---

## Why this exists

Most teams lose critical knowledge over time:

- Incidents are repeated
- Constraints are forgotten
- AI assistants confidently reintroduce old mistakes

This system solves that by enforcing:

- **Evidence-first memory** (every entry has a source)
- **Executable rules** (what MUST / MUST NOT be done)
- **Human-in-the-loop control**
- **Zero silent side effects**

---

## Core principles

```
❌ No automatic file writes
❌ No silent prompt injection
❌ No hallucinated "knowledge"

✅ Explicit rules and implications
✅ Append-only, auditable storage
```

**If something is written, a human explicitly approved it.**

---

## What's included

### Commands (Claude CLI)

| Command | Purpose |
|---------|---------|
| `/memory-save` | Create memory entries (manual, evidence-first) |
| `/memory-search` | Query existing memory safely |
| `/memory-import` | Extract memory candidates from documents (dry-run) |
| `/memory-verify` | Verify memory integrity (read-only) |

### Storage

All persistent memory lives in:

```
.memory/events.jsonl
```

- Append-only
- Git-tracked
- No silent mutation
- Schema enforced by `.memory/SCHEMA.md`

### Hard vs Soft Memory

| Type | Meaning |
|------|---------|
| **Hard** | Violations invalidate results or cause incidents |
| **Soft** | Contextual knowledge, not auto-enforced |

Severity (S1–S3) describes impact, not enforcement.

---

## Quick start (5 minutes)

### 1. Copy into your project

```bash
git clone https://github.com/anthropics/ef-memory-for-claude
cp -r ef-memory-for-claude/.claude your-project/
cp -r ef-memory-for-claude/.memory your-project/
```

Or use this repo as a **GitHub Template**.

### 2. Import historical incidents (dry-run)

```
/memory-import docs/decisions/INCIDENTS.md
```

- Review extracted MEMORY ENTRY blocks
- Nothing is written

### 3. Save approved memory

Copy reviewed entries and run:

```
/memory-save
```

Then explicitly append them to `.memory/events.jsonl`.

### 4. Query memory before changes

```
/memory-search leakage
```

Claude will return high-confidence rules and lessons, with sources.

---

## Directory structure

```
.claude/
└── commands/
    ├── memory-save.md     # Write path (output only, no file writes)
    ├── memory-search.md   # Read path (safe, bounded)
    ├── memory-import.md   # Extract path (dry-run)
    └── memory-verify.md   # Integrity check (read-only)

.memory/
├── SCHEMA.md              # Storage contract (v1.0)
└── events.jsonl           # Append-only memory store

examples/
├── INCIDENTS.sample.md    # Sample incident document
├── DECISIONS.sample.md    # Sample decision document
└── walkthrough.md         # Step-by-step tutorial
```

---

## What this system is NOT

- ❌ A note-taking tool
- ❌ A vector-only embedding store
- ❌ An autonomous AI memory

**It is a project governance layer for AI-assisted development.**

---

## Non-negotiable principles

These are the soul of this system:

1. **Memory is project-level, not session-level**
2. **No memory without evidence**
3. **No persistence without human intent**
4. **No silent enforcement**
5. **Append-only > mutable truth**

---

## Who should use this

- Teams using Claude Code seriously
- Long-lived systems with real incidents
- Projects where mistakes are expensive
- Anyone who wants AI help without losing control

---

## License

MIT

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

**Key rule**: Any change that violates the non-negotiable principles will be rejected.
