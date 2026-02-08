"""
EF Memory V3 — Working Memory (PWF Integration)

Short-term working memory for multi-step tasks, inspired by
Planning with Files. Maintains three session files:

  - task_plan.md    — phases, acceptance criteria, progress
  - findings.md     — discoveries + EF Memory prefill
  - progress.md     — session log: actions, errors, decisions

Integration with EF Memory:
  - Startup prefill: search_memory → inject into findings.md
  - Completion harvest: extract lesson/decision candidates
  - Session recovery: detect and resume stale sessions

All files live in .memory/working/ (gitignored).
No external dependencies — pure Python stdlib + internal search module.
"""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("efm.working_memory")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class PrefillEntry:
    """A memory entry formatted for findings.md injection."""
    entry_id: str
    title: str
    classification: str  # "hard" | "soft"
    severity: Optional[str]
    rule: Optional[str]
    source: List[str]
    score: float


@dataclass
class SessionStartReport:
    """Result of starting a new working memory session."""
    task_description: str
    working_dir: str
    files_created: List[str] = field(default_factory=list)
    prefill_count: int = 0
    prefill_entries: List[PrefillEntry] = field(default_factory=list)
    already_exists: bool = False
    duration_ms: float = 0.0


@dataclass
class SessionResumeReport:
    """Result of resuming an existing session."""
    task_description: str
    current_phase: str
    phases_total: int
    phases_done: int
    last_progress_line: str
    findings_count: int
    duration_ms: float = 0.0


@dataclass
class HarvestCandidate:
    """A memory entry candidate extracted from working files."""
    suggested_type: str       # "lesson" | "decision" | "constraint" | "risk" | "fact"
    title: str
    content: List[str]
    rule: Optional[str]
    implication: Optional[str]
    source_hint: str          # e.g., ".memory/working/findings.md"
    extraction_reason: str    # Why this was flagged


@dataclass
class HarvestReport:
    """Result of harvesting memory candidates from working files."""
    candidates: List[HarvestCandidate] = field(default_factory=list)
    findings_scanned: bool = False
    progress_scanned: bool = False
    duration_ms: float = 0.0


@dataclass
class SessionStatus:
    """Current status of a working memory session."""
    active: bool
    task_description: str = ""
    phases_total: int = 0
    phases_done: int = 0
    findings_count: int = 0
    progress_lines: int = 0
    created_at: str = ""
    last_modified: str = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TASK_PLAN_FILE = "task_plan.md"
FINDINGS_FILE = "findings.md"
PROGRESS_FILE = "progress.md"

# Harvest extraction patterns
_LESSON_PATTERN = re.compile(r"(?:LESSON|lesson|Lesson)\s*[:：]\s*(.+)", re.MULTILINE)
_CONSTRAINT_PATTERN = re.compile(r"(?:CONSTRAINT|constraint|Constraint|INVARIANT|invariant)\s*[:：]\s*(.+)", re.MULTILINE)
_DECISION_PATTERN = re.compile(r"(?:DECISION|decision|Decision|Decided|decided)\s*[:：]\s*(.+)", re.MULTILINE)
_WARNING_PATTERN = re.compile(r"(?:WARNING|warning|Warning|RISK|risk|Risk|DANGER|danger)\s*[:：]\s*(.+)", re.MULTILINE)
_MUST_PATTERN = re.compile(r"((?:MUST|NEVER|ALWAYS)\s+.{10,80})", re.MULTILINE)
_ERROR_FIX_PATTERN = re.compile(
    r"(?:Error|ERROR|Fix|FIX|Fixed|Bug|BUG|Resolved)\s*[:：]\s*(.+)", re.MULTILINE
)


# ---------------------------------------------------------------------------
# Template generators
# ---------------------------------------------------------------------------

def _generate_task_plan(task_description: str) -> str:
    """Generate initial task_plan.md content."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""# Task Plan

**Task**: {task_description}
**Created**: {now}
**Status**: In Progress

---

## Phases

### Phase 1: Investigation
- [ ] Understand the problem scope
- [ ] Identify relevant files and components

### Phase 2: Implementation
- [ ] Implement the solution
- [ ] Handle edge cases

### Phase 3: Verification
- [ ] Test the changes
- [ ] Verify acceptance criteria

---

## Acceptance Criteria

- [ ] (Define acceptance criteria for this task)

---

## Notes

(Add task-specific notes here)
"""


