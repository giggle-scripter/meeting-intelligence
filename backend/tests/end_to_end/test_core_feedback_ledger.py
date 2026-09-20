from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.core_api import create_app
from backend.app.jobs import MeetingJobStore
from backend.app.models import FinalTask, PipelineDiagnostics, PipelineResult
import scripts.experimental_distillation.train_v227_feedback as trainer


class _Core:
    def __init__(self, core_id: str):
        self.core_id = core_id
        adaptive = core_id == "v2-adaptive"
        self.capabilities = type(
            "Capabilities",
            (),
            {
                "adaptive": adaptive,
                "pipeline_version": "v227_experimental" if adaptive else "v1",
                "runtime_model_id": "v228-frozen-full-fit" if adaptive else "v1-frozen",
                "supports_meeting_note": not adaptive,
            },
        )()

    def process(self, meeting, **options):
        return PipelineResult(
            meeting_title=meeting.meeting_title,
            summary="ok",
            tasks=[
                FinalTask(
                    task_name="Prepare release",
                    assignee="Lan",
                    start_date=meeting.meeting_date,
                    due_date=meeting.meeting_date,
                    due_date_text="today",
                    evidence="Lan: prepare release",
                )
            ],
            diagnostics=PipelineDiagnostics(meeting_date_source=meeting.meeting_date_source),
        )


def _settings(root: Path) -> Settings:
    return Settings(
        power_automate_api_key="secret",
        meeting_feedback_tenant_id="tenant-a",
        meeting_feedback_directory=str(root),
    )


def _completed(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/meetings/jobs/process-file",
        content=b"Lan: prepare release",
        headers={"X-API-Key": "secret", "X-File-Name": "meeting.txt"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(
            f"/api/v1/meetings/jobs/{job_id}", headers={"X-API-Key": "secret"}
        ).json()
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not complete")


@pytest.mark.parametrize("core_id", ["v1-frozen", "v2-adaptive"])
def test_shared_feedback_ledger_records_both_cores(tmp_path: Path, core_id: str) -> None:
    app = create_app(core=_Core(core_id), settings=_settings(tmp_path), job_store=MeetingJobStore())
    with TestClient(app) as client:
        job = _completed(client)
        assert job["status"] == "succeeded"
        source_path = next((tmp_path / "tenant-a" / "sources").glob("*.json"))
        source = json.loads(source_path.read_text(encoding="utf-8"))
        assert source["content_hash"] == job["content_hash"]
        assert source["core_id"] == core_id
        assert source["adaptive_training_eligible"] is (core_id == "v2-adaptive")
        payload = {
            "job_id": job["job_id"],
            "content_hash": job["content_hash"],
            "corrected_final_tasks": [],
            "approval_metadata": {
                "reviewer": "reviewer",
                "reviewed_at": "2026-09-20T00:00:00+00:00",
                "approval": True,
            },
        }
        feedback = client.post(
            f"/api/v1/meetings/jobs/{job['job_id']}/feedback",
            json=payload,
            headers={"X-API-Key": "secret"},
        )
        assert feedback.status_code == 201
        assert "transcript" not in feedback.text


def test_source_is_committed_before_job_submission_failure(tmp_path: Path) -> None:
    class FailingStore:
        def submit(self, *args, **kwargs):
            raise RuntimeError("submission failed")

    app = create_app(core=_Core("v2-adaptive"), settings=_settings(tmp_path), job_store=FailingStore())
    with TestClient(app, raise_server_exceptions=True) as client:
        with pytest.raises(RuntimeError, match="submission failed"):
            client.post(
                "/api/v1/meetings/jobs/process-file",
                content=b"Lan: prepare release",
                headers={"X-API-Key": "secret", "X-File-Name": "meeting.txt"},
            )
    assert list((tmp_path / "tenant-a" / "sources").glob("*.json"))


def test_feedback_validation_idempotency_and_conflict(tmp_path: Path) -> None:
    app = create_app(core=_Core("v1-frozen"), settings=_settings(tmp_path), job_store=MeetingJobStore())
    with TestClient(app) as client:
        job = _completed(client)
        base = {
            "job_id": job["job_id"],
            "content_hash": job["content_hash"],
            "corrected_final_tasks": [],
            "approval_metadata": {
                "reviewer": "reviewer",
                "reviewed_at": "2026-09-20T00:00:00+00:00",
                "approval": True,
            },
        }
        invalid = {**base, "approval_metadata": {**base["approval_metadata"], "approval": False}}
        assert client.post(
            f"/api/v1/meetings/jobs/{job['job_id']}/feedback",
            json=invalid,
            headers={"X-API-Key": "secret"},
        ).status_code == 422
        first = client.post(
            f"/api/v1/meetings/jobs/{job['job_id']}/feedback",
            json=base,
            headers={"X-API-Key": "secret"},
        )
        assert first.status_code == 201
        repeat = client.post(
            f"/api/v1/meetings/jobs/{job['job_id']}/feedback",
            json=base,
            headers={"X-API-Key": "secret"},
        )
        assert repeat.status_code == 200
        conflict = {**base, "approval_metadata": {**base["approval_metadata"], "reviewer": "other"}}
        assert client.post(
            f"/api/v1/meetings/jobs/{job['job_id']}/feedback",
            json=conflict,
            headers={"X-API-Key": "secret"},
        ).status_code == 409


def test_sqlite_job_lookup_survives_unified_api_restart(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "jobs.sqlite3"
    settings = _settings(tmp_path / "ledger")
    first_app = create_app(
        core=_Core("v2-adaptive"),
        settings=settings,
        job_store=MeetingJobStore(sqlite_path=sqlite_path),
    )
    with TestClient(first_app) as client:
        original = _completed(client)
    second_app = create_app(
        core=_Core("v2-adaptive"),
        settings=settings,
        job_store=MeetingJobStore(sqlite_path=sqlite_path),
    )
    with TestClient(second_app) as client:
        restored = client.get(
            f"/api/v1/meetings/jobs/{original['job_id']}",
            headers={"X-API-Key": "secret"},
        )
        assert restored.status_code == 200
        assert restored.json()["content_hash"] == original["content_hash"]


def test_trainer_ignores_ineligible_ledger_records(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "base"
    base.mkdir()
    model = {"schema_version": "v228-frozen-model-v1", "feature_dimensions": 768, "weights": [0.0] * 768, "bias": 0.0}
    policy = {"schema_version": "v228-frozen-policy-v1", "policy": {name: 0.5 for name in trainer.REQUIRED_POLICY_FIELDS}}
    for name, value in (("model.json", model), ("frozen-policy.json", policy)):
        (base / name).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    manifest = {"schema_version": "v228-manifest-v1", "immutable": True, "artifact_hashes": {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in ("model.json", "frozen-policy.json")}}
    (base / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    monkeypatch.setattr(trainer, "FROZEN_MANIFEST_SHA256", hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest())
    tenant = tmp_path / "feedback" / "tenant-a"
    (tenant / "feedback").mkdir(parents=True)
    (tenant / "sources").mkdir()
    record = {"schema_version": "v227-feedback-correction-v1", "tenant_id": "tenant-a", "job_id": "v1-job", "core_id": "v1-frozen", "adaptive_training_eligible": False}
    (tenant / "feedback" / "v1-job.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="STOP_NO_APPROVED_FEEDBACK"):
        trainer.train(tenant_id="tenant-a", feedback_directory=tmp_path / "feedback", base_artifact_directory=base)
