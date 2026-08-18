"""Configuration tests for optional local ML infrastructure."""

from __future__ import annotations

import pytest

from backend.app.config import Settings


def test_ml_settings_have_safe_inactive_defaults(monkeypatch) -> None:
    for name in (
        "EMBEDDING_MODEL_NAME",
        "EMBEDDING_DEVICE",
        "EMBEDDING_FALLBACK_ENABLED",
        "EMBEDDING_FALLBACK_DIMENSION",
        "ACTION_CLASSIFIER_MODE",
        "ACTION_CLASSIFIER_MODEL_PATH",
        "CANDIDATE_ROUTER_MODE",
        "ACTION_CLEAR_THRESHOLD",
        "ACTION_AI_THRESHOLD",
        "CANDIDATE_THRESHOLD_VERSION",
        "TASK_CREATE_PROPOSAL_ENABLED",
        "AI_CREATE_PROPOSAL_ENABLED",
        "AI_CREATE_MAX_PROPOSALS_PER_MEETING",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.embedding_device == "cpu"
    assert settings.embedding_fallback_enabled is True
    assert settings.embedding_fallback_dimension == 384
    assert settings.action_classifier_mode == "off"
    assert settings.action_classifier_model_path is None
    assert settings.candidate_router_mode == "off"
    assert settings.action_clear_threshold == 0.82
    assert settings.action_ai_threshold == 0.45
    assert settings.task_create_proposal_enabled is False
    assert settings.ai_create_proposal_enabled is False
    assert settings.ai_create_max_proposals_per_meeting == 3


def test_ml_settings_read_explicit_environment(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "semantic-test-v1")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cuda:0")
    monkeypatch.setenv("EMBEDDING_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("EMBEDDING_FALLBACK_DIMENSION", "128")
    monkeypatch.setenv("ACTION_CLASSIFIER_MODEL_PATH", "artifacts/models/action.joblib")
    monkeypatch.setenv("ACTION_CLASSIFIER_MODE", "shadow")
    monkeypatch.setenv("CANDIDATE_ROUTER_MODE", "shadow")
    monkeypatch.setenv("ACTION_CLEAR_THRESHOLD", "0.9")
    monkeypatch.setenv("ACTION_AI_THRESHOLD", "0.6")
    monkeypatch.setenv("CANDIDATE_THRESHOLD_VERSION", "candidate-test-v2")

    settings = Settings.from_env()

    assert settings.embedding_model_name == "semantic-test-v1"
    assert settings.embedding_device == "cuda:0"
    assert settings.embedding_fallback_enabled is False
    assert settings.embedding_fallback_dimension == 128
    assert settings.action_classifier_mode == "shadow"
    assert settings.action_classifier_model_path == "artifacts/models/action.joblib"
    assert settings.candidate_router_mode == "shadow"
    assert settings.action_clear_threshold == 0.9
    assert settings.action_ai_threshold == 0.6
    assert settings.candidate_threshold_version == "candidate-test-v2"


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_ml_settings_reject_invalid_fallback_dimension(monkeypatch, value: str) -> None:
    monkeypatch.setenv("EMBEDDING_FALLBACK_DIMENSION", value)

    with pytest.raises(RuntimeError, match="EMBEDDING_FALLBACK_DIMENSION"):
        Settings.from_env()


def test_ml_settings_reject_ambiguous_boolean(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_FALLBACK_ENABLED", "sometimes")

    with pytest.raises(RuntimeError, match="must be true or false"):
        Settings.from_env()


def test_ml_settings_accept_matching_assist_modes(monkeypatch) -> None:
    monkeypatch.setenv("ACTION_CLASSIFIER_MODE", "assist")
    monkeypatch.setenv("CANDIDATE_ROUTER_MODE", "assist")
    monkeypatch.setenv("TASK_CREATE_PROPOSAL_ENABLED", "true")
    monkeypatch.setenv("AI_CREATE_PROPOSAL_ENABLED", "true")

    settings = Settings.from_env()

    assert settings.task_create_proposal_enabled is True
    assert settings.ai_create_proposal_enabled is True


def test_candidate_router_shadow_requires_classifier_shadow(monkeypatch) -> None:
    monkeypatch.setenv("ACTION_CLASSIFIER_MODE", "off")
    monkeypatch.setenv("CANDIDATE_ROUTER_MODE", "shadow")

    with pytest.raises(RuntimeError, match="requires ACTION_CLASSIFIER_MODE=shadow"):
        Settings.from_env()


def test_create_proposal_requires_assist_router(monkeypatch) -> None:
    monkeypatch.setenv("ACTION_CLASSIFIER_MODE", "shadow")
    monkeypatch.setenv("CANDIDATE_ROUTER_MODE", "shadow")
    monkeypatch.setenv("TASK_CREATE_PROPOSAL_ENABLED", "true")

    with pytest.raises(RuntimeError, match="CANDIDATE_ROUTER_MODE=assist"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("clear", "ai"),
    [("1.1", "0.4"), ("0.4", "0.5"), ("invalid", "0.4")],
)
def test_candidate_router_rejects_invalid_thresholds(
    monkeypatch,
    clear: str,
    ai: str,
) -> None:
    monkeypatch.setenv("ACTION_CLEAR_THRESHOLD", clear)
    monkeypatch.setenv("ACTION_AI_THRESHOLD", ai)

    with pytest.raises(RuntimeError, match="ACTION_"):
        Settings.from_env()
