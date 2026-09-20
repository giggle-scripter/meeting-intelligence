from backend.app.evaluation import aggregate_results, compare_case


def test_compare_case_reports_field_error_without_losing_task_match() -> None:
    expected = {
        "tasks": [
            {
                "task_name": "Gửi báo cáo",
                "assignee": "Linh",
                "start_date": "2026-07-21",
                "due_date": "2026-07-24",
                "due_date_text": "trước 18h ngày 24/07/2026",
                "status": "Proposed",
            }
        ]
    }
    actual = {
        "tasks": [
            {
                "task_name": "Gửi báo cáo",
                "assignee": "Linh",
                "start_date": "2026-07-21",
                "due_date": "2026-07-25",
                "due_date_text": "trước 18h ngày 24/07/2026",
                "status": "Proposed",
                "evidence": "Linh: Em sẽ gửi báo cáo.",
            }
        ]
    }

    comparison = compare_case("CASE-1", expected, actual)

    assert comparison.passed is False
    assert comparison.matched_task_count == 1
    assert comparison.field_errors == [
        {
            "task_name": "Gửi báo cáo",
            "field": "due_date",
            "expected": "2026-07-24",
            "actual": "2026-07-25",
        }
    ]


def test_aggregate_results_calculates_task_metrics() -> None:
    first = compare_case(
        "PASS",
        {"tasks": [{"task_name": "A", "assignee": "An"}]},
        {"tasks": [{"task_name": "A", "assignee": "An"}]},
    )
    second = compare_case(
        "FAIL",
        {"tasks": [{"task_name": "B", "assignee": "Bình"}]},
        {"tasks": [{"task_name": "C", "assignee": "Chi"}]},
    )

    metrics = aggregate_results([first, second])

    assert metrics["passed_case_count"] == 1
    assert metrics["task_precision"] == 0.5
    assert metrics["task_recall"] == 0.5
    assert metrics["task_identity_f1"] == 0.5
    assert metrics["matched_task_count"] == 1
    assert metrics["missing_task_count"] == 1
    assert metrics["unexpected_task_count"] == 1


def test_compare_case_matches_semantically_equivalent_task_names() -> None:
    expected = {
        "tasks": [
            {
                "task_name": "Phân tích lỗi timeout",
                "assignee": "Chi",
                "start_date": "2026-01-18",
                "due_date": "2026-01-19",
                "due_date_text": "ngày mai",
                "status": "Proposed",
            }
        ]
    }
    actual = {
        "tasks": [
            {
                "task_name": "Phân tích timeout",
                "assignee": "Chi",
                "start_date": "2026-01-18",
                "due_date": "2026-01-19",
                "due_date_text": "ngày mai",
                "status": "Proposed",
            }
        ]
    }

    assert compare_case("CASE", expected, actual).passed


def test_wrong_assignee_is_one_field_error_not_missing_and_unexpected() -> None:
    comparison = compare_case(
        "OWNER",
        {"tasks": [{"task_name": "Phân tích log thanh toán", "assignee": "Lan"}]},
        {"tasks": [{"task_name": "Phân tích log thanh toán", "assignee": "Minh"}]},
    )

    assert comparison.matched_task_count == 1
    assert comparison.missing_tasks == []
    assert comparison.unexpected_tasks == []
    assert [item["field"] for item in comparison.field_errors] == ["assignee"]


def test_task_key_has_precedence_over_similar_name() -> None:
    comparison = compare_case(
        "KEY",
        {"tasks": [{"task_key": "task-a", "task_name": "Viết tài liệu", "assignee": "Lan"}]},
        {"tasks": [{"task_key": "task-b", "task_name": "Viết tài liệu", "assignee": "Lan"}]},
    )

    assert comparison.matched_task_count == 0
    assert len(comparison.missing_tasks) == len(comparison.unexpected_tasks) == 1


def test_global_matching_does_not_use_owner_as_task_identity() -> None:
    comparison = compare_case(
        "GLOBAL",
        {"tasks": [
            {"task_name": "Kiểm tra log payment", "assignee": "Lan"},
            {"task_name": "Kiểm tra log checkout", "assignee": "Minh"},
        ]},
        {"tasks": [
            {"task_name": "Kiểm tra log checkout", "assignee": "Lan"},
            {"task_name": "Kiểm tra log payment", "assignee": "Minh"},
        ]},
    )

    assert comparison.matched_task_count == 2
    assert sorted(item["field"] for item in comparison.field_errors) == ["assignee", "assignee"]


def test_due_date_text_is_measured_separately_from_semantic_date() -> None:
    comparison = compare_case(
        "DATE",
        {"tasks": [{"task_name": "Gửi báo cáo", "due_date": "2026-02-20", "due_date_text": "deadline 20/02"}]},
        {"tasks": [{"task_name": "Gửi báo cáo", "due_date": "2026-02-20", "due_date_text": "20/02"}]},
    )
    metrics = aggregate_results([comparison])

    assert [item["field"] for item in comparison.field_errors] == ["due_date_text"]
    assert metrics["due_date_accuracy"] == 1.0
    assert metrics["due_date_text_exact_accuracy"] == 0.0
