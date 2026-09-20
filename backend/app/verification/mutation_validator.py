"""Fail-closed promotion gate for bounded AI mutation resolutions."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.ai.schemas import MutationResolutionResponse
from backend.app.candidate import CandidateRoute
from backend.app.models import Clause, ClauseAnnotation, DateMention, TaskEvent
from backend.app.preprocessing.unicode_normalizer import normalize_for_match
from backend.app.reduction.task_ledger import TaskLedger
from backend.app.utils.ids import make_id


MUTATION_TYPES = {
    "OWNER_ASSIGN", "OWNER_REASSIGN", "DEADLINE_SET", "DEADLINE_REPLACE",
    "TASK_CANCEL", "TASK_REJECT",
}
OWNER_TYPES = {"OWNER_ASSIGN", "OWNER_REASSIGN"}
DEADLINE_TYPES = {"DEADLINE_SET", "DEADLINE_REPLACE"}
FIRST_PERSON = {"em", "tôi", "mình", "chúng tôi", "chúng em", "i", "me", "we", "us"}


@dataclass(frozen=True)
class MutationValidationResult:
    accepted: bool
    reasons: tuple[str, ...]
    event: TaskEvent | None = None


def _owner_from_span(span: str, clauses: list[Clause]) -> str:
    normalized = normalize_for_match(span)
    if not normalized:
        return ""
    for clause in clauses:
        if normalized not in normalize_for_match(clause.text_raw):
            continue
        if span.strip().casefold() in FIRST_PERSON:
            return clause.speaker_name.strip()
        return span.strip()
    return ""


def _event_matches_cues(event_type: str, annotations: list[ClauseAnnotation]) -> bool:
    flags = set().union(*(item.flags for item in annotations))
    if event_type == "TASK_CANCEL":
        return "CANCELLATION" in flags
    if event_type == "TASK_REJECT":
        return "REJECTION" in flags
    if event_type in {"OWNER_ASSIGN", "OWNER_REASSIGN", "DEADLINE_SET", "DEADLINE_REPLACE"}:
        return "CORRECTION" in flags or "CANCELLATION" in flags or "REJECTION" in flags
    return False


def validate_mutation_resolution(
    response: MutationResolutionResponse,
    *,
    route: CandidateRoute,
    supplied_task_ids: set[str],
    ledger: TaskLedger,
    clauses_by_id: dict[str, Clause],
    annotations: dict[str, ClauseAnnotation],
    mentions: dict[str, DateMention],
    allowed_context_clause_ids: set[str],
    primary_clause_ids: set[str],
    minimum_confidence: float,
    start_sequence: int,
) -> MutationValidationResult:
    """Validate provider output in a fixed order before creating a TaskEvent."""

    if route != CandidateRoute.AI_MUTATION_CHECK:
        return MutationValidationResult(False, ("INVALID_CANDIDATE_ROUTE",))
    if response.decision == "UNRESOLVED":
        return MutationValidationResult(False, ("UNRESOLVED",))
    if response.confidence < minimum_confidence:
        return MutationValidationResult(False, ("LOW_CONFIDENCE",))
    if response.related_task_id not in supplied_task_ids:
        return MutationValidationResult(False, ("UNKNOWN_TASK_ID",))
    task = ledger.tasks.get(response.related_task_id)
    if task is None:
        return MutationValidationResult(False, ("TASK_NOT_AVAILABLE_AT_ANCHOR",))
    if task.status in {"CANCELLED", "REJECTED"}:
        return MutationValidationResult(False, ("TERMINAL_TASK",))
    if any(item not in allowed_context_clause_ids for item in response.source_clause_ids):
        return MutationValidationResult(False, ("SOURCE_OUTSIDE_BOUNDED_CONTEXT",))
    if not set(response.source_clause_ids) & primary_clause_ids:
        return MutationValidationResult(False, ("PRIMARY_SOURCE_NOT_CITED",))
    if response.anchor_clause_id not in primary_clause_ids:
        return MutationValidationResult(False, ("ANCHOR_OUTSIDE_PRIMARY",))
    if response.anchor_clause_id not in response.source_clause_ids:
        return MutationValidationResult(False, ("ANCHOR_NOT_CITED",))
    source_clauses = [clauses_by_id[item] for item in response.source_clause_ids]
    source_annotations = [annotations.get(item.clause_id) for item in source_clauses]
    if any(item is None for item in source_annotations):
        return MutationValidationResult(False, ("MISSING_SOURCE_ANNOTATION",))
    if response.event_type not in MUTATION_TYPES or not _event_matches_cues(
        response.event_type, [item for item in source_annotations if item is not None]
    ):
        return MutationValidationResult(False, ("EVENT_TYPE_NOT_GROUNDED",))

    owner = ""
    if response.event_type in OWNER_TYPES:
        if not response.owner_span:
            return MutationValidationResult(False, ("MISSING_OWNER_SPAN",))
        owner = _owner_from_span(response.owner_span, source_clauses)
        if not owner:
            return MutationValidationResult(False, ("UNGROUNDED_OWNER_SPAN",))
    elif response.owner_span:
        return MutationValidationResult(False, ("OWNER_NOT_ALLOWED_FOR_EVENT",))

    deadline_id = response.deadline_mention_id or ""
    if response.event_type in DEADLINE_TYPES:
        mention = mentions.get(deadline_id)
        if mention is None or mention.clause_id not in response.source_clause_ids:
            return MutationValidationResult(False, ("INVALID_DEADLINE_MENTION",))
    elif deadline_id:
        return MutationValidationResult(False, ("DEADLINE_NOT_ALLOWED_FOR_EVENT",))

    anchor = clauses_by_id[response.anchor_clause_id]
    return MutationValidationResult(
        True, (), TaskEvent(
            event_id=make_id("EVENT", start_sequence + 1),
            event_type=response.event_type,
            source_clause_ids=list(response.source_clause_ids),
            action_text=task.canonical_action,
            assignee=owner,
            deadline_mention_id=deadline_id,
            related_task_id=response.related_task_id,
            confidence=response.confidence,
            extraction_source="AI_MUTATION_ROUTER",
            order_index=anchor.order_index,
            anchor_clause_id=response.anchor_clause_id,
        )
    )
