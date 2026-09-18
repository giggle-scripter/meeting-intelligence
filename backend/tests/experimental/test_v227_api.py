from __future__ import annotations

from base64 import b64encode
from hashlib import sha256
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from backend.app.ingestion import build_meeting_package
from backend.tests.experimental.test_v227_experimental import _artifacts
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
