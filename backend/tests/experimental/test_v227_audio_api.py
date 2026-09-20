from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient

from backend.tests.experimental.test_v227_experimental import _artifacts
from scripts.experimental_distillation.v227_api import MAX_AUDIO_BYTES, create_app


def _audio_headers(name: str = "meeting.wav") -> dict[str, str]:
    return {
        "X-API-Key": "test-secret",
        "X-File-Name": name,
        "X-Meeting-Id": "audio-test-1",
        "X-Meeting-Title": "Audio test",
        "X-Meeting-Date": "2026-09-18",
        "Content-Type": "audio/wav",
    }


def _poll(client: TestClient, status_url: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(status_url, headers={"X-API-Key": "test-secret"})
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("audio job did not complete")


def test_audio_is_strictly_opt_in_and_never_calls_injected_provider(tmp_path: Path) -> None:
    called = False

    def transcriber(*args: object) -> object:
        nonlocal called
        called = True
        return {"segments": [{"speaker": "Lan", "text": "send the report"}]}

    artifacts = _artifacts(tmp_path)
    app = create_app(
        artifact_directory=artifacts,
        api_key="test-secret",
        audio_transcriber=transcriber,
        expected_manifest_sha256=sha256((artifacts / "manifest.json").read_bytes()).hexdigest(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/meetings/jobs/process-audio",
            content=b"audio",
            headers=_audio_headers(),
        )
    assert response.status_code == 404
    assert called is False


def test_audio_fake_transcriber_is_diarized_idempotent_and_private_source_is_audio(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-audio")
    private = tmp_path / "private-feedback"
    calls: list[tuple[bytes, str, str]] = []

    def transcriber(audio: bytes, file_name: str, content_type: str) -> dict:
        calls.append((audio, file_name, content_type))
        return {
            "segments": [
                {"speaker": "Lan", "text": "Please prepare the rollout checklist by Friday."},
                {"speaker": "Minh", "text": "I will review the checklist."},
            ],
        }

    artifacts = _artifacts(tmp_path)
    app = create_app(
        artifact_directory=artifacts,
        feedback_directory=private,
        api_key="test-secret",
        audio_enabled=True,
        audio_transcriber=transcriber,
        expected_manifest_sha256=sha256((artifacts / "manifest.json").read_bytes()).hexdigest(),
    )
    audio = b"fake-wav-bytes"
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/meetings/jobs/process-audio",
            content=audio,
            headers=_audio_headers(),
        )
        assert first.status_code == 202
        duplicate = client.post(
            "/api/v1/meetings/jobs/process-audio",
            content=audio,
            headers=_audio_headers(),
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["created"] is False
        assert duplicate.json()["job_id"] == first.json()["job_id"]
        job = _poll(client, first.json()["status_url"])

    assert job["status"] == "succeeded", job["error"]
    assert "source_modality" not in job
    assert job["model"] == "gpt-4o-transcribe-diarize"
    assert len(calls) == 1
    assert calls[0] == (audio, "meeting.wav", "audio/wav")
    source_path = private / "tenant-audio" / "sources" / f"{job['content_hash']}.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    assert source["source_modality"] == "audio"
    assert source["raw_upload_sha256"] == sha256(audio).hexdigest()
    assert source["transcript_sha256"] == sha256(source["transcript"].encode()).hexdigest()
    assert "fake-wav-bytes" not in source_path.read_text(encoding="utf-8")
    assert "Lan: Please prepare" in source["transcript"]


def test_audio_validates_date_format_extension_and_size_before_provider(tmp_path: Path) -> None:
    calls = 0

    def transcriber(*args: object) -> object:
        nonlocal calls
        calls += 1
        return {"segments": [{"speaker": "Lan", "text": "hello"}]}

    artifacts = _artifacts(tmp_path)
    app = create_app(
        artifact_directory=artifacts,
        api_key="test-secret",
        audio_enabled=True,
        audio_transcriber=transcriber,
        expected_manifest_sha256=sha256((artifacts / "manifest.json").read_bytes()).hexdigest(),
    )
    with TestClient(app) as client:
        missing_date = client.post(
            "/api/v1/meetings/jobs/process-audio",
            content=b"audio",
            headers={**_audio_headers(), "X-Meeting-Date": ""},
        )
        unsupported = client.post(
            "/api/v1/meetings/jobs/process-audio",
            content=b"audio",
            headers=_audio_headers("meeting.ogg"),
        )
        too_large = client.post(
            "/api/v1/meetings/jobs/process-audio",
            content=b"x" * (MAX_AUDIO_BYTES + 1),
            headers=_audio_headers(),
        )
    assert missing_date.status_code == 422
    assert unsupported.status_code == 422
    assert too_large.status_code == 413
    assert calls == 0


def test_audio_manifest_tamper_fails_job_before_provider(tmp_path: Path) -> None:
    calls = 0

    def transcriber(*args: object) -> object:
        nonlocal calls
        calls += 1
        return {"segments": [{"speaker": "Lan", "text": "hello"}]}

    artifacts = _artifacts(tmp_path)
    app = create_app(
        artifact_directory=artifacts,
        api_key="test-secret",
        audio_enabled=True,
        audio_transcriber=transcriber,
        expected_manifest_sha256=sha256((artifacts / "manifest.json").read_bytes()).hexdigest(),
    )
    with TestClient(app) as client:
        # Lifespan pins the original manifest hash; this simulates a package
        # being changed after startup and before the queued job runs.
        (artifacts / "manifest.json").write_text("{}", encoding="utf-8")
        submitted = client.post(
            "/api/v1/meetings/jobs/process-audio",
            content=b"audio",
            headers=_audio_headers(),
        )
        assert submitted.status_code == 202
        job = _poll(client, submitted.json()["status_url"])

    assert job["status"] == "failed"
    assert job["error"] == "STOP_FROZEN_V228_MANIFEST_HASH_MISMATCH"
    assert calls == 0