def _generate_findings(
    task_description: str,
    prefill_entries: Optional[List[PrefillEntry]] = None,
) -> str:
    """Generate initial findings.md with optional EF Memory prefill."""
    lines = ["# Findings\n"]

    if prefill_entries:
        lines.append("## Pre-loaded Context (from EF Memory)\n")
        for entry in prefill_entries:
            severity_tag = f" [{entry.severity}]" if entry.severity else ""
            lines.append(
                f"### [{entry.classification.capitalize()}]{severity_tag} {entry.title}"
            )
            if entry.rule:
                lines.append(f"- **Rule**: {entry.rule}")
            if entry.source:
                lines.append(f"- **Source**: {entry.source[0]}")
            lines.append(f"- **Score**: {entry.score:.2f}")
            lines.append("")
        lines.append("---\n")

    lines.append("## Session Discoveries\n")
    lines.append("(Record findings, insights, and discoveries here)\n")

    return "\n".join(lines)


def _generate_progress(task_description: str) -> str:
    """Generate initial progress.md content."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""# Progress Log

**Task**: {task_description}

---

## {now}
- Session started
"""


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def start_session(
    task_description: str,
    events_path: Path,
    working_dir: Path,
    config: dict,
    project_root: Optional[Path] = None,
) -> SessionStartReport:
    """
    Start a new working memory session.

    1. Create working directory and 3 session files
    2. Search EF Memory for relevant entries → prefill findings.md
    3. Return report with details

    Args:
        task_description: What the task is about
        events_path: Path to events.jsonl for prefill search
        working_dir: Path to .memory/working/
        config: EF Memory config dict
        project_root: Optional project root for search context
    """
    start_time = time.monotonic()
    report = SessionStartReport(
        task_description=task_description,
        working_dir=str(working_dir),
    )

    # Check if session already exists
    plan_path = working_dir / TASK_PLAN_FILE
    if plan_path.exists():
        report.already_exists = True
        report.duration_ms = (time.monotonic() - start_time) * 1000
        return report

    # Create working directory
    working_dir.mkdir(parents=True, exist_ok=True)

    # Prefill from EF Memory (if enabled and events exist)
    prefill_entries = []
    v3_config = config.get("v3", {})
    if v3_config.get("prefill_on_plan_start", True) and events_path.exists():
        max_entries = v3_config.get("max_prefill_entries", 5)
        prefill_entries = _search_for_prefill(
            task_description, events_path, config, max_entries
        )
        report.prefill_count = len(prefill_entries)
        report.prefill_entries = prefill_entries

    # Write session files
    plan_content = _generate_task_plan(task_description)
    findings_content = _generate_findings(task_description, prefill_entries)
    progress_content = _generate_progress(task_description)

    plan_path.write_text(plan_content)
    report.files_created.append(TASK_PLAN_FILE)

    (working_dir / FINDINGS_FILE).write_text(findings_content)
    report.files_created.append(FINDINGS_FILE)

    (working_dir / PROGRESS_FILE).write_text(progress_content)
    report.files_created.append(PROGRESS_FILE)

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report


def resume_session(working_dir: Path) -> Optional[SessionResumeReport]:
    """
    Resume an existing working memory session.

    Reads task_plan.md and progress.md to build a context summary.
    Returns None if no active session exists.
    """
    start_time = time.monotonic()

    plan_path = working_dir / TASK_PLAN_FILE
    if not plan_path.exists():
        return None

    plan_content = plan_path.read_text()
    progress_path = working_dir / PROGRESS_FILE
    findings_path = working_dir / FINDINGS_FILE

    # Extract task description
    task_desc = _extract_field(plan_content, "Task")

    # Count phases and completions
    phases_total, phases_done = _count_phases(plan_content)

    # Get current phase
    current_phase = _get_current_phase(plan_content)

    # Last progress line
    last_line = ""
    if progress_path.exists():
        lines = progress_path.read_text().strip().splitlines()
        # Find last non-empty, non-header line
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                last_line = stripped
                break

    # Count findings
    findings_count = 0
    if findings_path.exists():
        findings_content = findings_path.read_text()
        # Count non-empty lines in "Session Discoveries" section
        in_discoveries = False
        for line in findings_content.splitlines():
            if "Session Discoveries" in line:
                in_discoveries = True
                continue
            if in_discoveries and line.strip() and not line.strip().startswith("("):
                findings_count += 1

    report = SessionResumeReport(
        task_description=task_desc,
        current_phase=current_phase,
        phases_total=phases_total,
        phases_done=phases_done,
        last_progress_line=last_line,
        findings_count=findings_count,
    )
    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report


