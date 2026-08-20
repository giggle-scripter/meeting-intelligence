from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting


def test_shadow_preserves_public_output_and_reports_temporal_comparison() -> None:
    meeting = MeetingInput(
        "M-TEMP-1", "Temporal", "2024-03-01",
        "Nam: Em sẽ gửi báo cáo, cần 1 ngày làm việc.",
    )
    baseline = process_meeting(meeting, temporal_semantics_mode="off")
    shadow = process_meeting(meeting, temporal_semantics_mode="shadow")

    assert shadow.tasks == baseline.tasks
    assert shadow.diagnostics.temporal_expression_count == 1
    assert shadow.diagnostics.temporal_improve_count == 1


def test_assist_only_falls_back_for_legacy_unresolved_duration() -> None:
    meeting = MeetingInput(
        "M-TEMP-2", "Temporal", "2024-03-01",
        "Nam: Em sẽ gửi báo cáo, cần 1 ngày làm việc.",
    )

    result = process_meeting(meeting, temporal_semantics_mode="assist")

    assert result.tasks[0].due_date == "2024-03-04"
    assert result.diagnostics.temporal_disagreement_count == 0
