from __future__ import annotations

import json
import inspect
from pathlib import Path
import shutil

import pytest

from backend.app.ai.client import DisabledAiClient
from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting_by_version
import scripts.experimental_distillation.run_v227_experimental as v227
from scripts.experimental_distillation.run_v227_experimental import ARTIFACT_DIR, run


def _artifacts(tmp_path: Path) -> Path:
    target = tmp_path / "artifacts"
    target.mkdir()
    for name in ("model.json", "frozen-policy.json", "manifest.json"):
        shutil.copy2(ARTIFACT_DIR / name, target / name)
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