def get_session_status(working_dir: Path) -> SessionStatus:
    """
    Get the current status of a working memory session.

    Returns SessionStatus with active=False if no session exists.
    """
    plan_path = working_dir / TASK_PLAN_FILE
    if not plan_path.exists():
        return SessionStatus(active=False)

    plan_content = plan_path.read_text()
    task_desc = _extract_field(plan_content, "Task")
    phases_total, phases_done = _count_phases(plan_content)

    # Count findings (only in "Session Discoveries" section, not prefill)
    findings_count = 0
    findings_path = working_dir / FINDINGS_FILE
    if findings_path.exists():
        content = findings_path.read_text()
        in_discoveries = False
        for line in content.splitlines():
            if "Session Discoveries" in line:
                in_discoveries = True
                continue
            if in_discoveries and line.strip() and not line.strip().startswith("("):
                findings_count += 1

    # Count progress lines
    progress_lines = 0
    progress_path = working_dir / PROGRESS_FILE
    if progress_path.exists():
        content = progress_path.read_text()
        progress_lines = len([
            l for l in content.splitlines()
            if l.strip().startswith("- ") and not l.strip().startswith("- Session started")
        ])

    # File timestamps
    stat = plan_path.stat()
    created_at = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    return SessionStatus(
        active=True,
        task_description=task_desc,
        phases_total=phases_total,
        phases_done=phases_done,
        findings_count=findings_count,
        progress_lines=progress_lines,
        created_at=created_at,
        last_modified=last_modified,
    )


def harvest_session(
    working_dir: Path,
    events_path: Path,
    config: dict,
) -> HarvestReport:
    """
    Extract memory candidates from working memory files.

    Scans findings.md and progress.md for patterns that suggest
    memory-worthy knowledge (lessons, decisions, constraints, etc.).

    Returns candidates — does NOT write to events.jsonl.
    """
    start_time = time.monotonic()
    report = HarvestReport()

    findings_path = working_dir / FINDINGS_FILE
    progress_path = working_dir / PROGRESS_FILE

    # Track seen titles across all files to avoid cross-file duplicates
    seen_titles: set = set()

    if findings_path.exists():
        findings_content = findings_path.read_text()
        report.findings_scanned = True
        candidates = _extract_candidates(findings_content, str(findings_path), seen_titles)
        report.candidates.extend(candidates)

    if progress_path.exists():
        progress_content = progress_path.read_text()
        report.progress_scanned = True
        candidates = _extract_candidates(progress_content, str(progress_path), seen_titles)
        report.candidates.extend(candidates)

    report.duration_ms = (time.monotonic() - start_time) * 1000
    return report


def read_plan_summary(working_dir: Path, max_lines: int = 30) -> str:
    """
    Read the first N lines of task_plan.md.

    Equivalent to PWF's PreToolUse: `cat task_plan.md | head -30`.
    Returns empty string if no active session.
    """
    plan_path = working_dir / TASK_PLAN_FILE
    if not plan_path.exists():
        return ""

    lines = plan_path.read_text().splitlines()
    return "\n".join(lines[:max_lines])


def clear_session(working_dir: Path) -> bool:
    """
    Remove all working memory files (end session).

    Returns True if files were removed, False if no session existed.
    """
    if not working_dir.exists():
        return False

    removed = False
    for filename in (TASK_PLAN_FILE, FINDINGS_FILE, PROGRESS_FILE):
        filepath = working_dir / filename
        if filepath.exists():
            filepath.unlink()
            removed = True

    return removed


# ---------------------------------------------------------------------------
# Prefill search (integration with M2 search engine)
# ---------------------------------------------------------------------------

