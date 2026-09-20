from backend.app.evaluation import compare_case
from backend.app.quality import attribute_case, attribute_expected_tasks, summarize_attribution
from backend.app.quality.review_queue import build_review_queue


def _trace(*, flags=None, event=True, state=True, recap=False) -> dict:
    flags = flags or []
    return {
        "clauses": [
            {"clause_id": "C-1", "text_raw": "Lan sẽ hoàn thành tài liệu."},
            {"clause_id": "C-2", "text_raw": "Đây chỉ là một gợi ý."},
        ],
        "annotations": {"C-1": {"flags": flags}, "C-2": {"flags": []}},
        "candidate_windows": [{"window_id": "W-1", "primary_clause_ids": ["C-1"]}],
        "events_after_deduplication": ([{
            "event_id": "E-1", "event_type": "TASK_CREATE", "action_text": "Hoàn thành tài liệu",
            "source_clause_ids": ["C-1"], "extraction_source": "RULE_FINAL_RECAP" if recap else "RULE",
        }] if event else []),
        "task_states": ([{
            "task_id": "T-1", "task_name": "Hoàn thành tài liệu",
            "source_clause_ids": ["C-1"],
        }] if state else []),
    }


def test_missing_attribution_marks_source_candidate_route_drop() -> None:
    comparison = compare_case(
        "CASE", {"tasks": [{"task_name": "Hoàn thành tài liệu", "assignee": "Lan"}]},
        {"tasks": []},
    )

    records = attribute_case(
        "CASE", {}, {}, comparison, _trace(event=False, state=False),
        {"wave": 1, "labels": ["direct_assignment"]},
    )

    assert records[0].suggested_taxonomy == "SOURCE_CANDIDATE_ROUTED_DROP"
    assert records[0].candidate_window_ids == ("W-1",)
    assert records[0].review_status.value == "NEEDS_REVIEW"


def test_unexpected_attribution_preserves_negative_and_recap_provenance() -> None:
    comparison = compare_case(
        "CASE", {"tasks": []},
        {"tasks": [{"task_name": "Hoàn thành tài liệu", "assignee": "Lan"}]},
    )
    recap_record = attribute_case("CASE", {}, {}, comparison, _trace(recap=True), {})[0]
    negative_record = attribute_case(
        "CASE", {}, {}, comparison, _trace(flags=["SUGGESTION_ONLY"], recap=False), {}
    )[0]

    assert recap_record.suggested_taxonomy == "RECAP_DUPLICATE"
    assert negative_record.suggested_taxonomy == "NEGATIVE_GUARD_MISSED"


def test_review_queue_keeps_suggestions_unreviewed_and_summary_reports_coverage() -> None:
    missing = attribute_case(
        "CASE", {}, {},
        compare_case("CASE", {"tasks": [{"task_name": "Hoàn thành tài liệu"}]}, {"tasks": []}),
        _trace(event=False, state=False), {},
    )
    unexpected = attribute_case(
        "CASE", {}, {},
        compare_case("CASE", {"tasks": []}, {"tasks": [{"task_name": "Hoàn thành tài liệu"}]}),
        _trace(), {},
    )
    expected = attribute_expected_tasks(
        "CASE", {"tasks": [{"task_name": "Hoàn thành tài liệu"}]}, _trace(), {},
    )
    records = expected + missing + unexpected

    queue = build_review_queue(records)
    summary = summarize_attribution(records)

    assert {item.queue_kind for item in queue} == {"TASK_EVIDENCE", "CANDIDATE_NEGATIVE"}
    assert all(item.review_status.value == "NEEDS_REVIEW" for item in queue)
    assert summary["missing_source_coverage"] == {"covered": 1, "total": 1, "rate": 1.0}
    assert summary["expected_task_source_coverage"] == {"covered": 1, "total": 1, "rate": 1.0}
