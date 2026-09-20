from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.core_api import create_app
from backend.app.ingestion import build_meeting_package
from backend.app.jobs import MeetingJobStore
from backend.app.models import FinalTask, PipelineDiagnostics, PipelineResult


class FakeCore:
    def __init__(self, core_id: str, adaptive: bool, *, fail: bool = False) -> None:
        self.core_id = core_id
        is_v2 = core_id == "v2-adaptive"
        self.capabilities = type(
            "Capabilities",
            (),
            {
                "adaptive": adaptive,
                "pipeline_version": "v227_experimental" if is_v2 else "v1",
                "runtime_model_id": "v228-frozen-full-fit" if is_v2 else "v1-frozen",
                "supports_meeting_note": not is_v2,
            },
        )()
        self.fail = fail
        self.calls = 0

    def process(self, meeting, **options):
        self.calls += 1
        if self.fail:
            raise RuntimeError("fake core failed")
        return PipelineResult(
            meeting_title=meeting.meeting_title,
            summary=f"processed by {self.core_id}",
            tasks=[
                FinalTask(
                    task_name="Send release note",
                    assignee="Lan",
                    start_date=meeting.meeting_date,
                    due_date=meeting.meeting_date,
                    due_date_text="today",
                    evidence="Lan: Em sẽ gửi release note.",
                )
            ],
            diagnostics=PipelineDiagnostics(meeting_date_source=meeting.meeting_date_source),
        )


def _client(core: FakeCore) -> TestClient:
    return TestClient(
        create_app(
            core=core,
            settings=Settings(power_automate_api_key="secret"),
            job_store=MeetingJobStore(),
        )
    )


@pytest.mark.parametrize(
    ("core_id", "adaptive"),
    (("v1-frozen", False), ("v2-adaptive", True)),
)
def test_unified_core_api_selects_core_and_completes_text_job(
    core_id: str, adaptive: bool
) -> None:
    core = FakeCore(core_id, adaptive)
    with _client(core) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["core_id"] == core_id
        assert health.json()["adaptive"] is adaptive
        assert health.json()["pipeline_version"] == (
            "v227_experimental" if adaptive else "v1"
        )
        assert health.json()["runtime_model_id"] == (
            "v228-frozen-full-fit" if adaptive else "v1-frozen"
        )

        payload = b"=== MEETING METADATA ===\nmeeting_id: M-CORE\nmeeting_title: Core API\nmeeting_date: 2026-09-20\n=== END MEETING METADATA ===\n=== TRANSCRIPT ===\nLan: Em se gui release note."
        headers = {"X-API-Key": "secret", "X-File-Name": "meeting.txt"}
        first = client.post(
            "/api/v1/meetings/jobs/process-file", content=payload, headers=headers
        )
        duplicate = client.post(
            "/api/v1/meetings/jobs/process-file", content=payload, headers=headers
        )
        assert first.status_code == duplicate.status_code == 202
        assert first.json()["created"] is True
        assert duplicate.json()["created"] is False
        assert duplicate.json()["job_id"] == first.json()["job_id"]

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get(duplicate.json()["status_url"], headers=headers)
            if result.json()["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        assert result.json()["status"] == "succeeded"
        assert result.json()["result"]["summary"] == f"processed by {core_id}"
        assert result.json()["result"]["diagnostics"]["meeting_date_source"] == (
            "PACKAGE_METADATA"
        )
        job = client.get(duplicate.json()["status_url"], headers=headers).json()
        assert job["pipeline_version"] == ("v227_experimental" if adaptive else "v1")
        assert job["model"] == ("v228-frozen-full-fit" if adaptive else "v1-frozen")
        assert core.calls == 1


def test_unified_core_api_uses_same_api_key_authorization() -> None:
    with _client(FakeCore("v1-frozen", False)) as client:
        response = client.post(
            "/api/v1/meetings/jobs/process-file", content=b"Lan: hello"
        )
        assert response.status_code == 401
        assert client.get("/api/v1/meetings/jobs/unknown").status_code == 401


def test_unified_core_api_exposes_failed_core_job() -> None:
    core = FakeCore("v2-adaptive", True, fail=True)
    with _client(core) as client:
        response = client.post(
            "/api/v1/meetings/jobs/process-file",
            content=b"Lan: hello",
            headers={"X-API-Key": "secret"},
        )
        assert response.status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get(
                response.json()["status_url"], headers={"X-API-Key": "secret"}
            )
            if result.json()["status"] == "failed":
                break
            time.sleep(0.01)
        assert result.json()["status"] == "failed"
        assert result.json()["error"] == "fake core failed"


def test_unified_core_api_rejects_packaged_note_for_unsupported_core() -> None:
    core = FakeCore("v2-adaptive", True)
    with _client(core) as client:
        payload = build_meeting_package(
            "Lan: Em se gui bao cao.",
            "Secretary note",
            meeting_id="M-NOTE",
            meeting_date="2026-09-20",
        ).encode()
        response = client.post(
            "/api/v1/meetings/jobs/process-file",
            content=payload,
            headers={"X-API-Key": "secret"},
        )
        assert response.status_code == 422
        assert core.calls == 0


def test_unified_core_api_accepts_v1_meeting_note() -> None:
    core = FakeCore("v1-frozen", False)
    with _client(core) as client:
        payload = build_meeting_package(
            "Lan: Em se gui bao cao.",
            "Secretary note",
            meeting_id="M-NOTE-V1",
            meeting_date="2026-09-20",
        ).encode()
        response = client.post(
            "/api/v1/meetings/jobs/process-file",
            content=payload,
            headers={"X-API-Key": "secret"},
        )
        assert response.status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get(
                response.json()["status_url"], headers={"X-API-Key": "secret"}
            ).json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        assert job["status"] == "succeeded", job["error"]
        assert core.calls == 1
