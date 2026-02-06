"""
EF Memory V2 — Test Fixtures

Shared sample data and mock objects for unit tests.

Run from project root:
    python3 -m unittest discover -s .memory/tests -v
"""

import sys
import math
import hashlib
from pathlib import Path
from dataclasses import dataclass
from typing import List

# Ensure .memory/ is on the import path so 'lib' is importable
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))


# Sample memory entries matching SCHEMA.md
SAMPLE_ENTRIES = [
    {
        "id": "lesson-inc036-a3f8c2d1",
        "type": "lesson",
        "classification": "hard",
        "severity": "S1",
        "title": "Rolling statistics without shift(1) caused 999x backtest inflation",
        "content": [
            "42 rolling/ewm/pct_change calls missing shift(1) in feature engine",
            "Model learned to explain past, not predict future",
            "IC with T-5 returns (-0.115) > IC with T+1 returns (0.018)",
            "Backtest showed 49,979% return; after fix only 52%",
        ],
        "rule": "shift(1) MUST precede any rolling(), ewm(), pct_change() on price-derived data",
        "implication": "Backtest returns inflated 100-1000x; predictions structurally encode future information",
        "verify": "grep -rn 'rolling\\|ewm\\|pct_change' src/features/*.py | grep -v 'shift(1)'",
        "source": ["docs/decisions/INCIDENTS.md#INC-036:L553-L699"],
        "tags": ["leakage", "feature-engine", "shift", "rolling"],
        "created_at": "2026-02-01T14:30:00Z",
        "last_verified": None,
        "deprecated": False,
        "_meta": {},
    },
    {
        "id": "lesson-inc035-7b2e4f9a",
        "type": "lesson",
        "classification": "hard",
        "severity": "S1",
        "title": "Walk-Forward labels on full data caused 191x performance inflation",
        "content": [
            "Labels generated on full dataset before walk-forward split",
            "Training windows included future label information",
            "191x backtest inflation detected",
        ],
        "rule": "Labels MUST be generated inside each WF training window, then drop tail MAX_HORIZON rows",
        "implication": "All WF predictions invalid; model trained on future information",
        "source": ["docs/decisions/INCIDENTS.md#INC-035:L407-L498"],
        "tags": ["leakage", "walk-forward", "label"],
        "created_at": "2026-02-01T14:00:00Z",
        "last_verified": None,
        "deprecated": False,
        "_meta": {},
    },
    {
        "id": "fact-risk_adjusted-9c3a1e5f",
        "type": "fact",
        "classification": "soft",
        "severity": "S3",
        "title": "3K label uses dual-condition (return + drawdown), not just ATR breakout",
        "content": [
            "CLAUDE.md describes 3K as: close[t+3]/close[t] - 1 > ATR_14/close[t]",
            "Actual implementation uses create_return_drawdown_label(horizon=3)",
            "Dual conditions: future_return > 0.1% AND max_drawdown < 0.5%",
        ],
        "rule": None,
        "implication": "Stricter than documented; may affect threshold tuning expectations",
        "source": ["src/labels/risk_adjusted_labels.py:L93-L144"],
        "tags": ["label", "3k", "documentation"],
        "created_at": "2026-02-01T15:00:00Z",
        "last_verified": None,
        "deprecated": False,
        "_meta": {},
    },
]


@dataclass
class _EmbeddingResult:
    vector: List[float]
    model: str
    dimensions: int


class MockEmbedder:
    """Mock embedding provider for testing (returns deterministic vectors)."""

    def __init__(self, dimensions: int = 768):
        self._dims = dimensions

    @property
    def provider_id(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return "mock-embed-v1"

    @property
    def dimensions(self) -> int:
        return self._dims

    def embed_documents(self, texts):
        """Return deterministic vectors based on text hash."""
        return [
            _EmbeddingResult(
                vector=self._text_to_vector(text),
                model="mock-embed-v1",
                dimensions=self._dims,
            )
            for text in texts
        ]

    def embed_query(self, text):
        return _EmbeddingResult(
            vector=self._text_to_vector(text),
            model="mock-embed-v1",
            dimensions=self._dims,
        )

    def _text_to_vector(self, text: str) -> List[float]:
        """Generate a deterministic vector from text using simple hashing."""
        h = hashlib.sha256(text.encode()).digest()
        vec = [0.0] * self._dims
        for i in range(self._dims):
            byte_idx = i % len(h)
            vec[i] = (h[byte_idx] - 128) / 128.0
            vec[i] += (i % 7 - 3) * 0.01
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec
