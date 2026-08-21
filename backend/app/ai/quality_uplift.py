"""Calibrated preflight for AI task-create checks.

The provider receives only candidates that are already bounded by the local
router and have grounded action evidence.  This module deliberately does not
call a provider or promote an event.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from backend.app.candidate.action_candidates import ActionCandidate, CandidateState
from backend.app.candidate.commitment_router import CommitmentDecision, CommitmentRoute
from backend.app.candidate.evidence import CandidateEvidence
from backend.app.candidate.router import CandidateDecision, CandidateRoute


HARD_NEGATIVE_ROUTES = {CommitmentRoute.DROP}
NON_CREATE_KINDS = {"MUTATION", "REFERENCE", "RECAP_ITEM", "UNKNOWN"}


@dataclass(frozen=True)
class AiCreatePreflight:
    candidate_id: str
    focus_clause_id: str
    eligible: bool
    reason: str


def preflight_ai_create_checks(
    decisions: list[CandidateDecision],
    evidence: list[CandidateEvidence],
    action_candidates: list[ActionCandidate],
    commitment_decisions: list[CommitmentDecision],
    *,
    maximum: int,
) -> tuple[list[AiCreatePreflight], list[str]]:
    """Return all audits plus stable, bounded eligible candidate IDs.

    A hard-negative commitment decision always wins.  A grounded CREATE action
    span is required; provider output must therefore refine an existing
    candidate rather than discover an arbitrary transcript clause.
    """

    if maximum <= 0:
        raise ValueError("maximum must be positive")
    evidence_by_id = {item.candidate_id: item for item in evidence}
    actions_by_clause: dict[str, list[ActionCandidate]] = {}
    for candidate in action_candidates:
        for clause_id in candidate.primary_clause_ids:
            actions_by_clause.setdefault(clause_id, []).append(candidate)
    commitments_by_clause: dict[str, list[CommitmentDecision]] = {}
    action_by_id = {item.candidate_id: item for item in action_candidates}
    for decision in commitment_decisions:
        action = action_by_id.get(decision.candidate_id)
        if action is None:
            continue
        for clause_id in action.primary_clause_ids:
            commitments_by_clause.setdefault(clause_id, []).append(decision)

    audits: list[AiCreatePreflight] = []
    eligible: list[tuple[float, int, str]] = []
    for decision in decisions:
        if decision.route is not CandidateRoute.AI_CREATE_CHECK:
            continue
        item = evidence_by_id.get(decision.candidate_id)
        if item is None:
            continue
        if item.has_strong_negative_guard:
            reason = "EVIDENCE_HARD_NEGATIVE"
        elif any(
            choice.route in HARD_NEGATIVE_ROUTES
            for choice in commitments_by_clause.get(item.focus_clause_id, [])
        ):
            reason = "COMMITMENT_HARD_NEGATIVE"
        else:
            grounded = [
                candidate
                for candidate in actions_by_clause.get(item.focus_clause_id, [])
                if candidate.candidate_kind not in NON_CREATE_KINDS
                and candidate.state is not CandidateState.REFERENCE_ONLY
                and candidate.action_spans
            ]
            reason = "ELIGIBLE" if grounded else "NO_GROUNDED_CREATE_SPAN"
        audit = AiCreatePreflight(
            candidate_id=item.candidate_id,
            focus_clause_id=item.focus_clause_id,
            eligible=reason == "ELIGIBLE",
            reason=reason,
        )
        audits.append(audit)
        if audit.eligible:
            eligible.append((decision.confidence, len(eligible), decision.candidate_id))

    selected = [
        candidate_id
        for _, _, candidate_id in sorted(eligible, key=lambda item: (-item[0], item[1]))[:maximum]
    ]
    return audits, selected


def summarize_ai_create_preflight(records: list[AiCreatePreflight]) -> dict[str, int]:
    return dict(sorted(Counter(record.reason for record in records).items()))
