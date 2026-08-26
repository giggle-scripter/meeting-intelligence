from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.distilled_proposal_ranker.contracts import GroundedSpan, SpanExample
from experiments.distilled_proposal_ranker.context_builder import (
    build_context,
    context_to_raw_offset,
    raw_to_context_offset,
)
from experiments.distilled_proposal_ranker.span_dataset import bio_labels, read_evidence, validate_gold_span


def test_unicode_vietnamese_offsets_round_trip_exactly() -> None:
    clauses = [{"clause_id": "C1", "speaker_name": "Lan", "text_raw": "Tôi sẽ hoàn thành báo cáo.", "order_index": 0}]
    context, records, start, end = build_context(clauses, "C1")
    raw_start = clauses[0]["text_raw"].index("hoàn thành")
    context_start = raw_to_context_offset(records[0], raw_start)
    assert context[context_start : context_start + len("hoàn thành")] == "hoàn thành"
    assert context_to_raw_offset(records[0], context_start) == raw_start
    assert context[start:end] == clauses[0]["text_raw"]


def test_span_offset_shift_is_rejected() -> None:
    with pytest.raises(ValueError, match="exact"):
        validate_gold_span(GroundedSpan(clause_id="C1", start=1, end=4, text="abc"), "abcd")


def test_duplicate_and_incomplete_human_evidence_are_rejected(tmp_path: Path) -> None:
    row = {"case_id": "A", "expected_task_index": 0, "review_status": "HUMAN_CONFIRMED", "reviewer": "R", "reviewed_at": "2026-01-01"}
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicated"):
        read_evidence(duplicate)
    row.pop("reviewer")
    incomplete = tmp_path / "incomplete.jsonl"
    incomplete.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reviewer"):
        read_evidence(incomplete)


def test_context_tokens_outside_target_are_ignored() -> None:
    clauses = [
        {"clause_id": "C0", "speaker_name": "A", "text_raw": "before", "order_index": 0},
        {"clause_id": "C1", "speaker_name": "B", "text_raw": "do it", "order_index": 1},
    ]
    context, context_clauses, start, end = build_context(clauses, "C1")
    example = SpanExample(
        example_id="x",
        input_hash="0" * 64,
        case_id="A",
        fold_id=0,
        target_clause_id="C1",
        target_clause_text="do it",
        context=context,
        context_clauses=context_clauses,
        target_start_in_context=start,
        target_end_in_context=end,
        gold_spans=[GroundedSpan(clause_id="C1", start=0, end=2, text="do")],
        source_label="human_confirmed",
        weight=1.0,
    )
    labels = bio_labels([(0, 1), (start, start + 2), (start + 3, end), (0, 0)], example)
    assert labels == [-100, 1, 0, -100]
