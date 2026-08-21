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
        "ACTION_CANDIDATE_BUILDER_MODE",
        "ACTION_CANDIDATE_BUILDER_VERSION",
        "CANDIDATE_ROUTER_MODE",
        "ACTION_CLEAR_THRESHOLD",
        "ACTION_AI_THRESHOLD",
        "CANDIDATE_THRESHOLD_VERSION",
        "TASK_CREATE_PROPOSAL_ENABLED",
        "AI_CREATE_PROPOSAL_ENABLED",
        "AI_CREATE_MAX_PROPOSALS_PER_MEETING",
        "TASK_SEMANTIC_LINKER_MODE",
        "TASK_LINK_SEMANTIC_WEIGHT",
        "TASK_LINK_LEXICAL_WEIGHT",
        "TASK_LINK_TOPIC_WEIGHT",
        "TASK_LINK_OWNER_WEIGHT",
        "TASK_LINK_RECENCY_WEIGHT",
        "TASK_LINK_STRONG_THRESHOLD",
        "TASK_LINK_MIN_MARGIN",
        "TASK_LINK_AI_THRESHOLD",
        "TASK_LINK_RECENCY_HORIZON_CLAUSES",
        "TASK_LINK_TOP_K",
        "TASK_LINK_SCORING_VERSION",
        "CONTEXT_RETRIEVAL_MODE",
        "CONTEXT_MAX_CLAUSES",
        "CONTEXT_MAX_CHARACTERS",
        "CONTEXT_MAX_TASKS",
        "CONTEXT_LOCAL_BEFORE",
        "CONTEXT_LOCAL_AFTER",
        "CONTEXT_MAX_TOPIC_CLAUSES",
        "CONTEXT_MAX_TOPICS",
        "CONTEXT_MAX_HISTORY_EVENTS_PER_TASK",
        "CONTEXT_TOPIC_BOUNDARY_THRESHOLD",
        "CONTEXT_TOPIC_SMOOTHING_WINDOW",
        "CONTEXT_RETRIEVAL_VERSION",
        "TEMPORAL_SEMANTICS_MODE",
        "TEMPORAL_PARSER_VERSION",
        "TEMPORAL_WORKING_DAY_POLICY",
        "TEMPORAL_MIN_CONFIDENCE",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.embedding_device == "cpu"
    assert settings.embedding_fallback_enabled is True
    assert settings.embedding_fallback_dimension == 384
    assert settings.action_classifier_mode == "off"
    assert settings.action_classifier_model_path is None
    assert settings.action_candidate_builder_mode == "off"
    assert settings.action_candidate_builder_version == "action-candidate-v2"
    assert settings.candidate_router_mode == "off"
    assert settings.action_clear_threshold == 0.82
    assert settings.action_ai_threshold == 0.45
    assert settings.task_create_proposal_enabled is False
    assert settings.ai_create_proposal_enabled is False
    assert settings.ai_create_max_proposals_per_meeting == 3
    assert settings.task_semantic_linker_mode == "off"
    assert settings.task_link_semantic_weight == 0.55
    assert settings.task_link_strong_threshold == 0.78
    assert settings.task_link_min_margin == 0.12
    assert settings.task_link_ai_threshold == 0.60
    assert settings.task_link_top_k == 5
    assert settings.context_retrieval_mode == "off"
    assert settings.context_max_clauses == 30
    assert settings.context_max_characters == 12_000
    assert settings.context_max_tasks == 5
    assert settings.temporal_semantics_mode == "off"
    assert settings.temporal_parser_version == "temporal-parser-v1"
    assert settings.temporal_working_day_policy == "weekdays-only-v1"
    assert settings.temporal_min_confidence == 1.0


