"""Pure-Python inference for the clause-level shadow action classifier."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.ml.action_features import (
    ACTION_FEATURE_NAMES,
    FeatureValue,
    action_feature_vector,
    build_action_features,
    build_action_semantic_text,
)
from backend.app.ml.contracts import (
    ActionLabel,
    ActionPrediction,
    ActionProbabilities,
    EmbeddingModel,
)
from backend.app.models import Clause, ClauseAnnotation


LABEL_ORDER = (
    ActionLabel.CLEAR_ACTION,
    ActionLabel.POSSIBLE_ACTION,
    ActionLabel.UPDATE_ONLY,
    ActionLabel.NON_ACTION,
)
_RULE_ACTION_FLAGS = {
    "FIRST_PERSON_COMMITMENT",
    "DIRECT_ASSIGNMENT",
    "CONFIRMATION",
}


class ActionClassifierManifest(BaseModel):
    """Version identity embedded in every trained artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_model_version: str = Field(min_length=1)
    embedding_backend: str = Field(min_length=1)
    training_dataset_version: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    features_version: str = Field(min_length=1)
    label_schema: str = Field(min_length=1)


class LinearParameters(BaseModel):
    """Portable standardized multinomial logistic-regression parameters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    classes: list[ActionLabel]
    feature_names: list[str]
    embedding_dimension: int = Field(gt=0)
    scaler_mean: list[float]
    scaler_scale: list[float]
    coefficients: list[list[float]]
    intercepts: list[float]

    @model_validator(mode="after")
    def validate_dimensions(self) -> "LinearParameters":
        width = self.embedding_dimension + len(self.feature_names)
        if self.feature_names != list(ACTION_FEATURE_NAMES):
            raise ValueError("artifact feature names do not match action-features-v1")
        if set(self.classes) != set(LABEL_ORDER) or len(self.classes) != len(LABEL_ORDER):
            raise ValueError("artifact must contain every action label exactly once")
        if len(self.scaler_mean) != width or len(self.scaler_scale) != width:
            raise ValueError("artifact scaler dimensions do not match input width")
        if len(self.coefficients) != len(self.classes):
            raise ValueError("artifact coefficient row count does not match classes")
        if any(len(row) != width for row in self.coefficients):
            raise ValueError("artifact coefficient width does not match input width")
        if len(self.intercepts) != len(self.classes):
            raise ValueError("artifact intercept count does not match classes")
        if any(scale <= 0.0 for scale in self.scaler_scale):
            raise ValueError("artifact scaler values must be positive")
        values = (
            self.scaler_mean
            + self.scaler_scale
            + self.intercepts
            + [value for row in self.coefficients for value in row]
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("artifact contains a non-finite parameter")
        return self


class ActionClassifierArtifact(BaseModel):
    """Validated, dependency-free serialized classifier artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(pattern=r"^action-classifier-artifact-v1$")
    manifest: ActionClassifierManifest
    linear_model: LinearParameters

    @classmethod
    def from_path(cls, path: str | Path) -> "ActionClassifierArtifact":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(payload)


@dataclass(frozen=True, slots=True)
class ActionClassifierInput:
    semantic_text: str
    features: dict[str, FeatureValue]


@dataclass(slots=True)
class ActionClassifierShadowSummary:
    classifier_version: str = "disabled"
    embedding_model_version: str = "disabled"
    clause_count: int = 0
    prediction_counts: dict[str, int] = field(default_factory=dict)
    would_create_count: int = 0
    would_review_count: int = 0
    would_update_count: int = 0
    rule_action_clause_count: int = 0
    rule_agreement_count: int = 0
    rule_disagreement_count: int = 0


class LinearActionClassifier:
    """Run a frozen standardized linear model without sklearn at runtime."""

    def __init__(
        self,
        artifact: ActionClassifierArtifact,
        embedding_model: EmbeddingModel,
    ) -> None:
        parameters = artifact.linear_model
        if embedding_model.metadata.dimension != parameters.embedding_dimension:
            raise ValueError(
                "embedding dimension does not match classifier artifact: "
                f"{embedding_model.metadata.dimension} != "
                f"{parameters.embedding_dimension}"
            )
        if embedding_model.metadata.model_name != artifact.manifest.embedding_model:
            raise ValueError("embedding model identity does not match artifact manifest")
        if embedding_model.metadata.model_version != artifact.manifest.embedding_model_version:
            raise ValueError("embedding model version does not match artifact manifest")
        if embedding_model.metadata.backend != artifact.manifest.embedding_backend:
            raise ValueError("embedding backend does not match artifact manifest")
        self.artifact = artifact
        self.embedding_model = embedding_model

    @property
    def classifier_version(self) -> str:
        return self.artifact.manifest.model_name

    @property
    def embedding_model_version(self) -> str:
        manifest = self.artifact.manifest
        return f"{manifest.embedding_model}:{manifest.embedding_model_version}"

    def predict(
        self,
        semantic_text: str,
        features: dict[str, FeatureValue],
    ) -> ActionPrediction:
        return self.predict_many(
            [ActionClassifierInput(semantic_text=semantic_text, features=features)]
        )[0]

    def predict_many(
        self,
        inputs: Sequence[ActionClassifierInput],
    ) -> list[ActionPrediction]:
        if not inputs:
            return []
        embeddings = self.embedding_model.embed(
            [item.semantic_text for item in inputs]
        )
        return [
            self._predict_vector(embedding + action_feature_vector(item.features))
            for item, embedding in zip(inputs, embeddings, strict=True)
        ]

    def _predict_vector(self, vector: list[float]) -> ActionPrediction:
        parameters = self.artifact.linear_model
        standardized = [
            (value - mean) / scale
            for value, mean, scale in zip(
                vector,
                parameters.scaler_mean,
                parameters.scaler_scale,
                strict=True,
            )
        ]
        logits = [
            intercept
            + sum(weight * value for weight, value in zip(row, standardized, strict=True))
            for row, intercept in zip(
                parameters.coefficients,
                parameters.intercepts,
                strict=True,
            )
        ]
        maximum = max(logits)
        exponentials = [math.exp(value - maximum) for value in logits]
        total = sum(exponentials)
        by_label = {
            label: value / total
            for label, value in zip(parameters.classes, exponentials, strict=True)
        }
        label = max(parameters.classes, key=by_label.__getitem__)
        return ActionPrediction(
            label=label,
            confidence=by_label[label],
            probabilities=ActionProbabilities(
                clear_action=by_label[ActionLabel.CLEAR_ACTION],
                possible_action=by_label[ActionLabel.POSSIBLE_ACTION],
                update_only=by_label[ActionLabel.UPDATE_ONLY],
                non_action=by_label[ActionLabel.NON_ACTION],
            ),
            classifier_version=self.classifier_version,
            embedding_model_version=self.embedding_model_version,
        )


