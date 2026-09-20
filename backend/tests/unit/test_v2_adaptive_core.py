from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from backend.app.core import (
    V2_BASE_RUNTIME_MODEL_ID,
    V2_CHALLENGER_RUNTIME_MODEL_ID,
    V2_PIPELINE_VERSION,
    V2AdaptiveCore,
    V2AdaptiveUnavailableError,
    available_core_ids,
    get_core,
)
from backend.app.models import MeetingInput, PipelineResult
from backend.tests.experimental.test_v227_experimental import _artifacts
import scripts.experimental_distillation.v227_api as v227_api


def _meeting() -> MeetingInput:
    return MeetingInput(
        "adaptive-core-test",
        "Adaptive core test",
        "2026-09-20",
        "Lan: Please prepare the rollout checklist by Friday.\n",
    )


def _expected_manifest(artifacts: Path) -> str:
    return sha256((artifacts / "manifest.json").read_bytes()).hexdigest()


def test_adaptive_core_is_explicitly_registered_and_advertises_adaptive() -> None:
    assert "v2-adaptive" in available_core_ids()


@pytest.mark.parametrize(
    ("model_kind", "runtime_model_id"),
    (
        ("base", V2_BASE_RUNTIME_MODEL_ID),
        ("challenger", V2_CHALLENGER_RUNTIME_MODEL_ID),
    ),
)
def test_adaptive_core_reports_active_model_audit_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_kind: str,
    runtime_model_id: str,
) -> None:
    artifacts = _artifacts(tmp_path)
    monkeypatch.setattr(
        v227_api,
        "_load_active_bundle",
        lambda **_: (artifacts, {}, {}, {}, model_kind),
    )
    core = V2AdaptiveCore(
        artifact_directory=artifacts,
        expected_manifest_sha256=_expected_manifest(artifacts),
    )

    assert core.capabilities.pipeline_version == V2_PIPELINE_VERSION
    assert core.capabilities.runtime_model_id == runtime_model_id
    assert core.capabilities.supports_meeting_note is False


def test_missing_optional_artifacts_fail_selection_without_affecting_v1(
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)
    (artifacts / "model.json").unlink()
    with pytest.raises(V2AdaptiveUnavailableError, match="STOP_MISSING_PRIVATE_ARTIFACT"):
        V2AdaptiveCore(
            artifact_directory=artifacts,
            expected_manifest_sha256=_expected_manifest(artifacts),
        )

    # The optional package failing to select must not make the default core
    # unavailable or cause registry imports to load arbitrary code.
    assert get_core("v1-frozen").core_id == "v1-frozen"


def test_adapter_converts_existing_runner_payload_and_keeps_provider_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _artifacts(tmp_path)
    core = V2AdaptiveCore(
        artifact_directory=artifacts,
        feedback_directory=tmp_path / "private-feedback",
        expected_manifest_sha256=_expected_manifest(artifacts),
    )
    calls: list[tuple[Path, dict[str, object]]] = []

    def fake_run(source: Path, **options: object) -> dict[str, object]:
        calls.append((source, options))
        assert source.read_text(encoding="utf-8") == _meeting().transcript_raw
        return {
            "meeting_title": "Adaptive core test",
            "summary": "1 task",
            "tasks": [
                {
                    "task_name": "Prepare rollout checklist",
                    "assignee": "Lan",
                    "start_date": "2026-09-20",
                    "due_date": "2026-09-25",
                    "due_date_text": "Friday",
                    "evidence": "Please prepare the rollout checklist by Friday.",
                    "status": "Proposed",
                }
            ],
            "diagnostics": {
                "ai_provider_enabled": False,
                "ai_provider_call_count": 0,
            },
            "unresolved_window_ids": [],
        }

    monkeypatch.setattr(core._runner, "run", fake_run)
    result = core.process(_meeting())

    assert isinstance(result, PipelineResult)
    assert result.tasks[0].task_name == "Prepare rollout checklist"
    assert calls[0][1] == {
        "artifact_directory": artifacts.resolve(),
        "meeting_date": "2026-09-20",
        "meeting_id": "adaptive-core-test",
        "meeting_title": "Adaptive core test",
    }


def test_adapter_uses_existing_v227_inference_path_with_disabled_provider(
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)
    core = V2AdaptiveCore(
        artifact_directory=artifacts,
        expected_manifest_sha256=_expected_manifest(artifacts),
    )

    result = core.process(_meeting())

    assert isinstance(result, PipelineResult)
    assert result.diagnostics.ai_provider_enabled is False
    assert result.diagnostics.ai_provider_call_count == 0
