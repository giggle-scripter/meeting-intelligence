from __future__ import annotations

from base64 import b64encode
from hashlib import sha256
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from backend.app.ingestion import build_meeting_package
from backend.tests.experimental.test_v227_experimental import _artifacts
import scripts.experimental_distillation.v227_api as v227_api
from scripts.experimental_distillation.v227_api import create_app


def _headers() -> dict[str, str]:
    return {
        "X-API-Key": "test-secret",
        "X-File-Name-Base64": b64encode("họp thử.txt".encode()).decode(),
        "Content-Type": "application/octet-stream",
    }


def _app(artifacts: Path, key: str = "test-secret"):
    return create_app(
        artifact_directory=artifacts,
        api_key=key,
        expected_manifest_sha256=sha256((artifacts / "manifest.json").read_bytes()).hexdigest(),
    )


def test_v227_job_routes_keep_v1_contract_and_no_provider(tmp_path: Path) -> None:
    app = _app(_artifacts(tmp_path))
    transcript = build_meeting_package(
        "Lan: Please prepare the rollout checklist by Friday.\n"
        "Minh: I will review the checklist.\n",
        None,
        meeting_id="api-v227-smoke",
        meeting_title="Họp thử",
        meeting_date="2026-09-18",
    ).encode()
    with TestClient(app) as client:
        assert client.get("/health").json()["pipeline_version"] == "v227_experimental"
        assert client.post("/api/v1/meetings/jobs/process-file", content=transcript).status_code == 401
        first = client.post("/api/v1/meetings/jobs/process-file", content=transcript, headers=_headers())
        assert first.status_code == 202
        submitted = first.json()
        duplicate = client.post("/api/v1/meetings/jobs/process-file", content=transcript, headers=_headers())
        assert duplicate.json()["job_id"] == submitted["job_id"]
        assert duplicate.json()["created"] is False
        deadline = time.monotonic() + 5
        while True:
            polled = client.get(submitted["status_url"], headers={"X-API-Key": "test-secret"})
            assert polled.status_code == 200
            job = polled.json()
            if job["status"] in {"succeeded", "failed"} or time.monotonic() > deadline:
                break
            time.sleep(0.02)
        assert job["status"] == "succeeded", job["error"]
        assert job["pipeline_version"] == "v227_experimental"
        result = job["result"]
        assert set(result) == {"meeting_title", "summary", "tasks", "diagnostics", "unresolved_window_ids"}
        assert all(
            set(task) == {"task_name", "assignee", "start_date", "due_date", "due_date_text", "evidence", "status"}
            and task["status"] == "Proposed"
            for task in result["tasks"]
        )
        assert result["diagnostics"]["experimental_not_validated"] is True
        assert result["diagnostics"]["ai_provider_call_count"] == 0
        assert client.get(submitted["status_url"]).status_code == 401


def test_v227_api_rejects_note_and_missing_date(tmp_path: Path) -> None:
    app = _app(_artifacts(tmp_path))
    with TestClient(app) as client:
        without_date = client.post(
            "/api/v1/meetings/jobs/process-file",
            content=b"Lan: Please send the report.",
            headers=_headers(),
        )
        assert without_date.status_code == 422
        with_note = build_meeting_package(
            "Lan: Please send the report.",
            "Secretary note",
            meeting_date="2026-09-18",
        )
        response = client.post(
            "/api/v1/meetings/jobs/process-file",
            content=with_note.encode(),
            headers=_headers(),
        )
        assert response.status_code == 422


