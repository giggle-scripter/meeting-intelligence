"""Span-grounded action candidates for Q1 shadow evaluation.

Candidates are evidence objects only. They cannot create events or task IDs.
"""

from __future__ import annotations

from enum import Enum
from hashlib import sha256
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.models import Clause, ClauseAnnotation, DateMention


class CandidateState(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CONFIRMED = "CONFIRMED"
    REFERENCE_ONLY = "REFERENCE_ONLY"


class GroundedSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "GroundedSpan":
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        return self


class ActionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    primary_clause_ids: tuple[str, ...] = Field(min_length=1)
    support_clause_ids: tuple[str, ...] = ()
    action_spans: tuple[GroundedSpan, ...] = ()
    owner_spans: tuple[GroundedSpan, ...] = ()
    deadline_mention_ids: tuple[str, ...] = ()
    candidate_kind: Literal["CREATE", "MUTATION", "REFERENCE", "RECAP_ITEM", "UNKNOWN"]
    state: CandidateState
    commitment_signals: tuple[str, ...] = ()
    negative_signals: tuple[str, ...] = ()
    topic_ids: tuple[str, ...] = ()
    first_order_index: int
    last_order_index: int
    builder_version: str

    @model_validator(mode="after")
    def validate_identity(self) -> "ActionCandidate":
        if len(set(self.primary_clause_ids)) != len(self.primary_clause_ids):
            raise ValueError("primary_clause_ids must be unique")
        if set(self.primary_clause_ids) & set(self.support_clause_ids):
            raise ValueError("support clauses must not repeat primary clauses")
        if self.candidate_kind == "CREATE" and not self.action_spans:
            raise ValueError("CREATE candidates require a grounded action span")
        return self


_POSITIVE_FLAGS = {"FIRST_PERSON_COMMITMENT", "DIRECT_ASSIGNMENT", "CONFIRMATION"}
_NEGATIVE_FLAGS = {
    "ROOT_QUESTION", "SUGGESTION_ONLY", "HYPOTHETICAL", "PAST_COMPLETED",
    "PROGRESS_UPDATE", "FUTURE_DISCUSSION", "ADMIN_FOLLOWUP", "REJECTION", "CANCELLATION",
}
_MUTATION_FLAGS = {"CORRECTION", "CANCELLATION", "REJECTION"}
_RECAP_RE = re.compile(r"\b(?:tổng kết|chốt lại|recap|final list|final recap)\b", re.I)
_ACCEPT_RE = re.compile(r"^(?:dạ|vâng|ok(?:ay)?|được|em nhận|tôi nhận|mình nhận)\b", re.I)
_ACTION_PREFIX_RE = re.compile(r"\b(?:em|tôi|mình|chúng tôi|we)\s+(?:sẽ|will|nhận)\s+", re.I)
_DIRECT_ASSIGNMENT_RE = re.compile(
    r"\b(?:giao\s+cho\s+)?(?P<owner>[A-ZÀ-Ỹ][\wÀ-ỹ'-]+)\s*,?\s*"
    r"(?:em|anh|chị|bạn)?\s*(?P<action>(?:hoàn thành|chuẩn bị|viết|gửi|review|"
    r"kiểm tra|cập nhật|phân tích|triển khai|fix|sửa|làm)\b.+)", re.I,
)
_TRAILER_RE = re.compile(r"\s*(?:,|;)?\s*(?:trước|vào|đến|deadline|hạn(?:\s+chót)?|by|on)\b.*$", re.I)


def _candidate_id(primary_ids: tuple[str, ...], action: str) -> str:
    return "ACAND-" + sha256("|".join((*primary_ids, action.casefold().strip())).encode("utf-8")).hexdigest()[:16]


def _action_span(clause: Clause, annotation: ClauseAnnotation) -> GroundedSpan | None:
    text = clause.text_raw
    match = _ACTION_PREFIX_RE.search(text)
    if match:
        start = match.end()
        action = _TRAILER_RE.sub("", text[start:]).strip(" ,.;:!?")
        if action:
            return GroundedSpan(clause_id=clause.clause_id, start=start, end=start + len(action), text=action)
    assignment = _DIRECT_ASSIGNMENT_RE.search(text)
    if assignment and annotation.flags & {"DIRECT_ASSIGNMENT", "ROOT_QUESTION"}:
        start, end = assignment.span("action")
        action = _TRAILER_RE.sub("", text[start:end]).strip(" ,.;:!?")
        if action:
            return GroundedSpan(clause_id=clause.clause_id, start=start, end=start + len(action), text=action)
    return None


def _owner_span(clause: Clause) -> GroundedSpan | None:
    match = _DIRECT_ASSIGNMENT_RE.search(clause.text_raw)
    if not match:
        return None
    start, end = match.span("owner")
    return GroundedSpan(clause_id=clause.clause_id, start=start, end=end, text=match.group("owner"))


def build_action_candidates(
    clauses: list[Clause], annotations: dict[str, ClauseAnnotation], mentions: dict[str, DateMention], *,
    builder_version: str = "action-candidate-v2",
) -> list[ActionCandidate]:
    """Build stable, chronological candidates without changing production output."""

    mentions_by_clause: dict[str, list[str]] = {}
    for mention in mentions.values():
        if mention.purpose == "DEADLINE":
            mentions_by_clause.setdefault(mention.clause_id, []).append(mention.date_mention_id)
    candidates: list[ActionCandidate] = []
    pending_question: tuple[Clause, GroundedSpan] | None = None
    for clause in clauses:
        annotation = annotations[clause.clause_id]
        positive = tuple(sorted(annotation.flags & _POSITIVE_FLAGS))
        negative = tuple(sorted(annotation.flags & _NEGATIVE_FLAGS))
        action = _action_span(clause, annotation)
        if pending_question and _ACCEPT_RE.search(clause.text_raw):
            proposal, proposal_action = pending_question
            candidates.append(ActionCandidate(
                candidate_id=_candidate_id((proposal.clause_id, clause.clause_id), proposal_action.text),
                primary_clause_ids=(proposal.clause_id, clause.clause_id), action_spans=(proposal_action,),
                candidate_kind="CREATE", state=CandidateState.ACCEPTED,
                commitment_signals=("QUESTION_THEN_ACCEPTANCE", "EXPLICIT_ACCEPTANCE"),
                first_order_index=proposal.order_index, last_order_index=clause.order_index,
                builder_version=builder_version,
            ))
            pending_question = None
            continue
        if "ROOT_QUESTION" in annotation.flags and action:
            pending_question = (clause, action)
            continue
        if action and (positive or "ACTION_VERB" in annotation.flags):
            if annotation.flags & _MUTATION_FLAGS:
                kind, state = "MUTATION", CandidateState.REFERENCE_ONLY
            elif _RECAP_RE.search(clause.text_raw):
                kind, state = "RECAP_ITEM", CandidateState.CONFIRMED
            elif negative:
                kind, state = "UNKNOWN", CandidateState.REFERENCE_ONLY
            else:
                kind, state = "CREATE", CandidateState.CONFIRMED if "CONFIRMATION" in positive else CandidateState.PROPOSED
            owner = _owner_span(clause)
            candidates.append(ActionCandidate(
                candidate_id=_candidate_id((clause.clause_id,), action.text), primary_clause_ids=(clause.clause_id,),
                action_spans=(action,), owner_spans=(owner,) if owner else (),
                deadline_mention_ids=tuple(sorted(mentions_by_clause.get(clause.clause_id, []))),
                candidate_kind=kind, state=state, commitment_signals=positive, negative_signals=negative,
                first_order_index=clause.order_index, last_order_index=clause.order_index, builder_version=builder_version,
            ))
        elif mentions_by_clause.get(clause.clause_id) and candidates:
            previous = candidates[-1]
            if previous.last_order_index == clause.order_index - 1 and previous.candidate_kind == "CREATE":
                candidates[-1] = previous.model_copy(update={
                    "support_clause_ids": (clause.clause_id,),
                    "deadline_mention_ids": tuple(sorted(set(previous.deadline_mention_ids) | set(mentions_by_clause[clause.clause_id]))),
                    "last_order_index": clause.order_index,
                })
    return candidates


def summarize_action_candidates(candidates: list[ActionCandidate]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(candidates)}
    for candidate in candidates:
        counts[f"kind_{candidate.candidate_kind}"] = counts.get(f"kind_{candidate.candidate_kind}", 0) + 1
        counts[f"state_{candidate.state.value}"] = counts.get(f"state_{candidate.state.value}", 0) + 1
    return dict(sorted(counts.items()))
