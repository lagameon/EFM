"""
EF Memory V2 — Text Builder

Converts memory entries (dict from events.jsonl) into optimized text
for embedding, search queries, and deduplication.

No external dependencies — pure Python stdlib.
"""

from typing import Optional


def build_embedding_text(entry: dict) -> str:
    """
    Construct embedding text from a memory entry for indexing.

    Weighting strategy (order matters for embedding models):
    1. title (repeated 2x for emphasis — higher semantic weight)
    2. type + classification + severity (classification signal)
    3. rule (most actionable content)
    4. implication (consequence signal)
    5. content bullets (detail)
    6. tags (keyword coverage)

    Returns a single string ready for embedding.
    """
    parts: list[str] = []

    title = entry.get("title", "")

    # Title repeated for emphasis
    parts.append(title)
    parts.append(title)

    # Classification metadata as text tokens
    entry_type = entry.get("type", "")
    classification = entry.get("classification", "")
    severity = entry.get("severity", "")
    if entry_type or classification or severity:
        meta_parts = [p for p in [entry_type, classification, severity] if p]
        parts.append(" ".join(meta_parts))

    # Rule — the most actionable field
    rule = entry.get("rule")
    if rule:
        parts.append(f"Rule: {rule}")

    # Implication — consequence of violation
    implication = entry.get("implication")
    if implication:
        parts.append(f"Impact: {implication}")

    # Content bullets
    content = entry.get("content", [])
    if isinstance(content, list):
        for item in content:
            if item:
                parts.append(f"- {item}")

    # Tags for keyword coverage
    tags = entry.get("tags", [])
    if isinstance(tags, list) and tags:
        parts.append(f"Tags: {', '.join(tags)}")

    return "\n".join(parts)


def build_query_text(query: str, context: Optional[dict] = None) -> str:
    """
    Construct query text for embedding-based search.

    Optionally enriched with context (e.g., the file being edited)
    to improve retrieval relevance.

    Args:
        query: The user's search query string.
        context: Optional dict with keys:
            - current_file: str — path of file being edited
            - tags: list[str] — relevant tags from current context
    """
    parts: list[str] = [query]

    if context:
        current_file = context.get("current_file")
        if current_file:
            parts.append(f"Context: editing {current_file}")

        ctx_tags = context.get("tags")
        if isinstance(ctx_tags, list) and ctx_tags:
            parts.append(f"Related: {', '.join(ctx_tags)}")

    return " | ".join(parts)


def build_dedup_text(entry: dict) -> str:
    """
    Construct a shorter text for deduplication similarity checks.

    Focuses on the identity of the entry (title + rule + source)
    rather than full detail, to detect near-duplicate entries.
    """
    parts: list[str] = []

    title = entry.get("title", "")
    if title:
        parts.append(title)

    rule = entry.get("rule")
    if rule:
        parts.append(rule)

    sources = entry.get("source", [])
    if isinstance(sources, list):
        for src in sources:
            if src:
                parts.append(src)

    return " | ".join(parts)


def build_fts_fields(entry: dict) -> dict:
    """
    Extract fields for FTS5 indexing.

    Returns a dict with keys: text, title, tags
    suitable for inserting into the fts_entries virtual table.
    """
    title = entry.get("title", "")

    # Combine searchable text fields
    text_parts: list[str] = []
    rule = entry.get("rule")
    if rule:
        text_parts.append(rule)
    implication = entry.get("implication")
    if implication:
        text_parts.append(implication)
    content = entry.get("content", [])
    if isinstance(content, list):
        text_parts.extend(item for item in content if item)

    tags = entry.get("tags", [])
    tags_str = " ".join(tags) if isinstance(tags, list) else ""

    return {
        "title": title,
        "text": " ".join(text_parts),
        "tags": tags_str,
    }