def evaluate_shadow_predictions(
    classifier: LinearActionClassifier,
    clauses: Sequence[Clause],
    annotations: dict[str, ClauseAnnotation],
    *,
    speaker_names: Iterable[str] = (),
    note_supported_clause_ids: set[str] | None = None,
) -> ActionClassifierShadowSummary:
    """Compare shadow classifications to current rule cues without routing."""

    predictions = predict_clause_actions(
        classifier,
        clauses,
        annotations,
        speaker_names=speaker_names,
        note_supported_clause_ids=note_supported_clause_ids,
    )
    return summarize_shadow_predictions(predictions, clauses, annotations)


def predict_clause_actions(
    classifier: LinearActionClassifier,
    clauses: Sequence[Clause],
    annotations: dict[str, ClauseAnnotation],
    *,
    speaker_names: Iterable[str] = (),
    note_supported_clause_ids: set[str] | None = None,
) -> dict[str, ActionPrediction]:
    """Predict every clause once so shadow consumers can share the result."""

    note_supported_clause_ids = note_supported_clause_ids or set()
    inputs: list[ActionClassifierInput] = []
    for index, clause in enumerate(clauses):
        previous = clauses[index - 1] if index else None
        following = clauses[index + 1] if index + 1 < len(clauses) else None
        inputs.append(
            ActionClassifierInput(
                semantic_text=build_action_semantic_text(
                    clause,
                    previous_clauses=[previous.text_raw] if previous else [],
                    next_clauses=[following.text_raw] if following else [],
                ),
                features=build_action_features(
                    clause,
                    annotations[clause.clause_id],
                    previous_clause=previous,
                    speaker_names=speaker_names,
                    note_supported=clause.clause_id in note_supported_clause_ids,
                ),
            )
        )
    predictions = classifier.predict_many(inputs)
    return {
        clause.clause_id: prediction
        for clause, prediction in zip(clauses, predictions, strict=True)
    }


def summarize_shadow_predictions(
    predictions_by_clause: dict[str, ActionPrediction],
    clauses: Sequence[Clause],
    annotations: dict[str, ClauseAnnotation],
) -> ActionClassifierShadowSummary:
    """Summarize classifier-vs-rule behavior without changing either path."""

    predictions = [predictions_by_clause[clause.clause_id] for clause in clauses]
    counts = Counter(prediction.label.value for prediction in predictions)
    rule_actions = [
        bool(annotations[clause.clause_id].flags & _RULE_ACTION_FLAGS)
        and annotations[clause.clause_id].score > 0
        for clause in clauses
    ]
    classifier_actions = [
        prediction.label in {ActionLabel.CLEAR_ACTION, ActionLabel.POSSIBLE_ACTION}
        for prediction in predictions
    ]
    agreements = sum(
        rule_action == classifier_action
        for rule_action, classifier_action in zip(
            rule_actions, classifier_actions, strict=True
        )
    )
    classifier_version = predictions[0].classifier_version if predictions else "disabled"
    embedding_model_version = (
        predictions[0].embedding_model_version if predictions else "disabled"
    )
    return ActionClassifierShadowSummary(
        classifier_version=classifier_version,
        embedding_model_version=embedding_model_version,
        clause_count=len(clauses),
        prediction_counts=dict(sorted(counts.items())),
        would_create_count=counts[ActionLabel.CLEAR_ACTION.value],
        would_review_count=counts[ActionLabel.POSSIBLE_ACTION.value],
        would_update_count=counts[ActionLabel.UPDATE_ONLY.value],
        rule_action_clause_count=sum(rule_actions),
        rule_agreement_count=agreements,
        rule_disagreement_count=len(clauses) - agreements,
    )
