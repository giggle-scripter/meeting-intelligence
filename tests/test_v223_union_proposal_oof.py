from __future__ import annotations

from scripts.experimental_distillation.run_v223_union_proposal_oof import (
    UnionProposal,
    _dedupe_rows,
    _features,
    _oracle_selected,
    _runtime_occurrences,
    _union_rows,
)


def _trace() -> dict:
    return {
        "clauses": [
            {"clause_id": "C1", "text_raw": "Lan sẽ gửi báo cáo."},
            {"clause_id": "C2", "text_raw": "Hạn vào ngày 20 tháng 1."},
        ],
        "date_mentions": {
            "D1": {
                "date_mention_id": "D1", "clause_id": "C2", "raw_text": "ngày 20 tháng 1",
                "relation": "ON_DATE", "date_type": "DAY_MONTH", "explicit_day": 20,
                "explicit_month": 1, "span_start": 8, "span_end": 23, "purpose": "DEADLINE",
            }
        },
        "events_after_deduplication": [{
            "event_id": "E1", "source_clause_ids": ["C1"], "action_text": "Gửi báo cáo",
            "assignee": "Lan", "deadline_mention_id": "D1", "confidence": 0.9,
            "extraction_source": "RULE", "order_index": 0,
        }],
        "final_tasks": [{"task_name": "Gửi báo cáo", "assignee": "Lan", "status": "Proposed"}],
        # This is the runtime-only compatibility shape used by the locked
        # PR29 traces when V2.17 proposal identity records are absent.
        "action_candidates_v2": {"records": [{
            "candidate_id": "A1", "primary_clause_ids": ["C1"], "state": "PROPOSED",
            "action_spans": [{"clause_id": "C1", "start": 7, "end": 18, "text": "gửi báo cáo"}],
            "negative_signals": [],
        }]},
    }


def test_runtime_fallback_reads_only_inference_action_candidates() -> None:
    values = _runtime_occurrences(_trace())
    assert [(item.clause_id, item.start, item.end, item.text) for item in values] == [("C1", 7, 18, "gửi báo cáo")]


def test_union_dedup_is_identity_keyed_and_marks_cross_source_agreement() -> None:
    rows = _union_rows("CASE", _trace(), "2026-01-17")
    assert len(rows) == 1
    assert rows[0].origin == "both"
    assert rows[0].cross_source_agreement is True
    assert rows[0].bridge_cross_clause is True
    assert _features(rows[0], rows)


def test_union_oracle_recall_cannot_be_below_baseline_superset_recall() -> None:
    def proposal(source: str, title: str) -> UnionProposal:
        return UnionProposal(
            proposal_id=f"CASE::{source}", case_id="CASE", task={"task_name": title, "assignee": "Lan"},
            title=title, assignee="Lan", due_date="", due_date_text="", status="Proposed",
            provenance=source, confidence=0.5, context="", source_clause_ids=(), event_ids=(), origin=source,
        )

    expected = {"tasks": [{"task_name": "A", "assignee": "Lan"}, {"task_name": "B", "assignee": "Lan"}]}
    baseline = [proposal("baseline", "A")]
    union = _dedupe_rows(baseline + [proposal("bridge", "B")])
    assert len(_oracle_selected(expected, union)) >= len(_oracle_selected(expected, baseline))
