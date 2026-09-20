"""Q7 preflight must be evidence-only and provider-free."""

from backend.app.ai.schemas import AiEventResponse
from backend.app.ml.contracts import ActionLabel, ActionPrediction, ActionProbabilities
from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting


class CountingAiClient:
    enabled = True

    def __init__(self) -> None:
        self.call_count = 0

    def extract_events(self, _payload: dict) -> AiEventResponse:
        self.call_count += 1
        return AiEventResponse()


def _possible_predictions(_classifier, clauses, _annotations, **_kwargs):
    return {
        clause.clause_id: ActionPrediction(
            label=ActionLabel.POSSIBLE_ACTION,
            confidence=0.6,
            probabilities=ActionProbabilities(
                clear_action=0.1, possible_action=0.5,
                update_only=0.1, non_action=0.3,
            ),
            classifier_version="test-action-v1",
            embedding_model_version="test-embedding-v1",
        )
        for clause in clauses
    }


def test_q7_shadow_audits_grounded_candidate_without_provider_call(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.ml.model_registry.get_action_classifier", lambda _path: object())
    monkeypatch.setattr("backend.app.ml.action_classifier.predict_clause_actions", _possible_predictions)
    client = CountingAiClient()

    result = process_meeting(
        MeetingInput("q7-shadow", "Q7", "2026-08-21", "Lan: Em sẽ gửi báo cáo."),
        client,
        action_classifier_mode="shadow",
        candidate_router_mode="shadow",
        action_candidate_builder_mode="shadow",
        commitment_router_mode="shadow",
        ai_quality_uplift_mode="shadow",
    )

    assert result.diagnostics.ai_quality_create_candidate_count == 1
    assert result.diagnostics.ai_quality_create_eligible_count == 1
    assert result.diagnostics.ai_quality_create_selected_count == 1
    assert client.call_count == 0
