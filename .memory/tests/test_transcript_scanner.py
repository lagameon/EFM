"""
Tests for EF Memory V3 — Transcript Scanner

Covers: read_transcript_messages, scan_conversation_for_drafts
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Import path setup
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.transcript_scanner import (
    _MAX_TRANSCRIPT_BYTES,
    _strip_rules_echo,
    read_transcript_messages,
    scan_conversation_for_drafts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_transcript_line(role: str, text: str) -> str:
    """Create a single JSONL line in Claude Code transcript format."""
    return json.dumps({
        "type": role,
        "message": {
            "content": [{"type": "text", "text": text}],
        },
    })


def _make_assistant_with_tool_use(text: str, tool_name: str = "Read") -> str:
    """Create an assistant line with both text and tool_use content blocks."""
    return json.dumps({
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": text},
                {"type": "tool_use", "id": "tu_123", "name": tool_name, "input": {}},
            ],
        },
    })


def _write_transcript(tmpdir: str, lines: list) -> Path:
    """Write JSONL lines to a temporary transcript file."""
    path = Path(tmpdir) / "transcript.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests: read_transcript_messages
# ---------------------------------------------------------------------------

class TestReadTranscriptMessages(unittest.TestCase):

    def test_read_empty_file(self):
        """Empty JSONL returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            result = read_transcript_messages(path)
            self.assertEqual(result, [])

    def test_read_extracts_assistant_text(self):
        """Parses assistant messages and extracts text content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_transcript_line("assistant", "Hello, I can help with that."),
                _make_transcript_line("assistant", "LESSON: Always validate inputs."),
            ]
            path = _write_transcript(tmpdir, lines)
            result = read_transcript_messages(path)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0], "Hello, I can help with that.")
            self.assertEqual(result[1], "LESSON: Always validate inputs.")

    def test_read_skips_non_assistant(self):
        """User/system messages are ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_transcript_line("human", "Help me fix this bug"),
                _make_transcript_line("assistant", "LESSON: Check null refs first"),
                _make_transcript_line("human", "Thanks!"),
            ]
            path = _write_transcript(tmpdir, lines)
            result = read_transcript_messages(path)
            self.assertEqual(len(result), 1)
            self.assertIn("LESSON", result[0])

    def test_read_handles_missing_file(self):
        """Missing file returns empty list gracefully."""
        result = read_transcript_messages(Path("/nonexistent/transcript.jsonl"))
        self.assertEqual(result, [])

    def test_read_handles_tool_use_content(self):
        """Extracts only text blocks, skips tool_use blocks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_assistant_with_tool_use("Let me read that file."),
            ]
            path = _write_transcript(tmpdir, lines)
            result = read_transcript_messages(path)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0], "Let me read that file.")

    def test_read_skips_large_file(self):
        """Files larger than 10MB are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "huge.jsonl"
            # Create a file just over the limit
            with open(path, "w") as f:
                f.truncate(_MAX_TRANSCRIPT_BYTES + 1)
            result = read_transcript_messages(path)
            self.assertEqual(result, [])

    def test_read_handles_malformed_json(self):
        """Malformed JSON lines are skipped without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                "not valid json",
                _make_transcript_line("assistant", "Valid message"),
                "{broken: true",
            ]
            path = _write_transcript(tmpdir, lines)
            result = read_transcript_messages(path)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0], "Valid message")

    def test_read_handles_string_content(self):
        """Handles messages where content is a plain string (not array)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            line = json.dumps({
                "type": "assistant",
                "message": {"content": "Plain string content"},
            })
            path = _write_transcript(tmpdir, [line])
            result = read_transcript_messages(path)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0], "Plain string content")


# ---------------------------------------------------------------------------
# Tests: scan_conversation_for_drafts
# ---------------------------------------------------------------------------