def test_v227_api_fails_closed_without_key_or_artifact(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    with pytest.raises(RuntimeError, match="STOP_POWER_AUTOMATE_API_KEY_REQUIRED"):
        with TestClient(_app(artifacts, key="")):
            pass
    (artifacts / "model.json").unlink()
    with pytest.raises(RuntimeError, match="STOP_MISSING_PRIVATE_ARTIFACT"):
        with TestClient(_app(artifacts)):
            pass
    (artifacts / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="STOP_FROZEN_V228_MANIFEST_HASH_MISMATCH"):
        with TestClient(create_app(artifact_directory=artifacts, api_key="test-secret")):
            pass


def _completed_job(client: TestClient, transcript: bytes) -> dict:
    response = client.post("/api/v1/meetings/jobs/process-file", content=transcript, headers=_headers())
    assert response.status_code == 202
    submitted = response.json()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job_response = client.get(submitted["status_url"], headers={"X-API-Key": "test-secret"})
        assert job_response.status_code == 200
        job = job_response.json()
        if job["status"] in {"succeeded", "failed"}:
            assert job["status"] == "succeeded", job["error"]
            return job
        time.sleep(0.02)
    raise AssertionError("job did not complete")


def test_v227_feedback_requires_approval_and_writes_private_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-a")
    private = tmp_path / "private-feedback"
    artifacts = _artifacts(tmp_path)
    app = create_app(
        artifact_directory=artifacts,
        feedback_directory=private,
        api_key="test-secret",
        expected_manifest_sha256=sha256((artifacts / "manifest.json").read_bytes()).hexdigest(),
    )
    transcript = build_meeting_package(
        "Lan: Please prepare the rollout checklist by Friday.\n",
        None,
        meeting_id="feedback-1",
        meeting_title="Feedback test",
        meeting_date="2026-09-18",
    ).encode()
    with TestClient(app) as client:
        job = _completed_job(client, transcript)
        duplicate = client.post("/api/v1/meetings/jobs/process-file", content=transcript, headers=_headers())
        assert duplicate.status_code == 202
        assert duplicate.json()["job_id"] == job["job_id"]
        assert duplicate.json()["created"] is False
        endpoint = f"/api/v1/meetings/jobs/{job['job_id']}/feedback"
        common = {"job_id": job["job_id"], "content_hash": job["content_hash"], "corrected_final_tasks": []}
        assert client.post(endpoint, json=common, headers={"X-API-Key": "test-secret"}).status_code == 422
        approved = {
            **common,
            "approval_metadata": {
                "reviewer": "human@example.test",
                "reviewed_at": "2026-09-18T10:00:00+07:00",
                "approval": True,
            },
        }
        saved = client.post(endpoint, json=approved, headers={"X-API-Key": "test-secret"})
        assert saved.status_code == 201
        assert saved.json()["created"] is True
        source = private / "tenant-a" / "sources" / f"{job['content_hash']}.json"
        assert source.is_file()
        assert "Lan: Please prepare" in source.read_text(encoding="utf-8")


def test_v227_feedback_is_idempotent_and_rejects_conflicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-b")
    private = tmp_path / "private-feedback"
    artifacts = _artifacts(tmp_path)
    app = create_app(
        artifact_directory=artifacts,
        feedback_directory=private,
        api_key="test-secret",
        expected_manifest_sha256=sha256((artifacts / "manifest.json").read_bytes()).hexdigest(),
    )
    transcript = build_meeting_package(
        "Lan: Please prepare the rollout checklist by Friday.\n",
        None,
        meeting_id="feedback-2",
        meeting_title="Feedback test",
        meeting_date="2026-09-18",
    ).encode()
    with TestClient(app) as client:
        job = _completed_job(client, transcript)
        endpoint = f"/api/v1/meetings/jobs/{job['job_id']}/feedback"
        payload = {
            "job_id": job["job_id"],
            "content_hash": job["content_hash"],
            "corrected_final_tasks": [{
                "task_name": "corrected",
                "assignee": "Lan",
                "start_date": "",
                "due_date": "",
                "due_date_text": "",
                "evidence": "human correction",
                "status": "Proposed",
            }],
            "reviewer": "human@example.test",
            "reviewed_at": "2026-09-18T10:00:00+07:00",
            "approval": True,
        }
        headers = {"X-API-Key": "test-secret"}
        assert client.post(endpoint, json=payload, headers=headers).status_code == 201
        assert client.post(endpoint, json=payload, headers=headers).json()["created"] is False
        conflict = {**payload, "corrected_final_tasks": [{
            "task_name": "different",
            "assignee": "Lan",
            "start_date": "",
            "due_date": "",
            "due_date_text": "",
            "evidence": "human correction",
            "status": "Proposed",
        }]}
        assert client.post(endpoint, json=conflict, headers=headers).status_code == 409
        source = private / "tenant-b" / "sources" / f"{job['content_hash']}.json"
        feedback = private / "tenant-b" / "feedback" / f"{job['job_id']}.json"
        assert source.is_file() and feedback.is_file()
        assert "Lan: Please prepare" in source.read_text(encoding="utf-8")


def test_v227_feedback_rejects_malformed_corrected_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-shape")
    private = tmp_path / "private-feedback"
    artifacts = _artifacts(tmp_path)
    app = create_app(
        artifact_directory=artifacts,
        feedback_directory=private,
        api_key="test-secret",
        expected_manifest_sha256=sha256((artifacts / "manifest.json").read_bytes()).hexdigest(),
    )
    transcript = build_meeting_package(
        "Lan: Please prepare the rollout checklist by Friday.\n",
        None,
        meeting_id="feedback-shape",
        meeting_title="Feedback shape test",
        meeting_date="2026-09-18",
    ).encode()
    with TestClient(app) as client:
        job = _completed_job(client, transcript)
        endpoint = f"/api/v1/meetings/jobs/{job['job_id']}/feedback"
        approval = {
            "reviewer": "human@example.test",
            "reviewed_at": "2026-09-18T10:00:00+07:00",
            "approval": True,
        }
        malformed_tasks = [
            [{"task_name": "missing required fields"}],
            [{
                "task_name": "wrong type",
                "assignee": None,
                "start_date": "",
                "due_date": "",
                "due_date_text": "",
                "evidence": "",
                "status": "Proposed",
            }],
            [{
                "task_name": "wrong status",
                "assignee": "Lan",
                "start_date": "",
                "due_date": "",
                "due_date_text": "",
                "evidence": "",
                "status": "Approved",
            }],
            [{
                "task_name": "extra field",
                "assignee": "Lan",
                "start_date": "",
                "due_date": "",
                "due_date_text": "",
                "evidence": "",
                "status": "Proposed",
                "unexpected": "value",
            }],
        ]
        headers = {"X-API-Key": "test-secret"}
        for tasks in malformed_tasks:
            payload = {
                "job_id": job["job_id"],
                "content_hash": job["content_hash"],
                "corrected_final_tasks": tasks,
                "approval_metadata": approval,
            }
            assert client.post(endpoint, json=payload, headers=headers).status_code == 422
        assert not (private / "tenant-shape" / "feedback").exists()


def test_v227_feedback_source_write_failure_precedes_job_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-write-failure")
    private = tmp_path / "private-feedback"
    artifacts = _artifacts(tmp_path)
    app = create_app(
        artifact_directory=artifacts,
        feedback_directory=private,
        api_key="test-secret",
        expected_manifest_sha256=sha256((artifacts / "manifest.json").read_bytes()).hexdigest(),
    )
    submit_called = False

    def fail_source_write(*args: object, **kwargs: object) -> bool:
        raise OSError("simulated source write failure")

    def unexpected_job_submit(*args: object, **kwargs: object) -> object:
        nonlocal submit_called
        submit_called = True
        raise AssertionError("job submission must follow source persistence")

    monkeypatch.setattr(v227_api, "_atomic_create_json", fail_source_write)
    monkeypatch.setattr(v227_api.MeetingJobStore, "submit", unexpected_job_submit)
    transcript = build_meeting_package(
        "Lan: Please prepare the rollout checklist by Friday.\n",
        None,
        meeting_id="feedback-write-failure",
        meeting_title="Feedback write failure test",
        meeting_date="2026-09-18",
    ).encode()
    with TestClient(app) as client:
        response = client.post("/api/v1/meetings/jobs/process-file", content=transcript, headers=_headers())
    assert response.status_code == 500
    assert submit_called is False


def test_v227_atomic_feedback_record_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    assert v227_api._atomic_create_json(path, {"value": "first"}) is True
    assert v227_api._atomic_create_json(path, {"value": "second"}) is False
    assert path.read_text(encoding="utf-8") == '{"value":"first"}\n'
