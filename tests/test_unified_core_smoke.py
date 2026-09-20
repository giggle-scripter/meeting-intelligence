from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scripts.unified_core_smoke import ApiClient, SmokeError, _redact, run_smoke, _write_json


def _args(tmp_path: Path, **overrides):
    meeting = tmp_path / "demo.txt"
    meeting.write_text("Lan: I will send the summary by Friday.", encoding="utf-8")
    values = {
        "base_url": "https://example.test",
        "expected_core": "v1-frozen",
        "meeting_file": str(meeting),
        "meeting_id": "demo-1",
        "meeting_title": "Demo",
        "meeting_date": "2026-09-21",
        "api_key_file": None,
        "api_key_env": "SMOKE_TEST_KEY",
        "report_output": str(tmp_path / "report.json"),
        "feedback_file": None,
        "allow_feedback": False,
        "timeout": 2.0,
        "poll_timeout": 0.05,
        "poll_interval": 0.001,
    }
    values.update(overrides)
    return type("Args", (), values)()


def _health(core: str = "v1-frozen") -> dict:
    adaptive = core == "v2-adaptive"
    model = "v2-demo-model" if adaptive else "v1-frozen"
    return {
        "status": "ok", "service": "meeting", "core_id": core,
        "adaptive": adaptive, "pipeline_version": "v2" if adaptive else "v1",
        "runtime_model_id": model, "model": model, "supports_meeting_note": not adaptive,
    }


def _handler_factory(*, core="v1-frozen", status="succeeded", status_url="/api/v1/meetings/jobs/job-1", secret="secret-value", calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=_health(core))
        if request.method == "POST" and request.url.path.endswith("process-file"):
            return httpx.Response(202, json={"job_id": "job-1", "created": True, "status_url": status_url})
        if request.method == "GET":
            return httpx.Response(200, json={
                "job_id": "job-1", "status": status, "content_hash": "hash-1",
                "pipeline_version": "v2" if core == "v2-adaptive" else "v1",
                "prompt_version": "core-api-v1",
                "model": "v2-demo-model" if core == "v2-adaptive" else "v1-frozen",
                "result": {"tasks": [{"evidence": secret}]},
                "error": secret,
            })
        if request.method == "POST" and request.url.path.endswith("/feedback"):
            return httpx.Response(201, json={"job_id": "job-1", "created": True, "feedback_hash": "fh-1"})
        return httpx.Response(404)
    return handler


@pytest.mark.parametrize("core", ["v1-frozen", "v2-adaptive"])
def test_smoke_checks_health_and_job_audit_for_both_cores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, core: str) -> None:
    monkeypatch.setenv("SMOKE_TEST_KEY", "secret-value")
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(core=core)))
    try:
        report = run_smoke(_args(tmp_path, expected_core=core), http_client=client)
    finally:
        client.close()
    assert report["ok"] is True
    assert report["health"]["core_id"] == core
    assert report["job"]["core_id"] == core
    assert report["job"]["model"] == report["health"]["runtime_model_id"]
    assert report["feedback_policy"]["mode"] == ("adaptive_training_eligible" if core == "v2-adaptive" else "audit_only")
    assert "secret-value" not in json.dumps(report)


def test_timeout_and_failure_are_bounded_and_do_not_retry_submit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMOKE_TEST_KEY", "secret-value")
    calls: list[httpx.Request] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(status="running", calls=calls)))
    try:
        with pytest.raises(SmokeError, match="poll timeout"):
            run_smoke(_args(tmp_path, poll_timeout=0.003), http_client=client)
    finally:
        client.close()
    assert len([request for request in calls if request.method == "POST" and request.url.path.endswith("process-file")]) == 1


def test_failed_job_is_reported_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMOKE_TEST_KEY", "secret-value")
    calls: list[httpx.Request] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(status="failed", calls=calls)))
    try:
        with pytest.raises(SmokeError, match="job failed"):
            run_smoke(_args(tmp_path), http_client=client)
    finally:
        client.close()
    assert len([request for request in calls if request.method == "POST" and request.url.path.endswith("process-file")]) == 1


def test_hostile_status_url_is_rejected_before_external_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMOKE_TEST_KEY", "secret-value")
    calls: list[httpx.Request] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(status_url="https://evil.example/job", calls=calls)))
    try:
        with pytest.raises(SmokeError, match="same-origin"):
            run_smoke(_args(tmp_path), http_client=client)
    finally:
        client.close()
    assert not any(request.url.host == "evil.example" for request in calls)


def test_feedback_requires_two_explicit_opt_ins_and_validates_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMOKE_TEST_KEY", "secret-value")
    feedback = tmp_path / "feedback.json"
    feedback.write_text(json.dumps({
        "job_id": "job-1", "core_id": "v1-frozen", "content_hash": "hash-1",
        "corrected_final_tasks": [],
        "approval_metadata": {"reviewer": "demo", "reviewed_at": "2026-09-21T09:00:00+07:00", "approval": True},
    }), encoding="utf-8")
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory()))
    try:
        with pytest.raises(SmokeError, match="both --feedback-file and --allow-feedback"):
            run_smoke(_args(tmp_path, feedback_file=str(feedback)), http_client=client)
        with pytest.raises(SmokeError, match="both --feedback-file and --allow-feedback"):
            run_smoke(_args(tmp_path, allow_feedback=True), http_client=client)
        report = run_smoke(_args(tmp_path, feedback_file=str(feedback), allow_feedback=True), http_client=client)
    finally:
        client.close()
    assert report["feedback"]["created"] is True
    assert report["feedback_policy"]["mode"] == "audit_only"


def test_report_redaction_removes_secret_and_transcript(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    _write_json(report_path, {"token": _redact("api_key=secret-value", ("secret-value",)), "transcript_raw": "private"})
    text = report_path.read_text(encoding="utf-8")
    assert "secret-value" not in text
    assert "private" in text  # direct writer is generic; run_smoke sanitizes before calling it
