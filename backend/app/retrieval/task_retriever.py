"""Exact-first, margin-gated semantic task retrieval."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.preprocessing.unicode_normalizer import normalize_for_match

from .scoring import (
    TaskLinkScoringConfig,
    TaskScore,
    cosine_similarity,
    lexical_similarity,
    recency_score,
    set_compatibility,
    weighted_task_score,
)
from .task_index import MutationQuery, TaskIndex, TaskRepresentation


class TaskRetrievalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    score: TaskScore
    exact_alias: bool = False


class TaskRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["DIRECT_LINK", "AI_MUTATION_CHECK", "UNRESOLVED"]
    task_id: str | None = None
    reason: str = Field(min_length=1)
    margin: float = Field(ge=0.0, le=1.0)
    candidates: tuple[TaskRetrievalCandidate, ...] = ()


class TaskRetriever:
    VERSION = "task-semantic-retriever-v1"

    def __init__(
        self,
        index: TaskIndex,
        config: TaskLinkScoringConfig | None = None,
    ) -> None:
        self.index = index
        self.config = config or TaskLinkScoringConfig()

    @staticmethod
    def _normalized_aliases(entry: TaskRepresentation) -> set[str]:
        return {
            normalize_for_match(alias)
            for alias in entry.aliases
            if alias.strip()
        }

    def retrieve(self, query: MutationQuery) -> TaskRetrievalResult:
        entries = self.index.entries()
        if not entries:
            return TaskRetrievalResult(
                status="UNRESOLVED",
                reason="NO_ACTIVE_TASK_CANDIDATES",
                margin=0.0,
            )

        if query.explicit_task_id:
            if any(entry.task_id == query.explicit_task_id for entry in entries):
                return TaskRetrievalResult(
                    status="DIRECT_LINK",
                    task_id=query.explicit_task_id,
                    reason="EXACT_TASK_ID",
                    margin=1.0,
                )
            return TaskRetrievalResult(
                status="UNRESOLVED",
                reason="UNKNOWN_EXPLICIT_TASK_ID",
                margin=0.0,
            )

        normalized_ref = normalize_for_match(query.action_ref)
        exact_alias_entries = [
            entry
            for entry in entries
            if normalized_ref and normalized_ref in self._normalized_aliases(entry)
        ]
        if len(exact_alias_entries) == 1:
            return TaskRetrievalResult(
                status="DIRECT_LINK",
                task_id=exact_alias_entries[0].task_id,
                reason="EXACT_ALIAS",
                margin=1.0,
            )

        query_vector = self.index.embed_query(query)
        ranked: list[TaskRetrievalCandidate] = []
        for entry in entries:
            semantic = cosine_similarity(
                query_vector,
                self.index.vector(entry.task_id),
            )
            lexical = lexical_similarity(query.action_ref, entry.aliases)
            topic = set_compatibility(query.topic_ids, entry.topic_ids)
            owner = set_compatibility(query.owner_refs, entry.owners)
            recency = recency_score(
                query.order_index,
                entry.last_order_index,
                self.config.recency_horizon_clauses,
            )
            ranked.append(
                TaskRetrievalCandidate(
                    task_id=entry.task_id,
                    score=weighted_task_score(
                        semantic=semantic,
                        lexical=lexical,
                        topic=topic,
                        owner=owner,
                        recency=recency,
                        config=self.config,
                    ),
                    exact_alias=entry in exact_alias_entries,
                )
            )
        ranked.sort(key=lambda item: (-item.score.total, item.task_id))
        top = tuple(ranked[: self.config.top_k])
        best = top[0]
        second_score = top[1].score.total if len(top) > 1 else 0.0
        margin = max(0.0, best.score.total - second_score)

        if len(exact_alias_entries) > 1:
            exact_ids = {entry.task_id for entry in exact_alias_entries}
            return TaskRetrievalResult(
                status="AI_MUTATION_CHECK",
                reason="MULTIPLE_EXACT_ALIAS_MATCHES",
                margin=margin,
                candidates=tuple(item for item in top if item.task_id in exact_ids),
            )
        if (
            best.score.total >= self.config.strong_threshold
            and margin >= self.config.minimum_margin
        ):
            reason = (
                "LEXICAL_DIRECT_LINK"
                if best.score.lexical >= best.score.semantic
                else "SEMANTIC_DIRECT_LINK"
            )
            return TaskRetrievalResult(
                status="DIRECT_LINK",
                task_id=best.task_id,
                reason=reason,
                margin=margin,
                candidates=top,
            )
        if best.score.total >= self.config.ai_threshold:
            return TaskRetrievalResult(
                status="AI_MUTATION_CHECK",
                reason="AMBIGUOUS_TOP_K",
                margin=margin,
                candidates=top,
            )
        return TaskRetrievalResult(
            status="UNRESOLVED",
            reason="BELOW_TASK_LINK_THRESHOLD",
            margin=margin,
            candidates=top,
        )
