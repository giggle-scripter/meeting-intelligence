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
    proposal_kind: Literal["CREATE", "UPDATE", "REFERENCE"] = "REFERENCE"
    authority_evidence: tuple[str, ...] = ()
    confidence_components: dict[str, float] = Field(default_factory=dict)
    chronological_anchor_clause_id: str = ""
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
_ACTION_PREFIX_RE = re.compile(
    r"\b(?:em|tôi|mình|anh|chị|chúng\s+tôi|we|i)\s+(?:sẽ|will|nhận)\s+",
    re.I,
)
_DIRECT_ASSIGNMENT_RE = re.compile(
    r"\b(?:giao\s+cho\s+)?(?P<owner>[A-ZÀ-Ỹ][\wÀ-ỹ'-]+)\s*,?\s*"
    r"(?:em|anh|chị|bạn)?\s*(?P<action>(?:hoàn thành|chuẩn bị|viết|gửi|review|"
    r"kiểm tra|cập nhật|phân tích|triển khai|fix|sửa|làm)\b.+)", re.I,
)
_TRAILER_RE = re.compile(r"\s*(?:,|;)?\s*(?:trước|vào|đến|deadline|hạn(?:\s+chót)?|by|on)\b.*$", re.I)
_ACTION_VERB_RE = re.compile(
    r"\b(?:hoàn thành|chuẩn bị|viết|gửi|review|kiểm tra|cập nhật|phân tích|"
    r"triển khai|fix|sửa|làm|hỗ trợ|setup|soạn|tạo|cấp|cài đặt|xác định|"
    r"test|chụp|khảo sát|debug|thiết lập|cấu hình|xử lý|ẩn danh|update|"
    r"lập|check|yêu cầu|seed)\b",
    re.I,
)
_ACTION_STOP_RE = re.compile(
    r"(?=\s*(?:,\s*(?:vì|chứ|mà|để)\b|[,;]\s*(?:deadline|hạn)\b|"
    r"\s+(?:trước|vào|đến|deadline|hạn(?:\s+chót)?|trong|ngày|thứ)\b|"
    r"\s+(?:nhé|nhá|ạ|đi)\b|\s+(?:được không|thế nào)\b|\s*[.?!;]|\s*[\"”»]|"
    r"\s*(?:–|-)\s*deadline\b|\s+và\s+(?:hoàn thành|chuẩn bị|viết|gửi|"
    r"review|kiểm tra|cập nhật|phân tích|triển khai|fix|sửa|làm|test)\b))",
    re.I,
)


def _candidate_id(primary_ids: tuple[str, ...], action: str) -> str:
    return "ACAND-" + sha256("|".join((*primary_ids, action.casefold().strip())).encode("utf-8")).hexdigest()[:16]


def _span_from_action_match(clause: Clause, start: int) -> GroundedSpan | None:
    text = clause.text_raw
    suffix = text[start:]
    stop = _ACTION_STOP_RE.search(suffix)
    end = start + (stop.start() if stop else len(suffix))
    action = text[start:end].strip(" ,.;:!?")
    if not action:
        return None
    offset = text.find(action, start, end)
    return GroundedSpan(clause_id=clause.clause_id, start=offset, end=offset + len(action), text=action)


def extract_action_spans(clause: Clause, annotation: ClauseAnnotation) -> tuple[GroundedSpan, ...]:
    """Return every independently bounded action phrase present in a clause."""

    text = clause.text_raw
    spans: list[GroundedSpan] = []
    for match in _ACTION_VERB_RE.finditer(text):
        span = _span_from_action_match(clause, match.start())
        if span and span not in spans:
            spans.append(span)
    assignment = _DIRECT_ASSIGNMENT_RE.search(text)
    if assignment and annotation.flags & {"DIRECT_ASSIGNMENT", "ROOT_QUESTION"}:
        span = _span_from_action_match(clause, assignment.start("action"))
        if span and span not in spans:
            spans.append(span)
    return tuple(spans)


def extract_action_span(clause: Clause, annotation: ClauseAnnotation) -> GroundedSpan | None:
    spans = extract_action_spans(clause, annotation)
    return spans[0] if spans else None


