from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch
from torch import nn

from experiments.distilled_proposal_ranker.span_decode import DecodedSpan, decode_bio_spans, nms_spans
from experiments.distilled_proposal_ranker.contracts import GroundedSpan, SpanExample
from experiments.distilled_proposal_ranker.span_model import ActionSpanModel, combined_span_loss, set_deterministic_seed
from experiments.distilled_proposal_ranker.span_training import train_span_model


class TinyBackbone(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embedding = nn.Embedding(16, 4)

    def forward(self, input_ids, attention_mask=None):
        del attention_mask
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))


class TinyTokenizer:
    def __call__(self, text, *, truncation, max_length, return_offsets_mapping, padding):
        assert truncation and return_offsets_mapping and padding == "max_length"
        offsets = [(0, 0), (0, 2), (3, min(5, len(text)))] + [(0, 0)] * (max_length - 3)
        return {
            "input_ids": [0, 1, 2] + [0] * (max_length - 3),
            "attention_mask": [1, 1, 1] + [0] * (max_length - 3),
            "offset_mapping": offsets,
        }


def test_illegal_leading_i_decodes_to_valid_raw_boundary() -> None:
    spans = decode_bio_spans(
        [0, 2, 2, 0],
        [0.1, 0.9, 0.8, 0.1],
        [(0, 0), (10, 13), (14, 17), (18, 19)],
        raw_text="làm báo",
        target_start_in_context=10,
        target_end_in_context=17,
        has_action_probability=0.9,
    )
    assert spans[0].start == 0 and spans[0].end == 7 and spans[0].text == "làm báo"
    assert spans[0].score == pytest.approx(0.85)


def test_no_action_gate_and_balanced_quotes_are_fail_closed() -> None:
    assert decode_bio_spans([1], [1.0], [(0, 2)], raw_text="do", target_start_in_context=0, target_end_in_context=2, has_action_probability=0.19) == []
    spans = decode_bio_spans([1], [0.7], [(0, 4)], raw_text='"do"', target_start_in_context=0, target_end_in_context=4, has_action_probability=0.2)
    assert spans[0].text == '"do"'


def test_character_nms_tie_breaks_by_length_then_offsets() -> None:
    spans = [DecodedSpan(1, 9, "12345678", 0.8), DecodedSpan(0, 10, "0123456789", 0.8), DecodedSpan(20, 22, "xx", 0.7)]
    kept = nms_spans(spans)
    assert kept[0].start == 0 and len(kept) == 2


def test_tiny_dual_head_and_weighted_loss_smoke_is_deterministic() -> None:
    set_deterministic_seed(17)
    first = ActionSpanModel(TinyBackbone(), 4)
    input_ids = torch.tensor([[1, 2, 3], [3, 2, 1]])
    token_logits, clause_logits = first(input_ids)
    loss = combined_span_loss(
        token_logits,
        clause_logits,
        torch.tensor([[0, 1, 2], [0, 0, -100]]),
        torch.tensor([1.0, 0.0]),
        torch.tensor([1.0, 0.25]),
        torch.tensor([1.0, 2.0, 2.0]),
    )
    loss.backward()
    assert token_logits.shape == (2, 3, 3)
    assert clause_logits.shape == (2,)
    assert torch.isfinite(loss)


def test_tiny_fixture_training_writes_checkpoint_manifest(tmp_path) -> None:
    positive = SpanExample(
        example_id="positive", input_hash="0" * 64, case_id="A", fold_id=0,
        target_clause_id="C1", target_clause_text="do it", context="do it", context_clauses=[],
        target_start_in_context=0, target_end_in_context=5,
        gold_spans=[GroundedSpan(clause_id="C1", start=0, end=2, text="do")],
        source_label="human_confirmed", weight=1.0,
    )
    negative = positive.model_copy(
        update={"example_id": "negative", "target_clause_id": "C2", "gold_spans": [], "source_label": "deterministic_hard_negative", "weight": 0.25}
    )
    result = train_span_model(
        ActionSpanModel(TinyBackbone(), 4), TinyTokenizer(), [positive, negative], [positive],
        seed=17, learning_rate=0.001, weight_decay=0.01, warmup_ratio=0.1,
        max_epochs=1, patience=1, gradient_clip_norm=1.0, max_length=8,
        checkpoint_dir=tmp_path / "checkpoint",
        manifest_values={"protocol_hash": "p", "data_hash": "d", "model_name": "tiny", "code_hash": "c"},
    )
    assert len(result.checkpoint_hash) == 64
    assert (tmp_path / "checkpoint" / "manifest.json").exists()
