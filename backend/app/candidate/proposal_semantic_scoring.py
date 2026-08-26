"""Deterministic semantic-quality signals for shadow proposal ranking."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from .action_canonicalization import build_action_frame
from .proposal_span_identity import ProposalSpanIdentity


class SemanticProposalScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity_key: str
    primary_clause_id: str
    score: float = Field(ge=0.0, le=1.0)
    reasons: tuple[str, ...] = ()


_GENERIC_OBJECTS = {"việc", "phần", "task", "này", "đó", "it", "this", "that"}


def build_semantic_proposal_scores(
    identities: list[ProposalSpanIdentity],
) -> list[SemanticProposalScore]:
    """Score only meaning already present in an exact action span."""

    result: list[SemanticProposalScore] = []
    for identity in identities:
        if not identity.action_span:
            result.append(SemanticProposalScore(identity_key=identity.identity_key, primary_clause_id=identity.primary_clause_id, score=0.0, reasons=("NO_GROUNDED_SPAN",)))
            continue
        frame = build_action_frame(identity.action_span.text, (identity.primary_clause_id,))
        if not frame.valid:
            result.append(SemanticProposalScore(identity_key=identity.identity_key, primary_clause_id=identity.primary_clause_id, score=0.0, reasons=(f"INVALID_{frame.rejection_reason}",)))
            continue
        object_tokens = re.findall(r"\w+", frame.object.casefold())
        concrete_tokens = [item for item in object_tokens if item not in _GENERIC_OBJECTS]
        score = 0.55
        reasons = ["CONCRETE_ACTION"]
        if len(concrete_tokens) >= 2:
            score += 0.25; reasons.append("SPECIFIC_OBJECT")
        elif concrete_tokens:
            score += 0.10; reasons.append("MINIMAL_OBJECT")
        if len(concrete_tokens) >= 4:
            score += 0.10; reasons.append("DETAILED_DELIVERABLE")
        if not re.search(r"\b(?:có thể|nên|dự kiến|cần)\b", identity.action_span.text, re.I):
            score += 0.10; reasons.append("NON_SPECULATIVE")
        result.append(SemanticProposalScore(identity_key=identity.identity_key, primary_clause_id=identity.primary_clause_id, score=round(min(score, 1.0), 3), reasons=tuple(reasons)))
    return result
