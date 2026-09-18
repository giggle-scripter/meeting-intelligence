from __future__ import annotations

from scripts.experimental_distillation.run_v222_task_proposal_reranker_oof import (
    TaskProposal,
    _labels,
    _policy_select,
    _proposal_rows,
)


def _proposal(case_id: str, index: int, title: str, *, assignee: str = "", score_fields: tuple[str, ...] = ()) -> TaskProposal:
    return TaskProposal(
        proposal_id=f"{case_id}::task::{index}", case_id=case_id,
        task={"task_name": title, "assignee": assignee, "status": "Proposed"},
        title=title, assignee=assignee, due_date="", due_date_text="", status="Proposed",
        provenance="|".join(score_fields) or "unknown", confidence=0.5, context="",
        source_clause_ids=(), event_ids=(),
    )


def test_labels_use_identity_matching_and_ignore_fields() -> None:
    expected = {"tasks": [{"task_name": "Viết báo cáo", "assignee": "Lan"}]}
    proposals = [
        _proposal("CASE", 0, "Viết báo cáo", assignee="Minh"),
        _proposal("CASE", 1, "Trao đổi", assignee="Lan"),
    ]
    assert _labels(expected, proposals) == [1, 0]


def test_policy_selection_is_thresholded_then_budgeted_stably() -> None:
    proposals = [_proposal("CASE", 0, "A"), _proposal("CASE", 1, "B"), _proposal("CASE", 2, "C")]
    selected = _policy_select(proposals, [0.4, 0.9, 0.8], threshold=0.5, budget=1)
    assert [item.proposal_id for item in selected] == ["CASE::task::1"]


def test_trace_rows_carry_runtime_provenance_without_span_reconstruction() -> None:
    trace = {
        "clauses": [{"clause_id": "C1", "text_raw": "Lan hoàn thành báo cáo"}],
        "final_tasks": [{"task_name": "Hoàn thành báo cáo", "assignee": "Lan", "due_date": "2026-01-20", "status": "Proposed"}],
        "task_ledger": {"ledger": {"TASK-1": {"canonical_action": "Hoàn thành báo cáo", "assignees": ["Lan"], "source_clause_ids": ["C1"], "event_ids": ["E1"], "confidence": 0.8, "extraction_sources": ["RULE"]}}},
        "events_after_deduplication": [{"event_id": "E1", "action_text": "Hoàn thành báo cáo", "assignee": "Lan", "source_clause_ids": ["C1"], "confidence": 0.9, "extraction_source": "RULE_CONTEXT"}],
    }
    rows = _proposal_rows("CASE", trace)
    assert len(rows) == 1
    assert rows[0].source_clause_ids == ("C1",)
    assert rows[0].event_ids == ("E1",)
    assert rows[0].confidence == 0.9
    assert rows[0].provenance == "RULE|RULE_CONTEXT"
    assert "Lan hoàn thành báo cáo" in rows[0].context