def _search_for_prefill(
    task_description: str,
    events_path: Path,
    config: dict,
    max_entries: int = 5,
) -> List[PrefillEntry]:
    """
    Search EF Memory for entries relevant to the task description.

    Uses basic mode (no embedder/vectordb) since we want zero-dep prefill.
    Falls back gracefully if search module is unavailable.
    """
    try:
        from .search import search_memory
        report = search_memory(
            query=task_description,
            events_path=events_path,
            config=config,
            max_results=max_entries,
            force_mode="basic",
        )
    except Exception as exc:
        logger.warning(f"Prefill search failed: {exc}")
        return []

    entries = []
    for result in report.results:
        entry = result.entry
        entries.append(PrefillEntry(
            entry_id=result.entry_id,
            title=entry.get("title", ""),
            classification=entry.get("classification", "soft"),
            severity=entry.get("severity"),
            rule=entry.get("rule"),
            source=entry.get("source", []),
            score=result.score,
        ))

    return entries


# ---------------------------------------------------------------------------
# Harvest extraction helpers
# ---------------------------------------------------------------------------

def _extract_candidates(
    text: str,
    source_hint: str,
    seen_titles: Optional[set] = None,
) -> List[HarvestCandidate]:
    """Extract memory candidates from text using pattern matching.

    Args:
        text: Text content to scan for patterns.
        source_hint: Source file path for candidate attribution.
        seen_titles: Optional shared set for cross-file deduplication.
            If provided, titles already in the set are skipped, and
            new titles are added to it.
    """
    candidates = []
    if seen_titles is None:
        seen_titles = set()

    # Pattern 1: Explicit LESSON: markers
    for match in _LESSON_PATTERN.finditer(text):
        title = match.group(1).strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            candidates.append(HarvestCandidate(
                suggested_type="lesson",
                title=title[:120],
                content=[title],
                rule=None,
                implication=title,
                source_hint=source_hint,
                extraction_reason="Explicit LESSON: marker",
            ))

    # Pattern 2: Explicit CONSTRAINT/INVARIANT: markers
    for match in _CONSTRAINT_PATTERN.finditer(text):
        title = match.group(1).strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            candidates.append(HarvestCandidate(
                suggested_type="constraint",
                title=title[:120],
                content=[title],
                rule=title if any(kw in title.upper() for kw in ("MUST", "NEVER", "ALWAYS")) else None,
                implication=None,
                source_hint=source_hint,
                extraction_reason="Explicit CONSTRAINT/INVARIANT: marker",
            ))

    # Pattern 3: Explicit DECISION: markers
    for match in _DECISION_PATTERN.finditer(text):
        title = match.group(1).strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            candidates.append(HarvestCandidate(
                suggested_type="decision",
                title=title[:120],
                content=[title],
                rule=None,
                implication=title,
                source_hint=source_hint,
                extraction_reason="Explicit DECISION: marker",
            ))

    # Pattern 4: WARNING/RISK markers
    for match in _WARNING_PATTERN.finditer(text):
        title = match.group(1).strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            candidates.append(HarvestCandidate(
                suggested_type="risk",
                title=title[:120],
                content=[title],
                rule=None,
                implication=title,
                source_hint=source_hint,
                extraction_reason="Explicit WARNING/RISK: marker",
            ))

    # Pattern 5: MUST/NEVER/ALWAYS statements (if not already captured)
    for match in _MUST_PATTERN.finditer(text):
        statement = match.group(1).strip()
        if statement and statement not in seen_titles:
            # Check not already captured by other patterns
            if not any(statement in c.title for c in candidates):
                seen_titles.add(statement)
                candidates.append(HarvestCandidate(
                    suggested_type="constraint",
                    title=statement[:120],
                    content=[statement],
                    rule=statement,
                    implication=None,
                    source_hint=source_hint,
                    extraction_reason="MUST/NEVER/ALWAYS statement",
                ))

    # Pattern 6: Error/Fix patterns → lesson candidates
    for match in _ERROR_FIX_PATTERN.finditer(text):
        title = match.group(1).strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            candidates.append(HarvestCandidate(
                suggested_type="lesson",
                title=title[:120],
                content=[title],
                rule=None,
                implication=title,
                source_hint=source_hint,
                extraction_reason="Error/Fix pattern",
            ))

    return candidates


