"""Adapter for the opt-in V2.27/V2.28 adaptive inference path.

The experimental runner owns candidate construction and scoring.  This module
only adapts the runner to the stable core contract and delegates artifact and
tenant-pointer validation to the checks already used by ``v227_api``.
"""

from __future__ import annotations

from dataclasses import fields
import os
from pathlib import Path
import tempfile
from typing import Any

from backend.app.core.contracts import (
    CoreCapabilities,
    PipelineOutput,
    V2AdaptiveUnavailableError,
)
from backend.app.models import FinalTask, MeetingInput, PipelineDiagnostics, PipelineResult


V2_ADAPTIVE_CORE_ID = "v2-adaptive"
V2_PIPELINE_VERSION = "v227_experimental"
V2_BASE_RUNTIME_MODEL_ID = "v228-frozen-full-fit"
V2_CHALLENGER_RUNTIME_MODEL_ID = "v227-tenant-challenger"
V227_ARTIFACT_DIRECTORY_ENV = "V227_ARTIFACT_DIRECTORY"
V227_FEEDBACK_DIRECTORY_ENV = "V227_FEEDBACK_DIRECTORY"


class V2AdaptiveCore:
    """Expose the existing V2.27 adaptive runner as a selectable core.

    Construction performs the same startup package selection as the V2.27
    API.  The selected bundle is then re-verified before every inference, so a
    changed manifest, model, policy, or active tenant pointer fails closed.
    """

    core_id = V2_ADAPTIVE_CORE_ID

    def __init__(
        self,
        *,
        artifact_directory: Path | str | None = None,
        feedback_directory: Path | str | None = None,
        expected_manifest_sha256: str | None = None,
    ) -> None:
        try:
            import scripts.experimental_distillation.run_v227_experimental as runner
            import scripts.experimental_distillation.v227_api as v227_api
        except (ImportError, ModuleNotFoundError) as exc:
            raise V2AdaptiveUnavailableError(
                "V2 adaptive core unavailable: optional V2.27 inference code could not be imported"
            ) from exc

        self._runner = runner
        self._v227_api = v227_api
        self._artifact_directory = Path(
            artifact_directory
            or os.getenv(V227_ARTIFACT_DIRECTORY_ENV)
            or runner.ARTIFACT_DIR
        )
        self._feedback_directory = Path(
            feedback_directory
            or os.getenv(V227_FEEDBACK_DIRECTORY_ENV)
            or v227_api.DEFAULT_FEEDBACK_DIRECTORY
        )
        self._expected_manifest_sha256 = (
            expected_manifest_sha256 or v227_api.FROZEN_MANIFEST_SHA256
        )
        self._feedback_tenant = v227_api._feedback_tenant()

        try:
            (
                self._active_artifacts,
                _model,
                _policy,
                self._active_base_hashes,
                self._active_model_kind,
            ) = v227_api._load_active_bundle(
                artifact_directory=self._artifact_directory,
                feedback_directory=self._feedback_directory,
                tenant_id=self._feedback_tenant,
                expected_manifest_sha256=self._expected_manifest_sha256,
            )
            self._active_manifest_hash = runner._sha256(
                self._active_artifacts / "manifest.json"
            )
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            raise V2AdaptiveUnavailableError(
                f"V2 adaptive core unavailable: {exc}"
            ) from exc

        runtime_model_id = (
            V2_CHALLENGER_RUNTIME_MODEL_ID
            if self._active_model_kind == "challenger"
            else V2_BASE_RUNTIME_MODEL_ID
        )
        self.capabilities = CoreCapabilities(
            adaptive=True,
            pipeline_version=V2_PIPELINE_VERSION,
            runtime_model_id=runtime_model_id,
            supports_meeting_note=False,
        )

    def process(self, meeting: MeetingInput, **options: Any) -> PipelineOutput:
        """Run V2 inference for one existing ``MeetingInput``."""

        if not isinstance(meeting, MeetingInput):
            raise TypeError("meeting must be a MeetingInput")
        if meeting.meeting_note is not None:
            raise ValueError("V2 adaptive core does not support Meeting Note")

        self._v227_api._verify_active_bundle(
            self._active_artifacts,
            active_model_kind=self._active_model_kind,
            active_manifest_hash=self._active_manifest_hash,
            feedback_tenant=self._feedback_tenant,
            active_base_hashes=self._active_base_hashes,
        )

        suffix = Path(meeting.file_name or "meeting.txt").suffix.lower()
        if suffix not in {".txt", ".vtt", ".srt"}:
            suffix = ".txt"
        allowed_options = {
            key: options[key]
            for key in ("meeting_date", "meeting_id", "meeting_title")
            if key in options
        }
        allowed_options.setdefault("meeting_date", meeting.meeting_date)
        allowed_options.setdefault("meeting_id", meeting.meeting_id)
        allowed_options.setdefault("meeting_title", meeting.meeting_title)

        with tempfile.TemporaryDirectory(prefix="v2-adaptive-") as temporary:
            source = Path(temporary) / f"meeting{suffix}"
            source.write_text(meeting.transcript_raw, encoding="utf-8")
            payload = self._runner.run(
                source,
                artifact_directory=self._active_artifacts,
                **allowed_options,
            )
        return _pipeline_result_from_payload(payload, fallback_title=meeting.meeting_title)


