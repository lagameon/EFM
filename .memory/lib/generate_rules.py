"""
EF Memory V2 — Rule File Generator (Layer 1 Auto-Injection)

Converts Hard memory entries from events.jsonl into Claude Code-compatible
rule files in .claude/rules/ef-memory/*.md.

These generated rules are automatically loaded by Claude Code when
editing files in the project, enabling zero-effort knowledge injection.

Algorithm:
1. Read events.jsonl, resolve latest-wins
2. Filter: classification == "hard" AND deprecated == false
3. Group by domain (extracted from source[] paths and tags[])
4. For each domain, generate a .claude/rules/ef-memory/<domain>.md
5. Write an _index.md summarizing all generated rules

No external dependencies — pure Python stdlib.
"""

import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("efm.generate_rules")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class GenerateReport:
    """Summary of a rule generation operation."""
    entries_scanned: int = 0
    entries_hard: int = 0          # Total hard entries found
    entries_injected: int = 0      # Entries written to rule files
    files_written: List[str] = field(default_factory=list)
    files_removed: List[str] = field(default_factory=list)
    domains: Dict[str, int] = field(default_factory=dict)
    dry_run: bool = False
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Domain extraction
# ---------------------------------------------------------------------------

# Default domain mapping: source path prefix → domain name
DEFAULT_DOMAIN_MAP = {
    "src/features": "feature-engine",
    "src/labels": "labels",
    "src/models": "models",
    "src/data": "data-pipeline",
    "src/live": "live-trading",
    "deployment": "deployment",
    "docs/decisions": "incidents",
    "CLAUDE.md": "protocols",
}


def extract_domain(entry: dict, domain_map: Optional[dict] = None) -> str:
    """
    Extract a domain name from an entry's source[] and tags[].

    Priority:
    1. Match source path against domain_map
    2. Use first meaningful tag as domain
    3. Fall back to entry type
    4. Ultimate fallback: "general"
    """
    if domain_map is None:
        domain_map = DEFAULT_DOMAIN_MAP

    # Try source paths first
    sources = entry.get("source", [])
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, str):
                continue
            # Strip line number references
            path_part = re.split(r"[:#]", source)[0]
            for prefix, domain in domain_map.items():
                if path_part.startswith(prefix):
                    return domain

    # Try tags
    tags = entry.get("tags", [])
    if isinstance(tags, list) and tags:
        # Use first tag that looks like a domain (not too generic)
        generic_tags = {"leakage", "bug", "fix", "test", "debug", "error"}
        for tag in tags:
            if tag and tag.lower() not in generic_tags:
                return _sanitize_domain(tag.lower().replace(" ", "-"))

    # Fall back to entry type
    entry_type = entry.get("type", "")
    if entry_type:
        return _sanitize_domain(entry_type)

    return "general"


def _sanitize_domain(name: str) -> str:
    """
    Sanitize a domain name for safe use as a filename.

    Removes path separators and '..' to prevent path traversal.
    Strips non-alphanumeric chars (except hyphens), collapses runs.
    """
    # Remove path separators and parent-dir references
    name = name.replace("..", "").replace("/", "-").replace("\\", "-")
    # Keep only alphanumeric and hyphens
    name = re.sub(r"[^a-z0-9-]+", "-", name.lower())
    # Collapse multiple hyphens and strip edges
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "general"


# ---------------------------------------------------------------------------
# Entry loading and filtering
# ---------------------------------------------------------------------------

