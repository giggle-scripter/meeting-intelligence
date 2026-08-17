"""Embedding implementations with no import-time model loading."""

from __future__ import annotations

import hashlib
import importlib
import math
import re
from collections.abc import Sequence
from typing import Any

from backend.app.ml.contracts import (
    EmbeddingModelMetadata,
    EmbeddingVector,
)


HASHING_FALLBACK_MODEL = "hashing-fallback-v1"
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


class EmbeddingModelLoadError(RuntimeError):
    """Raised when a configured embedding backend cannot be loaded."""


class EmbeddingInferenceError(RuntimeError):
    """Raised when a backend returns an invalid embedding batch."""


class HashingEmbeddingModel:
    """Small deterministic fallback based on signed feature hashing.

    This backend is intentionally lexical. It keeps callers operational when a
    semantic model is unavailable, but metadata marks that fallback explicitly
    so evaluation never mistakes it for the configured primary model.
    """

    def __init__(
        self,
        dimension: int = 384,
        *,
        requested_model: str = HASHING_FALLBACK_MODEL,
        is_fallback: bool = False,
        load_error_code: str | None = None,
    ) -> None:
        self._metadata = EmbeddingModelMetadata(
            requested_model=requested_model,
            model_name=HASHING_FALLBACK_MODEL,
            model_version="v1",
            backend="hashing",
            dimension=dimension,
            device="cpu",
            is_fallback=is_fallback,
            load_error_code=load_error_code,
        )

    @property
    def metadata(self) -> EmbeddingModelMetadata:
        return self._metadata

    def embed(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        if isinstance(texts, (str, bytes)):
            raise TypeError("texts must be a sequence of strings, not one string")
        vectors: list[EmbeddingVector] = []
        for text in texts:
            if not isinstance(text, str):
                raise TypeError("every embedding input must be a string")
            vector = [0.0] * self.metadata.dimension
            for token in _TOKEN_PATTERN.findall(text.casefold()):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:8], "big") % self.metadata.dimension
                sign = 1.0 if digest[8] & 1 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


class SentenceTransformerEmbeddingModel:
    """Lazy adapter for the optional ``sentence-transformers`` package."""

    def __init__(self, model_name: str, device: str = "cpu") -> None:
        try:
            module = importlib.import_module("sentence_transformers")
            model_type = getattr(module, "SentenceTransformer")
            model = model_type(model_name, device=device)
            dimension = int(model.get_sentence_embedding_dimension())
        except Exception as exc:
            raise EmbeddingModelLoadError(
                f"Unable to load embedding model {model_name!r}."
            ) from exc
        self._model: Any = model
        self._metadata = EmbeddingModelMetadata(
            requested_model=model_name,
            model_name=model_name,
            model_version=model_name,
            backend="sentence-transformers",
            dimension=dimension,
            device=device,
        )

    @property
    def metadata(self) -> EmbeddingModelMetadata:
        return self._metadata

    def embed(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        if isinstance(texts, (str, bytes)):
            raise TypeError("texts must be a sequence of strings, not one string")
        inputs = list(texts)
        if any(not isinstance(text, str) for text in inputs):
            raise TypeError("every embedding input must be a string")
        if not inputs:
            return []
        try:
            encoded = self._model.encode(
                inputs,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            raw_vectors = encoded.tolist() if hasattr(encoded, "tolist") else encoded
            vectors = [[float(value) for value in vector] for vector in raw_vectors]
        except Exception as exc:
            raise EmbeddingInferenceError("Embedding inference failed.") from exc
        _validate_vectors(vectors, len(inputs), self.metadata.dimension)
        return vectors


def _validate_vectors(
    vectors: list[EmbeddingVector], expected_count: int, expected_dimension: int
) -> None:
    if len(vectors) != expected_count:
        raise EmbeddingInferenceError("Embedding backend returned the wrong batch size.")
    for vector in vectors:
        if len(vector) != expected_dimension:
            raise EmbeddingInferenceError(
                "Embedding backend returned the wrong vector dimension."
            )
        if any(not math.isfinite(value) for value in vector):
            raise EmbeddingInferenceError(
                "Embedding backend returned a non-finite vector value."
            )
