"""Contracts shared by local ML components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable


EmbeddingVector = list[float]


@dataclass(frozen=True, slots=True)
class EmbeddingModelMetadata:
    """Auditable identity for one loaded embedding backend."""

    requested_model: str
    model_name: str
    model_version: str
    backend: str
    dimension: int
    device: str = "cpu"
    is_fallback: bool = False
    load_error_code: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "requested_model",
            "model_name",
            "model_version",
            "backend",
            "device",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.dimension <= 0:
            raise ValueError("dimension must be greater than zero")
        if self.load_error_code and not self.is_fallback:
            raise ValueError("load_error_code is only valid for a fallback model")


@runtime_checkable
class EmbeddingModel(Protocol):
    """Minimal embedding interface used by classifier and retrieval layers."""

    @property
    def metadata(self) -> EmbeddingModelMetadata:
        """Return stable model/version information for diagnostics."""

        ...

    def embed(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        """Embed texts in input order without mutating their contents."""

        ...
