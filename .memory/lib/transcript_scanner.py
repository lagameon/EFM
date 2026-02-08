"""
EF Memory V3 — Transcript Scanner

Reads Claude Code conversation transcripts (JSONL) and scans for
memory-worthy patterns. Creates draft entries in .memory/drafts/
for human review.

This module bridges the gap between normal conversations (no working
memory session) and the draft queue system (auto_capture.py).

Integration:
  - Stop hook calls scan_conversation_for_drafts() when no session exists
  - Reuses _extract_candidates() from working_memory.py (6 harvest patterns)
  - Reuses _convert_candidate_to_entry() for schema-compliant entries
  - Writes to .memory/drafts/ via create_draft() (never events.jsonl)

Safety:
  - Rules echo filtering: strips auto-injected rule content before scanning
  - Dedup: checks against existing events.jsonl and pending drafts
  - Performance: skips transcripts >10MB

No external dependencies — pure Python stdlib + internal modules.
"""

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("efm.transcript_scanner")

# Safety: skip transcripts larger than 10 MB to avoid blocking stop
_MAX_TRANSCRIPT_BYTES = 10 * 1024 * 1024

# Markers that identify auto-injected rule content from .claude/rules/ef-memory/
# These lines (and their surrounding block) are stripped to prevent re-harvesting
# existing rules that were injected into the conversation context.
_RULES_ECHO_MARKERS = [
    "<!-- EF Memory Auto-Inject",
    "(Auto-generated from Memory)",
    "**Memory:** `",
    "**Implication:**",
]


def _strip_rules_echo(text: str) -> str:
    """Remove auto-injected rule content from transcript text.

    EF Memory auto-generated rules (in .claude/rules/ef-memory/*.md) contain
    distinctive markers like ``<!-- EF Memory Auto-Inject`` and ``**Memory:**``.
    When Claude loads these rules into the conversation context and echoes them
    back, the harvest patterns would re-capture them as "new" discoveries.

    This function strips lines containing rule markers and their surrounding
    block (until the next blank line) to prevent this echo effect.

    Args:
        text: Concatenated assistant message text from transcript.

    Returns:
        Text with rule-injected blocks removed.
    """
    lines = text.splitlines()
    filtered: List[str] = []
    skip_block = False

    for line in lines:
        # Start skipping when we see a rules marker
        if any(marker in line for marker in _RULES_ECHO_MARKERS):
            skip_block = True
            continue
        # Stop skipping at blank line (end of block)
        if skip_block and not line.strip():
            skip_block = False
            continue
        if not skip_block:
            filtered.append(line)

    return "\n".join(filtered)


def read_transcript_messages(transcript_path: Path) -> List[str]:
    """Read a Claude Code transcript JSONL and extract assistant message texts.

    The JSONL format contains one JSON object per line. Each object has a
    "type" field. We look for assistant messages and extract text content
    blocks from message.content.

    Expected format:
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}}
        {"type": "human", "message": {"content": [{"type": "text", "text": "..."}]}}

    Returns:
        List of text strings from assistant turns only.
        Returns [] on any error (graceful degradation).
    """
    if not transcript_path.exists():
        return []

    try:
        file_size = transcript_path.stat().st_size
        if file_size > _MAX_TRANSCRIPT_BYTES:
            logger.info(
                f"Transcript too large ({file_size / 1024 / 1024:.1f} MB), "
                f"skipping scan"
            )
            return []
        if file_size == 0:
            return []
    except OSError:
        return []

    texts: List[str] = []
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if obj.get("type") != "assistant":
                    continue

                message = obj.get("message", {})
                content = message.get("content", [])
                if isinstance(content, str):
                    texts.append(content)
                    continue

                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            texts.append(text)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Cannot read transcript: {e}")
        return []

    return texts


def scan_conversation_for_drafts(
    transcript_path: Path,
    drafts_dir: Path,
    project_root: Path,
    config: dict,
) -> Dict:
    """Scan conversation transcript for memory-worthy patterns and create drafts.

    Steps:
        1. read_transcript_messages() → list of assistant texts
        2. Concatenate and strip rules echo content
        3. _extract_candidates() — reuse 6 harvest patterns from working_memory
        4. Dedup against existing events.jsonl and pending drafts
        5. create_draft() — write to .memory/drafts/ (never events.jsonl)

    Args:
        transcript_path: Path to the conversation JSONL file
        drafts_dir: Path to .memory/drafts/
        project_root: Project root for source normalization
        config: EF Memory config dict

    Returns:
        {
            "candidates_found": int,
            "drafts_created": int,
            "draft_types": {"lesson": N, "constraint": N, ...},
            "errors": []
        }
    """
    result: Dict = {
        "candidates_found": 0,
        "drafts_created": 0,
        "draft_types": {},
        "errors": [],
    }

    # Step 1: Read transcript
    texts = read_transcript_messages(transcript_path)
    if not texts:
        return result

    # Step 2: Concatenate and strip rules echo
    full_text = "\n\n".join(texts)
    full_text = _strip_rules_echo(full_text)

    # Step 3: Extract candidates (reuse working_memory patterns)
    try:
        from .working_memory import _extract_candidates, _convert_candidate_to_entry
    except ImportError as e:
        result["errors"].append(f"Cannot import working_memory: {e}")
        return result

    source_hint = f"conversation:{transcript_path.stem}"
    seen_titles: set = set()
    candidates = _extract_candidates(full_text, source_hint, seen_titles)
    result["candidates_found"] = len(candidates)

    if not candidates:
        return result

    # Step 4: Import dedup and draft tools
    try:
        from .auto_capture import create_draft, list_drafts
        from .auto_verify import check_duplicates, _load_entries_latest_wins
    except ImportError as e:
        result["errors"].append(f"Cannot import modules: {e}")
        return result

    # Pre-load existing entries for dedup (once, not per candidate)
    events_path = project_root / ".memory" / "events.jsonl"
    dedup_threshold = config.get("automation", {}).get("dedup_threshold", 0.85)
    preloaded = (
        _load_entries_latest_wins(events_path) if events_path.exists() else {}
    )

    # Collect titles of existing pending drafts to avoid duplicates
    existing_draft_titles: set = set()
    try:
        for draft in list_drafts(drafts_dir):
            title = draft.entry.get("title", "")
            if title:
                existing_draft_titles.add(title)
    except Exception:
        pass  # If drafts dir doesn't exist yet, no problem

    # Step 5: Convert, dedup, and create drafts
    type_counts: Counter = Counter()
    for candidate in candidates:
        try:
            # Skip if a pending draft with the same title already exists
            if candidate.title in existing_draft_titles:
                continue

            entry = _convert_candidate_to_entry(candidate, project_root)

            # Dedup against existing events.jsonl
            dedup = check_duplicates(
                entry, events_path, dedup_threshold,
                _preloaded_entries=preloaded,
            )
            if dedup.is_duplicate:
                logger.info(
                    f"Skipped duplicate draft '{candidate.title[:50]}': "
                    f"similar to {dedup.similar_entries[0][0]}"
                )
                continue

            draft_info = create_draft(entry, drafts_dir)
            if draft_info.path.exists():
                result["drafts_created"] += 1
                type_counts[candidate.suggested_type] += 1
                # Track this title to avoid duplicates within the same scan
                existing_draft_titles.add(candidate.title)
        except Exception as e:
            result["errors"].append(
                f"Draft failed for '{candidate.title[:50]}': {e}"
            )

    result["draft_types"] = dict(type_counts)
    return result
