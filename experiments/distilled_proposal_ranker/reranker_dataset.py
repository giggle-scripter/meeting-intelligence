"""Gold-isolated reranker labels and hard-negative pairs."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import GroundedSpan, ProposalRecord
from .hard_negatives import select_hard_negatives


@dataclass(frozen=True)
class RerankerLabel:
    proposal_id: str
    label: float
    sample_weight: float
    paired_negative_ids: tuple[str, ...]


def character_iou(left: GroundedSpan, right: GroundedSpan) -> float:
    if left.clause_id != right.clause_id:
        return 0.0
    intersection = max(0, min(left.end, right.end) - max(left.start, right.start))
    union = max(left.end, right.end) - min(left.start, right.start)
    return intersection / union if union else 0.0


def label_training_proposals(
    proposals: list[ProposalRecord], gold_spans: list[GroundedSpan], *, human_weight: float = 1.0
) -> list[RerankerLabel]:
    labels: list[RerankerLabel] = []
    positive_ids: list[str] = []
    raw_labels: dict[str, float] = {}
    for proposal in proposals:
        exact = any(proposal.action_span == gold for gold in gold_spans)
        best_iou = max((character_iou(proposal.action_span, gold) for gold in gold_spans), default=0.0)
        label = 1.0 if exact else best_iou if best_iou >= 0.8 else 0.0
        raw_labels[proposal.proposal_id] = label
        if label == 1.0:
            positive_ids.append(proposal.proposal_id)
    for proposal in proposals:
        pairs = ()
        if proposal.proposal_id in positive_ids:
            pairs = tuple(item.proposal_id for item in select_hard_negatives(proposals, proposal.proposal_id))
        labels.append(RerankerLabel(proposal.proposal_id, raw_labels[proposal.proposal_id], human_weight, pairs))
    return labels
