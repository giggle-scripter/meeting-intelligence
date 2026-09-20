from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import local_v227_preflight as preflight


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "artifacts"
    bundle.mkdir()
    model = {
        "schema_version": "v228-frozen-model-v1",
        "feature_dimensions": 768,
        "weights": [0.0] * 768,
        "bias": 0.0,
    }
    policy = {"schema_version": "v228-frozen-policy-v1", "policy": {
        "adaptive_budget": 1, "adaptive_threshold": 0.1, "base_budget": 1,
        "base_threshold": 0.1, "bridge_weight": 1.0, "field_completeness_min": 0.1,
        "intermediate_share_min": 0.1, "intermediate_weight": 1.0,
        "score_mean_cap": 1.0, "volume_cutoff": 1,
    }}
    (bundle / "model.json").write_text(json.dumps(model), encoding="utf-8")
    (bundle / "frozen-policy.json").write_text(json.dumps(policy), encoding="utf-8")
    hashes = {
        name: hashlib.sha256((bundle / name).read_bytes()).hexdigest()
        for name in ("model.json", "frozen-policy.json")
    }
    (bundle / "manifest.json").write_text(json.dumps({
        "schema_version": "v228-manifest-v1", "immutable": True,
        "artifact_hashes": hashes,
    }), encoding="utf-8")
    return bundle


def _env(tmp_path: Path, bundle: Path) -> dict[str, str]:
    return {
        "POWER_AUTOMATE_API_KEY": "server-secret",
        "V227_FEEDBACK_TENANT_ID": "tenant-a",
        "V227_FEEDBACK_DIRECTORY": str(tmp_path / "feedback"),
        "MEETING_JOB_SQLITE_PATH": str(tmp_path / "runtime" / "jobs.sqlite3"),
        "V227_ARTIFACT_DIRECTORY": str(bundle),
        "V227_AUDIO_TO_TEXT_ENABLED": "false",
        "OPENAI_API_KEY": "",
    }


def test_preflight_passes_without_printing_secrets(tmp_path: Path, monkeypatch, capsys) -> None:
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(preflight, "FROZEN_MANIFEST_SHA256", preflight._sha256(bundle / "manifest.json"))
    env = _env(tmp_path, bundle)
    report = preflight.validate_preflight(env=env, root=tmp_path)
    assert report["status"] == "PASS"
    assert "server-secret" not in json.dumps(report)
    assert capsys.readouterr().out == ""


