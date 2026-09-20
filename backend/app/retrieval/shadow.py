"""Chronological shadow evaluation against the current production linker."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models import Clause, TaskEvent
from backend.app.reduction.task_ledger import LedgerTask, TaskLedger

from .scoring import TaskLinkScoringConfig
from .task_index import (
    MutationQuery,
    TaskIndex,
    TaskRepresentation,
    extract_identity_entities,
)
from .task_retriever import TaskRetrievalResult, TaskRetriever


MUTATION_EVENTS = {
    "TASK_CANCEL",
    "TASK_REJECT",
    "OWNER_REASSIGN",
    "DEADLINE_SET",
    "DEADLINE_REPLACE",
}


@dataclass(slots=True)
class TaskLinkerShadowSummary:
    linker_version: str = "disabled"
    index_version: str = "disabled"
    scoring_version: str = "disabled"
    embedding_model_version: str = "disabled"
    query_count: int = 0
    scored_query_count: int = 0
    route_counts: dict[str, int] = field(default_factory=dict)
    reason_counts: dict[str, int] = field(default_factory=dict)
    production_agreement_count: int = 0
    production_disagreement_count: int = 0
    ambiguous_sibling_count: int = 0
    mean_top1_score: float = 0.0
    mean_margin: float = 0.0


class TaskLinkerShadowRecord(BaseModel):
    """One mutation query/result pair retained for downstream shadow consumers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    source_clause_ids: tuple[str, ...]
    query: MutationQuery
    result: TaskRetrievalResult


def _topics_for_clauses(
    clause_ids: set[str] | list[str],
    topic_ids_by_clause: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                topic_id
                for clause_id in clause_ids
                for topic_id in topic_ids_by_clause.get(clause_id, ())
            }
        )
    )


def build_task_representation(
    task: LedgerTask,
    topic_ids_by_clause: dict[str, tuple[str, ...]],
) -> TaskRepresentation:
    aliases = tuple(sorted(task.candidate_aliases()))
    return TaskRepresentation(
        task_id=task.task_id,
        action=task.canonical_action,
        aliases=aliases,
        owners=tuple(sorted(task.assignees)),
        topic_ids=_topics_for_clauses(task.source_clause_ids, topic_ids_by_clause),
        entities=extract_identity_entities(aliases),
        created_order_index=task.created_order_index,
        last_order_index=task.last_order_index,
    )


def build_mutation_query(
    event: TaskEvent,
    clauses_by_id: dict[str, Clause],
    topic_ids_by_clause: dict[str, tuple[str, ...]],
) -> MutationQuery:
    source_clauses = [
        clauses_by_id[clause_id]
        for clause_id in event.source_clause_ids
        if clause_id in clauses_by_id
    ]
    mutation_text = " ".join(clause.text_raw for clause in source_clauses).strip()
    if not mutation_text:
        mutation_text = event.action_text or event.related_task_hint or event.event_type
    speaker = source_clauses[-1].speaker_name if source_clauses else ""
    owner_refs = tuple(
        sorted({item for item in (event.assignee, speaker) if item.strip()})
    )
    return MutationQuery(
        explicit_task_id=event.related_task_id,
        action_ref=event.related_task_hint or event.action_text,
        mutation_text=mutation_text,
        speaker=speaker,
        owner_refs=owner_refs,
        topic_ids=_topics_for_clauses(event.source_clause_ids, topic_ids_by_clause),
        order_index=event.order_index,
    )


def evaluate_task_linker_shadow(
    events: list[TaskEvent],
    clauses_by_id: dict[str, Clause],
    *,
    embedding_model,
    config: TaskLinkScoringConfig,
    topic_ids_by_clause: dict[str, tuple[str, ...]] | None = None,
) -> tuple[TaskLinkerShadowSummary, list[TaskLinkerShadowRecord]]:
    """Replay production chronology while recording non-authoritative retrieval."""

    topic_ids_by_clause = topic_ids_by_clause or {}
    ledger = TaskLedger()
    index = TaskIndex(embedding_model)
    retriever = TaskRetriever(index, config)
    records: list[TaskLinkerShadowRecord] = []
    route_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    agreement = 0
    disagreement = 0
    siblings = 0
    top_scores: list[float] = []
    margins: list[float] = []

    for event in sorted(events, key=lambda item: (item.order_index, item.event_id)):
        result = None
        if event.event_type in MUTATION_EVENTS:
            index.sync(
                build_task_representation(task, topic_ids_by_clause)
                for task in ledger.candidate_tasks()
                if task.canonical_action.strip()
            )
            query = build_mutation_query(event, clauses_by_id, topic_ids_by_clause)
            result = retriever.retrieve(query)
            records.append(
                TaskLinkerShadowRecord(
                    event_id=event.event_id,
                    source_clause_ids=tuple(event.source_clause_ids),
                    query=query,
                    result=result,
                )
            )
            route_counts[result.status] += 1
            reason_counts[result.reason] += 1
            siblings += int(result.reason == "MULTIPLE_EXACT_ALIAS_MATCHES")
            margins.append(result.margin)
            if result.candidates:
                top_scores.append(result.candidates[0].score.total)

        production = ledger.apply(event)
        if result is not None:
            if result.task_id == production.task_id:
                agreement += 1
            else:
                disagreement += 1

    count = len(records)
    return (
        TaskLinkerShadowSummary(
            linker_version=retriever.VERSION,
            index_version=index.VERSION,
            scoring_version=config.version,
            embedding_model_version=index.embedding_model_version,
            query_count=count,
            scored_query_count=len(top_scores),
            route_counts=dict(sorted(route_counts.items())),
            reason_counts=dict(sorted(reason_counts.items())),
            production_agreement_count=agreement,
            production_disagreement_count=disagreement,
            ambiguous_sibling_count=siblings,
            mean_top1_score=(sum(top_scores) / len(top_scores) if top_scores else 0.0),
            mean_margin=(sum(margins) / len(margins) if margins else 0.0),
        ),
        records,
    )
