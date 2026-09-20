from backend.app.ai.quality_uplift import preflight_ai_create_checks
from backend.app.candidate import (
    ActionCandidate,
    CandidateDecision,
    CandidateEvidence,
    CandidateRoute,
    CandidateState,
    CommitmentDecision,
    CommitmentRoute,
    GroundedSpan,
)


def _evidence(*, negative: bool = False) -> CandidateEvidence:
    return CandidateEvidence(
        candidate_id="CAND-C-1", focus_clause_id="C-1", clause_ids=["C-1"],
        classifier_score=0.6, classifier_clear_score=0.1,
        classifier_possible_score=0.5, classifier_update_score=0.0,
        classifier_non_action_score=0.4, classifier_label="POSSIBLE_ACTION",
        has_commitment_cue=True, has_assignment_cue=False, has_date=False,
        has_question_cue=False, has_hypothetical_cue=False,
        has_past_completed_cue=False, has_rejection_cue=False, has_action_verb=True,
        has_rule_create_cue=True, has_rule_mutation_cue=False,
        has_unique_mutation_target=False, has_strong_negative_guard=negative,
        has_note_action_signal=False, has_note_state_signal=False,
        has_ambiguous_note_signal=False,
    )


def _action() -> ActionCandidate:
    return ActionCandidate(
        candidate_id="ACAND-1", primary_clause_ids=("C-1",),
        action_spans=(GroundedSpan(clause_id="C-1", start=0, end=12, text="gửi báo cáo"),),
        candidate_kind="CREATE", state=CandidateState.PROPOSED,
        commitment_signals=("FIRST_PERSON_COMMITMENT",),
        first_order_index=0, last_order_index=0, builder_version="test",
    )


def _decision() -> CandidateDecision:
    return CandidateDecision(
        candidate_id="CAND-C-1", route=CandidateRoute.AI_CREATE_CHECK,
        confidence=0.6, reasons=["CLASSIFIER_POSSIBLE_ACTION"],
    )


def test_preflight_selects_only_grounded_uncertain_create() -> None:
    records, selected = preflight_ai_create_checks([_decision()], [_evidence()], [_action()], [], maximum=2)

    assert records[0].reason == "ELIGIBLE"
    assert selected == ["CAND-C-1"]


def test_preflight_rejects_hard_negative_before_provider() -> None:
    records, selected = preflight_ai_create_checks([_decision()], [_evidence(negative=True)], [_action()], [], maximum=2)

    assert records[0].reason == "EVIDENCE_HARD_NEGATIVE"
    assert selected == []


def test_preflight_rejects_commitment_hard_negative() -> None:
    records, selected = preflight_ai_create_checks(
        [_decision()], [_evidence()], [_action()],
        [CommitmentDecision("ACAND-1", CommitmentRoute.DROP, "HYPOTHETICAL", ("HARD_NEGATIVE",))],
        maximum=2,
    )

    assert records[0].reason == "COMMITMENT_HARD_NEGATIVE"
    assert selected == []
