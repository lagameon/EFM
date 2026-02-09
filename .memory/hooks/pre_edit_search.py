#!/usr/bin/env python3
"""
EF Memory — PreToolUse Hook (Edit/Write)

Reads the tool input from stdin, extracts the file path being modified,
and searches memory for relevant entries. Outputs search results as
additionalContext so Claude sees them before editing.

Fast path: skips if file is in .memory/, .claude/, or non-code files.
"""

import json
import sys
from pathlib import Path

# Resolve paths
_SCRIPT_DIR = Path(__file__).resolve().parent
_MEMORY_DIR = _SCRIPT_DIR.parent
_PROJECT_ROOT = _MEMORY_DIR.parent

sys.path.insert(0, str(_MEMORY_DIR))


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    # Extract file path from tool input
    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        sys.exit(0)

    # Fast path: skip non-interesting files
    rel_path = file_path
    try:
        rel_path = str(Path(file_path).relative_to(_PROJECT_ROOT))
    except ValueError:
        pass

    skip_prefixes = (".memory/", ".claude/", "node_modules/", ".git/", "__pycache__/")
    if any(rel_path.startswith(p) for p in skip_prefixes):
        sys.exit(0)

    skip_extensions = (".json", ".lock", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".env")
    if Path(rel_path).suffix.lower() in skip_extensions:
        sys.exit(0)

    # Build search query from file path components
    parts = Path(rel_path).parts
    # Use filename (without extension) + parent dir as search terms
    name = Path(rel_path).stem
    query_parts = [name]
    if len(parts) > 1:
        query_parts.append(parts[-2])  # parent directory

    query = " ".join(query_parts)

    # Load config (with preset resolution)
    config_path = _MEMORY_DIR / "config.json"
    try:
        from lib.config_presets import load_config
        config = load_config(config_path)
    except Exception:
        config = {}

    # Check if hook is disabled via config
    if not config.get("hooks", {}).get("pre_edit_search_enabled", True):
        sys.exit(0)

    # Run memory search
    try:
        from lib.search import search_memory

        events_path = _PROJECT_ROOT / ".memory" / "events.jsonl"

        results = search_memory(query, events_path, config=config, max_results=3)

        if results:
            lines = [f"[EF Memory] Relevant entries for {rel_path}:"]
            for r in results:
                entry = r.get("entry", r) if isinstance(r, dict) else r
                if hasattr(entry, "title"):
                    title = entry.title
                    rule = getattr(entry, "rule", None)
                elif isinstance(entry, dict):
                    title = entry.get("title", "")
                    rule = entry.get("rule")
                else:
                    continue

                line = f"  - {title}"
                if rule:
                    line += f" | Rule: {rule}"
                lines.append(line)

            print("\n".join(lines))
    except Exception:
        # Never block edits due to search failures
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
