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
        "ACTION_CLASSIFIER_MODEL_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.embedding_device == "cpu"
    assert settings.embedding_fallback_enabled is True
    assert settings.embedding_fallback_dimension == 384
    assert settings.action_classifier_model_path is None


def test_ml_settings_read_explicit_environment(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "semantic-test-v1")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cuda:0")
    monkeypatch.setenv("EMBEDDING_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("EMBEDDING_FALLBACK_DIMENSION", "128")
    monkeypatch.setenv("ACTION_CLASSIFIER_MODEL_PATH", "artifacts/models/action.joblib")

    settings = Settings.from_env()

    assert settings.embedding_model_name == "semantic-test-v1"
    assert settings.embedding_device == "cuda:0"
    assert settings.embedding_fallback_enabled is False
    assert settings.embedding_fallback_dimension == 128
    assert settings.action_classifier_model_path == "artifacts/models/action.joblib"


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_ml_settings_reject_invalid_fallback_dimension(monkeypatch, value: str) -> None:
    monkeypatch.setenv("EMBEDDING_FALLBACK_DIMENSION", value)

    with pytest.raises(RuntimeError, match="EMBEDDING_FALLBACK_DIMENSION"):
        Settings.from_env()


def test_ml_settings_reject_ambiguous_boolean(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_FALLBACK_ENABLED", "sometimes")

    with pytest.raises(RuntimeError, match="must be true or false"):
        Settings.from_env()
