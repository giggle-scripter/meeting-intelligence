from __future__ import annotations

import hashlib
import json
import time
from base64 import b64encode
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from backend.app.ingestion import build_meeting_package
from backend.tests.experimental.test_v227_experimental import _artifacts
import scripts.experimental_distillation.v227_api as v227_api
import scripts.experimental_distillation.train_v227_feedback as trainer
from scripts.experimental_distillation.train_v227_feedback import train
from scripts.experimental_distillation.v227_api import create_app


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _base(path):
    model = {"schema_version": "v228-frozen-model-v1", "feature_dimensions": 768, "weights": [0.0] * 768, "bias": 0.0}
    policy = {"schema_version": "v228-frozen-policy-v1", "policy": {name: 0.5 for name in ("adaptive_budget", "adaptive_threshold", "base_budget", "base_threshold", "bridge_weight", "field_completeness_min", "intermediate_share_min", "intermediate_weight", "score_mean_cap", "volume_cutoff")}}
    (path / "model.json").write_text(_canonical(model) + "\n", encoding="utf-8")
    (path / "frozen-policy.json").write_text(_canonical(policy) + "\n", encoding="utf-8")
    manifest = {"schema_version": "v228-manifest-v1", "immutable": True, "artifact_hashes": {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in ("model.json", "frozen-policy.json")}}
    (path / "manifest.json").write_text(_canonical(manifest) + "\n", encoding="utf-8")


