"""Evidence-merging and shadow candidate-router tests."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app.candidate import (
    CandidateEvidence,
    CandidateRoute,
    CandidateRouter,
    CandidateRouterConfig,
    build_candidate_evidence,
)
from backend.app.ml.action_features import ACTION_FEATURE_NAMES
from backend.app.ml.contracts import (
    ActionLabel,
    ActionPrediction,
    ActionProbabilities,
)
from backend.app.models import Clause, ClauseAnnotation, MeetingInput
from backend.app.pipeline import process_meeting


def _candidate(**overrides: object) -> CandidateEvidence:
    payload: dict[str, object] = {
        "candidate_id": "CAND-1",
        "focus_clause_id": "CLAUSE-1",
        "clause_ids": ["CLAUSE-1"],
        "rule_score": 0.0,
        "classifier_score": 0.0,
        "classifier_clear_score": 0.0,
        "classifier_possible_score": 0.0,
        "classifier_update_score": 0.0,
        "classifier_non_action_score": 1.0,
        "classifier_label": "NON_ACTION",
        "note_score": None,
        "has_commitment_cue": False,
        "has_assignment_cue": False,
        "has_date": False,
        "has_question_cue": False,
        "has_hypothetical_cue": False,
        "has_past_completed_cue": False,
        "has_rejection_cue": False,
        "has_action_verb": False,
        "has_rule_create_cue": False,
        "has_rule_mutation_cue": False,
        "has_unique_mutation_target": False,
        "has_strong_negative_guard": False,
        "has_note_action_signal": False,
        "has_note_state_signal": False,
        "has_ambiguous_note_signal": False,
        "topic_id": None,
    }
    payload.update(overrides)
    return CandidateEvidence.model_validate(payload)


def _action_scores(score: float) -> dict[str, object]:
    clear = score * 0.75
    possible = score - clear
    non_action = 1.0 - score
    return {
        "classifier_score": score,
        "classifier_clear_score": clear,
        "classifier_possible_score": possible,
        "classifier_update_score": 0.0,
        "classifier_non_action_score": non_action,
        "classifier_label": "CLEAR_ACTION" if score >= 0.5 else "NON_ACTION",
    }


def _write_hashing_artifact(tmp_path: Path) -> Path:
    classes = ["CLEAR_ACTION", "NON_ACTION", "POSSIBLE_ACTION", "UPDATE_ONLY"]
    dimension = 4
    width = dimension + len(ACTION_FEATURE_NAMES)
    payload = {
        "schema_version": "action-classifier-artifact-v1",
        "manifest": {
            "model_name": "router-action-test-v1",
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
    path = tmp_path / "router-action.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_evidence_builder_merges_rule_classifier_note_and_topic() -> None:
    clause = Clause(
        "CLAUSE-1", "SENT-1", "speaker-1", "Lan", 0, 1,
        "Em sẽ gửi báo cáo thứ Sáu.", "em sẽ gửi báo cáo thứ sáu",
    )
    annotation = ClauseAnnotation(
        "CLAUSE-1",
        {"FIRST_PERSON_COMMITMENT", "ACTION_VERB", "DATE_MENTION"},
        0.91,
        7,
    )
    prediction = ActionPrediction(
        label=ActionLabel.CLEAR_ACTION,
        confidence=0.7,
        probabilities=ActionProbabilities(
            clear_action=0.7,
            possible_action=0.2,
            update_only=0.05,
            non_action=0.05,
        ),
        classifier_version="test-v1",
        embedding_model_version="embedding-v1",
    )
    cue = SimpleNamespace(
        grounding_score=0.88,
        kind="ACTION_HINT",
        status="GROUNDED",
    )
    context = SimpleNamespace(
        clause_relevance={"CLAUSE-1": SimpleNamespace(topic_ids=("TOPIC-2",))}
    )

    evidence = build_candidate_evidence(
        [clause],
        {"CLAUSE-1": annotation},
        predictions_by_clause={"CLAUSE-1": prediction},
        note_cues_by_clause={"CLAUSE-1": (cue,)},  # type: ignore[arg-type]
        meeting_context=context,  # type: ignore[arg-type]
    )[0]

    assert evidence.classifier_score == pytest.approx(0.9)
    assert evidence.rule_score == 7.0
    assert evidence.note_score == 0.88
    assert evidence.has_rule_create_cue is True
    assert evidence.has_note_action_signal is True
    assert evidence.topic_id == "TOPIC-2"


def test_clear_classifier_action_routes_local_create_with_corroboration() -> None:
    candidate = _candidate(
        **_action_scores(0.9),
        has_rule_create_cue=True,
        has_note_action_signal=True,
        note_score=0.8,
    )

    decision = CandidateRouter().route_one(candidate)

    assert decision.route == CandidateRoute.LOCAL_CREATE
    assert decision.confidence == pytest.approx(0.9)
    assert decision.reasons == [
        "RULE_CREATE_CORROBORATED",
        "CLASSIFIER_CLEAR_ACTION",
        "NOTE_ACTION_CORROBORATED",
    ]


def test_strong_negative_guard_precedes_high_action_score() -> None:
    candidate = _candidate(
        **_action_scores(0.95),
        has_question_cue=True,
        has_strong_negative_guard=True,
    )

    decision = CandidateRouter().route_one(candidate)

    assert decision.route == CandidateRoute.CONTEXT_ONLY
    assert "QUESTION_GUARD" in decision.reasons


def test_uncertain_action_routes_ai_create_check_without_executing_it() -> None:
    decision = CandidateRouter().route_one(_candidate(**_action_scores(0.6)))

    assert decision.route == CandidateRoute.AI_CREATE_CHECK
    assert decision.confidence == pytest.approx(0.6)


@pytest.mark.parametrize(
    ("unique_target", "expected_route"),
    [
        (True, CandidateRoute.LOCAL_MUTATION),
        (False, CandidateRoute.AI_MUTATION_CHECK),
    ],
)
def test_rule_mutation_route_depends_on_unique_target(
    unique_target: bool,
    expected_route: CandidateRoute,
) -> None:
    candidate = _candidate(
        has_rule_mutation_cue=True,
        has_unique_mutation_target=unique_target,
        has_rejection_cue=True,
    )

    assert CandidateRouter().route_one(candidate).route == expected_route


def test_router_thresholds_are_config_not_hidden_cutoffs() -> None:
    candidate = _candidate(**_action_scores(0.6))

    default_route = CandidateRouter().route_one(candidate).route
    stricter_route = CandidateRouter(
        CandidateRouterConfig(
            action_clear_threshold=0.9,
            action_ai_threshold=0.7,
            threshold_version="strict-test-v1",
        )
    ).route_one(candidate).route

    assert default_route == CandidateRoute.AI_CREATE_CHECK
    assert stricter_route == CandidateRoute.DROP
    with pytest.raises(ValidationError, match="less than or equal"):
        CandidateRouterConfig(
            action_clear_threshold=0.4,
            action_ai_threshold=0.5,
        )


def test_shadow_router_does_not_change_rule_output(tmp_path: Path) -> None:
    artifact_path = _write_hashing_artifact(tmp_path)
    meeting = MeetingInput(
        "router-shadow",
        "Router shadow",
        "2026-08-17",
        "[09:00:00] Lan: Em sẽ gửi báo cáo trước thứ Sáu.",
    )

    baseline = process_meeting(meeting)
    shadow = process_meeting(
        meeting,
        action_classifier_mode="shadow",
        action_classifier_model_path=str(artifact_path),
        candidate_router_mode="shadow",
    )

    assert asdict(shadow)["tasks"] == asdict(baseline)["tasks"]
    assert shadow.diagnostics.candidate_router_version == (
        "candidate-evidence-router-v1"
    )
    assert shadow.diagnostics.candidate_evidence_count == (
        shadow.diagnostics.clause_count
    )
    assert shadow.diagnostics.candidate_decision_count == (
        shadow.diagnostics.clause_count
    )
    assert shadow.diagnostics.candidate_router_error_count == 0
    assert shadow.diagnostics.candidate_route_counts["LOCAL_CREATE"] > 0


def test_shadow_router_requires_shadow_classifier() -> None:
    meeting = MeetingInput(
        "router-config",
        "Router config",
        "2026-08-17",
        "[09:00:00] Lan: Em sẽ gửi báo cáo.",
    )

    with pytest.raises(ValueError, match="requires action_classifier_mode=shadow"):
        process_meeting(meeting, candidate_router_mode="shadow")
