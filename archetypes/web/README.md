# Web Archetype

For web applications, API services, and backend systems.

## Target Projects

- REST/GraphQL APIs
- Web applications
- Microservices
- Backend services

## Key Risks

| Risk | Description | Rule ID |
|------|-------------|---------|
| **Missing Validation** | Unvalidated user input | web-001 |
| **Auth Gaps** | Unprotected routes | web-002 |
| **Error Exposure** | Leaking internal errors | web-003 |

## Additional Rules

This archetype adds 3 verification rules:

### web-001: Input Validation Check
Looks for validation patterns in API/route handlers.

### web-002: Auth Middleware Check
Verifies authentication middleware is present.

### web-003: Error Handling Check
Flags potential error information leakage.

## Usage

1. Copy `memory.config.patch.json` to your project
2. Merge `paths_override` into `.memory/config.json`
3. Add `archetypes/web/rules/verify-web.rules.json` to `verify.rulesets`

## Recommended Tags

```
validation, auth, injection, xss, csrf, error-handling, middleware, security
```