def _load_hard_entries(events_path: Path) -> tuple[List[dict], int]:
    """
    Load Hard, non-deprecated entries from events.jsonl.

    Returns (hard_entries, total_scanned) where:
    - hard_entries: sorted by severity (S1 first, then S2, S3, None)
    - total_scanned: count of all unique entries resolved (latest-wins)

    Uses latest-wins semantics for duplicate entry IDs.
    """
    entries_by_id: Dict[str, dict] = {}

    if not events_path.exists():
        return [], 0

    with open(events_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entry_id = entry.get("id")
                if entry_id:
                    entries_by_id[entry_id] = entry
            except json.JSONDecodeError:
                continue

    total_scanned = len(entries_by_id)

    # Filter: hard + not deprecated
    hard_entries = [
        e for e in entries_by_id.values()
        if e.get("classification") == "hard" and not e.get("deprecated", False)
    ]

    # Sort by severity: S1 → S2 → S3 → None
    severity_order = {"S1": 0, "S2": 1, "S3": 2}
    hard_entries.sort(
        key=lambda e: severity_order.get(e.get("severity", ""), 99)
    )

    return hard_entries, total_scanned


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def _generate_domain_markdown(domain: str, entries: List[dict]) -> str:
    """Generate a single domain rule file as Markdown."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry_ids = [e.get("id", "unknown") for e in entries]

    lines = []
    # Header
    domain_title = domain.replace("-", " ").title()
    lines.append(f"# {domain_title} Rules (Auto-generated from Memory)")
    lines.append(f"<!-- EF Memory Auto-Inject | DO NOT EDIT MANUALLY -->")
    lines.append(f"<!-- Generated: {now} | Entries: {len(entries)} -->")
    lines.append(f"<!-- IDs: {', '.join(entry_ids)} -->")
    lines.append("")

    # Each entry as a rule section
    for entry in entries:
        severity = entry.get("severity", "")
        title = entry.get("title", "(no title)")
        entry_id = entry.get("id", "unknown")
        rule = entry.get("rule")
        implication = entry.get("implication")
        sources = entry.get("source", [])
        verify = entry.get("verify")

        # Section header with severity
        severity_tag = f"[{severity}] " if severity else ""
        lines.append(f"## {severity_tag}{title}")
        lines.append(f"**Memory:** `{entry_id}`")

        # Source
        if isinstance(sources, list) and sources:
            for src in sources:
                lines.append(f"**Source:** `{src}`")

        # Implication
        if implication:
            lines.append(f"**Implication:** {implication}")

        lines.append("")

        # Rule as the main actionable content
        if rule:
            lines.append(f"**Rule:** {rule}")
            lines.append("")

        # Verify command (if present)
        if verify:
            lines.append(f"**Verify:** `{verify}`")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _generate_index_markdown(
    domains: Dict[str, List[dict]],
    output_dir: Path,
) -> str:
    """Generate an index file summarizing all injected rules."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total_entries = sum(len(entries) for entries in domains.values())

    lines = []
    lines.append("# EF Memory — Auto-Injected Rules Index")
    lines.append(f"<!-- Generated: {now} | Total entries: {total_entries} -->")
    lines.append("")
    lines.append("These rules are auto-generated from Hard memory entries in `.memory/events.jsonl`.")
    lines.append("**DO NOT EDIT MANUALLY** — changes will be overwritten on next generation.")
    lines.append("")
    lines.append("To regenerate: `python3 .memory/scripts/generate_rules_cli.py`")
    lines.append("")

    # Domain summary table
    lines.append("| Domain | File | Entries | Severities |")
    lines.append("|--------|------|---------|------------|")

    for domain in sorted(domains.keys()):
        entries = domains[domain]
        filename = f"{domain}.md"
        severity_counts = {}
        for e in entries:
            s = e.get("severity", "?")
            severity_counts[s] = severity_counts.get(s, 0) + 1
        sev_str = ", ".join(f"{k}:{v}" for k, v in sorted(severity_counts.items()))
        lines.append(f"| {domain} | `{filename}` | {len(entries)} | {sev_str} |")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------

def generate_rule_files(
    events_path: Path,
    output_dir: Path,
    config: Optional[dict] = None,
    dry_run: bool = False,
    clean_first: bool = True,
) -> GenerateReport:
    """
    Generate .claude/rules/ef-memory/*.md from Hard memory entries.

    Args:
        events_path: Path to events.jsonl.
        output_dir: Directory to write rule files (e.g., .claude/rules/ef-memory/).
        config: Optional config dict (for domain_map override).
        dry_run: If True, compute but don't write files.
        clean_first: If True, remove existing generated files before writing.

    Returns:
        GenerateReport with operation summary.
    """
    start_time = time.monotonic()
    report = GenerateReport(dry_run=dry_run)

    # Load and filter entries
    hard_entries, total_scanned = _load_hard_entries(events_path)
    report.entries_scanned = total_scanned
    report.entries_hard = len(hard_entries)

    if not hard_entries:
        report.duration_ms = (time.monotonic() - start_time) * 1000
        return report

    # Extract domain mapping from config if available
    domain_map = None
    if config:
        paths_config = config.get("paths", {})
        if paths_config:
            # Build domain map from config paths
            custom_map = {}
            for key, paths in paths_config.items():
                if isinstance(paths, list):
                    for p in paths:
                        domain_name = key.lower().replace("_roots", "").replace("_root", "")
                        custom_map[p.rstrip("/")] = domain_name
                elif isinstance(paths, str):
                    domain_name = key.lower().replace("_roots", "").replace("_root", "")
                    custom_map[paths.rstrip("/")] = domain_name
            if custom_map:
                domain_map = {**DEFAULT_DOMAIN_MAP, **custom_map}

    # Group entries by domain
    domains: Dict[str, List[dict]] = {}
    for entry in hard_entries:
        domain = extract_domain(entry, domain_map)
        if domain not in domains:
            domains[domain] = []
        domains[domain].append(entry)
        report.entries_injected += 1

    report.domains = {d: len(entries) for d, entries in domains.items()}

    if dry_run:
        # Report what would be generated without writing
        for domain in domains:
            report.files_written.append(str(output_dir / f"{domain}.md"))
        report.files_written.append(str(output_dir / "_index.md"))
        report.duration_ms = (time.monotonic() - start_time) * 1000
        return report

    # Clean existing files if requested
    if clean_first and output_dir.exists():
        for existing in output_dir.glob("*.md"):
            report.files_removed.append(str(existing))
            existing.unlink()

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write domain files
    for domain, entries in domains.items():
        content = _generate_domain_markdown(domain, entries)
        filepath = output_dir / f"{domain}.md"
        filepath.write_text(content, encoding="utf-8")
        report.files_written.append(str(filepath))

    # Write index file
    index_content = _generate_index_markdown(domains, output_dir)
    index_path = output_dir / "_index.md"
    index_path.write_text(index_content, encoding="utf-8")
    report.files_written.append(str(index_path))

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report


def clean_rule_files(output_dir: Path) -> List[str]:
    """Remove all generated rule files from output directory."""
    removed = []
    if output_dir.exists():
        for filepath in output_dir.glob("*.md"):
            removed.append(str(filepath))
            filepath.unlink()
        # Remove directory if empty
        try:
            output_dir.rmdir()
        except OSError:
            pass  # Not empty (has non-.md files)
    return removed
