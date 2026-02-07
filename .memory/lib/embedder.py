"""
EF Memory V2 — Embedding Provider Abstraction

Unified interface for Gemini, OpenAI, and Ollama embedding providers.
Each provider SDK is lazily imported via try/except — install only what you need.

Usage:
    from embedder import create_embedder
    embedder = create_embedder(config["embedding"])
    # Returns None if no provider is available (graceful degradation)
"""

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("efm.embedder")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class EmbeddingResult:
    """Result of an embedding operation."""
    vector: List[float]
    model: str
    dimensions: int


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class EmbeddingProvider(ABC):
    """
    Base class for all embedding providers.

    Subclasses must implement:
    - embed_documents(): batch embedding for indexing
    - embed_query(): single embedding for search
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Provider identifier: 'gemini', 'openai', 'ollama'."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model name used for embedding."""
        ...

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Output vector dimensions."""
        ...

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[EmbeddingResult]:
        """Embed a batch of documents for indexing (RETRIEVAL_DOCUMENT)."""
        ...

    @abstractmethod
    def embed_query(self, text: str) -> EmbeddingResult:
        """Embed a single query for retrieval (RETRIEVAL_QUERY)."""
        ...

    def embed_for_similarity(self, text: str) -> EmbeddingResult:
        """
        Embed for semantic similarity comparison (dedup).
        Default: delegates to embed_query(). Override if provider
        supports a dedicated similarity task type (e.g., Gemini).
        """
        return self.embed_query(text)


# ---------------------------------------------------------------------------
# Gemini Provider
# ---------------------------------------------------------------------------

class GeminiEmbedder(EmbeddingProvider):
    """
    Google Gemini embedding via google-genai SDK.

    Features:
    - Asymmetric task types (RETRIEVAL_DOCUMENT vs RETRIEVAL_QUERY)
    - Matryoshka dimensions (768 recommended for EF Memory)
    - SEMANTIC_SIMILARITY task type for dedup
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-embedding-001",
        dims: int = 768,
    ):
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise ImportError(
                "Gemini embeddings require the google-genai package.\n"
                "Install with: pip install google-genai"
            )

        resolved_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Gemini API key not found. Set GOOGLE_API_KEY or GEMINI_API_KEY "
                "environment variable, or pass api_key directly."
            )

        self._client = genai.Client(api_key=resolved_key)
        self._types = types
        self._model = model
        self._dims = dims

    @property
    def provider_id(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dims

    def embed_documents(self, texts: List[str]) -> List[EmbeddingResult]:
        if not texts:
            return []
        result = self._client.models.embed_content(
            model=self._model,
            contents=texts,
            config=self._types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=self._dims,
            ),
        )
        return [
            EmbeddingResult(
                vector=emb.values,
                model=self._model,
                dimensions=self._dims,
            )
            for emb in result.embeddings
        ]

    def embed_query(self, text: str) -> EmbeddingResult:
        result = self._client.models.embed_content(
            model=self._model,
            contents=text,
            config=self._types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=self._dims,
            ),
        )
        return EmbeddingResult(
            vector=result.embeddings[0].values,
            model=self._model,
            dimensions=self._dims,
        )

    def embed_for_similarity(self, text: str) -> EmbeddingResult:
        result = self._client.models.embed_content(
            model=self._model,
            contents=text,
            config=self._types.EmbedContentConfig(
                task_type="SEMANTIC_SIMILARITY",
                output_dimensionality=self._dims,
            ),
        )
        return EmbeddingResult(
            vector=result.embeddings[0].values,
            model=self._model,
            dimensions=self._dims,
        )


# ---------------------------------------------------------------------------
# OpenAI Provider
# ---------------------------------------------------------------------------

