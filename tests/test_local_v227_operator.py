from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scripts.local_v227_operator import (
    ApiClient,
    OperatorError,
    _redact,
    send_feedback,
    submit_review,
)


def _args(**overrides):
    values = {
        "base_url": "https://example.test",
        "transcript": "meeting.txt",
        "review_output": None,
        "meeting_id": "m-1",
        "meeting_title": "Weekly",
        "meeting_date": "2026-09-19",
        "api_key_file": None,
        "api_key_env": "TEST_OPERATOR_KEY",
        "timeout": 2.0,
        "poll_timeout": 0.05,
        "poll_interval": 0.001,
    }
    values.update(overrides)
    return type("Args", (), values)()


def _task(name="Do it"):
    return {
        "task_name": name,
        "assignee": "A",
        "start_date": "",
        "due_date": "",
        "due_date_text": "",
        "evidence": "A said so",
        "status": "Proposed",
    }


def test_submit_success_writes_review_without_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transcript = tmp_path / "meeting.txt"
    transcript.write_text("SECRET TRANSCRIPT SHOULD NEVER BE WRITTEN", encoding="utf-8")
    monkeypatch.setenv("TEST_OPERATOR_KEY", "super-secret")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1", "status_url": "/api/v1/meetings/jobs/job-1"})
        return httpx.Response(200, json={
            "job_id": "job-1", "status": "succeeded", "content_hash": "hash-1",
            "result": {"meeting_id": "m-1", "meeting_title": "Weekly", "meeting_date": "2026-09-19", "tasks": [_task()]},
        })

    output = tmp_path / "review.json"
    args = _args(transcript=str(transcript), review_output=str(output))
    result = submit_review(args, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    body = json.loads(output.read_text(encoding="utf-8"))
    assert result == output
    assert body["job_id"] == "job-1"
    assert body["corrected_final_tasks"] == body["proposed_tasks"]
    assert body["approval_metadata"]["approval"] is False
    assert "SECRET TRANSCRIPT" not in output.read_text(encoding="utf-8")
    assert calls[0].content == transcript.read_bytes()


def test_send_feedback_rejects_before_http_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = tmp_path / "review.json"
    review.write_text(json.dumps({
        "job_id": "job-1", "content_hash": "hash-1", "corrected_final_tasks": [_task()],
        "approval_metadata": {"reviewer": "", "reviewed_at": "", "approval": False},
    }), encoding="utf-8")
    monkeypatch.setenv("TEST_OPERATOR_KEY", "secret")
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, json={})

    args = _args(review=str(review), receipt=str(tmp_path / "receipt.json"))
    with pytest.raises(OperatorError, match="explicitly true"):
        send_feedback(args, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert called is False


def _approved_review(path: Path) -> None:
    path.write_text(json.dumps({
        "job_id": "job-1", "content_hash": "hash-1", "corrected_final_tasks": [_task()],
        "approval_metadata": {
            "reviewer": "reviewer", "reviewed_at": "2026-09-19T12:00:00+07:00", "approval": True,
        },
    }), encoding="utf-8")


def test_send_feedback_receipt_is_idempotent_without_second_http_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    review = tmp_path / "review.json"
    _approved_review(review)
    monkeypatch.setenv("TEST_OPERATOR_KEY", "secret")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(201, json={
            "job_id": "job-1", "created": True, "feedback_hash": "server-hash-1",
        })

    receipt = tmp_path / "receipt.json"
    args = _args(review=str(review), receipt=str(receipt))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert send_feedback(args, http_client=client) == receipt
        assert send_feedback(args, http_client=client) == receipt
    finally:
        client.close()
    assert calls == 1
    body = json.loads(receipt.read_text(encoding="utf-8"))
    assert body["request_sha256"]
    assert "feedback_hash" not in body
    assert body["response"]["feedback_hash"] == "server-hash-1"


@pytest.mark.parametrize(
    "response, message",
    [
        ({"job_id": "other", "created": True, "feedback_hash": "server-hash"}, "matching job_id"),
        ({"job_id": "job-1", "created": "yes", "feedback_hash": "server-hash"}, "created boolean"),
        ({"job_id": "job-1", "created": True, "feedback_hash": ""}, "nonempty feedback_hash"),
    ],
)
def test_send_feedback_rejects_malformed_success_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: dict[str, object], message: str,
) -> None:
    review = tmp_path / "review.json"
    _approved_review(review)
    monkeypatch.setenv("TEST_OPERATOR_KEY", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=response)

    with pytest.raises(OperatorError, match=message):
        send_feedback(_args(review=str(review), receipt=str(tmp_path / "receipt.json")),
                      http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_operator_documentation_uses_v227_port() -> None:
    documentation = Path(__file__).parents[1].joinpath("docs/run-local-v227-operator-vi.md").read_text(encoding="utf-8")
    assert "127.0.0.1:8011" in documentation
    assert "127.0.0.1:8010" not in documentation


def test_submit_failed_job_does_not_write_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transcript = tmp_path / "meeting.txt"
    transcript.write_text("private transcript", encoding="utf-8")
    monkeypatch.setenv("TEST_OPERATOR_KEY", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-f", "status_url": "/status/job-f"})
        return httpx.Response(200, json={"job_id": "job-f", "status": "failed", "error": "private transcript"})

    output = tmp_path / "review.json"
    with pytest.raises(OperatorError, match="job failed"):
        submit_review(_args(transcript=str(transcript), review_output=str(output)),
                      http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert not output.exists()


def test_submit_timeout_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transcript = tmp_path / "meeting.txt"
    transcript.write_text("private transcript", encoding="utf-8")
    monkeypatch.setenv("TEST_OPERATOR_KEY", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-t", "status_url": "/status/job-t"})
        return httpx.Response(200, json={"job_id": "job-t", "status": "running"})

    with pytest.raises(OperatorError, match="poll timeout"):
        submit_review(_args(transcript=str(transcript), poll_timeout=0.002, poll_interval=0.001),
                      http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_http_error_and_redaction_never_expose_secret() -> None:
    secret = "super-secret-value"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"X-API-Key: {secret}; transcript=do not expose")

    client = ApiClient("https://example.test", secret, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(OperatorError) as caught:
            client.feedback("job-1", {})
        assert secret not in str(caught.value)
        assert "transcript" not in str(caught.value).lower()
        assert secret not in _redact(f"api_key={secret}", (secret,))
    finally:
        client.close()
