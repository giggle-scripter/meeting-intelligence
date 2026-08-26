from __future__ import annotations

from experiments.distilled_proposal_ranker.contracts import GroundedSpan, ProposalRecord
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from experiments.distilled_proposal_ranker.reranker_dataset import character_iou, label_training_proposals, serialize_proposal
from experiments.distilled_proposal_ranker.reranker_model import ProposalReranker, reranker_loss


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


def test_update_reference_and_drop_are_never_positive_create_labels() -> None:
    gold = GroundedSpan(clause_id="C1", start=0, end=10, text="x" * 10)
    proposals = [
        _proposal(kind.casefold(), 0, 10).model_copy(update={"kind": kind})
        for kind in ("UPDATE", "REFERENCE", "DROP")
    ]
    labels = label_training_proposals(proposals, [gold])
    assert [item.label for item in labels] == [0.0, 0.0, 0.0]
    assert character_iou(gold, GroundedSpan(clause_id="C2", start=0, end=10, text="x" * 10)) == 0.0


def test_expected_task_name_cannot_enter_dataset_contract() -> None:
    proposal = _proposal("p", 0, 3)
    assert "task_name" not in proposal.model_dump_json()


class WordTokenizer:
    def __init__(self, multiplier: int = 1) -> None:
        self.multiplier = multiplier

    def __call__(self, text, **_kwargs):
        return {"input_ids": list(range(max(1, len(text.split()) * self.multiplier)))}


def test_cross_encoder_serialization_marks_action_and_contains_no_gold() -> None:
    proposal = _proposal("p", 0, 3).model_copy(update={"action_span": GroundedSpan(clause_id="C1", start=0, end=3, text="Làm")})
    trace = {
        "clauses": [{"clause_id": "C1", "speaker_name": "Lan", "text_raw": "Làm báo cáo", "order_index": 0}],
        "date_mentions": {},
    }
    serialized = serialize_proposal(proposal, trace, WordTokenizer())
    assert "[ACTION]" in serialized and "[ACT]Làm[/ACT]" in serialized
    assert "[AUTHORITY]" in serialized and "[NEGATIVE]" in serialized and "[CONTEXT]" in serialized
    assert "expected task" not in serialized.casefold()


def test_oversized_core_evidence_fails_without_truncating_action() -> None:
    proposal = _proposal("p", 0, 3).model_copy(update={"action_span": GroundedSpan(clause_id="C1", start=0, end=3, text="Làm")})
    trace = {"clauses": [{"clause_id": "C1", "speaker_name": "Lan", "text_raw": "Làm báo cáo", "order_index": 0}], "date_mentions": {}}
    with pytest.raises(ValueError, match="STOP_OVERSIZED_CORE_EVIDENCE"):
        serialize_proposal(proposal, trace, WordTokenizer(multiplier=100), max_length=10)


class TinyBackbone(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embedding = nn.Embedding(8, 4)

    def forward(self, input_ids, attention_mask=None):
        del attention_mask
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))


def test_cross_encoder_pairwise_margin_loss_smoke() -> None:
    model = ProposalReranker(TinyBackbone(), 4)
    logits = model(torch.tensor([[1, 2], [2, 1]]))
    loss = reranker_loss(
        logits,
        torch.tensor([1.0, 0.0]),
        torch.tensor([1.0, 0.25]),
        torch.tensor(1.0),
        [(0, 1)],
    )
    loss.backward()
    assert torch.isfinite(loss)