class TestScanConversationForDrafts(unittest.TestCase):

    def _make_config(self) -> dict:
        """Minimal config for testing."""
        return {
            "v3": {
                "auto_draft_from_conversation": True,
                "working_memory_dir": ".memory/working",
            },
            "automation": {
                "human_review_required": True,
            },
        }

    def test_scan_finds_lesson_marker(self):
        """LESSON: marker in assistant text creates a draft."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_transcript_line("assistant", "After investigating, LESSON: always check null references before dereferencing pointers"),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, Path(tmpdir), self._make_config()
            )

            self.assertEqual(result["candidates_found"], 1)
            self.assertEqual(result["drafts_created"], 1)
            self.assertIn("lesson", result["draft_types"])

    def test_scan_finds_constraint_marker(self):
        """CONSTRAINT: marker creates a constraint draft."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_transcript_line("assistant", "CONSTRAINT: MUST validate all user input before database queries"),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, Path(tmpdir), self._make_config()
            )

            self.assertGreaterEqual(result["candidates_found"], 1)
            self.assertGreaterEqual(result["drafts_created"], 1)
            self.assertIn("constraint", result["draft_types"])

    def test_scan_finds_must_never(self):
        """MUST/NEVER statements create constraint drafts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_transcript_line("assistant", "This is important: MUST validate input before sending to the API endpoint always"),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, Path(tmpdir), self._make_config()
            )

            self.assertGreaterEqual(result["candidates_found"], 1)
            self.assertGreaterEqual(result["drafts_created"], 1)

    def test_scan_finds_error_fix(self):
        """Error/Fix patterns create lesson drafts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_transcript_line("assistant", "Fix: the root cause was a missing await on the async database call"),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, Path(tmpdir), self._make_config()
            )

            self.assertGreaterEqual(result["candidates_found"], 1)
            self.assertGreaterEqual(result["drafts_created"], 1)

    def test_scan_no_matches(self):
        """Clean conversation without markers produces 0 drafts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_transcript_line("assistant", "Sure, I can help with that."),
                _make_transcript_line("assistant", "Here is the updated code."),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, Path(tmpdir), self._make_config()
            )

            self.assertEqual(result["candidates_found"], 0)
            self.assertEqual(result["drafts_created"], 0)
            # drafts_dir should not exist since no drafts were created
            # (create_draft creates it, but we never called it)

    def test_scan_creates_draft_files(self):
        """Verify .memory/drafts/*.json files are actually created with valid content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_transcript_line("assistant", "LESSON: always run tests before committing code changes"),
                _make_transcript_line("assistant", "DECISION: use pytest instead of unittest for this project"),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, Path(tmpdir), self._make_config()
            )

            self.assertEqual(result["drafts_created"], 2)

            # Verify draft files exist
            draft_files = list(drafts_dir.glob("*.json"))
            self.assertEqual(len(draft_files), 2)

            # Verify each draft has valid JSON with required fields
            for draft_file in draft_files:
                content = json.loads(draft_file.read_text())
                self.assertIn("id", content)
                self.assertIn("type", content)
                self.assertIn("title", content)
                self.assertIn("content", content)
                self.assertIn("classification", content)
                self.assertIn("_meta", content)
                self.assertEqual(content["_meta"]["draft_status"], "pending")

    def test_scan_empty_transcript(self):
        """Empty transcript returns zero candidates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, Path(tmpdir), self._make_config()
            )

            self.assertEqual(result["candidates_found"], 0)
            self.assertEqual(result["drafts_created"], 0)

    def test_scan_missing_transcript(self):
        """Missing transcript file returns zero candidates gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nonexistent.jsonl"
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, Path(tmpdir), self._make_config()
            )

            self.assertEqual(result["candidates_found"], 0)
            self.assertEqual(result["drafts_created"], 0)
            self.assertEqual(result["errors"], [])

    def test_scan_deduplicates_same_pattern(self):
        """Same pattern appearing twice produces only one draft."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_transcript_line("assistant", "LESSON: always check permissions first"),
                _make_transcript_line("assistant", "As I said, LESSON: always check permissions first"),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, Path(tmpdir), self._make_config()
            )

            # _extract_candidates deduplicates by title
            self.assertEqual(result["candidates_found"], 1)
            self.assertEqual(result["drafts_created"], 1)

    def test_scan_source_attribution(self):
        """Draft entries include conversation source attribution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lines = [
                _make_transcript_line("assistant", "LESSON: always backup before migrations"),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            scan_conversation_for_drafts(
                path, drafts_dir, Path(tmpdir), self._make_config()
            )

            draft_files = list(drafts_dir.glob("*.json"))
            self.assertEqual(len(draft_files), 1)
            content = json.loads(draft_files[0].read_text())
            # Source should reference the conversation
            self.assertTrue(any("conversation:" in s for s in content.get("source", [])))


# ---------------------------------------------------------------------------
# Tests: _strip_rules_echo
# ---------------------------------------------------------------------------

class TestStripRulesEcho(unittest.TestCase):

    def test_removes_auto_inject_marker(self):
        """Lines with <!-- EF Memory Auto-Inject are stripped with their block."""
        text = (
            "Normal discussion about the code.\n"
            "\n"
            "<!-- EF Memory Auto-Inject | DO NOT EDIT MANUALLY -->\n"
            "**Memory:** `shift(1) MUST precede rolling()`\n"
            "**Implication:** Backtest results inflated 100x\n"
            "\n"
            "Back to normal discussion."
        )
        result = _strip_rules_echo(text)
        self.assertNotIn("Auto-Inject", result)
        self.assertNotIn("shift(1) MUST precede", result)
        self.assertNotIn("Implication", result)
        self.assertIn("Normal discussion about the code.", result)
        self.assertIn("Back to normal discussion.", result)

    def test_removes_auto_generated_marker(self):
        """Lines with (Auto-generated from Memory) are stripped."""
        text = (
            "Reviewing the rules:\n"
            "(Auto-generated from Memory)\n"
            "Some rule content here\n"
            "\n"
            "Continuing work."
        )
        result = _strip_rules_echo(text)
        self.assertNotIn("Auto-generated", result)
        self.assertNotIn("Some rule content", result)
        self.assertIn("Reviewing the rules:", result)
        self.assertIn("Continuing work.", result)

    def test_removes_memory_marker(self):
        """Lines with **Memory:** ` are stripped with block."""
        text = (
            "Looking at the rules:\n"
            "**Memory:** `Always validate input`\n"
            "**Implication:** Security risk\n"
            "\n"
            "Now let's write code."
        )
        result = _strip_rules_echo(text)
        self.assertNotIn("Memory:", result)
        self.assertNotIn("validate input", result)
        self.assertIn("Looking at the rules:", result)
        self.assertIn("Now let's write code.", result)

    def test_preserves_normal_text(self):
        """Text without any rules markers is preserved unchanged."""
        text = (
            "LESSON: Always run tests before merging.\n"
            "\n"
            "MUST validate input before database queries.\n"
            "\n"
            "The fix was to add a null check."
        )
        result = _strip_rules_echo(text)
        self.assertEqual(result, text)

    def test_preserves_similar_but_different_text(self):
        """Text that mentions 'Memory' or 'Implication' without exact marker format is kept."""
        text = (
            "The memory usage was high.\n"
            "Implication of the bug was data loss.\n"
            "Auto-generated reports are useful."
        )
        result = _strip_rules_echo(text)
        # These should be preserved since they don't match exact marker patterns
        self.assertIn("memory usage", result)
        # "Implication of" doesn't match "**Implication:**" pattern
        self.assertIn("Implication of the bug", result)

    def test_multiple_rule_blocks_stripped(self):
        """Multiple injected rule blocks are all removed."""
        text = (
            "First discussion.\n"
            "\n"
            "<!-- EF Memory Auto-Inject | DO NOT EDIT MANUALLY -->\n"
            "**Memory:** `Rule one`\n"
            "\n"
            "Middle discussion.\n"
            "\n"
            "<!-- EF Memory Auto-Inject | DO NOT EDIT MANUALLY -->\n"
            "**Memory:** `Rule two`\n"
            "\n"
            "End discussion."
        )
        result = _strip_rules_echo(text)
        self.assertNotIn("Rule one", result)
        self.assertNotIn("Rule two", result)
        self.assertIn("First discussion.", result)
        self.assertIn("Middle discussion.", result)
        self.assertIn("End discussion.", result)

    def test_empty_text(self):
        """Empty string returns empty string."""
        self.assertEqual(_strip_rules_echo(""), "")


# ---------------------------------------------------------------------------
# Tests: scan dedup against events.jsonl
# ---------------------------------------------------------------------------

class TestScanDedupAgainstEvents(unittest.TestCase):

    def _make_config(self) -> dict:
        return {
            "v3": {
                "auto_draft_from_conversation": True,
                "working_memory_dir": ".memory/working",
            },
            "automation": {
                "human_review_required": True,
                "dedup_threshold": 0.85,
            },
        }

    def test_scan_skips_existing_events(self):
        """Candidates that already exist in events.jsonl are not drafted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Pre-create events.jsonl with an existing entry
            # The dedup comparison uses build_dedup_text (title | rule | source).
            # _extract_candidates for "LESSON: X" produces title=X, rule=None.
            # So the existing entry should also have no rule and matching title
            # for the similarity to exceed the 0.85 threshold.
            events_dir = project_root / ".memory"
            events_dir.mkdir(parents=True)
            events_path = events_dir / "events.jsonl"

            existing_entry = {
                "id": "lesson-test01-aaaabbbb",
                "type": "lesson",
                "classification": "soft",
                "severity": "S3",
                "title": "always validate input before processing data",
                "content": ["Validate all input"],
                "rule": None,
                "source": ["conversation:old"],
                "tags": ["validation"],
                "created_at": "2026-02-01T00:00:00Z",
                "deprecated": False,
                "_meta": {},
            }
            events_path.write_text(
                json.dumps(existing_entry) + "\n", encoding="utf-8"
            )

            # Transcript contains the same lesson text
            lines = [
                _make_transcript_line(
                    "assistant",
                    "LESSON: always validate input before processing data"
                ),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, project_root, self._make_config()
            )

            # Should find the candidate but skip it as duplicate
            self.assertGreaterEqual(result["candidates_found"], 1)
            self.assertEqual(result["drafts_created"], 0)

    def test_scan_creates_draft_for_new_content(self):
        """Content NOT in events.jsonl should create a draft."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create empty events.jsonl
            events_dir = project_root / ".memory"
            events_dir.mkdir(parents=True)
            (events_dir / "events.jsonl").write_text("", encoding="utf-8")

            lines = [
                _make_transcript_line(
                    "assistant",
                    "LESSON: always backup database before running migrations"
                ),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, project_root, self._make_config()
            )

            self.assertGreaterEqual(result["candidates_found"], 1)
            self.assertGreaterEqual(result["drafts_created"], 1)


# ---------------------------------------------------------------------------
# Tests: scan dedup against existing drafts
# ---------------------------------------------------------------------------

class TestScanDedupAgainstDrafts(unittest.TestCase):

    def _make_config(self) -> dict:
        return {
            "v3": {
                "auto_draft_from_conversation": True,
                "working_memory_dir": ".memory/working",
            },
            "automation": {
                "human_review_required": True,
                "dedup_threshold": 0.85,
            },
        }

    def test_scan_skips_existing_drafts(self):
        """Candidates with same title as pending draft are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create .memory dir with empty events.jsonl
            events_dir = project_root / ".memory"
            events_dir.mkdir(parents=True)
            (events_dir / "events.jsonl").write_text("", encoding="utf-8")

            # The exact LESSON text will become the candidate title.
            # "LESSON: always check permissions first" → title = "always check permissions first"
            lesson_text = "always check permissions first"

            # Pre-create a pending draft with that exact title
            drafts_dir = Path(tmpdir) / "drafts"
            drafts_dir.mkdir(parents=True)
            existing_draft = {
                "id": "lesson-existing-12345678",
                "type": "lesson",
                "classification": "soft",
                "severity": "S3",
                "title": lesson_text,
                "content": ["Check permissions"],
                "rule": None,
                "source": ["conversation:old"],
                "tags": ["permissions"],
                "created_at": "2026-02-01T00:00:00Z",
                "deprecated": False,
                "_meta": {"draft_status": "pending"},
            }
            draft_path = drafts_dir / "draft_lesson-existing-12345678.json"
            draft_path.write_text(json.dumps(existing_draft), encoding="utf-8")

            # Transcript with the exact same lesson text
            lines = [
                _make_transcript_line(
                    "assistant",
                    f"LESSON: {lesson_text}"
                ),
            ]
            path = _write_transcript(tmpdir, lines)

            result = scan_conversation_for_drafts(
                path, drafts_dir, project_root, self._make_config()
            )

            # Should find the candidate but skip it (matching draft title)
            self.assertGreaterEqual(result["candidates_found"], 1)
            self.assertEqual(result["drafts_created"], 0)


# ---------------------------------------------------------------------------
# Tests: rules echo filtering in scan pipeline
# ---------------------------------------------------------------------------

class TestScanRulesEchoFiltering(unittest.TestCase):

    def _make_config(self) -> dict:
        return {
            "v3": {
                "auto_draft_from_conversation": True,
                "working_memory_dir": ".memory/working",
            },
            "automation": {
                "human_review_required": True,
                "dedup_threshold": 0.85,
            },
        }

    def test_scan_filters_rules_echo(self):
        """Rules injected via auto-inject markers are not captured as candidates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            events_dir = project_root / ".memory"
            events_dir.mkdir(parents=True)
            (events_dir / "events.jsonl").write_text("", encoding="utf-8")

            # Transcript where assistant echoes injected rules
            lines = [
                _make_transcript_line(
                    "assistant",
                    "I see these project rules:\n"
                    "<!-- EF Memory Auto-Inject | DO NOT EDIT MANUALLY -->\n"
                    "**Memory:** `MUST validate input before database queries`\n"
                    "**Implication:** SQL injection risk\n"
                    "\n"
                    "Let me follow these rules."
                ),
            ]
            path = _write_transcript(tmpdir, lines)
            drafts_dir = Path(tmpdir) / "drafts"

            result = scan_conversation_for_drafts(
                path, drafts_dir, project_root, self._make_config()
            )

            # The MUST statement was inside a rules echo block — should be filtered
            self.assertEqual(result["candidates_found"], 0)
            self.assertEqual(result["drafts_created"], 0)


if __name__ == "__main__":
    unittest.main()