# ---------------------------------------------------------------------------
# Auto-harvest automation (V3 M10 — closed-loop plan sessions)
# ---------------------------------------------------------------------------

def _hash8(text: str) -> str:
    """Generate first 8 chars of SHA-256 hex digest."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _sanitize_anchor(source_hint: str) -> str:
    """Convert a source path to a valid ID anchor component.

    Example: ".memory/working/findings.md" → "working_findings"
    """
    # Take filename without extension + parent dir name
    p = Path(source_hint)
    stem = p.stem.lower()
    parent = p.parent.name.lower() if p.parent.name else ""

    anchor = f"{parent}_{stem}" if parent else stem
    # Keep only [a-z0-9_]
    anchor = re.sub(r"[^a-z0-9_]", "", anchor)
    # Collapse runs of underscores and strip edges
    anchor = re.sub(r"_+", "_", anchor).strip("_")
    return anchor or "session"


def _convert_candidate_to_entry(
    candidate: HarvestCandidate,
    project_root: Path,
) -> dict:
    """Convert a HarvestCandidate to a full EFM schema entry.

    Generates a complete entry with proper id, type, classification,
    severity, source, tags, and timestamps.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry_type = candidate.suggested_type

    # Classification: constraint/risk → hard, rest → soft
    if entry_type in ("constraint", "risk"):
        classification = "hard"
    else:
        classification = "soft"

    # Severity heuristic
    severity = None
    rule_text = candidate.rule or ""
    if any(kw in rule_text.upper() for kw in ("MUST", "NEVER", "ALWAYS")):
        severity = "S1"
    elif entry_type == "risk":
        severity = "S2"
    elif classification == "hard":
        severity = "S2"

    # ID generation: {type}-{anchor}-{hash8}
    anchor = _sanitize_anchor(candidate.source_hint)
    hash_input = candidate.title + candidate.source_hint
    entry_id = f"{entry_type}-{anchor}-{_hash8(hash_input)}"

    # Source normalization
    source_path = candidate.source_hint
    if project_root and source_path.startswith(str(project_root)):
        source_path = str(Path(source_path).relative_to(project_root))
    source = [f"{source_path}:L0-L0"]

    # Content: ensure 2-6 items
    content = list(candidate.content)
    if candidate.extraction_reason and candidate.extraction_reason not in content:
        content.append(f"Extracted via: {candidate.extraction_reason}")
    content = content[:6]
    if len(content) < 2:
        content.append(f"Auto-harvested from working memory session")

    return {
        "id": entry_id,
        "type": entry_type,
        "classification": classification,
        "severity": severity,
        "title": candidate.title[:120],
        "content": content,
        "rule": candidate.rule,
        "implication": candidate.implication,
        "verify": None,
        "source": source,
        "tags": _extract_tags(candidate.title, content),
        "created_at": now,
        "last_verified": None,
        "deprecated": False,
        "_meta": {"auto_harvested": True},
    }


def _extract_tags(title: str, content: List[str]) -> List[str]:
    """Extract keyword tags from title and content."""
    text = (title + " " + " ".join(content)).lower()
    # Split on non-alphanumeric
    words = re.findall(r"[a-z]{3,}", text)
    # Filter common stop words
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "are",
        "was", "were", "has", "have", "had", "not", "but", "can",
        "will", "should", "must", "never", "always", "when", "before",
        "after", "into", "all", "each", "every", "any", "use", "using",
        "auto", "harvested", "working", "memory", "session", "extracted",
    }
    # Deduplicate preserving order, skip stop words
    seen: set = set()
    tags = []
    for w in words:
        if w not in stop and w not in seen:
            seen.add(w)
            tags.append(w)
    return tags[:5]