def _pipeline_result_from_payload(
    payload: Any,
    *,
    fallback_title: str,
) -> PipelineResult:
    """Convert the runner's JSON-compatible payload to the domain result."""

    if isinstance(payload, PipelineResult):
        if payload.diagnostics.ai_provider_enabled or payload.diagnostics.ai_provider_call_count:
            raise RuntimeError("STOP_V2_ADAPTIVE_PROVIDER_CALL")
        return payload
    if not isinstance(payload, dict):
        raise RuntimeError("STOP_INVALID_V2_ADAPTIVE_RESULT")

    raw_diagnostics = payload.get("diagnostics")
    if not isinstance(raw_diagnostics, dict):
        raise RuntimeError("STOP_INVALID_V2_ADAPTIVE_DIAGNOSTICS")
    if raw_diagnostics.get("ai_provider_enabled") is True:
        raise RuntimeError("STOP_V2_ADAPTIVE_PROVIDER_ENABLED")
    if int(raw_diagnostics.get("ai_provider_call_count", 0) or 0) != 0:
        raise RuntimeError("STOP_V2_ADAPTIVE_PROVIDER_CALL")

    task_values = payload.get("tasks", [])
    if not isinstance(task_values, list):
        raise RuntimeError("STOP_INVALID_V2_ADAPTIVE_TASKS")
    try:
        tasks = [FinalTask(**task) for task in task_values]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("STOP_INVALID_V2_ADAPTIVE_TASKS") from exc

    diagnostic_names = {field.name for field in fields(PipelineDiagnostics)}
    diagnostics = PipelineDiagnostics(
        **{name: value for name, value in raw_diagnostics.items() if name in diagnostic_names}
    )
    unresolved = payload.get("unresolved_window_ids", [])
    if not isinstance(unresolved, list) or not all(isinstance(item, str) for item in unresolved):
        raise RuntimeError("STOP_INVALID_V2_ADAPTIVE_UNRESOLVED_WINDOWS")
    summary = payload.get("summary")
    if not isinstance(summary, str):
        raise RuntimeError("STOP_INVALID_V2_ADAPTIVE_SUMMARY")
    title = payload.get("meeting_title", fallback_title)
    if not isinstance(title, str):
        raise RuntimeError("STOP_INVALID_V2_ADAPTIVE_TITLE")
    return PipelineResult(title, summary, tasks, diagnostics, unresolved)


__all__ = [
    "V2_ADAPTIVE_CORE_ID",
    "V2_BASE_RUNTIME_MODEL_ID",
    "V2_CHALLENGER_RUNTIME_MODEL_ID",
    "V2_PIPELINE_VERSION",
    "V2AdaptiveCore",
    "V2AdaptiveUnavailableError",
]
