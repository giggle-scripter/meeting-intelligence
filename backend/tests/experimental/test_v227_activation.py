from __future__ import annotations

import hashlib
import json
from base64 import b64encode
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from backend.app.ingestion import build_meeting_package
from backend.tests.experimental.test_v227_experimental import _artifacts
import scripts.experimental_distillation.v227_api as api


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _challenger(base: Path, root: Path, tenant: str, *, weight: float = 0.125) -> Path:
    output = root / tenant / "challengers" / "challenger-1"
    output.mkdir(parents=True)
    model = {"schema_version": "v227-tenant-challenger-model-v1", "immutable": True, "feature_dimensions": 768, "weights": [weight] + [0.0] * 767, "bias": 0.0}
    policy_base = json.loads((base / "frozen-policy.json").read_text(encoding="utf-8"))
    policy = {"schema_version": "v227-tenant-challenger-policy-v1", "immutable": True, "base_policy_sha256": hashlib.sha256((base / "frozen-policy.json").read_bytes()).hexdigest(), "policy": policy_base["policy"]}
    files = {
        "model.json": model,
        "frozen-policy.json": policy,
        "source-hashes.json": {"schema_version": "v227-feedback-source-hashes-v1", "tenant_id": tenant, "base_artifact_hashes": {}},
        "coverage.json": {"schema_version": "v227-feedback-coverage-v1", "meeting_count": 1, "candidate_count": 1, "approved_task_count": 1, "covered_task_count": 1, "uncovered_tasks": [], "reconstruction_modes": []},
        "holdout-diagnostics.json": {"schema_version": "v227-feedback-holdout-v1", "meetings": []},
    }
    for name, value in files.items():
        (output / name).write_text(_canonical(value) + "\n", encoding="utf-8")
    base_hashes = {
        "model": hashlib.sha256((base / "model.json").read_bytes()).hexdigest(),
        "policy": hashlib.sha256((base / "frozen-policy.json").read_bytes()).hexdigest(),
        "manifest": hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest(),
    }
    manifest = {
        "schema_version": "v227-tenant-challenger-manifest-v1", "immutable": True,
        "tenant_id": tenant, "run_digest": "digest", "base_artifact_hashes": base_hashes,
        "artifact_hashes": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in files},
        "auto_promotion": False, "active_model_mutated": False,
        "provider_call_count": 0, "network_calls": 0, "gpu_used": False,
    }
    (output / "manifest.json").write_text(_canonical(manifest) + "\n", encoding="utf-8")
    (output / "status.json").write_text(_canonical({"schema_version": "v227-feedback-status-v1", "status": "complete", "promotion": "NOT_READY", "manifest_sha256": hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()}) + "\n", encoding="utf-8")
    return output


def test_activation_serves_changed_weights_and_rollback_returns_to_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = "tenant-a"
    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", tenant)
    base = _artifacts(tmp_path)
    feedback = tmp_path / "feedback"
    challenger = _challenger(base, feedback, tenant)
    base_sha = hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest()
    pointer = api.activate_challenger(challenger, feedback_directory=feedback, artifact_directory=base, expected_manifest_sha256=base_sha)
    assert pointer["active"] is True
    pointer_path = feedback / tenant / api.ACTIVE_POINTER_NAME
    assert json.loads(pointer_path.read_text(encoding="utf-8"))["tenant_id"] == tenant
    assert not (feedback / api.ACTIVE_POINTER_NAME).exists()

    app = api.create_app(artifact_directory=base, feedback_directory=feedback, api_key="secret", expected_manifest_sha256=base_sha)
    transcript = build_meeting_package("Lan: Prepare the checklist by Friday.\n", None, meeting_date="2026-09-18").encode()
    headers = {"X-API-Key": "secret", "X-File-Name-Base64": b64encode(b"meeting.txt").decode()}
    with TestClient(app) as client:
        submitted = client.post("/api/v1/meetings/jobs/process-file", content=transcript, headers=headers).json()
        for _ in range(250):
            job = client.get(submitted["status_url"], headers={"X-API-Key": "secret"}).json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        assert job["status"] == "succeeded", job.get("error")
        assert job["model"] == "v227-tenant-challenger"
        assert job["result"]["diagnostics"]["artifact_hashes"]["model"] == hashlib.sha256((challenger / "model.json").read_bytes()).hexdigest()

    rollback = api.rollback_activation(feedback_directory=feedback, artifact_directory=base, expected_manifest_sha256=base_sha)
    assert rollback["active"] is False


def test_activation_rejects_cross_tenant_and_tampered_challenger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-a")
    base = _artifacts(tmp_path)
    feedback = tmp_path / "feedback"
    other = _challenger(base, feedback, "tenant-b")
    base_sha = hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="STOP_CHALLENGER_PATH_TENANT_MISMATCH"):
        api.activate_challenger(other, feedback_directory=feedback, artifact_directory=base, expected_manifest_sha256=base_sha)
    challenger = _challenger(base, feedback, "tenant-a")
    (challenger / "model.json").write_text((challenger / "model.json").read_text(encoding="utf-8").replace("0.125", "0.25"), encoding="utf-8")
    with pytest.raises(RuntimeError, match="STOP_ARTIFACT_HASH_MISMATCH:model.json"):
        api.activate_challenger(challenger, feedback_directory=feedback, artifact_directory=base, expected_manifest_sha256=base_sha)


def test_activation_isolated_for_two_tenants_sharing_feedback_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _artifacts(tmp_path)
    feedback = tmp_path / "feedback"
    base_sha = hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest()
    challenger_a = _challenger(base, feedback, "tenant-a", weight=0.125)
    challenger_b = _challenger(base, feedback, "tenant-b", weight=0.25)

    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-a")
    api.activate_challenger(
        challenger_a, feedback_directory=feedback, artifact_directory=base,
        expected_manifest_sha256=base_sha,
    )
    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-b")
    api.activate_challenger(
        challenger_b, feedback_directory=feedback, artifact_directory=base,
        expected_manifest_sha256=base_sha,
    )

    pointer_a = feedback / "tenant-a" / api.ACTIVE_POINTER_NAME
    pointer_b = feedback / "tenant-b" / api.ACTIVE_POINTER_NAME
    assert json.loads(pointer_a.read_text(encoding="utf-8"))["tenant_id"] == "tenant-a"
    assert json.loads(pointer_b.read_text(encoding="utf-8"))["tenant_id"] == "tenant-b"
    assert not (feedback / api.ACTIVE_POINTER_NAME).exists()

    monkeypatch.setenv("V227_FEEDBACK_TENANT_ID", "tenant-a")
    api.rollback_activation(
        feedback_directory=feedback, artifact_directory=base,
        expected_manifest_sha256=base_sha,
    )
    loaded_a = api._load_active_bundle(
        artifact_directory=base, feedback_directory=feedback,
        tenant_id="tenant-a", expected_manifest_sha256=base_sha,
    )
    loaded_b = api._load_active_bundle(
        artifact_directory=base, feedback_directory=feedback,
        tenant_id="tenant-b", expected_manifest_sha256=base_sha,
    )
    assert loaded_a[-1] == "base"
    assert loaded_b[-1] == "challenger"
    assert json.loads(pointer_b.read_text(encoding="utf-8"))["active"] is True
