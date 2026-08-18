"""Assist-mode integration tests for grounded create proposals."""

from __future__ import annotations

import httpx

from backend.app.ai.schemas import AiEventResponse
from backend.app.candidate import TaskCreateProposalResponse
from backend.app.ml.contracts import (
    ActionLabel,
    ActionPrediction,
    ActionProbabilities,
)
from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting


def _possible_predictions(_classifier, clauses, _annotations, **_kwargs):
    return {
        clause.clause_id: ActionPrediction(
            label=ActionLabel.POSSIBLE_ACTION,
            confidence=0.6,
            probabilities=ActionProbabilities(
                clear_action=0.1,
                possible_action=0.5,
                update_only=0.1,
                non_action=0.3,
            ),
            classifier_version="test-action-v1",
            embedding_model_version="test-embedding-v1",
        )
        for clause in clauses
    }


class GroundedCreateClient:
    enabled = True

    def extract_events(self, payload: dict) -> AiEventResponse:
        return AiEventResponse()

    def propose_task(self, payload: dict) -> TaskCreateProposalResponse:
        primary = payload["primary_clauses"][0]
        return TaskCreateProposalResponse(
            decision="PROPOSE",
            source_clause_ids=[primary["clause_id"]],
            action_span="Checklist UAT",
            owner_span="Minh",
            deadline_mention_id=None,
            commitment_type="ASSIGNMENT",
            confidence=0.84,
        )


class FailingCreateClient(GroundedCreateClient):
    def propose_task(self, payload: dict) -> TaskCreateProposalResponse:
        raise httpx.ConnectError("provider unavailable")


def _run(monkeypatch, client):
    monkeypatch.setattr(
        "backend.app.ml.model_registry.get_action_classifier",
        lambda _path: object(),
    )
    monkeypatch.setattr(
        "backend.app.ml.action_classifier.predict_clause_actions",
        _possible_predictions,
    )
    return process_meeting(
        MeetingInput(
            "M-CREATE-1",
            "UAT sync",
            "2026-08-17",
            "Nam: Checklist UAT, Minh.",
        ),
        client,
        action_classifier_mode="assist",
        candidate_router_mode="assist",
        task_create_proposal_enabled=True,
        ai_create_proposal_enabled=True,
    )


def test_grounded_create_proposal_is_promoted(monkeypatch) -> None:
    result = _run(monkeypatch, GroundedCreateClient())

    assert [(task.task_name, task.assignee) for task in result.tasks] == [
        ("Checklist UAT", "Minh")
    ]
    assert result.diagnostics.task_create_proposal_call_count == 1
    assert result.diagnostics.task_create_proposal_accepted_count == 1
    assert result.diagnostics.ai_event_count == 1


def test_create_provider_failure_stays_unresolved_without_heuristic_promotion(
    monkeypatch,
) -> None:
    result = _run(monkeypatch, FailingCreateClient())

    assert result.tasks == []
    assert result.diagnostics.task_create_proposal_call_count == 1
    assert result.diagnostics.task_create_proposal_unresolved_count == 1
