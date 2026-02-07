"""
Tests for EF Memory V2 — Embedder Factory + Helpers

Covers: create_embedder, _resolve_api_key, empty-input guards on MockEmbedder.
Provider classes (Gemini/OpenAI/Ollama) require external SDKs and are not tested here.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Import path setup
_MEMORY_DIR = Path(__file__).resolve().parent.parent
if str(_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_MEMORY_DIR))

from lib.embedder import create_embedder, _resolve_api_key
from tests.conftest import MockEmbedder


class TestResolveApiKey(unittest.TestCase):

    def test_returns_env_var_value(self):
        with patch.dict("os.environ", {"MY_API_KEY": "secret123"}):
            result = _resolve_api_key({"api_key_env": "MY_API_KEY"})
            self.assertEqual(result, "secret123")

    def test_returns_none_when_env_var_missing(self):
        result = _resolve_api_key({"api_key_env": "NONEXISTENT_KEY_12345"})
        self.assertIsNone(result)

    def test_returns_none_when_no_env_key_configured(self):
        result = _resolve_api_key({})
        self.assertIsNone(result)


class TestCreateEmbedder(unittest.TestCase):

    def test_disabled_returns_none(self):
        result = create_embedder({"enabled": False})
        self.assertIsNone(result)

    def test_disabled_by_default(self):
        result = create_embedder({})
        self.assertIsNone(result)

    def test_unknown_provider_returns_none(self):
        config = {
            "enabled": True,
            "provider": "nonexistent_provider_xyz",
            "fallback": [],
        }
        result = create_embedder(config)
        self.assertIsNone(result)

    def test_import_error_graceful(self):
        """Providers whose SDK is not installed should be skipped."""
        config = {
            "enabled": True,
            "provider": "gemini",
            "fallback": ["openai", "ollama"],
            "providers": {},
        }
        result = create_embedder(config)
        self.assertIsNone(result)

    def test_fallback_chain_skips_duplicate_primary(self):
        config = {
            "enabled": True,
            "provider": "gemini",
            "fallback": ["gemini", "openai"],
            "providers": {},
        }
        result = create_embedder(config)
        self.assertIsNone(result)


class TestMockEmbedderEmptyGuard(unittest.TestCase):

    def test_embed_documents_empty_list(self):
        embedder = MockEmbedder(dimensions=8)
        result = embedder.embed_documents([])
        self.assertEqual(result, [])

    def test_embed_documents_nonempty(self):
        embedder = MockEmbedder(dimensions=8)
        result = embedder.embed_documents(["hello", "world"])
        self.assertEqual(len(result), 2)
        self.assertEqual(len(result[0].vector), 8)


if __name__ == "__main__":
    unittest.main()