def test_feedback_trainer_replays_without_mutating_base(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    _base(base)
    root = tmp_path / "feedback"
    tenant = root / "tenant-a"
    (tenant / "sources").mkdir(parents=True)
    (tenant / "feedback").mkdir()
    trace = {"final_tasks": [
        {"task_name": "Gửi báo cáo", "assignee": "Lan", "start_date": "2026-01-01", "due_date": "", "due_date_text": "", "status": "Proposed", "evidence": "Lan gửi báo cáo"},
        {"task_name": "Chuẩn bị demo", "assignee": "Minh", "start_date": "2026-01-01", "due_date": "", "due_date_text": "", "status": "Proposed", "evidence": "Minh chuẩn bị demo"},
    ], "clauses": [], "events_after_deduplication": [], "task_ledger": {"ledger": {}}}
    transcript = "Meeting date: 2026-01-01\nLan gửi báo cáo. Minh chuẩn bị demo."
    transcript_hash = hashlib.sha256(transcript.encode()).hexdigest()
    raw_upload_sha256 = transcript_hash
    identity = ["meeting.txt", "m1", "Demo", "2026-01-01", raw_upload_sha256]
    content_hash = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    source = {"schema_version": "v227-feedback-source-v1", "tenant_id": "tenant-a", "content_hash": content_hash, "raw_upload_sha256": raw_upload_sha256, "transcript_sha256": transcript_hash, "transcript": transcript, "file_name": "meeting.txt", "meeting_id": "m1", "meeting_title": "Demo", "meeting_date": "2026-01-01", "trace": trace}
    (tenant / "sources" / f"{content_hash}.json").write_text(_canonical(source) + "\n", encoding="utf-8")
    feedback = {"schema_version": "v227-feedback-correction-v1", "tenant_id": "tenant-a", "job_id": "job-1", "content_hash": content_hash, "source_transcript_sha256": transcript_hash, "corrected_final_tasks": [trace["final_tasks"][0]], "approval_metadata": {"reviewer": "reviewer", "reviewed_at": "2026-01-02T00:00:00+00:00", "approval": True}}
    feedback_hash = hashlib.sha256(_canonical(feedback).encode()).hexdigest()
    feedback["feedback_hash"] = feedback_hash
    (tenant / "feedback" / "job-1.json").write_text(_canonical(feedback) + "\n", encoding="utf-8")
    before = (base / "model.json").read_bytes()
    monkeypatch.setattr(trainer, "FROZEN_MANIFEST_SHA256", hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest())
    result = train(tenant_id="tenant-a", feedback_directory=root, base_artifact_directory=base)
    assert result["status"] == "complete"
    assert result["coverage"]["covered_task_count"] == 1
    assert result["coverage"]["uncovered_tasks"] == []
    assert (base / "model.json").read_bytes() == before
    assert (tmp_path / "feedback" / "tenant-a" / "challengers" / result["manifest"]["run_digest"][:24] / "manifest.json").is_file()
    rerun = train(tenant_id="tenant-a", feedback_directory=root, base_artifact_directory=base)
    assert rerun["output"] == result["output"]
    assert rerun["manifest"] == result["manifest"]


def test_api_utf16_intake_persists_raw_identity_and_trains(tmp_path: Path, monkeypatch) -> None:
    """A packaged UTF-16 upload keeps its byte identity through offline replay."""
    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-packaged")
    artifacts = _artifacts(tmp_path)
    base_sha = hashlib.sha256((artifacts / "manifest.json").read_bytes()).hexdigest()
    monkeypatch.setattr(trainer, "FROZEN_MANIFEST_SHA256", base_sha)
    private = tmp_path / "feedback"
    app = create_app(
        artifact_directory=artifacts,
        feedback_directory=private,
        api_key="test-secret",
        expected_manifest_sha256=base_sha,
    )
    package = build_meeting_package(
        "Lan: Please prepare the rollout checklist by Friday.\n",
        None,
        meeting_id="packaged-1",
        meeting_title="Packaged UTF16",
        meeting_date="2026-09-18",
    )
    raw = package.encode("utf-16")
    headers = {
        "X-API-Key": "test-secret",
        "X-File-Name-Base64": b64encode(b"meeting.txt").decode(),
    }
    with TestClient(app) as client:
        submitted = client.post("/api/v1/meetings/jobs/process-file", content=raw, headers=headers).json()
        deadline = time.monotonic() + 5
        while True:
            job = client.get(submitted["status_url"], headers={"X-API-Key": "test-secret"}).json()
            if job["status"] in {"succeeded", "failed"} or time.monotonic() > deadline:
                break
            time.sleep(0.02)
        assert job["status"] == "succeeded", job["error"]
        feedback = {
            "job_id": job["job_id"],
            "content_hash": job["content_hash"],
            "corrected_final_tasks": [],
            "approval_metadata": {
                "reviewer": "reviewer",
                "reviewed_at": "2026-09-19T00:00:00+00:00",
                "approval": True,
            },
        }
        assert client.post(
            f"/api/v1/meetings/jobs/{job['job_id']}/feedback",
            json=feedback,
            headers={"X-API-Key": "test-secret"},
        ).status_code == 201
    source_path = next((private / "tenant-packaged" / "sources").glob("*.json"))
    source = json.loads(source_path.read_text(encoding="utf-8"))
    assert source["raw_upload_sha256"] == hashlib.sha256(raw).hexdigest()
    result = train(
        tenant_id="tenant-packaged",
        feedback_directory=private,
        base_artifact_directory=artifacts,
    )
    assert result["status"] == "complete"
    source_hashes = json.loads(
        (Path(result["output"]) / "source-hashes.json").read_text(encoding="utf-8")
    )
    assert source_hashes["meetings"][next(iter(source_hashes["meetings"]))]["raw_upload_sha256"] == hashlib.sha256(raw).hexdigest()


def test_api_sqlite_feedback_and_idempotency_survive_restart(tmp_path: Path, monkeypatch) -> None:
    """Completed V2.27 jobs remain reviewable and idempotent after an app restart."""
    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-restart")
    # Keep this test independent from operator settings that may be present in
    # a developer shell or CI worker.  Every path and secret used by the app is
    # supplied explicitly below.
    for name in (
        "V227_FEEDBACK_DIRECTORY",
        "V227_ARTIFACT_DIRECTORY",
        "V227_AUDIO_TO_TEXT_ENABLED",
        "V227_TRANSCRIPTION_ENABLED",
        "V227_AUDIO_ENABLED",
        "POWER_AUTOMATE_API_KEY",
        "OPENAI_API_KEY",
        "MEETING_JOB_SQLITE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    artifacts = _artifacts(tmp_path)
    expected_manifest_sha256 = hashlib.sha256((artifacts / "manifest.json").read_bytes()).hexdigest()
    feedback_directory = tmp_path / "feedback"
    sqlite_path = tmp_path / "jobs.sqlite3"
    api_key = "restart-test-secret"
    transcript = (
        "Lan: Please prepare the rollout checklist by Friday.\n"
        "Minh: I will review the checklist.\n"
    )
    raw = transcript.encode("utf-8")
    headers = {
        "X-API-Key": api_key,
        "X-File-Name-Base64": b64encode(b"restart-meeting.txt").decode("ascii"),
        "X-Meeting-Id": "restart-meeting-1",
        "X-Meeting-Title": "Restart integration",
        "X-Meeting-Date": "2026-09-18",
    }
    calls = 0
    original_run = v227_api.run

    def counted_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_run(*args, **kwargs)

    monkeypatch.setattr(v227_api, "run", counted_run)
    app = create_app(
        artifact_directory=artifacts,
        feedback_directory=feedback_directory,
        api_key=api_key,
        expected_manifest_sha256=expected_manifest_sha256,
        job_sqlite_path=sqlite_path,
    )
    with TestClient(app) as client:
        submitted = client.post(
            "/api/v1/meetings/jobs/process-file",
            content=raw,
            headers=headers,
        )
        assert submitted.status_code == 202, submitted.text
        submission = submitted.json()
        deadline = time.monotonic() + 5
        while True:
            response = client.get(submission["status_url"], headers={"X-API-Key": api_key})
            job = response.json()
            if job["status"] in {"succeeded", "failed"} or time.monotonic() > deadline:
                break
            time.sleep(0.02)
        assert job["status"] == "succeeded", job["error"]
        assert job["content_hash"]
        assert job["result"]
        assert job["result"]["tasks"]
        original_job = job
        original_job_id = submission["job_id"]
        original_status_url = submission["status_url"]
    assert calls == 1

    # TestClient closes the first app/lifespan here.  The second app must load
    # the same completed row and source record from the supplied paths.
    restarted_app = create_app(
        artifact_directory=artifacts,
        feedback_directory=feedback_directory,
        api_key=api_key,
        expected_manifest_sha256=expected_manifest_sha256,
        job_sqlite_path=sqlite_path,
    )
    with TestClient(restarted_app) as client:
        restored_response = client.get(original_status_url, headers={"X-API-Key": api_key})
        assert restored_response.status_code == 200
        restored = restored_response.json()
        assert restored["job_id"] == original_job_id
        assert restored["content_hash"] == original_job["content_hash"]
        assert restored["result"] == original_job["result"]
        assert restored["result"]["tasks"] == original_job["result"]["tasks"]

        feedback = {
            "job_id": original_job_id,
            "content_hash": restored["content_hash"],
            "corrected_final_tasks": [],
            "approval_metadata": {
                "reviewer": "restart-reviewer",
                "reviewed_at": "2026-09-19T00:00:00+00:00",
                "approval": True,
            },
        }
        feedback_response = client.post(
            f"/api/v1/meetings/jobs/{original_job_id}/feedback",
            json=feedback,
            headers={"X-API-Key": api_key},
        )
        assert feedback_response.status_code == 201, feedback_response.text
        receipt = feedback_response.json()
        assert receipt["job_id"] == original_job_id
        assert receipt["created"] is True
        assert receipt["feedback_hash"]

        feedback_path = feedback_directory / "tenant-restart" / "feedback" / f"{original_job_id}.json"
        persisted_feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        assert persisted_feedback["feedback_hash"] == receipt["feedback_hash"]
        before = feedback_path.read_bytes()
        repeat_feedback = client.post(
            f"/api/v1/meetings/jobs/{original_job_id}/feedback",
            json=feedback,
            headers={"X-API-Key": api_key},
        )
        assert repeat_feedback.status_code == 200
        assert repeat_feedback.json() == {**receipt, "created": False}
        assert feedback_path.read_bytes() == before

        retried = client.post(
            "/api/v1/meetings/jobs/process-file",
            content=raw,
            headers=headers,
        )
        assert retried.status_code == 202, retried.text
        assert retried.json()["job_id"] == original_job_id
        assert retried.json()["created"] is False
        assert retried.json()["status_url"] == original_status_url
        assert calls == 1


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [("raw", "STOP_SOURCE_CONTENT_HASH_MISMATCH"), ("transcript", "STOP_TRANSCRIPT_HASH_MISMATCH")],
)
def test_trainer_rejects_source_identity_mismatch(tmp_path, monkeypatch, mutation, expected_error):
    base = tmp_path / "base"
    base.mkdir()
    _base(base)
    monkeypatch.setattr(trainer, "FROZEN_MANIFEST_SHA256", hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest())
    root = tmp_path / "feedback"
    tenant = root / "tenant-a"
    (tenant / "sources").mkdir(parents=True)
    (tenant / "feedback").mkdir()
    transcript = "Meeting date: 2026-01-01\nLan gửi báo cáo."
    transcript_hash = hashlib.sha256(transcript.encode()).hexdigest()
    identity = ["meeting.txt", "m1", "Demo", "2026-01-01", transcript_hash]
    content_hash = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    source = {"schema_version": "v227-feedback-source-v1", "tenant_id": "tenant-a", "content_hash": content_hash, "raw_upload_sha256": transcript_hash, "transcript_sha256": transcript_hash, "transcript": transcript, "file_name": "meeting.txt", "meeting_id": "m1", "meeting_title": "Demo", "meeting_date": "2026-01-01", "trace": {"final_tasks": [], "clauses": [], "events_after_deduplication": [], "task_ledger": {"ledger": {}}}}
    if mutation == "raw":
        source["raw_upload_sha256"] = "0" * 64
    else:
        source["transcript"] = transcript + "tampered"
    (tenant / "sources" / f"{content_hash}.json").write_text(_canonical(source) + "\n", encoding="utf-8")
    feedback = {"schema_version": "v227-feedback-correction-v1", "tenant_id": "tenant-a", "job_id": "job-1", "content_hash": content_hash, "source_transcript_sha256": transcript_hash, "corrected_final_tasks": [], "approval_metadata": {"reviewer": "reviewer", "reviewed_at": "2026-01-02T00:00:00+00:00", "approval": True}}
    feedback["feedback_hash"] = hashlib.sha256(_canonical(feedback).encode()).hexdigest()
    (tenant / "feedback" / "job-1.json").write_text(_canonical(feedback) + "\n", encoding="utf-8")
    try:
        train(tenant_id="tenant-a", feedback_directory=root, base_artifact_directory=base)
    except ValueError as exc:
        assert str(exc) == expected_error
    else:
        raise AssertionError(f"{mutation} identity mismatch was accepted")
