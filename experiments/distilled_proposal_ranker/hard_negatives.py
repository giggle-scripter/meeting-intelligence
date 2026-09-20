"""Deterministic priority taxonomy for within-meeting hard negatives."""

from __future__ import annotations

from .contracts import ProposalRecord


PRIORITY = {
    "SIBLING_AMBIGUITY": 1,
    "SUGGESTION_ONLY": 2,
    "QUESTION_UNACCEPTED": 3,
    "PAST_COMPLETED": 4,
    "PROGRESS_ONLY": 4,
    "RECAP_DUPLICATE": 5,
    "MUTATION_ONLY": 6,
    "REFERENCE_ONLY": 6,
    "OWNER_FRAGMENT": 7,
    "DEADLINE_FRAGMENT": 7,
    "BASELINE_FALSE_CREATE": 8,
    "LEXICAL_NEAR": 9,
    "CROSS_MEETING": 10,
}


def negative_reason(proposal: ProposalRecord) -> str:
    reasons = set(proposal.deterministic_reasons)
    if "SIBLING_AMBIGUITY" in proposal.ambiguity_flags:
        return "SIBLING_AMBIGUITY"
    if "SUGGESTION" in reasons:
        return "SUGGESTION_ONLY"
    if "QUESTION" in reasons:
        return "QUESTION_UNACCEPTED"
    if "PAST_COMPLETED" in reasons:
        return "PAST_COMPLETED"
    if "PROGRESS_ONLY" in reasons:
        return "PROGRESS_ONLY"
    if "RECAP_ITEM" in reasons:
        return "RECAP_DUPLICATE"
    if proposal.kind == "UPDATE":
        return "MUTATION_ONLY"
    if proposal.kind == "REFERENCE":
        return "REFERENCE_ONLY"
    if not proposal.action_span.text and proposal.owner_refs:
        return "OWNER_FRAGMENT"
    if not proposal.action_span.text and proposal.deadline_refs:
        return "DEADLINE_FRAGMENT"
    return "LEXICAL_NEAR"


def select_hard_negatives(proposals: list[ProposalRecord], positive_id: str, limit: int = 8) -> list[ProposalRecord]:
    negatives = [item for item in proposals if item.proposal_id != positive_id]
    negatives.sort(key=lambda item: (PRIORITY[negative_reason(item)], item.order_index, item.proposal_id))
    return negatives[:limit]
