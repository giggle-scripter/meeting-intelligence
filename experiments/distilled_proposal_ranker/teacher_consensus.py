"""Fail-closed consensus across authority-first and lifecycle-first teachers."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import TeacherDecisionRecord, TeacherResponse


@dataclass(frozen=True)
class ConsensusLabel:
    proposal_id: str
    decision: str
    source_label: str
    confidence: float


def teacher_consensus(responses: list[TeacherResponse]) -> list[ConsensusLabel]:
    by_proposal: dict[str, list[TeacherDecisionRecord]] = {}
    for response in responses:
        for record in response.records:
            by_proposal.setdefault(record.proposal_id, []).append(record)
    output: list[ConsensusLabel] = []
    for proposal_id in sorted(by_proposal):
        items = by_proposal[proposal_id]
        if len(items) == 2 and items[0].decision == items[1].decision and items[0].action_span_ref == items[1].action_span_ref:
            output.append(ConsensusLabel(proposal_id, items[0].decision, "teacher_consensus", min(item.confidence for item in items)))
        elif len(items) == 1 and items[0].confidence >= 0.90:
            output.append(ConsensusLabel(proposal_id, items[0].decision, "teacher_single", items[0].confidence))
        else:
            output.append(ConsensusLabel(proposal_id, "UNRESOLVED", "unresolved", max(item.confidence for item in items)))
    return output
