"""
EF Memory V2 — Auto-Capture (Draft Management)

CRUD operations for memory draft files:
  - create_draft: write a candidate entry to .memory/drafts/
  - list_drafts: list all pending draft files
  - approve_draft: validate + append to events.jsonl + delete draft
  - reject_draft: delete a draft file
  - review_drafts: list drafts with full verification status

Human-in-the-loop invariant: drafts are NEVER auto-promoted.
approve_draft is only callable via CLI.

No external dependencies — pure Python stdlib + internal modules.
"""

import copy
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .auto_verify import ValidationResult, validate_schema, verify_entry

logger = logging.getLogger("efm.auto_capture")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DraftInfo:
    """Information about a single draft file."""
    path: Path = field(default_factory=lambda: Path())
    entry: dict = field(default_factory=dict)
    filename: str = ""
    draft_status: str = "pending"
    capture_timestamp: str = ""
    validation: Optional[ValidationResult] = None


@dataclass
class ApproveResult:
    """Result of approving a draft."""
    success: bool = False
    entry_id: str = ""
    message: str = ""


@dataclass
class ReviewReport:
    """Report from reviewing all pending drafts."""
    total_drafts: int = 0
    valid_drafts: int = 0
    invalid_drafts: int = 0
    drafts: List[DraftInfo] = field(default_factory=list)
    verification_results: List[dict] = field(default_factory=list)
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_title(title: str) -> str:
    """
    Sanitize a title for use in a filename.

    - Lowercase
    - Replace non-alphanumeric with underscores
    - Collapse multiple underscores
    - Truncate to 50 chars
    - Strip leading/trailing underscores
    - Fallback to 'untitled' if empty
    """
    if not title:
        return "untitled"

    result = title.lower()
    result = re.sub(r"[^a-z0-9]+", "_", result)
    result = re.sub(r"_+", "_", result)
    result = result.strip("_")
    result = result[:50].rstrip("_")

    return result or "untitled"


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def create_draft(entry: dict, drafts_dir: Path) -> DraftInfo:
    """
    Write a single draft JSON file to drafts_dir.

    Steps:
    1. Validate schema (advisory — record but don't reject)
    2. Add _meta.draft_status and _meta.capture_timestamp
    3. Generate filename: {YYYYMMDD_HHMMSS}_{sanitized_title}.json
    4. Write pretty-printed JSON (indent=2)

    Returns DraftInfo with path, entry, validation result.
    Does NOT write to events.jsonl.
    """
    info = DraftInfo()

    # Deep copy to avoid mutating caller's dict
    entry = copy.deepcopy(entry)

    # Advisory validation
    validation = validate_schema(entry)
    info.validation = validation

    # Ensure _meta exists
    if "_meta" not in entry:
        entry["_meta"] = {}

    # Set draft metadata
    now = datetime.now(timezone.utc)
    entry["_meta"]["draft_status"] = "pending"
    entry["_meta"]["capture_timestamp"] = now.isoformat()

    # Generate filename
    title = entry.get("title", "")
    sanitized = _sanitize_title(title)
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp_str}_{sanitized}.json"

    # Ensure drafts_dir exists
    drafts_dir.mkdir(parents=True, exist_ok=True)

    # Handle collision (same second + same title)
    target = drafts_dir / filename
    counter = 1
    while target.exists():
        filename = f"{timestamp_str}_{sanitized}_{counter:03d}.json"
        target = drafts_dir / filename
        counter += 1

    # Write
    target.write_text(
        json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    info.path = target
    info.entry = entry
    info.filename = filename
    info.draft_status = "pending"
    info.capture_timestamp = entry["_meta"]["capture_timestamp"]

    return info


def list_drafts(drafts_dir: Path) -> List[DraftInfo]:
    """
    List all pending draft files, sorted by capture_timestamp (oldest first).

    Skips non-JSON files, invalid JSON, and missing directories.
    """
    drafts: List[DraftInfo] = []

    if not drafts_dir.exists():
        return drafts

    for json_file in sorted(drafts_dir.glob("*.json")):
        try:
            text = json_file.read_text(encoding="utf-8")
            entry = json.loads(text)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Skipping invalid draft: {json_file.name}: {e}")
            continue

        meta = entry.get("_meta", {})
        info = DraftInfo(
            path=json_file,
            entry=entry,
            filename=json_file.name,
            draft_status=meta.get("draft_status", "pending"),
            capture_timestamp=meta.get("capture_timestamp", ""),
        )
        drafts.append(info)

    # Sort by capture_timestamp (oldest first)
    drafts.sort(key=lambda d: d.capture_timestamp or "")

    return drafts


def approve_draft(draft_path: Path, events_path: Path) -> ApproveResult:
    """
    Validate a draft and append it to events.jsonl.

    Steps:
    1. Read draft JSON
    2. Strict schema validation (errors → reject)
    3. Strip _meta.draft_status and _meta.capture_timestamp
    4. Append single JSON line to events.jsonl
    5. Delete the draft file

    Returns ApproveResult with success/failure and message.
    Human-in-the-loop: called only via CLI.
    """
    result = ApproveResult()

    # Read draft
    if not draft_path.exists():
        result.message = f"Draft not found: {draft_path}"
        return result

    try:
        text = draft_path.read_text(encoding="utf-8")
        entry = json.loads(text)
    except (json.JSONDecodeError, OSError) as e:
        result.message = f"Cannot read draft: {e}"
        return result

    # Strict validation
    validation = validate_schema(entry)
    if not validation.valid:
        result.message = (
            f"Schema validation failed: {'; '.join(validation.errors)}"
        )
        return result

    # Strip draft-specific _meta fields
    meta = entry.get("_meta", {})
    meta.pop("draft_status", None)
    meta.pop("capture_timestamp", None)
    if meta:
        entry["_meta"] = meta
    elif "_meta" in entry:
        del entry["_meta"]

    # Append to events.jsonl (create if missing)
    try:
        with open(events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        result.message = f"Cannot write to events.jsonl: {e}"
        return result

    # Delete draft file
    try:
        draft_path.unlink()
    except OSError as e:
        logger.warning(f"Draft approved but could not delete file: {e}")

    result.success = True
    result.entry_id = entry.get("id", "")
    result.message = f"Approved: {result.entry_id}"
    return result


def reject_draft(draft_path: Path) -> bool:
    """
    Delete a draft file.

    Returns True if deleted, False if file not found or error.
    """
    try:
        if draft_path.exists():
            draft_path.unlink()
            return True
        return False
    except OSError as e:
        logger.warning(f"Cannot delete draft: {e}")
        return False


def review_drafts(
    drafts_dir: Path,
    events_path: Path,
    project_root: Path,
    config: dict,
) -> ReviewReport:
    """
    List all drafts with full verification status.

    Runs verify_entry on each draft for comprehensive reporting.
    """
    report = ReviewReport()
    start_time = time.monotonic()

    drafts = list_drafts(drafts_dir)
    report.total_drafts = len(drafts)
    report.drafts = drafts

    for draft in drafts:
        verify_result = verify_entry(
            draft.entry, events_path, project_root, config
        )
        report.verification_results.append(verify_result)

        if verify_result["overall"] in ("OK", "WARN"):
            report.valid_drafts += 1
        else:
            report.invalid_drafts += 1

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report
