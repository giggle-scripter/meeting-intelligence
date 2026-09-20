"""Contracts shared by local ML components."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


EmbeddingVector = list[float]


class ActionLabel(str, Enum):
    """Initial clause-level labels for the local action classifier."""

    CLEAR_ACTION = "CLEAR_ACTION"
    POSSIBLE_ACTION = "POSSIBLE_ACTION"
    UPDATE_ONLY = "UPDATE_ONLY"
    NON_ACTION = "NON_ACTION"


class ActionProbabilities(BaseModel):
    """Calibrated probabilities for every classifier label."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    clear_action: float = Field(ge=0.0, le=1.0)
    possible_action: float = Field(ge=0.0, le=1.0)
    update_only: float = Field(ge=0.0, le=1.0)
    non_action: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def probabilities_sum_to_one(self) -> "ActionProbabilities":
        total = sum(
            (
                self.clear_action,
                self.possible_action,
                self.update_only,
                self.non_action,
            )
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError("action probabilities must sum to one")
        return self


class ActionPrediction(BaseModel):
    """Auditable clause-level action classification result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: ActionLabel
    confidence: float = Field(ge=0.0, le=1.0)
    probabilities: ActionProbabilities
    classifier_version: str = Field(min_length=1)
    embedding_model_version: str = Field(min_length=1)


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
