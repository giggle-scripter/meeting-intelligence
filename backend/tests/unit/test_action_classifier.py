"""Portable classifier, registry, and shadow-boundary tests."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.ml.action_classifier import (
    ActionClassifierArtifact,
    LinearActionClassifier,
)
from backend.app.ml.action_features import ACTION_FEATURE_NAMES
from backend.app.ml.contracts import ActionProbabilities
from backend.app.ml.embeddings import HashingEmbeddingModel
from backend.app.ml.model_registry import ModelRegistry
from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting


def _artifact_payload(dimension: int = 4) -> dict:
    classes = ["CLEAR_ACTION", "NON_ACTION", "POSSIBLE_ACTION", "UPDATE_ONLY"]
    width = dimension + len(ACTION_FEATURE_NAMES)
    return {
        "schema_version": "action-classifier-artifact-v1",
        "manifest": {
            "model_name": "action-test-v1",
            "embedding_model": "hashing-fallback-v1",
            "embedding_model_version": "v1",
            "embedding_backend": "hashing",
            "training_dataset_version": "test-dataset-v1",
            "created_at": "2026-08-17T00:00:00Z",
            "features_version": "action-features-v1",
            "label_schema": "action-labels-v1",
        },
        "linear_model": {
            "classes": classes,
            "feature_names": list(ACTION_FEATURE_NAMES),
            "embedding_dimension": dimension,
            "scaler_mean": [0.0] * width,
            "scaler_scale": [1.0] * width,
            "coefficients": [[0.0] * width for _ in classes],
            "intercepts": [8.0, 0.0, 0.0, 0.0],
        },
    }


def _write_artifact(tmp_path: Path) -> Path:
    path = tmp_path / "action.json"
    path.write_text(json.dumps(_artifact_payload()), encoding="utf-8")
    return path


def test_probability_contract_requires_a_normalized_distribution() -> None:
    with pytest.raises(ValidationError, match="sum to one"):
        ActionProbabilities(
            clear_action=0.5,
            possible_action=0.4,
            update_only=0.2,
            non_action=0.1,
        )


def test_portable_classifier_returns_all_probabilities() -> None:
    artifact = ActionClassifierArtifact.model_validate(_artifact_payload())
    classifier = LinearActionClassifier(artifact, HashingEmbeddingModel(4))
    features = {name: False for name in ACTION_FEATURE_NAMES}
    features["rule_score"] = 0

    prediction = classifier.predict("[FOCUS]\nLan: Em sẽ gửi báo cáo.", features)

    assert prediction.label.value == "CLEAR_ACTION"
    assert prediction.confidence > 0.99
    assert prediction.classifier_version == "action-test-v1"
    assert prediction.embedding_model_version == "hashing-fallback-v1:v1"


def test_registry_caches_action_classifier_by_resolved_path(tmp_path: Path) -> None:
    path = _write_artifact(tmp_path)
    registry = ModelRegistry()

    first = registry.get_action_classifier(path)
    second = registry.get_action_classifier(path)

    assert first is second


def test_shadow_classifier_never_changes_rule_output(tmp_path: Path) -> None:
    path = _write_artifact(tmp_path)
    meeting = MeetingInput(
        meeting_id="shadow-test",
        meeting_title="Shadow test",
        meeting_date="2026-08-17",
        transcript_raw=(
            "[09:00:00] Lan: Em sẽ gửi báo cáo trước thứ Sáu.\n"
            "[09:01:00] Minh: Cảm ơn Lan."
        ),
    )

    baseline = process_meeting(meeting, action_classifier_mode="off")
    shadow = process_meeting(
        meeting,
        action_classifier_mode="shadow",
        action_classifier_model_path=str(path),
    )

    assert asdict(shadow)["tasks"] == asdict(baseline)["tasks"]
    assert shadow.diagnostics.action_classifier_mode == "shadow"
    assert shadow.diagnostics.action_classifier_clause_count > 0
    assert shadow.diagnostics.action_classifier_would_create_count > 0
    assert shadow.diagnostics.action_classifier_error_count == 0


def test_shadow_classifier_fails_open_when_artifact_is_missing(tmp_path: Path) -> None:
    meeting = MeetingInput(
        "shadow-missing",
        "Shadow missing",
        "2026-08-17",
        "[09:00:00] Lan: Em sẽ gửi báo cáo.",
    )

    result = process_meeting(
        meeting,
        action_classifier_mode="shadow",
        action_classifier_model_path=str(tmp_path / "missing.json"),
    )

    assert result.tasks
    assert result.diagnostics.action_classifier_version == "unavailable"
    assert result.diagnostics.action_classifier_error_count == 1
