from __future__ import annotations

import json
import inspect
import hashlib
from pathlib import Path

import pytest

from backend.app.ai.client import DisabledAiClient
from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting_by_version
import scripts.experimental_distillation.run_v227_experimental as v227
from scripts.experimental_distillation.run_v227_experimental import run


def _artifacts(tmp_path: Path) -> Path:
    target = tmp_path / "artifacts"
    target.mkdir()
    # Tests must pass in a clean Git checkout with no private model package.
    model = {
        "schema_version": "v228-frozen-model-v1",
        "feature_dimensions": 768,
        "weights": [0.0] * 768,
        "bias": 0.0,
    }
    policy = {
        "schema_version": "v228-frozen-policy-v1",
        "policy": {
            "adaptive_budget": 20,
            "adaptive_threshold": 0.15,
            "base_budget": 8,
            "base_threshold": 0.35,
            "bridge_weight": 0.5,
            "field_completeness_min": 0.5,
            "intermediate_share_min": 0.33,
            "intermediate_weight": 0.75,
            "score_mean_cap": 0.3,
            "volume_cutoff": 30,
        },
    }
    hashes = {}
    for name, payload in (("model.json", model), ("frozen-policy.json", policy)):
        path = target / name
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (target / "manifest.json").write_text(
        json.dumps({"schema_version": "v228-manifest-v1", "immutable": True, "artifact_hashes": hashes}),
        encoding="utf-8",
    )
    return target


def _transcript(tmp_path: Path) -> Path:
    path = tmp_path / "new-meeting.txt"
    path.write_text(
        "Lan: Please prepare the rollout checklist by Friday.\n"
        "Minh: I will review the checklist.\n",
        encoding="utf-8",
    )
    return path


def test_new_transcript_is_deterministic_and_never_calls_provider(tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    artifacts = _artifacts(tmp_path)
    first = run(transcript, meeting_date="2026-09-18", artifact_directory=artifacts)
    second = run(transcript, meeting_date="2026-09-18", artifact_directory=artifacts)
    assert first == second
    assert first["experimental"]["experimental_not_validated"] is True
    assert first["experimental"]["provider_call_count"] == 0
    assert first["diagnostics"]["ai_provider_call_count"] == 0


@pytest.mark.parametrize("missing", ["model.json", "frozen-policy.json"])
def test_missing_private_artifact_fails_closed(tmp_path: Path, missing: str) -> None:
    artifacts = _artifacts(tmp_path)
    (artifacts / missing).unlink()
    with pytest.raises(RuntimeError, match="STOP_MISSING_PRIVATE_ARTIFACT"):
        run(_transcript(tmp_path), meeting_date="2026-09-18", artifact_directory=artifacts)


def test_mismatched_private_artifact_fails_closed(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    model_path = artifacts / "model.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model["bias"] = float(model["bias"]) + 0.001
    model_path.write_text(json.dumps(model), encoding="utf-8")
    with pytest.raises(RuntimeError, match="STOP_ARTIFACT_HASH_MISMATCH:model.json"):
        run(_transcript(tmp_path), meeting_date="2026-09-18", artifact_directory=artifacts)


def _declare_extra_artifact(artifacts: Path, name: str = "closeout.md") -> Path:
    extra = artifacts / name
    extra.write_text("frozen bundle audit", encoding="utf-8")
    manifest_path = artifacts / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hashes"][name] = hashlib.sha256(extra.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return extra


def test_frozen_bundle_validates_declared_extra_artifact(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    _declare_extra_artifact(artifacts)

    result = run(_transcript(tmp_path), meeting_date="2026-09-18", artifact_directory=artifacts)

    assert result["diagnostics"]["ai_provider_call_count"] == 0


def test_frozen_bundle_rejects_missing_declared_extra_artifact(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    extra = _declare_extra_artifact(artifacts)
    extra.unlink()

    with pytest.raises(RuntimeError, match="STOP_MISSING_RUNTIME_ARTIFACT"):
        run(_transcript(tmp_path), meeting_date="2026-09-18", artifact_directory=artifacts)


def test_frozen_bundle_rejects_tampered_declared_extra_artifact(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    extra = _declare_extra_artifact(artifacts)
    extra.write_text("tampered", encoding="utf-8")

    with pytest.raises(RuntimeError, match="STOP_ARTIFACT_HASH_MISMATCH:closeout.md"):
        run(_transcript(tmp_path), meeting_date="2026-09-18", artifact_directory=artifacts)


def test_normal_v1_route_remains_available_without_a_key() -> None:
    meeting = MeetingInput(
        meeting_id="v1-compatibility",
        meeting_title="Compatibility",
        meeting_date="2026-09-18",
        transcript_raw="Lan: Please send the report.",
    )
    result = process_meeting_by_version(
        meeting,
        pipeline_version="v1",
        ai_client=DisabledAiClient(),
    )
    assert result.diagnostics.ai_provider_call_count == 0


def test_inference_module_has_no_gold_or_validation_case_access() -> None:
    source = inspect.getsource(v227)
    assert "expected_output" not in source
    assert "data/validation" not in source
