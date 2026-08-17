"""Merge rule, classifier, and grounded-note signals without routing them."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.ml.contracts import ActionLabel, ActionPrediction
from backend.app.models import Clause, ClauseAnnotation

if TYPE_CHECKING:
    from backend.app.v2.models import MeetingContext, NoteCue


_RULE_CREATE_FLAGS = {
    "FIRST_PERSON_COMMITMENT",
    "DIRECT_ASSIGNMENT",
    "CONFIRMATION",
}
_RULE_MUTATION_FLAGS = {"CORRECTION", "CANCELLATION", "REJECTION"}
_STRONG_NEGATIVE_FLAGS = {
    "ADMIN_FOLLOWUP",
    "BRAINSTORM",
    "FUTURE_DISCUSSION",
    "HYPOTHETICAL",
    "PAST_COMPLETED",
    "PROGRESS_UPDATE",
    "ROOT_QUESTION",
    "SUGGESTION_ONLY",
}


class CandidateEvidence(BaseModel):
    """Version-1 evidence envelope; deliberately contains no final decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    focus_clause_id: str = Field(min_length=1)
    clause_ids: list[str] = Field(min_length=1)

    rule_score: float | None = None
    classifier_score: float | None = Field(default=None, ge=0.0, le=1.0)
    classifier_clear_score: float | None = Field(default=None, ge=0.0, le=1.0)
    classifier_possible_score: float | None = Field(default=None, ge=0.0, le=1.0)
    classifier_update_score: float | None = Field(default=None, ge=0.0, le=1.0)
    classifier_non_action_score: float | None = Field(default=None, ge=0.0, le=1.0)
    classifier_label: ActionLabel | None = None
    note_score: float | None = Field(default=None, ge=0.0, le=1.0)

    has_commitment_cue: bool
    has_assignment_cue: bool
    has_date: bool
    has_question_cue: bool
    has_hypothetical_cue: bool
    has_past_completed_cue: bool
    has_rejection_cue: bool
    has_action_verb: bool
    has_rule_create_cue: bool
    has_rule_mutation_cue: bool
    has_unique_mutation_target: bool
    has_strong_negative_guard: bool
    has_note_action_signal: bool
    has_note_state_signal: bool
    has_ambiguous_note_signal: bool
    topic_id: str | None = None

    @model_validator(mode="after")
    def validate_clause_identity(self) -> "CandidateEvidence":
        if self.focus_clause_id not in self.clause_ids:
            raise ValueError("focus_clause_id must be included in clause_ids")
        if len(set(self.clause_ids)) != len(self.clause_ids):
            raise ValueError("clause_ids must not contain duplicates")
        classifier_fields = (
            self.classifier_score,
            self.classifier_clear_score,
            self.classifier_possible_score,
            self.classifier_update_score,
            self.classifier_non_action_score,
        )
        if self.classifier_label is None and any(
            value is not None for value in classifier_fields
        ):
            raise ValueError("classifier scores require classifier_label")
        if self.classifier_label is not None and any(
            value is None for value in classifier_fields
        ):
            raise ValueError("classifier_label requires every classifier score")
        if self.classifier_label is not None:
            assert self.classifier_score is not None
            assert self.classifier_clear_score is not None
            assert self.classifier_possible_score is not None
            assert self.classifier_update_score is not None
            assert self.classifier_non_action_score is not None
            if abs(
                self.classifier_score
                - (self.classifier_clear_score + self.classifier_possible_score)
            ) > 1e-6:
                raise ValueError(
                    "classifier_score must equal clear plus possible probability"
                )
            if abs(
                self.classifier_clear_score
                + self.classifier_possible_score
                + self.classifier_update_score
                + self.classifier_non_action_score
                - 1.0
            ) > 1e-6:
                raise ValueError("classifier probabilities must sum to one")
        return self


def _note_signals(cues: Sequence[NoteCue]) -> tuple[float | None, bool, bool, bool]:
    if not cues:
        return None, False, False, False
    return (
        max(cue.grounding_score for cue in cues),
        any(getattr(cue.kind, "value", cue.kind) == "ACTION_HINT" for cue in cues),
        any(getattr(cue.kind, "value", cue.kind) == "STATE_HINT" for cue in cues),
        any(cue.status == "AMBIGUOUS" for cue in cues),
    )


def _topic_id(context: MeetingContext | None, clause_id: str) -> str | None:
    if context is None:
        return None
    relevance = context.clause_relevance.get(clause_id)
    if relevance is None or not relevance.topic_ids:
        return None
    return sorted(relevance.topic_ids)[0]


def build_candidate_evidence(
    clauses: Sequence[Clause],
    annotations: dict[str, ClauseAnnotation],
    *,
    predictions_by_clause: dict[str, ActionPrediction] | None = None,
    note_cues_by_clause: dict[str, tuple[NoteCue, ...]] | None = None,
    meeting_context: MeetingContext | None = None,
) -> list[CandidateEvidence]:
    """Build one auditable evidence record per clause in stable input order."""

    predictions_by_clause = predictions_by_clause or {}
    note_cues_by_clause = note_cues_by_clause or {}
    result: list[CandidateEvidence] = []
    for clause in clauses:
        annotation = annotations[clause.clause_id]
        flags = annotation.flags
        prediction = predictions_by_clause.get(clause.clause_id)
        note_score, note_action, note_state, note_ambiguous = _note_signals(
            note_cues_by_clause.get(clause.clause_id, ())
        )
        probabilities = prediction.probabilities if prediction else None
        result.append(
            CandidateEvidence(
                candidate_id=f"CAND-{clause.clause_id}",
                focus_clause_id=clause.clause_id,
                clause_ids=[clause.clause_id],
                rule_score=float(annotation.score),
                classifier_score=(
                    probabilities.clear_action + probabilities.possible_action
                    if probabilities
                    else None
                ),
                classifier_clear_score=(
                    probabilities.clear_action if probabilities else None
                ),
                classifier_possible_score=(
                    probabilities.possible_action if probabilities else None
                ),
                classifier_update_score=(
                    probabilities.update_only if probabilities else None
                ),
                classifier_non_action_score=(
                    probabilities.non_action if probabilities else None
                ),
                classifier_label=prediction.label if prediction else None,
                note_score=note_score,
                has_commitment_cue="FIRST_PERSON_COMMITMENT" in flags,
                has_assignment_cue="DIRECT_ASSIGNMENT" in flags,
                has_date="DATE_MENTION" in flags,
                has_question_cue=(
                    "ROOT_QUESTION" in flags
                    or clause.text_raw.rstrip().endswith("?")
                ),
                has_hypothetical_cue="HYPOTHETICAL" in flags,
                has_past_completed_cue="PAST_COMPLETED" in flags,
                has_rejection_cue="REJECTION" in flags,
                has_action_verb="ACTION_VERB" in flags,
                has_rule_create_cue=bool(flags & _RULE_CREATE_FLAGS),
                has_rule_mutation_cue=bool(flags & _RULE_MUTATION_FLAGS),
                has_unique_mutation_target="EXPLICIT_TASK_LABEL" in flags,
                has_strong_negative_guard=bool(flags & _STRONG_NEGATIVE_FLAGS),
                has_note_action_signal=note_action,
                has_note_state_signal=note_state,
                has_ambiguous_note_signal=note_ambiguous,
                topic_id=_topic_id(meeting_context, clause.clause_id),
            )
        )
    return result
