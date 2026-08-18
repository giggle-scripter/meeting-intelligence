"""Configurable evidence scoring for task identity retrieval."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.preprocessing.unicode_normalizer import normalize_for_match
from backend.app.utils.text_similarity import similarity, token_overlap


class TaskLinkScoringConfig(BaseModel):
    """Versioned weights and routing thresholds; no cutoff is hidden in logic."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_weight: float = Field(default=0.55, ge=0.0, le=1.0)
    lexical_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    topic_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    owner_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    recency_weight: float = Field(default=0.05, ge=0.0, le=1.0)
    strong_threshold: float = Field(default=0.78, ge=0.0, le=1.0)
    minimum_margin: float = Field(default=0.12, ge=0.0, le=1.0)
    ai_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    recency_horizon_clauses: int = Field(default=200, gt=0)
    top_k: int = Field(default=5, gt=0, le=20)
    version: str = Field(default="task-link-scoring-v1", min_length=1)

    @model_validator(mode="after")
    def validate_configuration(self) -> "TaskLinkScoringConfig":
        total = sum(
            (
                self.semantic_weight,
                self.lexical_weight,
                self.topic_weight,
                self.owner_weight,
                self.recency_weight,
            )
        )
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError("task-link scoring weights must sum to one")
        if self.ai_threshold > self.strong_threshold:
            raise ValueError("ai_threshold must not exceed strong_threshold")
        return self


class TaskScore(BaseModel):
    """Auditable score breakdown for one query/task pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic: float = Field(ge=0.0, le=1.0)
    lexical: float = Field(ge=0.0, le=1.0)
    topic: float = Field(ge=0.0, le=1.0)
    owner: float = Field(ge=0.0, le=1.0)
    recency: float = Field(ge=0.0, le=1.0)
    total: float = Field(ge=0.0, le=1.0)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("embedding vectors must have the same non-zero dimension")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    raw = sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
    return min(1.0, max(0.0, raw))


def lexical_similarity(query: str, aliases: tuple[str, ...]) -> float:
    normalized_query = normalize_for_match(query)
    if not normalized_query:
        return 0.0
    scores: list[float] = []
    for alias in aliases:
        normalized_alias = normalize_for_match(alias)
        if not normalized_alias:
            continue
        scores.append(
            max(
                token_overlap(normalized_query, normalized_alias),
                similarity(normalized_query, normalized_alias),
            )
        )
    return max(scores, default=0.0)


def set_compatibility(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    normalized_left = {normalize_for_match(item) for item in left if item.strip()}
    normalized_right = {normalize_for_match(item) for item in right if item.strip()}
    if not normalized_left or not normalized_right:
        return 0.0
    return len(normalized_left & normalized_right) / len(
        normalized_left | normalized_right
    )


def recency_score(query_order: int, task_order: int, horizon: int) -> float:
    distance = query_order - task_order
    if distance < 0:
        return 0.0
    return max(0.0, 1.0 - min(distance, horizon) / horizon)


def weighted_task_score(
    *,
    semantic: float,
    lexical: float,
    topic: float,
    owner: float,
    recency: float,
    config: TaskLinkScoringConfig,
) -> TaskScore:
    total = (
        config.semantic_weight * semantic
        + config.lexical_weight * lexical
        + config.topic_weight * topic
        + config.owner_weight * owner
        + config.recency_weight * recency
    )
    return TaskScore(
        semantic=semantic,
        lexical=lexical,
        topic=topic,
        owner=owner,
        recency=recency,
        total=min(1.0, max(0.0, total)),
    )