def auto_harvest_and_persist(
    working_dir: Path,
    events_path: Path,
    project_root: Path,
    config: dict,
    run_pipeline_after: bool = True,
) -> dict:
    """Full closed-loop automation: harvest → convert → write → pipeline → clear.

    Steps:
      1. harvest_session() → candidates
      2. Convert each to a proper schema entry
      3. Validate schema, skip invalid
      4. Append valid entries to events.jsonl
      5. Optionally run pipeline (sync + generate_rules)
      6. clear_session()

    Returns summary dict with counts and errors.
    """
    result = {
        "candidates_found": 0,
        "entries_written": 0,
        "entries_skipped": 0,
        "pipeline_run": False,
        "session_cleared": False,
        "errors": [],
    }

    # Step 1: Harvest
    try:
        harvest_report = harvest_session(working_dir, events_path, config)
        result["candidates_found"] = len(harvest_report.candidates)
    except Exception as e:
        result["errors"].append(f"Harvest failed: {e}")
        return result

    if not harvest_report.candidates:
        # No candidates — just clear
        result["session_cleared"] = clear_session(working_dir)
        return result

    # Step 2-3: Convert, validate, and dedup against existing entries
    from .auto_verify import validate_schema, check_duplicates, _load_entries_latest_wins

    dedup_threshold = config.get("automation", {}).get("dedup_threshold", 0.85)
    # Pre-load entries once to avoid re-reading events.jsonl per candidate
    preloaded = _load_entries_latest_wins(events_path) if events_path.exists() else {}

    valid_entries = []
    for candidate in harvest_report.candidates:
        try:
            entry = _convert_candidate_to_entry(candidate, project_root)
            # Schema validation
            vr = validate_schema(entry)
            if not vr.valid:
                result["entries_skipped"] += 1
                logger.warning(
                    f"Skipped invalid entry '{candidate.title}': "
                    f"{[c.message for c in vr.checks if not c.passed]}"
                )
                continue

            # Dedup check against existing events.jsonl
            dedup = check_duplicates(
                entry, events_path, dedup_threshold,
                _preloaded_entries=preloaded,
            )
            if dedup.is_duplicate:
                result["entries_skipped"] += 1
                logger.info(
                    f"Skipped duplicate entry '{candidate.title}': "
                    f"similar to {dedup.similar_entries[0][0]} "
                    f"({dedup.similar_entries[0][1]:.0%})"
                )
                continue

            valid_entries.append(entry)
        except Exception as e:
            result["entries_skipped"] += 1
            result["errors"].append(f"Convert failed for '{candidate.title}': {e}")

    # Step 4: Write to events.jsonl
    if valid_entries:
        try:
            with open(events_path, "a") as f:
                for entry in valid_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            result["entries_written"] = len(valid_entries)
        except Exception as e:
            result["errors"].append(f"Write failed: {e}")

    # Step 5: Run pipeline
    if run_pipeline_after and result["entries_written"] > 0:
        try:
            from .auto_sync import run_pipeline
            run_pipeline(events_path, config, project_root)
            result["pipeline_run"] = True
        except Exception as e:
            result["errors"].append(f"Pipeline failed: {e}")

    # Step 6: Clear session
    result["session_cleared"] = clear_session(working_dir)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_field(text: str, field_name: str) -> str:
    """Extract a **Field**: Value from markdown text."""
    pattern = re.compile(rf"\*\*{field_name}\*\*\s*[:：]\s*(.+)")
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _count_phases(plan_text: str) -> Tuple[int, int]:
    """Count total phases and completed phases in task_plan.md."""
    total = 0
    done = 0
    in_phases = False
    for line in plan_text.splitlines():
        if line.strip().startswith("## Phases"):
            in_phases = True
            continue
        if in_phases and line.strip().startswith("## "):
            break  # Next section
        if in_phases and line.strip().startswith("### Phase"):
            total += 1
            # Phase is done when header contains [DONE] marker
            if "[DONE]" in line or "[done]" in line:
                done += 1
    return total, done


def _get_current_phase(plan_text: str) -> str:
    """Get the name of the current (first uncompleted) phase."""
    in_phases = False
    for line in plan_text.splitlines():
        if line.strip().startswith("## Phases"):
            in_phases = True
            continue
        if in_phases and line.strip().startswith("## "):
            break
        if in_phases and line.strip().startswith("### Phase"):
            if "[DONE]" not in line and "[done]" not in line:
                # Extract phase name
                name = line.strip().lstrip("#").strip()
                return name
    return "Unknown"
