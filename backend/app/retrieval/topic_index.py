"""Meeting-local topic segmentation and two-stage semantic retrieval."""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from backend.app.ml.contracts import EmbeddingModel, EmbeddingVector
from backend.app.models import Clause
from backend.app.preprocessing.unicode_normalizer import normalize_for_match
from backend.app.v2.context.keywords import extract_keywords

from .scoring import cosine_similarity


_DISCOURSE_MARKER = re.compile(
    r"^(?:chuyen sang|tiep theo|ve phan|con chuyen|next item|moving on|regarding)\b"
)


class TopicRecord(BaseModel):
    """One contiguous topic with an auditable centroid."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    topic_id: str = Field(min_length=1)
    clause_ids: tuple[str, ...] = Field(min_length=1)
    centroid_embedding: tuple[float, ...] = Field(min_length=1)
    keywords: tuple[str, ...] = ()
    first_order_index: int
    last_order_index: int


class TopicClauseMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    topic_id: str = Field(min_length=1)
    clause_id: str = Field(min_length=1)
    topic_score: float = Field(ge=0.0, le=1.0)
    clause_score: float = Field(ge=0.0, le=1.0)


def _centroid(vectors: Iterable[EmbeddingVector]) -> EmbeddingVector:
    values = list(vectors)
    if not values:
        raise ValueError("cannot calculate an empty centroid")
    dimension = len(values[0])
    if not dimension or any(len(value) != dimension for value in values):
        raise ValueError("topic embeddings must share a non-zero dimension")
    return [
        sum(value[index] for value in values) / len(values)
        for index in range(dimension)
    ]


class TopicIndex:
    """Segment once, then retrieve nearest topics before nearest clauses."""

    VERSION = "meeting-topic-index-v1"

    def __init__(
        self,
        clauses: list[Clause],
        embedding_model: EmbeddingModel,
        *,
        boundary_threshold: float = 0.42,
        smoothing_window: int = 3,
    ) -> None:
        if not 0.0 <= boundary_threshold <= 1.0:
            raise ValueError("boundary_threshold must be between zero and one")
        if not 2 <= smoothing_window <= 3:
            raise ValueError("smoothing_window must be two or three")
        self.embedding_model = embedding_model
        self.boundary_threshold = boundary_threshold
        self.smoothing_window = smoothing_window
        self._clauses = sorted(
            clauses,
            key=lambda item: (item.order_index, item.clause_id),
        )
        self._clauses_by_id = {item.clause_id: item for item in self._clauses}
        vectors = (
            embedding_model.embed([item.text_raw for item in self._clauses])
            if self._clauses
            else []
        )
        self._vectors = {
            clause.clause_id: vector
            for clause, vector in zip(self._clauses, vectors, strict=True)
        }
        self._topics = self._segment()

    @property
    def embedding_model_version(self) -> str:
        metadata = self.embedding_model.metadata
        return f"{metadata.model_name}:{metadata.model_version}"

    @staticmethod
    def _has_discourse_marker(text: str) -> bool:
        return bool(_DISCOURSE_MARKER.search(normalize_for_match(text)))

    @staticmethod
    def _is_short_outlier(text: str) -> bool:
        return len(normalize_for_match(text).split()) < 4

    def _segment(self) -> tuple[TopicRecord, ...]:
        if not self._clauses:
            return ()
        # Clauses from one turn share speaker and source captions. Segment on
        # smoothed turn centroids, while a marker can start a new unit mid-turn.
        units: list[list[Clause]] = []
        for clause in self._clauses:
            same_turn = bool(units) and (
                units[-1][-1].speaker_id == clause.speaker_id
                and units[-1][-1].source_caption_ids == clause.source_caption_ids
            )
            if (
                not same_turn
                or self._has_discourse_marker(clause.text_raw)
            ):
                units.append([])
            units[-1].append(clause)

        groups: list[list[Clause]] = []
        current_units: list[list[Clause]] = []
        for unit in units:
            boundary = False
            unit_vector = _centroid(
                self._vectors[clause.clause_id] for clause in unit
            )
            if current_units:
                recent_units = current_units[-self.smoothing_window :]
                rolling = _centroid(
                    _centroid(self._vectors[item.clause_id] for item in recent)
                    for recent in recent_units
                )
                similarity = cosine_similarity(rolling, unit_vector)
                boundary = self._has_discourse_marker(unit[0].text_raw) or (
                    similarity < self.boundary_threshold
                    and not self._is_short_outlier(
                        " ".join(clause.text_raw for clause in unit)
                    )
                )
            if boundary:
                groups.append(
                    [clause for recent in current_units for clause in recent]
                )
                current_units = []
            current_units.append(unit)
        if current_units:
            groups.append([clause for unit in current_units for clause in unit])

        topics: list[TopicRecord] = []
        for index, group in enumerate(groups, start=1):
            combined = " ".join(item.text_raw for item in group)
            topics.append(
                TopicRecord(
                    topic_id=f"TOPIC-{index:04d}",
                    clause_ids=tuple(item.clause_id for item in group),
                    centroid_embedding=tuple(
                        _centroid(self._vectors[item.clause_id] for item in group)
                    ),
                    keywords=extract_keywords(combined),
                    first_order_index=group[0].order_index,
                    last_order_index=group[-1].order_index,
                )
            )
        return tuple(topics)

    def topics(self) -> tuple[TopicRecord, ...]:
        return self._topics

    def topic_ids_by_clause(self) -> dict[str, tuple[str, ...]]:
        return {
            clause_id: (topic.topic_id,)
            for topic in self._topics
            for clause_id in topic.clause_ids
        }

    def retrieve(
        self,
        query_text: str,
        *,
        max_topics: int = 3,
        max_clauses: int = 12,
        exclude_clause_ids: set[str] | None = None,
    ) -> tuple[TopicClauseMatch, ...]:
        if (
            not query_text.strip()
            or not self._topics
            or max_topics <= 0
            or max_clauses <= 0
        ):
            return ()
        excluded = exclude_clause_ids or set()
        query_vector = self.embedding_model.embed([query_text])[0]
        ranked_topics = sorted(
            (
                (cosine_similarity(query_vector, list(topic.centroid_embedding)), topic)
                for topic in self._topics
            ),
            key=lambda item: (-item[0], item[1].first_order_index, item[1].topic_id),
        )[:max_topics]
        matches: list[TopicClauseMatch] = []
        for topic_score, topic in ranked_topics:
            ranked_clauses = sorted(
                (
                    (
                        cosine_similarity(query_vector, self._vectors[clause_id]),
                        clause_id,
                    )
                    for clause_id in topic.clause_ids
                    if clause_id not in excluded
                ),
                key=lambda item: (
                    -item[0],
                    self._clauses_by_id[item[1]].order_index,
                    item[1],
                ),
            )
            matches.extend(
                TopicClauseMatch(
                    topic_id=topic.topic_id,
                    clause_id=clause_id,
                    topic_score=topic_score,
                    clause_score=clause_score,
                )
                for clause_score, clause_id in ranked_clauses
            )
        matches.sort(
            key=lambda item: (
                -item.topic_score,
                -item.clause_score,
                self._clauses_by_id[item.clause_id].order_index,
                item.clause_id,
            )
        )
        return tuple(matches[:max_clauses])
