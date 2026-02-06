"""
EF Memory V2 — Auto-Verify

Schema validation, source verification, staleness detection, and
duplicate checking for memory entries.

This module implements verify-core.rules.json rules (core-001..core-014)
programmatically. All I/O is wrapped in try/except for graceful
degradation when files or git are unavailable.

No external dependencies — pure Python stdlib + internal text_builder.
"""

import difflib
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .text_builder import build_dedup_text

logger = logging.getLogger("efm.auto_verify")


# ---------------------------------------------------------------------------
# Constants — SCHEMA.md / verify-core.rules.json
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = ["id", "type", "classification", "title", "content", "source", "created_at"]

VALID_TYPES = {"decision", "lesson", "constraint", "risk", "fact"}
VALID_CLASSIFICATIONS = {"hard", "soft"}
VALID_SEVERITIES = {"S1", "S2", "S3"}

ID_PATTERN = re.compile(r"^[a-z]+-[a-z0-9_]+-[a-f0-9]{8}$")

# Source format patterns (from SCHEMA.md)
SOURCE_CODE_PATTERN = re.compile(r"^.+:L\d+(-L\d+)?$")
SOURCE_MD_PATTERN = re.compile(r"^.+#.+:L\d+(-L\d+)?$")
SOURCE_COMMIT_PATTERN = re.compile(r"^commit\s+[0-9a-f]{7,40}$")
SOURCE_PR_PATTERN = re.compile(r"^PR\s*#\d+$")
SOURCE_FUNC_PATTERN = re.compile(r"^.+::.+$")

# Verify command safety (core-011, core-012)
DENY_PATTERNS = [">", ">>", "rm ", "mv ", "cp ", "tee ", "chmod ", "chown ",
                 "git ", "sed ", "awk "]
