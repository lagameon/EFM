# Sample Incident Log

This is an example incident document to demonstrate `/memory-import`.

---

## INC-001: Missing validation caused data corruption (2026-01-15)

**Status**: Resolved
**Severity**: P0
**Impact**: 3 days of corrupted data in production

### Summary

External API inputs were processed without validation, allowing malformed data to propagate through the pipeline.

### Root Cause

1. Input validation was intentionally skipped "for performance"
2. No schema enforcement at API boundary
3. Downstream systems assumed data was clean

### Fix

```python
# Before (wrong)
def process(data):
    return transform(data)

# After (correct)
def process(data):
    validated = validate_schema(data)  # MUST validate first
    return transform(validated)
```

### Regression Check

- [ ] All API endpoints have validation middleware
- [ ] Schema tests cover edge cases
- [ ] Monitoring alerts on validation failures

### Lessons Learned

- Never skip validation for performance
- Assume all external data is hostile
- Add validation to the CI pipeline

---

## INC-002: Cache key collision caused wrong data returned (2026-01-20)

**Status**: Resolved
**Severity**: P1
**Impact**: Some users saw other users' data for 2 hours

### Summary

Cache keys were generated using only user ID, without considering the query context.

### Root Cause

```python
# Wrong: key doesn't include query params
cache_key = f"user:{user_id}"

# Correct: key includes full context
cache_key = f"user:{user_id}:query:{hash(query_params)}"
```

### Fix

Updated cache key generation to include all relevant context.

### Regression Check

```bash
# Verify cache keys are unique per query
grep -rn "cache_key =" src/ | grep -v "query"
```

### Lessons Learned

- Cache keys must capture full request context
- Add cache key collision tests
- Log cache hits/misses for debugging

---

## INC-003: Timezone bug caused scheduled jobs to run twice (2026-01-25)

**Status**: Resolved
**Severity**: P2
**Impact**: Duplicate processing, wasted compute

### Summary

Scheduler used local time, but workers used UTC. During DST transition, jobs ran twice.

### Root Cause

- Scheduler: `datetime.now()` (local time)
- Worker: `datetime.utcnow()` (UTC)
- No timezone awareness in job records

### Fix

```python
# All timestamps MUST be timezone-aware UTC
from datetime import datetime, timezone

timestamp = datetime.now(timezone.utc)  # Correct
```

### Regression Check

```bash
grep -rn "datetime.now()" src/ | grep -v "timezone"
```

### Lessons Learned

- Always use timezone-aware datetimes
- Store all times in UTC
- Add linter rule for naive datetime usage
