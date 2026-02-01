# Sample Decision Log

This is an example decision document to demonstrate structured decision records.

---

## DEC-001: Use PostgreSQL over MongoDB for primary storage

**Date**: 2026-01-10
**Status**: Approved
**Type**: Architecture

### Background

We need a primary database for the new service. Options considered:
- PostgreSQL (relational)
- MongoDB (document)
- DynamoDB (managed NoSQL)

### Decision

**Use PostgreSQL** as the primary database.

### Rationale

1. Strong consistency requirements for financial data
2. Complex queries with JOINs are common
3. Team has existing PostgreSQL expertise
4. ACID compliance is non-negotiable

### Consequences

- Need to design schema upfront
- May need read replicas for scale
- Migration from prototype MongoDB required

### Constraints (derived)

- All financial transactions MUST use PostgreSQL
- MongoDB may be used for caching/analytics only
- Schema changes require migration scripts

---

## DEC-002: Adopt trunk-based development

**Date**: 2026-01-12
**Status**: Approved
**Type**: Process

### Background

Current GitFlow model causes:
- Long-lived branches with merge conflicts
- Delayed integration feedback
- Complex release process

### Decision

**Switch to trunk-based development** with feature flags.

### Rationale

1. Faster feedback loops
2. Smaller, safer changes
3. Continuous deployment capability
4. Reduced merge complexity

### Constraints (derived)

- All commits to main MUST pass CI
- Features behind flags until ready
- No long-lived branches (max 2 days)

---

## DEC-003: Freeze API v1 schema (locked)

**Date**: 2026-01-20
**Status**: Locked
**Type**: Contract

### Background

API v1 has been in production for 6 months with external consumers.

### Decision

**Freeze API v1 schema.** No breaking changes allowed.

### Rationale

1. External consumers depend on current contract
2. Breaking changes require coordinated migration
3. v2 development can proceed independently

### Constraints (derived)

- API v1 endpoints MUST NOT change response schema
- New fields may be added (additive only)
- Deprecation requires 6-month notice
- Breaking changes go to API v2

### This decision is LOCKED and cannot be reversed without executive approval.
