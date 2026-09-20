"""Deterministic owner evidence for shadow evaluation."""

from __future__ import annotations

from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models import Clause, TaskEvent
from backend.app.preprocessing.unicode_normalizer import normalize_for_match


class OwnerEvidenceType(str, Enum):
    DIRECT_ASSIGNMENT = "DIRECT_ASSIGNMENT"
    SELF_COMMITMENT = "SELF_COMMITMENT"
    EXPLICIT_ACCEPTANCE = "EXPLICIT_ACCEPTANCE"
    OWNER_REASSIGNMENT = "OWNER_REASSIGNMENT"
    ROLE_ASSIGNMENT = "ROLE_ASSIGNMENT"


class OwnerEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_id: str
    span: str = Field(min_length=1)
    resolved_participant: str = Field(min_length=1)
    evidence_type: OwnerEvidenceType
    order_index: int


_SELF_COMMITMENT_RE = re.compile(
    r"^(?:dạ|vâng|ok(?:ay)?)[,.\s]+|"
    r"\b(?:em|tôi|mình|i|we)\s+(?:sẽ|will|nhận|phụ\s+trách)\b",
    re.IGNORECASE,
)
_ACCEPTANCE_RE = re.compile(
    r"^(?:dạ|vâng|ok(?:ay)?|được)[,.\s]*"
    r"(?:em|tôi|mình|i|we)?\s*(?:nhận|làm|take)\b",
    re.IGNORECASE,
)
_ROLE_ASSIGNMENT_RE = re.compile(r"\b(?:manager|lead|owner|phụ trách)\b", re.I)


def _owner_parts(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _find_owner_span(clause: Clause, owner: str) -> str:
    match = re.search(rf"(?<!\w){re.escape(owner)}(?!\w)", clause.text_raw, re.I)
    return match.group(0) if match else ""


def _evidence_type(event: TaskEvent, clause: Clause, owner: str) -> OwnerEvidenceType:
    if event.event_type == "OWNER_REASSIGN":
        return OwnerEvidenceType.OWNER_REASSIGNMENT
    if normalize_for_match(owner) == normalize_for_match(clause.speaker_name):
        if _ACCEPTANCE_RE.search(clause.text_raw):
            return OwnerEvidenceType.EXPLICIT_ACCEPTANCE
        if _SELF_COMMITMENT_RE.search(clause.text_raw):
            return OwnerEvidenceType.SELF_COMMITMENT
    if _ROLE_ASSIGNMENT_RE.search(clause.text_raw):
        return OwnerEvidenceType.ROLE_ASSIGNMENT
    return OwnerEvidenceType.DIRECT_ASSIGNMENT


def build_owner_evidence(
    event: TaskEvent,
    clauses_by_id: dict[str, Clause],
) -> tuple[OwnerEvidence, ...]:
    """Return only exact, source-clause-grounded owner evidence."""

    if not event.assignee:
        return ()
    evidence: list[OwnerEvidence] = []
    for owner in _owner_parts(event.assignee):
        for clause_id in event.source_clause_ids:
            clause = clauses_by_id.get(clause_id)
            if clause is None:
                continue
            span = _find_owner_span(clause, owner)
            if not span and normalize_for_match(owner) == normalize_for_match(clause.speaker_name):
                if _SELF_COMMITMENT_RE.search(clause.text_raw) or _ACCEPTANCE_RE.search(clause.text_raw):
                    span = clause.speaker_name
            if not span:
                continue
            evidence.append(OwnerEvidence(
                clause_id=clause.clause_id,
                span=span,
                resolved_participant=owner,
                evidence_type=_evidence_type(event, clause, owner),
                order_index=clause.order_index,
            ))
            break
    return tuple(evidence)
