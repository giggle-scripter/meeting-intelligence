"""Fail-closed validation and promotion of task-create proposals."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.candidate.proposal import TaskCreateProposal
from backend.app.models import Clause, ClauseAnnotation, DateMention, TaskEvent
from backend.app.preprocessing.unicode_normalizer import normalize_for_match
from backend.app.utils.ids import make_id
from backend.app.utils.text_similarity import similarity


NEGATIVE_GUARDS = {
    "ROOT_QUESTION": "QUESTION",
    "SUGGESTION_ONLY": "SUGGESTION",
    "HYPOTHETICAL": "HYPOTHETICAL",
    "PAST_COMPLETED": "PAST_COMPLETED",
    "PROGRESS_UPDATE": "PROGRESS_ONLY",
    "REJECTION": "REJECTED_PROPOSAL",
    "FUTURE_DISCUSSION": "FUTURE_DISCUSSION",
}
FIRST_PERSON_OWNER_SPANS = {
    "em", "tôi", "mình", "chúng tôi", "chúng em", "i", "me", "we", "us",
}


@dataclass(frozen=True)
class ProposalValidationResult:
    accepted: bool
    reasons: tuple[str, ...]
    event: TaskEvent | None = None


def _grounded_span(span: str, source_texts: list[str]) -> bool:
    normalized_span = normalize_for_match(span)
    if not normalized_span:
        return False
    for text in source_texts:
        normalized_text = normalize_for_match(text)
        if normalized_span in normalized_text:
            return True
        # Tolerate punctuation/transcription spacing only; semantic paraphrases
        # remain below this deliberately tight threshold.
        if similarity(normalized_span, normalized_text) >= 0.94:
            return True
    return False


def _resolve_owner(owner_span: str | None, clauses: list[Clause]) -> str:
    if owner_span is None:
        return ""
    normalized_owner = normalize_for_match(owner_span)
    for clause in clauses:
        if normalized_owner not in normalize_for_match(clause.text_raw):
            continue
        # Keep Vietnamese diacritics here: the name "Minh" must not collide
        # with the first-person pronoun "mình" after accent normalization.
        if owner_span.strip().casefold() in FIRST_PERSON_OWNER_SPANS:
            return clause.speaker_name.strip()
        return owner_span.strip()
    return ""


def validate_task_create_proposal(
    proposal: TaskCreateProposal,
    *,
    clauses_by_id: dict[str, Clause],
    annotations: dict[str, ClauseAnnotation],
    mentions: dict[str, DateMention],
    start_sequence: int,
    allowed_source_clause_ids: set[str] | None = None,
    required_primary_clause_ids: set[str] | None = None,
) -> ProposalValidationResult:
    """Validate evidence in a fixed order and promote only grounded proposals."""

    missing = [item for item in proposal.source_clause_ids if item not in clauses_by_id]
    if missing:
        return ProposalValidationResult(False, ("UNKNOWN_SOURCE_CLAUSE",))
    if allowed_source_clause_ids is not None and any(
        item not in allowed_source_clause_ids for item in proposal.source_clause_ids
    ):
        return ProposalValidationResult(False, ("SOURCE_OUTSIDE_BOUNDED_CONTEXT",))
    if required_primary_clause_ids is not None and not (
        set(proposal.source_clause_ids) & required_primary_clause_ids
    ):
        return ProposalValidationResult(False, ("PRIMARY_SOURCE_NOT_CITED",))
    source_clauses = [clauses_by_id[item] for item in proposal.source_clause_ids]

    if not _grounded_span(
        proposal.action_span,
        [clause.text_raw for clause in source_clauses],
    ):
        return ProposalValidationResult(False, ("UNGROUNDED_ACTION_SPAN",))

    assignee = _resolve_owner(proposal.owner_span, source_clauses)
    if proposal.owner_span is not None and not assignee:
        return ProposalValidationResult(False, ("UNGROUNDED_OWNER_SPAN",))

    deadline_id = proposal.deadline_mention_id or ""
    if deadline_id:
        mention = mentions.get(deadline_id)
        if mention is None or mention.clause_id not in proposal.source_clause_ids:
            return ProposalValidationResult(False, ("INVALID_DEADLINE_MENTION",))

    guard_reasons: list[str] = []
    for clause in source_clauses:
        annotation = annotations.get(clause.clause_id)
        if annotation is None:
            return ProposalValidationResult(False, ("MISSING_SOURCE_ANNOTATION",))
        for flag, reason in NEGATIVE_GUARDS.items():
            if flag in annotation.flags and reason not in guard_reasons:
                guard_reasons.append(reason)
        if clause.text_raw.rstrip().endswith("?") and "QUESTION" not in guard_reasons:
            guard_reasons.append("QUESTION")
    if guard_reasons:
        return ProposalValidationResult(False, tuple(guard_reasons))

    chronology_clauses = source_clauses
    if required_primary_clause_ids is not None:
        chronology_clauses = [
            clause for clause in source_clauses
            if clause.clause_id in required_primary_clause_ids
        ]
    anchor = min(chronology_clauses, key=lambda item: item.order_index)
    event = TaskEvent(
        event_id=make_id("EVENT", start_sequence + 1),
        event_type="TASK_CREATE",
        source_clause_ids=list(proposal.source_clause_ids),
        action_text=proposal.action_span,
        assignee=assignee,
        deadline_mention_id=deadline_id,
        confidence=proposal.confidence,
        extraction_source="AI_CREATE_PROPOSAL",
        order_index=anchor.order_index,
        anchor_clause_id=anchor.clause_id,
    )
    return ProposalValidationResult(True, (), event)
