from backend.app.models import (
    Clause,
    DateMention,
    PipelineDiagnostics,
    TaskState,
)
from backend.app.output import build_pipeline_result


def test_serializer_uses_best_phrase_for_latest_deadline_state() -> None:
    clause = Clause(
        "C-1", "S-1", "SP-1", "Lan", None, None,
        "Deadline cho cấu hình monitoring là 21/03.",
        "deadline cho cấu hình monitoring là 21/03.",
    )
    mentions = {
        "D-1": DateMention(
            "D-1", "C-1", "deadline cho cấu hình monitoring là 21/03",
            "ON_DAY", "ABSOLUTE_DATE", explicit_day=21, explicit_month=3,
        ),
        "D-2": DateMention(
            "D-2", "C-1", "21/03", "ON_DAY", "ABSOLUTE_DATE",
            explicit_day=21, explicit_month=3,
        ),
    }
    state = TaskState(
        "TASK-000001",
        "Cấu hình monitoring",
        "Lan",
        deadline_mention_id="D-2",
        deadline_event_ids=["E-1", "E-2"],
        deadline_mention_history=["D-1", "D-2"],
        source_clause_ids=["C-1"],
    )

    result = build_pipeline_result(
        "Release",
        "2026-03-01",
        [state],
        {"C-1": clause},
        mentions,
        PipelineDiagnostics(),
        [],
    )

    assert result.tasks[0].due_date == "2026-03-21"
    assert result.tasks[0].due_date_text == (
        "deadline cho cấu hình monitoring là 21/03"
    )


def test_serializer_preserves_richer_timed_boundary_for_same_due_date() -> None:
    clause = Clause(
        "C-1", "S-1", "SP-1", "Lan", None, None,
        "Em sẽ commit trước 16h hôm nay. Recap: deadline 11/02.",
        "em sẽ commit trước 16h hôm nay. recap: deadline 11/02.",
    )
    mentions = {
        "D-1": DateMention(
            "D-1", "C-1", "trước 16h hôm nay", "BEFORE_TIME",
            "RELATIVE_DAY", relative_day_offset=0,
        ),
        "D-2": DateMention(
            "D-2", "C-1", "deadline 11/02", "ON_DATE", "DAY_MONTH",
            explicit_day=11, explicit_month=2,
        ),
    }
    state = TaskState(
        "TASK-1", "Fix API", "Lan", deadline_mention_id="D-2",
        deadline_mention_history=["D-1", "D-2"], source_clause_ids=["C-1"],
    )

    result = build_pipeline_result(
        "Release", "2026-02-11", [state], {"C-1": clause}, mentions,
        PipelineDiagnostics(), [],
    )

    assert result.tasks[0].due_date == "2026-02-11"
    assert result.tasks[0].due_date_text == "trước 16h hôm nay"


def test_serializer_does_not_replace_on_date_with_prior_before_day_phrase() -> None:
    clause = Clause(
        "C-1", "S-1", "SP-1", "Lan", None, None,
        "Hạn cũ trước 12/02; hạn mới deadline 11/02.",
        "hạn cũ trước 12/02; hạn mới deadline 11/02.",
    )
    mentions = {
        "D-1": DateMention(
            "D-1", "C-1", "trước 12/02", "BEFORE_DAY", "DAY_MONTH",
            explicit_day=12, explicit_month=2,
        ),
        "D-2": DateMention(
            "D-2", "C-1", "deadline 11/02", "ON_DATE", "DAY_MONTH",
            explicit_day=11, explicit_month=2,
        ),
    }
    state = TaskState(
        "TASK-1", "Fix API", "Lan", deadline_mention_id="D-2",
        deadline_mention_history=["D-1", "D-2"], source_clause_ids=["C-1"],
    )

    result = build_pipeline_result(
        "Release", "2026-02-10", [state], {"C-1": clause}, mentions,
        PipelineDiagnostics(), [],
    )

    assert result.tasks[0].due_date_text == "deadline 11/02"


def test_serializer_prefers_explicit_task_start_and_uses_it_for_duration() -> None:
    clause = Clause(
        "C-START", "S-START", "SP-1", "Lan", None, None,
        "Em bắt đầu triển khai từ 20/02/2026 và cần 4 ngày lịch.",
        "em bắt đầu triển khai từ 20/02/2026 và cần 4 ngày lịch.",
    )
    mentions = {
        "D-START": DateMention(
            "D-START", "C-START", "từ 20/02/2026", "ON_DATE",
            "ABSOLUTE_DATE", explicit_day=20, explicit_month=2,
            explicit_year=2026, purpose="START_DATE",
        ),
        "D-DURATION": DateMention(
            "D-DURATION", "C-START", "cần 4 ngày lịch", "DURATION",
            "CALENDAR_DURATION", duration_days=4,
        ),
    }
    state = TaskState(
        "TASK-START",
        "Triển khai API",
        "Lan",
        deadline_mention_id="D-DURATION",
        deadline_mention_history=["D-DURATION"],
        source_clause_ids=["C-START"],
    )

    result = build_pipeline_result(
        "Triển khai",
        "2026-02-17",
        [state],
        {"C-START": clause},
        mentions,
        PipelineDiagnostics(),
        [],
    )

    assert result.tasks[0].start_date == "2026-02-20"
    assert result.tasks[0].due_date == "2026-02-24"
    assert result.diagnostics.explicit_task_start_date_count == 1


def test_serializer_falls_back_to_non_empty_meeting_date() -> None:
    state = TaskState("TASK-FALLBACK", "Triển khai API", "Lan")

    result = build_pipeline_result(
        "Triển khai",
        "2026-02-17",
        [state],
        {},
        {},
        PipelineDiagnostics(),
        [],
    )

    assert result.tasks[0].start_date == "2026-02-17"
