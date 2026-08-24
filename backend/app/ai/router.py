"""Bounded, chronological execution path for AI mutation candidates."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import httpx

from backend.app.candidate import CandidateDecision, CandidateEvidence, CandidateRoute
from backend.app.models import Clause, ClauseAnnotation, DateMention, MeetingInput, TaskEvent
from backend.app.reduction.task_ledger import TaskLedger
from backend.app.retrieval import ContextRetriever, TaskContextEvidence, TaskIndex, TaskLinkScoringConfig, TaskRetriever
from backend.app.retrieval.shadow import build_mutation_query, build_task_representation
from backend.app.verification import validate_mutation_resolution


@dataclass(slots=True)
class MutationRouterSummary:
    mode: str
    version: str = "ai-mutation-router-v1"
    prompt_version: str = "mutation-resolution-v2"
    candidate_count: int = 0
    payload_count: int = 0
    call_count: int = 0
    event_count: int = 0
    unresolved_count: int = 0
    rejected_count: int = 0
    error_count: int = 0
    unknown_task_id_count: int = 0
    invalid_source_count: int = 0
    invalid_anchor_count: int = 0
    invalid_owner_span_count: int = 0
    invalid_deadline_count: int = 0
    candidate_task_count: int = 0
    context_clause_count: int = 0
    context_character_count: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)


class MutationRouter:
    """Python controls candidates, retrieval, context, validation, and ledger input."""

    VERSION = "ai-mutation-router-v1"

    def __init__(self, *, minimum_confidence: float = 0.70, prompt_version: str = "mutation-resolution-v2") -> None:
        self.minimum_confidence = minimum_confidence
        self.prompt_version = prompt_version

    @staticmethod
    def _task_context(task, events_by_id: dict[str, TaskEvent]) -> TaskContextEvidence:
        history = [events_by_id[item] for item in task.event_ids if item in events_by_id]
        mutations = [
            item for item in history
            if item.event_type in {"OWNER_ASSIGN", "OWNER_REASSIGN", "DEADLINE_SET", "DEADLINE_REPLACE", "TASK_CANCEL", "TASK_REJECT"}
        ]
        return TaskContextEvidence(
            task_id=task.task_id,
            source_clause_ids=tuple(sorted(task.source_clause_ids)),
            mutation_history_event_ids=tuple(item.event_id for item in mutations),
            mutation_history_clause_ids=tuple(
                clause_id for item in mutations for clause_id in item.source_clause_ids
            ),
        )

    def execute(
        self,
        *,
        meeting: MeetingInput,
        decision: CandidateDecision,
        evidence: CandidateEvidence,
        deterministic_events: list[TaskEvent],
        clauses_by_id: dict[str, Clause],
        annotations: dict[str, ClauseAnnotation],
        mentions: dict[str, DateMention],
        context_retriever: ContextRetriever,
        embedding_model,
        scoring: TaskLinkScoringConfig,
        ai_client,
        mode: str,
        start_sequence: int,
    ) -> tuple[TaskEvent | None, dict, MutationRouterSummary]:
        summary = MutationRouterSummary(mode=mode, prompt_version=self.prompt_version)
        if decision.route != CandidateRoute.AI_MUTATION_CHECK:
            return None, {}, summary
        summary.candidate_count = 1
        trace = {"candidate_id": decision.candidate_id, "route": decision.route.value, "provider_called": False, "provider_response": None, "validation": {"accepted": False, "reasons": []}, "executed": False}
        primary = tuple(evidence.clause_ids)
        anchor_clause = clauses_by_id[evidence.focus_clause_id]
        prior_events = [
            item for item in deterministic_events
            if item.order_index < anchor_clause.order_index
        ]
        ledger = TaskLedger()
        for item in sorted(prior_events, key=lambda value: (value.order_index, value.event_id)):
            ledger.apply(item)
        index = TaskIndex(embedding_model)
        index.sync(build_task_representation(task, {}) for task in ledger.candidate_tasks())
        query_event = TaskEvent(
            event_id="ROUTER-QUERY", event_type="DEADLINE_REPLACE",
            source_clause_ids=list(primary), action_text=anchor_clause.text_raw,
            order_index=anchor_clause.order_index, anchor_clause_id=evidence.focus_clause_id,
        )
        retrieval = TaskRetriever(index, scoring).retrieve(build_mutation_query(query_event, clauses_by_id, {}))
        supplied_task_ids = [item.task_id for item in retrieval.candidates]
        if retrieval.task_id and retrieval.task_id not in supplied_task_ids:
            supplied_task_ids.insert(0, retrieval.task_id)
        supplied_task_ids = supplied_task_ids[:5]
        if not supplied_task_ids:
            summary.unresolved_count = 1
            trace["validation"]["reasons"] = ["NO_CANDIDATE_TASKS"]
            return None, trace, summary
        task_evidence = [self._task_context(ledger.tasks[item], {event.event_id: event for event in prior_events}) for item in supplied_task_ids]
        selection = context_retriever.retrieve(primary, anchor_clause.text_raw, task_evidence=task_evidence)
        allowed_context_ids = set(selection.bundle.local_clause_ids) | set(selection.bundle.topic_clause_ids)
        payload = {
            "meeting": {"meeting_id": meeting.meeting_id, "meeting_date": meeting.meeting_date},
            "candidate": {"candidate_id": decision.candidate_id, "route": decision.route.value, "primary_clause_ids": list(primary), "mutation_kinds": sorted(annotations[evidence.focus_clause_id].flags & {"CORRECTION", "CANCELLATION", "REJECTION"})},
            "clauses": [
                {"clause_id": item.clause_id, "speaker": item.speaker_name, "text": item.text_raw, "order_index": item.order_index}
                for clause_id in sorted(allowed_context_ids, key=lambda item: (clauses_by_id[item].order_index, item))
                for item in [clauses_by_id[clause_id]]
            ],
            "candidate_tasks": [
                {"task_id": task.task_id, "canonical_action": task.canonical_action, "owners": sorted(task.assignees), "status": task.status, "source_clause_ids": sorted(task.source_clause_ids), "history_event_ids": list(task.event_ids)}
                for task_id in supplied_task_ids for task in [ledger.tasks[task_id]]
            ],
            "date_mentions": [
                {"date_mention_id": item.date_mention_id, "clause_id": item.clause_id, "text": item.raw_text}
                for item in mentions.values() if item.clause_id in allowed_context_ids
            ],
        }
        summary.payload_count = 1
        summary.candidate_task_count = len(supplied_task_ids)
        summary.context_clause_count = len(allowed_context_ids)
        summary.context_character_count = selection.total_character_count
        trace["context_bundle"] = selection.bundle.model_dump(mode="json")
        trace["supplied_task_ids"] = supplied_task_ids
        if mode == "shadow":
            return None, trace, summary
        if mode != "assist" or not ai_client.enabled:
            return None, trace, summary
        try:
            response = ai_client.resolve_mutation(payload)
            summary.call_count = 1
            trace["provider_called"] = True
            trace["provider_response"] = {"decision": response.decision, "event_type": response.event_type, "related_task_id": response.related_task_id}
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            summary.error_count = 1
            summary.unresolved_count = 1
            trace["validation"]["reasons"] = ["PROVIDER_ERROR"]
            return None, trace, summary
        validation = validate_mutation_resolution(
            response, route=decision.route, supplied_task_ids=set(supplied_task_ids), ledger=ledger,
            clauses_by_id=clauses_by_id, annotations=annotations, mentions=mentions,
            allowed_context_clause_ids=allowed_context_ids, primary_clause_ids=set(primary),
            minimum_confidence=self.minimum_confidence, start_sequence=start_sequence,
        )
        trace["validation"] = {"accepted": validation.accepted, "reasons": list(validation.reasons)}
        if validation.accepted:
            summary.event_count = 1
            trace["executed"] = True
            return validation.event, trace, summary
        if validation.reasons == ("UNRESOLVED",):
            summary.unresolved_count = 1
        else:
            summary.rejected_count = 1
            counter = Counter(validation.reasons)
            summary.rejection_reasons = dict(counter)
            summary.unknown_task_id_count = counter["UNKNOWN_TASK_ID"]
            summary.invalid_source_count = counter["SOURCE_OUTSIDE_BOUNDED_CONTEXT"]
            summary.invalid_anchor_count = counter["ANCHOR_OUTSIDE_PRIMARY"] + counter["ANCHOR_NOT_CITED"]
            summary.invalid_owner_span_count = counter["UNGROUNDED_OWNER_SPAN"]
            summary.invalid_deadline_count = counter["INVALID_DEADLINE_MENTION"]
        return None, trace, summary