def test_preflight_reports_missing_key_and_tenant(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(preflight, "FROZEN_MANIFEST_SHA256", preflight._sha256(bundle / "manifest.json"))
    env = _env(tmp_path, bundle)
    env.pop("POWER_AUTOMATE_API_KEY")
    env.pop("V227_FEEDBACK_TENANT_ID")
    report = preflight.validate_preflight(env=env, root=tmp_path)
    codes = {check["code"] for check in report["checks"]}
    assert report["status"] == "FAIL"
    assert "STOP_POWER_AUTOMATE_API_KEY_REQUIRED" in codes
    assert "STOP_INVALID_V227_FEEDBACK_TENANT_ID" in codes


def test_preflight_rejects_wrong_manifest(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    env = _env(tmp_path, bundle)
    report = preflight.validate_preflight(env=env, root=tmp_path)
    check = next(item for item in report["checks"] if item["name"] == "frozen_artifacts")
    assert check["code"] == "STOP_FROZEN_V228_MANIFEST_HASH_MISMATCH"


def _rewrite_manifest(bundle: Path, mutate) -> None:
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_preflight_accepts_extra_hashed_artifact(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(tmp_path)
    extra = bundle / "closeout.md"
    extra.write_text("validated", encoding="utf-8")
    _rewrite_manifest(bundle, lambda manifest: manifest["artifact_hashes"].update(
        {extra.name: hashlib.sha256(extra.read_bytes()).hexdigest()}
    ))
    monkeypatch.setattr(preflight, "FROZEN_MANIFEST_SHA256", preflight._sha256(bundle / "manifest.json"))

    report = preflight.validate_preflight(env=_env(tmp_path, bundle), root=tmp_path)

    assert report["status"] == "PASS"


def test_preflight_rejects_missing_required_artifact_entry(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(tmp_path)
    _rewrite_manifest(bundle, lambda manifest: manifest["artifact_hashes"].pop("model.json"))
    monkeypatch.setattr(preflight, "FROZEN_MANIFEST_SHA256", preflight._sha256(bundle / "manifest.json"))

    report = preflight.validate_preflight(env=_env(tmp_path, bundle), root=tmp_path)

    check = next(item for item in report["checks"] if item["name"] == "frozen_artifacts")
    assert check["code"] == "STOP_INVALID_RUNTIME_ARTIFACT_SET"


def test_preflight_rejects_missing_hashed_artifact(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(tmp_path)
    _rewrite_manifest(bundle, lambda manifest: manifest["artifact_hashes"].update(
        {"missing.json": "0" * 64}
    ))
    monkeypatch.setattr(preflight, "FROZEN_MANIFEST_SHA256", preflight._sha256(bundle / "manifest.json"))

    report = preflight.validate_preflight(env=_env(tmp_path, bundle), root=tmp_path)

    check = next(item for item in report["checks"] if item["name"] == "frozen_artifacts")
    assert check["code"] == "STOP_MISSING_RUNTIME_ARTIFACT"


def test_preflight_rejects_hash_mismatched_artifact(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(tmp_path)
    _rewrite_manifest(bundle, lambda manifest: manifest["artifact_hashes"].update(
        {"model.json": "0" * 64}
    ))
    monkeypatch.setattr(preflight, "FROZEN_MANIFEST_SHA256", preflight._sha256(bundle / "manifest.json"))

    report = preflight.validate_preflight(env=_env(tmp_path, bundle), root=tmp_path)

    check = next(item for item in report["checks"] if item["name"] == "frozen_artifacts")
    assert check["code"] == "STOP_ARTIFACT_BUNDLE_HASH_MISMATCH"


def test_preflight_rejects_unsafe_hashed_artifact_name(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(tmp_path)
    _rewrite_manifest(bundle, lambda manifest: manifest["artifact_hashes"].update(
        {"../outside.json": "0" * 64}
    ))
    monkeypatch.setattr(preflight, "FROZEN_MANIFEST_SHA256", preflight._sha256(bundle / "manifest.json"))

    report = preflight.validate_preflight(env=_env(tmp_path, bundle), root=tmp_path)

    check = next(item for item in report["checks"] if item["name"] == "frozen_artifacts")
    assert check["code"] == "STOP_INVALID_RUNTIME_ARTIFACT_ENTRY"


def test_preflight_rejects_sqlite_inside_tracked_source(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(preflight, "FROZEN_MANIFEST_SHA256", preflight._sha256(bundle / "manifest.json"))
    env = _env(tmp_path, bundle)
    env["MEETING_JOB_SQLITE_PATH"] = str(preflight.ROOT / "backend" / "app" / "jobs.sqlite3")
    report = preflight.validate_preflight(env=env, root=preflight.ROOT)
    check = next(item for item in report["checks"] if item["name"] == "sqlite_job_store")
    assert check["code"] == "STOP_MEETING_JOB_SQLITE_PATH_INSIDE_TRACKED_SOURCE"


def test_preflight_accepts_ignored_evaluation_runtime_sqlite() -> None:
    """The ignored local runtime directory is valid for the durable job store."""
    sqlite_path = preflight.ROOT / "evaluation" / "runtime" / "v227-feedback" / "meeting-jobs.sqlite3"

    ok, code = preflight._sqlite_check(str(sqlite_path), preflight.ROOT)

    assert ok is True
    assert code == "PASS_MEETING_JOB_SQLITE_PATH"


def test_preflight_requires_openai_key_when_audio_enabled(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(preflight, "FROZEN_MANIFEST_SHA256", preflight._sha256(bundle / "manifest.json"))
    env = _env(tmp_path, bundle)
    env["V227_AUDIO_TO_TEXT_ENABLED"] = "true"
    report = preflight.validate_preflight(env=env, root=tmp_path)
    check = next(item for item in report["checks"] if item["name"] == "audio_provider_key")
    assert check["code"] == "STOP_OPENAI_API_KEY_REQUIRED_FOR_AUDIO"
