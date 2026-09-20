"""Tests for embedding contracts, fallback, and process-local caching."""

from __future__ import annotations

import math

import pytest

from backend.app.ml.contracts import EmbeddingModelMetadata
from backend.app.ml.embeddings import (
    HASHING_FALLBACK_MODEL,
    EmbeddingModelLoadError,
    HashingEmbeddingModel,
)
from backend.app.ml.model_registry import ModelRegistry


class _FakeEmbeddingModel:
    def __init__(self, name: str, device: str) -> None:
        self.metadata = EmbeddingModelMetadata(
            requested_model=name,
            model_name=name,
            model_version="test-v1",
            backend="fake",
            dimension=2,
            device=device,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def test_hashing_embedding_is_deterministic_normalized_and_ordered() -> None:
    model = HashingEmbeddingModel(dimension=32)

    first = model.embed(["Lan cập nhật dashboard", "Minh review API", ""])
    second = model.embed(["Lan cập nhật dashboard", "Minh review API", ""])

    assert first == second
    assert first[0] != first[1]
    assert math.isclose(sum(value * value for value in first[0]), 1.0)
    assert first[2] == [0.0] * 32


def test_hashing_embedding_rejects_single_string_and_non_string_item() -> None:
    model = HashingEmbeddingModel(dimension=8)

    with pytest.raises(TypeError, match="sequence of strings"):
        model.embed("one input")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="every embedding input"):
        model.embed(["valid", 42])  # type: ignore[list-item]


def test_registry_loads_same_configuration_once() -> None:
    calls: list[tuple[str, str]] = []

    def loader(name: str, device: str) -> _FakeEmbeddingModel:
        calls.append((name, device))
        return _FakeEmbeddingModel(name, device)

    registry = ModelRegistry(embedding_loader=loader)

    first = registry.get_embedding_model("semantic-v1", device="cpu")
    second = registry.get_embedding_model("semantic-v1", device="cpu")

    assert first is second
    assert calls == [("semantic-v1", "cpu")]


def test_registry_uses_auditable_hashing_fallback_after_load_error() -> None:
    def failing_loader(name: str, device: str) -> _FakeEmbeddingModel:
        raise OSError("model asset missing")

    registry = ModelRegistry(embedding_loader=failing_loader)
    model = registry.get_embedding_model(
        "semantic-v1", fallback_dimension=24, allow_fallback=True
    )

    assert model.metadata.model_name == HASHING_FALLBACK_MODEL
    assert model.metadata.requested_model == "semantic-v1"
    assert model.metadata.dimension == 24
    assert model.metadata.is_fallback is True
    assert model.metadata.load_error_code == "builtins.OSError"
    assert len(model.embed(["fallback"])[0]) == 24


def test_registry_can_disable_fallback() -> None:
    def failing_loader(name: str, device: str) -> _FakeEmbeddingModel:
        raise OSError("model asset missing")

    registry = ModelRegistry(embedding_loader=failing_loader)

    with pytest.raises(EmbeddingModelLoadError, match="semantic-v1"):
        registry.get_embedding_model("semantic-v1", allow_fallback=False)


def test_registry_explicit_hashing_model_never_calls_primary_loader() -> None:
    def unexpected_loader(name: str, device: str) -> _FakeEmbeddingModel:
        raise AssertionError("primary loader must not be called")

    registry = ModelRegistry(embedding_loader=unexpected_loader)
    model = registry.get_embedding_model(HASHING_FALLBACK_MODEL)

    assert model.metadata.backend == "hashing"
    assert model.metadata.is_fallback is False
