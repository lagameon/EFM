# .memory/drafts/

Auto-capture candidate queue for EF Memory V2.

## Convention

- Files: `{timestamp}_{short_title}.json` (e.g., `20260206_143000_cache_collision.json`)
- Each file contains a single JSON object matching SCHEMA.md format
- Additional field: `_meta.draft_status: "pending"`
- Drafts are **never** auto-promoted to `events.jsonl`
- Human must review and explicitly approve via `/memory-save`

## Gitignore

Draft JSON files are gitignored by default (local workspace only).
This README is tracked to preserve the directory structure.
