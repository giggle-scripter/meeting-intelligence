import json
from dataclasses import asdict
from pathlib import Path

from backend.app.jobs import MeetingJob
from backend.app.models import FinalTask, PipelineDiagnostics, PipelineResult


ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = ROOT / "power-automate" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_power_automate_job_envelope_schemas_match_runtime_contract() -> None:
    submit_schema = _schema("job-submit.schema.json")
    status_schema = _schema("job-status.schema.json")
    runtime_status = MeetingJob(
        job_id="job-test",
        idempotency_key="key",
        status="queued",
        created_at="2026-08-17T00:00:00Z",
    ).as_response()

    assert set(submit_schema["required"]) == {
        "job_id",
        "status",
        "created",
        "status_url",
    }
    assert set(status_schema["required"]) == set(runtime_status)
    assert set(status_schema["properties"]) == set(runtime_status)


def test_power_automate_pipeline_schema_matches_public_result_contract() -> None:
    schema = _schema("pipeline-output.schema.json")
    result = asdict(
        PipelineResult(
            meeting_title="Release sync",
            summary="Một task được đề xuất.",
            tasks=[
                FinalTask(
                    task_name="Gửi release note",
                    assignee="Lan",
                    start_date="2026-08-17",
                    due_date="",
                    due_date_text="sau review",
                    evidence="Lan: Em sẽ gửi release note sau review.",
                )
            ],
            diagnostics=PipelineDiagnostics(),
        )
    )

    assert set(schema["required"]) == set(result)
    assert set(schema["properties"]) == set(result)
    task_schema = schema["properties"]["tasks"]["items"]
    assert set(task_schema["required"]) == set(result["tasks"][0])
    assert set(task_schema["properties"]) == set(result["tasks"][0])