class OpenAIEmbedder(EmbeddingProvider):
    """
    OpenAI embedding via openai SDK.

    Models:
    - text-embedding-3-small: 1536 dims (default)
    - text-embedding-3-large: 3072 dims
    """

    DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "text-embedding-3-small",
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "OpenAI embeddings require the openai package.\n"
                "Install with: pip install openai"
            )

        resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not resolved_key:
            raise ValueError(
                "OpenAI API key not found. Set OPENAI_API_KEY "
                "environment variable, or pass api_key directly."
            )

        self._client = OpenAI(api_key=resolved_key)
        self._model = model
        if model not in self.DIMENSIONS:
            logger.warning(
                f"Unknown OpenAI model '{model}' — dimensions will be "
                f"inferred from first embedding call"
            )
        self._dims = self.DIMENSIONS.get(model, 0)  # 0 = infer on first call
        self._dims_inferred = model in self.DIMENSIONS

    @property
    def provider_id(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dims

    def _maybe_infer_dims(self, vector: list) -> None:
        """Infer dimensions from actual vector on first call if needed."""
        if not self._dims_inferred:
            self._dims = len(vector)
            self._dims_inferred = True
            logger.info(f"OpenAI model '{self._model}' inferred dimensions: {self._dims}")

    def embed_documents(self, texts: List[str]) -> List[EmbeddingResult]:
        if not texts:
            return []
        response = self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        results = []
        for item in response.data:
            self._maybe_infer_dims(item.embedding)
            results.append(EmbeddingResult(
                vector=item.embedding,
                model=self._model,
                dimensions=self._dims,
            ))
        return results

    def embed_query(self, text: str) -> EmbeddingResult:
        response = self._client.embeddings.create(
            model=self._model,
            input=text,
        )
        vec = response.data[0].embedding
        self._maybe_infer_dims(vec)
        return EmbeddingResult(
            vector=vec,
            model=self._model,
            dimensions=self._dims,
        )


# ---------------------------------------------------------------------------
# Ollama Provider
# ---------------------------------------------------------------------------

class OllamaEmbedder(EmbeddingProvider):
    """
    Ollama local embedding via ollama SDK.

    Models:
    - nomic-embed-text: 768 dims (default)
    - mxbai-embed-large: 1024 dims
    """

    DIMENSIONS = {
        "nomic-embed-text": 768,
        "mxbai-embed-large": 1024,
    }

    def __init__(
        self,
        model: str = "nomic-embed-text",
        host: str = "http://localhost:11434",
    ):
        try:
            import ollama as ollama_sdk
        except ImportError:
            raise ImportError(
                "Ollama embeddings require the ollama package.\n"
                "Install with: pip install ollama"
            )

        self._client = ollama_sdk.Client(host=host)
        self._model = model
        if model not in self.DIMENSIONS:
            logger.warning(
                f"Unknown Ollama model '{model}' — dimensions will be "
                f"inferred from first embedding call"
            )
        self._dims = self.DIMENSIONS.get(model, 0)  # 0 = infer on first call
        self._dims_inferred = model in self.DIMENSIONS

    @property
    def provider_id(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dims

    def _maybe_infer_dims(self, vector: list) -> None:
        """Infer dimensions from actual vector on first call if needed."""
        if not self._dims_inferred:
            self._dims = len(vector)
            self._dims_inferred = True
            logger.info(f"Ollama model '{self._model}' inferred dimensions: {self._dims}")

    def embed_documents(self, texts: List[str]) -> List[EmbeddingResult]:
        if not texts:
            return []
        results = []
        for text in texts:
            response = self._client.embed(model=self._model, input=text)
            vec = response["embeddings"][0]
            self._maybe_infer_dims(vec)
            results.append(
                EmbeddingResult(
                    vector=vec,
                    model=self._model,
                    dimensions=self._dims,
                )
            )
        return results

    def embed_query(self, text: str) -> EmbeddingResult:
        response = self._client.embed(model=self._model, input=text)
        vec = response["embeddings"][0]
        self._maybe_infer_dims(vec)
        return EmbeddingResult(
            vector=vec,
            model=self._model,
            dimensions=self._dims,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_PROVIDER_CONSTRUCTORS = {
    "gemini": lambda cfg: GeminiEmbedder(
        api_key=_resolve_api_key(cfg),
        model=cfg.get("model", "gemini-embedding-001"),
        dims=cfg.get("dimensions", 768),
    ),
    "openai": lambda cfg: OpenAIEmbedder(
        api_key=_resolve_api_key(cfg),
        model=cfg.get("model", "text-embedding-3-small"),
    ),
    "ollama": lambda cfg: OllamaEmbedder(
        model=cfg.get("model", "nomic-embed-text"),
        host=cfg.get("host", "http://localhost:11434"),
    ),
}


def _resolve_api_key(provider_config: dict) -> Optional[str]:
    """Resolve API key from provider config or environment."""
    env_var = provider_config.get("api_key_env")
    if env_var:
        return os.environ.get(env_var)
    return None


def create_embedder(embedding_config: dict) -> Optional[EmbeddingProvider]:
    """
    Create an embedding provider from the embedding section of config.json.

    Tries the primary provider first, then walks the fallback chain.
    Returns None if no provider is available (graceful degradation to Phase I).

    Args:
        embedding_config: The "embedding" section of .memory/config.json

    Returns:
        An EmbeddingProvider instance, or None if all providers fail.
    """
    if not embedding_config.get("enabled", False):
        logger.info("Embedding layer is disabled.")
        return None

    providers_config = embedding_config.get("providers", {})
    primary = embedding_config.get("provider", "gemini")
    fallbacks = embedding_config.get("fallback", [])

    # Build ordered list of providers to try
    to_try = [primary] + [f for f in fallbacks if f != "none" and f != primary]

    for provider_id in to_try:
        constructor = _PROVIDER_CONSTRUCTORS.get(provider_id)
        if not constructor:
            logger.warning(f"Unknown embedding provider: {provider_id}")
            continue

        provider_cfg = providers_config.get(provider_id, {})
        try:
            embedder = constructor(provider_cfg)
            logger.info(
                f"Embedding provider initialized: {embedder.provider_id} "
                f"({embedder.model_name}, {embedder.dimensions}d)"
            )
            return embedder
        except ImportError as e:
            logger.warning(f"Provider '{provider_id}' SDK not installed: {e}")
        except ValueError as e:
            logger.warning(f"Provider '{provider_id}' config error: {e}")
        except Exception as e:
            logger.warning(f"Provider '{provider_id}' init failed: {e}")

    logger.info("No embedding provider available. Operating in Phase I mode.")
    return None