DEFAULT_ALLOWED_COMMANDS = ["grep", "rg", "find", "wc", "head", "tail", "echo"]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Result of schema validation for a single entry."""
    valid: bool = True
    errors: List[str] = field(default_factory=list)    # FAIL-level
    warnings: List[str] = field(default_factory=list)  # WARN-level


@dataclass
class SourceCheckResult:
    """Result of verifying a single source reference."""
    source: str = ""
    status: str = "OK"       # "OK" | "WARN" | "FAIL" | "SKIP"
    message: str = ""
    source_type: str = ""    # "code" | "markdown" | "commit" | "pr" | "function"


@dataclass
class StalenessResult:
    """Result of staleness check for a single entry."""
    stale: bool = False
    days_since_created: int = 0
    days_since_verified: Optional[int] = None
    threshold_days: int = 90


@dataclass
class DedupResult:
    """Result of duplicate check for a single entry."""
    is_duplicate: bool = False
    similar_entries: List[Tuple[str, float]] = field(default_factory=list)
    threshold: float = 0.85


@dataclass
class VerifyReport:
    """Composite verification report for one or more entries."""
    entries_checked: int = 0
    entries_valid: int = 0
    entries_warnings: int = 0
    entries_errors: int = 0
    results: List[Dict] = field(default_factory=list)
    # Each dict: {entry_id, schema, sources, staleness, dedup}
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Schema validation (core-001 .. core-007)
# ---------------------------------------------------------------------------

def validate_schema(entry: dict) -> ValidationResult:
    """
    Check all SCHEMA.md validation rules against a single entry.

    Implements verify-core.rules.json rules core-001 through core-007,
    plus additional source format and severity consistency checks.
    """
    result = ValidationResult()

    # core-001: Required fields present
    for fld in REQUIRED_FIELDS:
        if fld not in entry or entry[fld] is None:
            result.errors.append(f"Missing required field: {fld}")

    # If we can't even identify the entry, return early
    if result.errors:
        result.valid = False
        return result

    # core-002: ID format
    entry_id = entry.get("id", "")
    if not isinstance(entry_id, str) or not ID_PATTERN.match(entry_id):
        result.errors.append(
            f"Invalid id format: '{entry_id}' "
            f"(expected ^[a-z]+-[a-z0-9_]+-[a-f0-9]{{8}}$)"
        )

    # core-003: Type enum
    entry_type = entry.get("type", "")
    if entry_type not in VALID_TYPES:
        result.errors.append(
            f"Invalid type: '{entry_type}' "
            f"(expected one of {sorted(VALID_TYPES)})"
        )

    # core-004: Classification enum
    classification = entry.get("classification", "")
    if classification not in VALID_CLASSIFICATIONS:
        result.errors.append(
            f"Invalid classification: '{classification}' "
            f"(expected one of {sorted(VALID_CLASSIFICATIONS)})"
        )

    # Severity enum (WARN if invalid, not FAIL)
    severity = entry.get("severity")
    if severity is not None and severity not in VALID_SEVERITIES:
        result.warnings.append(
            f"Invalid severity: '{severity}' "
            f"(expected one of {sorted(VALID_SEVERITIES)} or null)"
        )

    # core-005: Executable constraint
    rule = entry.get("rule")
    implication = entry.get("implication")
    if rule is None and implication is None:
        result.errors.append(
            "At least one of 'rule' or 'implication' must be non-null"
        )

    # core-006: Content length (WARN)
    content = entry.get("content", [])
    if not isinstance(content, list):
        result.errors.append("'content' must be a list")
    elif len(content) < 2:
        result.warnings.append(
            f"Content has {len(content)} items (expected 2-6)"
        )
    elif len(content) > 6:
        result.warnings.append(
            f"Content has {len(content)} items (expected 2-6)"
        )

    # core-007: Title length (WARN)
    title = entry.get("title", "")
    if not isinstance(title, str):
        result.errors.append("'title' must be a string")
    elif not title:
        result.errors.append("'title' must be non-empty")
    elif len(title) > 120:
        result.warnings.append(
            f"Title is {len(title)} chars (max 120)"
        )

    # Source format check
    sources = entry.get("source", [])
    if not isinstance(sources, list) or len(sources) == 0:
        result.errors.append("'source' must be a non-empty list")
    elif isinstance(sources, list):
        for i, src in enumerate(sources):
            if not isinstance(src, str) or not src.strip():
                result.errors.append(f"source[{i}] must be a non-empty string")
            elif not _matches_source_pattern(src):
                result.warnings.append(
                    f"source[{i}] '{src}' does not match any known format"
                )

    # created_at: valid ISO 8601
    created_at = entry.get("created_at", "")
    if isinstance(created_at, str) and created_at:
        try:
            _parse_iso8601(created_at)
        except ValueError:
            result.errors.append(
                f"Invalid created_at: '{created_at}' (expected ISO 8601)"
            )
    elif not created_at:
        # Already caught by required fields check above
        pass

    # Severity consistency (WARN)
    if classification == "hard" and severity is None:
        result.warnings.append(
            "Hard entry without severity (consider adding S1/S2/S3)"
        )

    result.valid = len(result.errors) == 0
    return result


def _matches_source_pattern(src: str) -> bool:
    """Check if a source string matches any known normalized format."""
    return bool(
        SOURCE_MD_PATTERN.match(src)
        or SOURCE_CODE_PATTERN.match(src)
        or SOURCE_COMMIT_PATTERN.match(src)
        or SOURCE_PR_PATTERN.match(src)
        or SOURCE_FUNC_PATTERN.match(src)
    )


def _parse_iso8601(timestamp: str) -> datetime:
    """Parse an ISO 8601 timestamp string to datetime (UTC)."""
    # Handle common variants
    ts = timestamp.rstrip("Z")
    if "+" not in ts and "-" not in ts[10:]:
        # No timezone info, assume UTC
        ts += "+00:00"
    elif ts.endswith("Z") or timestamp.endswith("Z"):
        ts = ts.rstrip("Z") + "+00:00"

    # Try parsing with fromisoformat
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        pass

    # Fallback: try common format
    for fmt in ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"]:
        try:
            return datetime.strptime(timestamp, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {timestamp}")


# ---------------------------------------------------------------------------
# Source verification (core-008, core-009)
# ---------------------------------------------------------------------------

def _parse_source_ref(source_str: str) -> Tuple[str, str, Optional[str], Optional[str]]:
    """
    Parse a normalized source reference into components.

    Returns: (source_type, file_path, anchor, line_range)
    """
    # Order matters — markdown before code (markdown has # before :L)
    if SOURCE_COMMIT_PATTERN.match(source_str):
        parts = source_str.split(maxsplit=1)
        commit_hash = parts[1] if len(parts) > 1 else ""
        return ("commit", "", commit_hash, None)

    if SOURCE_PR_PATTERN.match(source_str):
        return ("pr", "", source_str, None)

    if SOURCE_MD_PATTERN.match(source_str):
        # path#anchor:L10-L20
        hash_idx = source_str.index("#")
        file_path = source_str[:hash_idx]
        rest = source_str[hash_idx + 1:]
        # Split anchor from line range
        line_match = re.search(r":L(\d+)(-L\d+)?$", rest)
        if line_match:
            anchor = rest[:line_match.start()]
            line_range = rest[line_match.start() + 1:]  # skip the ':'
        else:
            anchor = rest
            line_range = None
        return ("markdown", file_path, anchor, line_range)

    if SOURCE_FUNC_PATTERN.match(source_str):
        # path::function_name
        parts = source_str.split("::", 1)
        return ("function", parts[0], parts[1] if len(parts) > 1 else None, None)

    if SOURCE_CODE_PATTERN.match(source_str):
        # path:L10-L20
        line_match = re.search(r":L(\d+)(-L\d+)?$", source_str)
        if line_match:
            file_path = source_str[:line_match.start()]
            line_range = source_str[line_match.start() + 1:]
            return ("code", file_path, None, line_range)

    # Unknown format
    return ("unknown", source_str, None, None)


def verify_source(source_str: str, project_root: Path) -> SourceCheckResult:
    """
    Check if a source reference is still valid.

    Reads files and optionally runs git. All I/O wrapped in try/except
    for graceful degradation.
    """
    result = SourceCheckResult(source=source_str)
    source_type, file_path, anchor, line_range = _parse_source_ref(source_str)
    result.source_type = source_type

    if source_type == "pr":
        result.status = "OK"
        result.message = "PR reference (informational, not verified)"
        return result

    if source_type == "commit":
        return _verify_commit(anchor or "", project_root, result)

    if source_type == "unknown":
        result.status = "WARN"
        result.message = f"Unknown source format: {source_str}"
        return result

    # File-based sources: code, markdown, function
    if not file_path:
        result.status = "WARN"
        result.message = "Empty file path"
        return result

    full_path = project_root / file_path
    try:
        if not full_path.exists():
            result.status = "FAIL"
            result.message = f"File not found: {file_path}"
            return result
    except OSError as e:
        result.status = "SKIP"
        result.message = f"Cannot access path: {e}"
        return result

    if source_type == "code":
        return _verify_code_source(full_path, line_range, result)
    elif source_type == "markdown":
        return _verify_markdown_source(full_path, anchor, line_range, result)
    elif source_type == "function":
        return _verify_function_source(full_path, anchor, result)

    return result


def _verify_commit(commit_hash: str, project_root: Path, result: SourceCheckResult) -> SourceCheckResult:
    """Verify a commit hash exists via git."""
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-t", commit_hash],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode == 0 and proc.stdout.strip() == "commit":
            result.status = "OK"
            result.message = f"Commit {commit_hash} verified"
        else:
            result.status = "FAIL"
            result.message = f"Commit {commit_hash} not found in git"
    except FileNotFoundError:
        result.status = "SKIP"
        result.message = "git not available"
    except subprocess.TimeoutExpired:
        result.status = "SKIP"
        result.message = "git command timed out"
    except Exception as e:
        result.status = "SKIP"
        result.message = f"git check failed: {e}"
    return result


def _verify_code_source(
    full_path: Path,
    line_range: Optional[str],
    result: SourceCheckResult,
) -> SourceCheckResult:
    """Verify a code source (file + line range)."""
    try:
        lines = full_path.read_text(encoding="utf-8").splitlines()
        total_lines = len(lines)
    except Exception as e:
        result.status = "WARN"
        result.message = f"Cannot read file: {e}"
        return result

    if not line_range:
        result.status = "OK"
        result.message = f"File exists ({total_lines} lines)"
        return result

    # Parse line range: L10 or L10-L20
    match = re.match(r"L(\d+)(?:-L(\d+))?", line_range)
    if not match:
        result.status = "WARN"
        result.message = f"Cannot parse line range: {line_range}"
        return result

    start_line = int(match.group(1))
    end_line = int(match.group(2)) if match.group(2) else start_line

    if end_line > total_lines:
        result.status = "WARN"
        result.message = (
            f"Line range {line_range} exceeds file length "
            f"({total_lines} lines)"
        )
    else:
        result.status = "OK"
        result.message = f"File exists, line range valid ({total_lines} lines)"

    return result


def _verify_markdown_source(
    full_path: Path,
    anchor: Optional[str],
    line_range: Optional[str],
    result: SourceCheckResult,
) -> SourceCheckResult:
    """Verify a markdown source (file + heading anchor + line range)."""
    try:
        content = full_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        total_lines = len(lines)
    except Exception as e:
        result.status = "WARN"
        result.message = f"Cannot read file: {e}"
        return result

    if not anchor:
        result.status = "OK"
        result.message = "File exists (no anchor to verify)"
        return result

    # Search for heading anchor (case-insensitive)
    anchor_lower = anchor.lower().replace("-", " ").replace("_", " ")
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading_text = stripped.lstrip("#").strip()
            heading_normalized = heading_text.lower().replace("-", " ").replace("_", " ")
            if anchor_lower in heading_normalized or heading_normalized in anchor_lower:
                found = True
                break

    if not found:
        result.status = "WARN"
        result.message = f"Heading '{anchor}' not found in file"
        return result

    # Check line range if present
    if line_range:
        match = re.match(r"L(\d+)(?:-L(\d+))?", line_range)
        if match:
            end_line = int(match.group(2)) if match.group(2) else int(match.group(1))
            if end_line > total_lines:
                result.status = "WARN"
                result.message = (
                    f"Heading found, but line range {line_range} exceeds "
                    f"file length ({total_lines} lines)"
                )
                return result

    result.status = "OK"
    result.message = f"File exists, heading '{anchor}' found"
    return result


def _verify_function_source(
    full_path: Path,
    func_name: Optional[str],
    result: SourceCheckResult,
) -> SourceCheckResult:
    """Verify a function source (file + function/class definition)."""
    if not func_name:
        result.status = "OK"
        result.message = "File exists (no function to verify)"
        return result

    try:
        content = full_path.read_text(encoding="utf-8")
    except Exception as e:
        result.status = "WARN"
        result.message = f"Cannot read file: {e}"
        return result

    # Search for def or class definition
    patterns = [
        f"def {func_name}",
        f"class {func_name}",
        f"def {func_name}(",
        f"class {func_name}(",
        f"class {func_name}:",
    ]
    for pattern in patterns:
        if pattern in content:
            result.status = "OK"
            result.message = f"'{func_name}' found in file"
            return result

    result.status = "WARN"
    result.message = f"'{func_name}' not found in file"
    return result


# ---------------------------------------------------------------------------
# Staleness check (core-013)
# ---------------------------------------------------------------------------

def check_staleness(entry: dict, threshold_days: int = 90) -> StalenessResult:
    """
    Check if an entry is stale (not verified within threshold).

    Uses last_verified (if set) or falls back to created_at.
    """
    result = StalenessResult(threshold_days=threshold_days)
    now = datetime.now(timezone.utc)

    # Parse created_at
    created_at = entry.get("created_at", "")
    try:
        created_dt = _parse_iso8601(created_at)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        result.days_since_created = (now - created_dt).days
    except (ValueError, TypeError):
        result.days_since_created = 999  # Can't parse, treat as very old

    # Check last_verified
    last_verified = entry.get("last_verified")
    if last_verified:
        try:
            verified_dt = _parse_iso8601(last_verified)
            if verified_dt.tzinfo is None:
                verified_dt = verified_dt.replace(tzinfo=timezone.utc)
            result.days_since_verified = (now - verified_dt).days
            result.stale = result.days_since_verified > threshold_days
        except (ValueError, TypeError):
            result.days_since_verified = None
            result.stale = result.days_since_created > threshold_days
    else:
        result.days_since_verified = None
        result.stale = result.days_since_created > threshold_days

    return result


# ---------------------------------------------------------------------------
# Duplicate check
# ---------------------------------------------------------------------------

def _load_entries_latest_wins(events_path: Path) -> Dict[str, dict]:
    """Load entries from events.jsonl with latest-wins semantics."""
    entries: Dict[str, dict] = {}
    if not events_path.exists():
        return entries

    try:
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entry_id = entry.get("id")
                    if entry_id:
                        entries[entry_id] = entry
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass

    return entries


def check_duplicates(
    entry: dict,
    events_path: Path,
    threshold: float = 0.85,
) -> DedupResult:
    """
    Check for near-duplicate entries using text similarity.

    Uses difflib.SequenceMatcher (stdlib) instead of embedding cosine
    similarity. This ensures dedup works in no-embed mode.

    Threshold default: 0.85 (lower than embedding's 0.92 because
    text similarity is less precise).
    """
    result = DedupResult(threshold=threshold)
    entry_id = entry.get("id", "")
    candidate_text = build_dedup_text(entry)

    if not candidate_text:
        return result

    # Load existing entries
    existing = _load_entries_latest_wins(events_path)

    for existing_id, existing_entry in existing.items():
        # Don't compare against self
        if existing_id == entry_id:
            continue
        # Skip deprecated entries
        if existing_entry.get("deprecated", False):
            continue

        existing_text = build_dedup_text(existing_entry)
        if not existing_text:
            continue

        ratio = difflib.SequenceMatcher(
            None, candidate_text, existing_text
        ).ratio()

        if ratio >= threshold:
            result.similar_entries.append((existing_id, round(ratio, 4)))

    # Sort by similarity descending
    result.similar_entries.sort(key=lambda x: x[1], reverse=True)
    result.is_duplicate = len(result.similar_entries) > 0

    return result


# ---------------------------------------------------------------------------
# Verify command safety (core-010, core-011, core-012)
# ---------------------------------------------------------------------------

def check_verify_command(
    command: Optional[str],
    allowed_commands: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """
    Static safety check on a verify command (NEVER executed).

    Returns: (status, message) where status is "OK" | "WARN" | "FAIL"
    """
    if command is None:
        return ("OK", "No verify command (optional field)")

    if not isinstance(command, str) or not command.strip():
        return ("WARN", "Empty verify command")

    cmd = command.strip()
    allowed = allowed_commands or DEFAULT_ALLOWED_COMMANDS

    # core-011: Check for write operators
    for pattern in DENY_PATTERNS:
        if pattern in cmd:
            return ("FAIL", f"Destructive pattern '{pattern.strip()}' in verify command")

    # core-012: Check command allowlist
    first_word = cmd.split()[0] if cmd.split() else ""
    # Handle piped commands
    pipe_commands = [part.strip().split()[0] for part in cmd.split("|") if part.strip()]
    for pc in pipe_commands:
        if pc not in allowed:
            return ("WARN", f"Command '{pc}' not in allowlist {allowed}")

    return ("OK", "Safe read-only command")


# ---------------------------------------------------------------------------
# Composite entry verification
# ---------------------------------------------------------------------------

def verify_entry(
    entry: dict,
    events_path: Path,
    project_root: Path,
    config: dict,
) -> Dict:
    """
    Run all verification checks on a single entry.

    Returns a dict with:
        entry_id, schema, sources, staleness, dedup, verify_cmd, overall
    """
    entry_id = entry.get("id", "<unknown>")

    # Schema
    schema_result = validate_schema(entry)

    # Sources
    source_results = []
    for src in entry.get("source", []):
        source_results.append(verify_source(src, project_root))

    # Staleness
    threshold = config.get("verify", {}).get("staleness_threshold_days", 90)
    staleness_result = check_staleness(entry, threshold)

    # Dedup
    dedup_threshold = config.get("automation", {}).get("dedup_threshold", 0.85)
    dedup_result = check_duplicates(entry, events_path, dedup_threshold)

    # Verify command
    allowed_cmds = config.get("verify", {}).get("allowed_commands", DEFAULT_ALLOWED_COMMANDS)
    verify_status, verify_msg = check_verify_command(
        entry.get("verify"), allowed_cmds
    )

    # Determine overall status
    has_errors = not schema_result.valid
    has_warnings = (
        len(schema_result.warnings) > 0
        or any(sr.status == "WARN" for sr in source_results)
        or any(sr.status == "FAIL" for sr in source_results)
        or staleness_result.stale
        or dedup_result.is_duplicate
        or verify_status == "WARN"
    )
    has_source_errors = any(sr.status == "FAIL" for sr in source_results)

    if has_errors or has_source_errors or verify_status == "FAIL":
        overall = "FAIL"
    elif has_warnings:
        overall = "WARN"
    else:
        overall = "OK"

    return {
        "entry_id": entry_id,
        "schema": schema_result,
        "sources": source_results,
        "staleness": staleness_result,
        "dedup": dedup_result,
        "verify_cmd": (verify_status, verify_msg),
        "overall": overall,
    }


def verify_all_entries(
    events_path: Path,
    project_root: Path,
    config: dict,
) -> VerifyReport:
    """
    Verify all entries in events.jsonl.

    Loads entries with latest-wins semantics, skips deprecated,
    runs verify_entry on each, aggregates into VerifyReport.
    """
    report = VerifyReport()
    start_time = time.monotonic()

    entries = _load_entries_latest_wins(events_path)

    for entry_id, entry in entries.items():
        if entry.get("deprecated", False):
            continue

        report.entries_checked += 1
        result = verify_entry(entry, events_path, project_root, config)
        report.results.append(result)

        if result["overall"] == "OK":
            report.entries_valid += 1
        elif result["overall"] == "WARN":
            report.entries_warnings += 1
        else:
            report.entries_errors += 1

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report
