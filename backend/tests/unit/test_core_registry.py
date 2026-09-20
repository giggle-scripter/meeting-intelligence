from dataclasses import asdict

import pytest

from backend.app.core import (
    CORE_ENVIRONMENT_VARIABLE,
    DEFAULT_CORE_ID,
    UnknownCoreError,
    V2AdaptiveUnavailableError,
    V1FrozenCore,
    available_core_ids,
    get_core,
)
from backend.app.core import registry
from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting


def _meeting() -> MeetingInput:
    return MeetingInput(
        "core-contract",
        "Core contract",
        "2026-09-20",
        "Lan: Lan sẽ viết tài liệu triển khai.",
    )


def test_unset_environment_selects_frozen_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CORE_ENVIRONMENT_VARIABLE, raising=False)

    core = get_core()

    assert isinstance(core, V1FrozenCore)
    assert core.core_id == DEFAULT_CORE_ID == "v1-frozen"
    assert core.capabilities.adaptive is False
    assert core.capabilities.pipeline_version == "v1"
    assert core.capabilities.runtime_model_id == "v1-frozen"
    assert core.capabilities.supports_meeting_note is True


def test_explicit_environment_selects_registered_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CORE_ENVIRONMENT_VARIABLE, "v1-frozen")

    assert isinstance(get_core(), V1FrozenCore)


def test_unknown_core_fails_closed_without_dynamic_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CORE_ENVIRONMENT_VARIABLE, "backend.app.pipeline:process_meeting")

    with pytest.raises(UnknownCoreError, match="Unknown meeting core"):
        get_core()


def test_missing_optional_package_keeps_v1_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import_module = registry.importlib.import_module

    def import_without_optional_package(name: str, package: str | None = None):
        if name == "meeting_v2_adaptive":
            raise ModuleNotFoundError(
                "No module named 'meeting_v2_adaptive'", name=name
            )
        return real_import_module(name, package)

    monkeypatch.setattr(registry.importlib, "import_module", import_without_optional_package)

    assert available_core_ids() == ("v1-frozen",)
    assert get_core("v1-frozen").core_id == "v1-frozen"
    with pytest.raises(V2AdaptiveUnavailableError, match="meeting_v2_adaptive"):
        get_core("v2-adaptive")


def test_v1_adapter_delegates_and_keeps_pipeline_output_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = process_meeting(_meeting())
    calls: list[tuple[MeetingInput, dict]] = []

    def fake_process(meeting: MeetingInput, **options: object):
        calls.append((meeting, options))
        return expected

    monkeypatch.setattr("backend.app.pipeline.process_meeting", fake_process)
    result = V1FrozenCore().process(_meeting(), summary_topic="Core contract")

    assert calls[0][0].meeting_id == "core-contract"
    assert calls[0][1] == {"summary_topic": "Core contract"}
    assert result is expected
    assert set(asdict(result)) == {
        "meeting_title",
        "summary",
        "tasks",
        "diagnostics",
        "unresolved_window_ids",
    }
