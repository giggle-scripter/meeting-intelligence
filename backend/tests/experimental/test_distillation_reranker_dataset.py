from __future__ import annotations

from experiments.distilled_proposal_ranker.contracts import GroundedSpan, ProposalRecord
from experiments.distilled_proposal_ranker.reranker_dataset import character_iou, label_training_proposals


def _proposal(identifier: str, start: int, end: int) -> ProposalRecord:
    return ProposalRecord(
        proposal_id=identifier, input_hash="0" * 64, case_id="CASE", kind="CREATE", cluster_identity=identifier,
        action_span=GroundedSpan(clause_id="C1", start=start, end=end, text="x" * (end - start)),
        authority_refs=["C2"], acceptance_refs=[], owner_refs=[], deadline_refs=[], negative_refs=[], relations=[],
        deterministic_score=0, deterministic_reasons=[], neural_span_score=0, source_type="LATTICE", ambiguity_flags=[], order_index=start,
    )


def test_exact_positive_and_near_boundary_soft_label() -> None:
    gold = GroundedSpan(clause_id="C1", start=0, end=10, text="x" * 10)
    exact = _proposal("exact", 0, 10)
    near = _proposal("near", 0, 9)
    wrong = _proposal("wrong", 20, 25)
    labels = {item.proposal_id: item for item in label_training_proposals([exact, near, wrong], [gold])}
    assert labels["exact"].label == 1.0
    assert labels["near"].label == 0.9
    assert labels["wrong"].label == 0.0
    assert character_iou(gold, GroundedSpan(clause_id="C2", start=0, end=10, text="x" * 10)) == 0.0


def test_expected_task_name_cannot_enter_dataset_contract() -> None:
    proposal = _proposal("p", 0, 3)
    assert "task_name" not in proposal.model_dump_json()
