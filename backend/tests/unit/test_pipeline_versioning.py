import json

from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting, process_meeting_by_version


def test_opt_in_trace_contains_first_divergence_artifacts(tmp_path) -> None:
    meeting = MeetingInput("trace-case", "Trace", "2026-02-20", "Lan: Lan sẽ viết tài liệu triển khai.")
    process_meeting(meeting, trace_enabled=True, trace_directory=str(tmp_path))

    trace_files = list(tmp_path.glob("*.json"))
    assert len(trace_files) == 1
    payload = json.loads(trace_files[0].read_text(encoding="utf-8"))
    assert payload["pipeline_version"] == "v1"
    assert {"clauses", "annotations", "candidate_windows", "date_mentions", "task_states", "final_tasks", "openai_usage"} <= set(payload)
    assert payload["openai_usage"] is None


def test_shadow_returns_v1_result_and_writes_v1_v2_diff(tmp_path) -> None:
    meeting = MeetingInput("shadow-case", "Shadow", "2026-02-20", "Lan: Task A: Write documentation, owner Lan.")
    shadow = process_meeting_by_version(
        meeting, pipeline_version="shadow", trace_enabled=True, trace_directory=str(tmp_path)
    )
    direct_v1 = process_meeting(meeting)

    assert shadow.tasks == direct_v1.tasks
    trace_files = list(tmp_path.glob("*-shadow-*.json"))
    assert len(trace_files) == 1
    payload = json.loads(trace_files[0].read_text(encoding="utf-8"))
    assert {"v1_final_tasks", "v2_final_tasks", "v2_decisions", "openai_usage"} <= set(payload)


def test_v2_flag_returns_v2_output_adapter() -> None:
    meeting = MeetingInput("v2-case", "V2", "2026-02-20", "Lan: Task A: Write documentation, owner Lan.")
    result = process_meeting_by_version(meeting, pipeline_version="v2")

    assert len(result.tasks) == 1
    assert result.tasks[0].task_name == "Write documentation"
