# Contributing to EF Memory for Claude

Thank you for your interest in contributing!

## Non-negotiable principles

Before contributing, you must understand and respect these principles:

1. **Memory is project-level, not session-level**
2. **No memory without evidence**
3. **No persistence without human intent**
4. **No silent enforcement**
5. **Append-only > mutable truth**

**Any PR that violates these principles will be rejected.**

---

## What we accept

### Bug fixes

- Typos in documentation
- Broken links
- Schema validation issues

### Improvements

- Clearer documentation
- Better examples
- Additional sample documents

### New features (with discussion)

- New verification checks for `/memory-verify`
- Support for additional document formats
- Tooling improvements

---

## What we do NOT accept

- Automatic file writes without human approval
- Silent prompt injection
- "AI decides what's important" features
- Embedding-only storage (must have evidence)
- Breaking changes to the schema without migration path

---

## How to contribute

### 1. Open an issue first

For anything beyond typo fixes, please open an issue to discuss:

- What problem you're solving
- Your proposed approach
- How it aligns with the principles

### 2. Fork and branch

```bash
git fork https://github.com/anthropics/ef-memory-for-claude
git checkout -b feature/your-feature-name
```

### 3. Make your changes

- Follow existing code style
- Update documentation if needed
- Add examples if applicable

### 4. Test locally

- Verify commands work with Claude Code CLI
- Check that examples are valid
- Validate JSON schema if touching storage

### 5. Submit PR

- Reference the issue
- Explain what changed and why
- Confirm alignment with principles

---

## Code of conduct

Be respectful. Be constructive. Focus on the work.

---

## Questions?

Open an issue with the `question` label.
