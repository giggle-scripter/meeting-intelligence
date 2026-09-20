"""Chronological shadow evaluation for bounded mutation context."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models import Clause, TaskEvent
from backend.app.reduction.task_ledger import LedgerTask, TaskLedger
from backend.app.v2.models.context import NoteCue

from .context_retriever import (
    ContextBundle,
    ContextRetriever,
    TaskContextEvidence,
)
from .shadow import MUTATION_EVENTS, TaskLinkerShadowRecord


@dataclass(slots=True)
class ContextRetrievalShadowSummary:
    retriever_version: str = "disabled"
    topic_index_version: str = "disabled"
    embedding_model_version: str = "disabled"
    bundle_count: int = 0
    total_clause_count: int = 0
    total_character_count: int = 0
    total_task_count: int = 0
    total_history_event_count: int = 0
    total_note_cue_count: int = 0
    max_clause_count_observed: int = 0
    max_character_count_observed: int = 0
    clause_cap_hit_count: int = 0
    character_cap_hit_count: int = 0
    tier_clause_counts: dict[str, int] = field(default_factory=dict)
    error_count: int = 0


class ContextRetrievalShadowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    bundle: ContextBundle
    total_character_count: int = Field(ge=0)
    tier_clause_counts: dict[str, int]
    clause_cap_hit: bool = False
    character_cap_hit: bool = False


def _candidate_task_ids(record: TaskLinkerShadowRecord, max_tasks: int) -> list[str]:
    values: list[str] = []
    if record.result.task_id:
        values.append(record.result.task_id)
    values.extend(candidate.task_id for candidate in record.result.candidates)
    return list(dict.fromkeys(values))[:max_tasks]


def _task_evidence(
    task: LedgerTask,
    events_by_id: dict[str, TaskEvent],
    clauses_by_id: dict[str, Clause],
) -> TaskContextEvidence:
    source_ids = {
        clause_id
        for event_id in task.event_ids
        for event in (events_by_id.get(event_id),)
        if event is not None and event.event_type not in MUTATION_EVENTS
        for clause_id in event.source_clause_ids
        if clause_id in clauses_by_id
    }
    if not source_ids:
        source_ids = set(task.source_clause_ids)
    source_clause_ids = tuple(
        sorted(
            source_ids,
            key=lambda clause_id: (
                clauses_by_id[clause_id].order_index
                if clause_id in clauses_by_id
                else 10**9,
                clause_id,
            ),
        )
    )
    history_pairs: list[tuple[str, str]] = []
    for event_id in task.event_ids:
        event = events_by_id.get(event_id)
        if not event or event.event_type not in MUTATION_EVENTS:
            continue
        history_pairs.extend(
            (event_id, clause_id)
            for clause_id in event.source_clause_ids
            if clause_id in clauses_by_id
        )
    history_pairs.sort(
        key=lambda pair: (
            events_by_id[pair[0]].order_index,
            clauses_by_id[pair[1]].order_index,
            pair[0],
            pair[1],
        )
    )
    return TaskContextEvidence(
        task_id=task.task_id,
        source_clause_ids=source_clause_ids,
        mutation_history_event_ids=tuple(pair[0] for pair in history_pairs),
        mutation_history_clause_ids=tuple(pair[1] for pair in history_pairs),
    )


def evaluate_context_retrieval_shadow(
    events: list[TaskEvent],
    clauses_by_id: dict[str, Clause],
    task_linker_records: list[TaskLinkerShadowRecord],
    *,
    retriever: ContextRetriever,
    note_cues_by_clause: dict[str, tuple[NoteCue, ...]] | None = None,
) -> tuple[ContextRetrievalShadowSummary, list[ContextRetrievalShadowRecord]]:
    """Build context before applying each mutation, so future memory cannot leak."""

    records_by_event = {record.event_id: record for record in task_linker_records}
    events_by_id = {event.event_id: event for event in events}
    ledger = TaskLedger()
    output: list[ContextRetrievalShadowRecord] = []
    tier_counts: Counter[str] = Counter()
    error_count = 0

    for event in sorted(events, key=lambda item: (item.order_index, item.event_id)):
        link_record = records_by_event.get(event.event_id)
        if link_record is not None:
            try:
                task_evidence = [
                    _task_evidence(ledger.tasks[task_id], events_by_id, clauses_by_id)
                    for task_id in _candidate_task_ids(
                        link_record,
                        retriever.config.max_tasks,
                    )
                    if task_id in ledger.tasks
                ]
                selection = retriever.retrieve(
                    link_record.source_clause_ids,
                    link_record.query.embedding_text(),
                    task_evidence=task_evidence,
                    note_cues_by_clause=note_cues_by_clause,
                )
                tier_counts.update(selection.tier_clause_counts)
                output.append(
                    ContextRetrievalShadowRecord(
                        event_id=event.event_id,
                        bundle=selection.bundle,
                        total_character_count=selection.total_character_count,
                        tier_clause_counts=selection.tier_clause_counts,
                        clause_cap_hit=selection.clause_cap_hit,
                        character_cap_hit=selection.character_cap_hit,
                    )
                )
            except (KeyError, TypeError, ValueError):
                error_count += 1
        ledger.apply(event)

    return (
        ContextRetrievalShadowSummary(
            retriever_version=retriever.config.version,
            topic_index_version=retriever.topic_index.VERSION,
            embedding_model_version=retriever.topic_index.embedding_model_version,
            bundle_count=len(output),
            total_clause_count=sum(item.bundle.total_clause_count for item in output),
            total_character_count=sum(item.total_character_count for item in output),
            total_task_count=sum(len(item.bundle.related_task_ids) for item in output),
            total_history_event_count=sum(
                len(item.bundle.task_history_event_ids) for item in output
            ),
            total_note_cue_count=sum(len(item.bundle.note_cue_ids) for item in output),
            max_clause_count_observed=max(
                (item.bundle.total_clause_count for item in output), default=0
            ),
            max_character_count_observed=max(
                (item.total_character_count for item in output), default=0
            ),
            clause_cap_hit_count=sum(item.clause_cap_hit for item in output),
            character_cap_hit_count=sum(item.character_cap_hit for item in output),
            tier_clause_counts=dict(sorted(tier_counts.items())),
            error_count=error_count,
        ),
        output,
    )