def test_ml_settings_read_explicit_environment(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "semantic-test-v1")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cuda:0")
    monkeypatch.setenv("EMBEDDING_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("EMBEDDING_FALLBACK_DIMENSION", "128")
    monkeypatch.setenv("ACTION_CLASSIFIER_MODEL_PATH", "artifacts/models/action.joblib")
    monkeypatch.setenv("ACTION_CLASSIFIER_MODE", "shadow")
    monkeypatch.setenv("ACTION_CANDIDATE_BUILDER_MODE", "shadow")
    monkeypatch.setenv("ACTION_CANDIDATE_BUILDER_VERSION", "action-candidate-test-v2")
    monkeypatch.setenv("CANDIDATE_ROUTER_MODE", "shadow")
    monkeypatch.setenv("ACTION_CLEAR_THRESHOLD", "0.9")
    monkeypatch.setenv("ACTION_AI_THRESHOLD", "0.6")
    monkeypatch.setenv("CANDIDATE_THRESHOLD_VERSION", "candidate-test-v2")
    monkeypatch.setenv("TASK_SEMANTIC_LINKER_MODE", "shadow")
    monkeypatch.setenv("TASK_LINK_STRONG_THRESHOLD", "0.8")
    monkeypatch.setenv("TASK_LINK_MIN_MARGIN", "0.15")
    monkeypatch.setenv("TASK_LINK_AI_THRESHOLD", "0.5")
    monkeypatch.setenv("TEMPORAL_SEMANTICS_MODE", "shadow")

    settings = Settings.from_env()

    assert settings.embedding_model_name == "semantic-test-v1"
    assert settings.embedding_device == "cuda:0"
    assert settings.embedding_fallback_enabled is False
    assert settings.embedding_fallback_dimension == 128
    assert settings.action_classifier_mode == "shadow"
    assert settings.action_classifier_model_path == "artifacts/models/action.joblib"
    assert settings.action_candidate_builder_mode == "shadow"
    assert settings.action_candidate_builder_version == "action-candidate-test-v2"
    assert settings.candidate_router_mode == "shadow"
    assert settings.action_clear_threshold == 0.9
    assert settings.action_ai_threshold == 0.6
    assert settings.candidate_threshold_version == "candidate-test-v2"
    assert settings.task_semantic_linker_mode == "shadow"
    assert settings.task_link_strong_threshold == 0.8
    assert settings.task_link_min_margin == 0.15
    assert settings.task_link_ai_threshold == 0.5
    assert settings.temporal_semantics_mode == "shadow"


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


def test_task_semantic_linker_rejects_active_mode(monkeypatch) -> None:
    monkeypatch.setenv("TASK_SEMANTIC_LINKER_MODE", "assist")

    with pytest.raises(RuntimeError, match="must be off or shadow"):
        Settings.from_env()


def test_context_shadow_requires_semantic_linker_shadow(monkeypatch) -> None:
    monkeypatch.setenv("TASK_SEMANTIC_LINKER_MODE", "off")
    monkeypatch.setenv("CONTEXT_RETRIEVAL_MODE", "shadow")

    with pytest.raises(RuntimeError, match="requires TASK_SEMANTIC_LINKER_MODE=shadow"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("CONTEXT_MAX_CLAUSES", "31", "CONTEXT_MAX_CLAUSES"),
        ("CONTEXT_MAX_CHARACTERS", "12001", "CONTEXT_MAX_CHARACTERS"),
        ("CONTEXT_MAX_TASKS", "6", "CONTEXT_MAX_TASKS"),
        ("CONTEXT_TOPIC_SMOOTHING_WINDOW", "4", "SMOOTHING_WINDOW"),
    ],
)
def test_context_hard_caps_are_not_configurable_above_contract(
    monkeypatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=message):
        Settings.from_env()


def test_task_link_weights_must_sum_to_one(monkeypatch) -> None:
    monkeypatch.setenv("TASK_LINK_SEMANTIC_WEIGHT", "0.50")

    with pytest.raises(RuntimeError, match="sum to one"):
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


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TEMPORAL_SEMANTICS_MODE", "active"),
        ("TEMPORAL_WORKING_DAY_POLICY", "holiday-v1"),
        ("TEMPORAL_MIN_CONFIDENCE", "0.99"),
    ],
)
def test_temporal_settings_reject_non_deterministic_configuration(
    monkeypatch, name: str, value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="TEMPORAL_"):
        Settings.from_env()