def _action_span(clause: Clause, annotation: ClauseAnnotation) -> GroundedSpan | None:
    """Backward-compatible private alias for the V2 candidate builder."""

    return extract_action_span(clause, annotation)


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
            if pending_question is not None:
                proposal, proposal_action = pending_question
                candidates.append(ActionCandidate(
                    candidate_id=_candidate_id((proposal.clause_id,), proposal_action.text),
                    primary_clause_ids=(proposal.clause_id,), action_spans=(proposal_action,),
                    candidate_kind="UNKNOWN", state=CandidateState.REFERENCE_ONLY,
                    commitment_signals=("QUESTION_UNCONFIRMED",), negative_signals=("ROOT_QUESTION",),
                    first_order_index=proposal.order_index, last_order_index=proposal.order_index,
                    builder_version=builder_version,
                ))
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
    if pending_question is not None:
        proposal, proposal_action = pending_question
        candidates.append(ActionCandidate(
            candidate_id=_candidate_id((proposal.clause_id,), proposal_action.text),
            primary_clause_ids=(proposal.clause_id,), action_spans=(proposal_action,),
            candidate_kind="UNKNOWN", state=CandidateState.REFERENCE_ONLY,
            commitment_signals=("QUESTION_UNCONFIRMED",), negative_signals=("ROOT_QUESTION",),
            first_order_index=proposal.order_index, last_order_index=proposal.order_index,
            builder_version=builder_version,
        ))
    return candidates


def build_action_proposals_v3(
    clauses: list[Clause], annotations: dict[str, ClauseAnnotation],
    mentions: dict[str, DateMention], *, builder_version: str = "action-proposal-v3",
    note_supported_clause_ids: set[str] | None = None,
) -> list[ActionCandidate]:
    """Build grounded, multi-clause V3 proposals in shadow mode.

    V3 keeps the stable V2 span/negative rules, then makes the proposal contract
    explicit.  It is deliberately not a router: callers may inspect its
    confidence and authority evidence but cannot create a ledger event from it.
    """

    note_supported_clause_ids = note_supported_clause_ids or set()
    base = build_action_candidates(
        clauses, annotations, mentions, builder_version=builder_version,
    )
    enriched: list[ActionCandidate] = []
    for candidate in base:
        if candidate.candidate_kind == "CREATE":
            proposal_kind = "CREATE"
        elif candidate.candidate_kind == "MUTATION":
            proposal_kind = "UPDATE"
        else:
            proposal_kind = "REFERENCE"
        authority = tuple(sorted(set(candidate.commitment_signals)))
        primary_flags = set()
        for clause_id in candidate.primary_clause_ids:
            primary_flags.update(annotations[clause_id].flags)
        confidence = {
            "rule_cue": 1.0 if authority else 0.35,
            "grounded_action": 1.0 if candidate.action_spans else 0.0,
            "negative_guard": 0.0 if candidate.negative_signals else 1.0,
            "multi_clause_support": 1.0 if len(candidate.primary_clause_ids) + len(candidate.support_clause_ids) > 1 else 0.0,
            "note_grounding": 1.0 if set(candidate.primary_clause_ids) & note_supported_clause_ids else 0.0,
        }
        # A recap can support an existing identity but is never a V3 create.
        if candidate.candidate_kind == "RECAP_ITEM":
            proposal_kind = "REFERENCE"
            authority = tuple(sorted(set(authority) | {"RECAP_REFERENCE"}))
        enriched.append(candidate.model_copy(update={
            "proposal_kind": proposal_kind,
            "authority_evidence": authority or tuple(sorted(primary_flags & _POSITIVE_FLAGS)),
            "confidence_components": confidence,
            "chronological_anchor_clause_id": candidate.primary_clause_ids[0],
        }))
    return enriched


def summarize_action_candidates(candidates: list[ActionCandidate]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(candidates)}
    for candidate in candidates:
        counts[f"kind_{candidate.candidate_kind}"] = counts.get(f"kind_{candidate.candidate_kind}", 0) + 1
        counts[f"state_{candidate.state.value}"] = counts.get(f"state_{candidate.state.value}", 0) + 1
    return dict(sorted(counts.items()))
