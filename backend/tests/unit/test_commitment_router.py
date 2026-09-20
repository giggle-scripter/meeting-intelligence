from backend.app.candidate import (
    ActionCandidate,
    AuthorityKind,
    CandidateState,
    CommitmentRoute,
    GroundedSpan,
    route_commitments,
)
from backend.app.models import Clause, MeetingInput
from backend.app.pipeline import process_meeting


def _candidate(*, signals: tuple[str, ...] = (), negative: tuple[str, ...] = ()) -> ActionCandidate:
    return ActionCandidate(
        candidate_id="ACAND-1",
        primary_clause_ids=("C-1",),
        action_spans=(GroundedSpan(clause_id="C-1", start=7, end=20, text="gửi báo cáo"),),
        candidate_kind="CREATE",
        state=CandidateState.PROPOSED,
        commitment_signals=signals,
        negative_signals=negative,
        first_order_index=0,
        last_order_index=0,
        builder_version="test",
    )


def _clause(text: str) -> Clause:
    return Clause("C-1", "S", "SPK", "Lan", None, None, text, text.casefold(), [], 0)


def test_hard_negative_precedes_active_positive_authority() -> None:
    decision = route_commitments(
        [_candidate(signals=("FIRST_PERSON_COMMITMENT",), negative=("HYPOTHETICAL",))],
        {"C-1": _clause("Em sẽ gửi báo cáo nếu cần.")},
        active_authorities=frozenset({AuthorityKind.SELF_COMMITMENT}),
    )[0]

    assert decision.route is CommitmentRoute.DROP
    assert decision.authority_kind is AuthorityKind.HYPOTHETICAL
    assert "HARD_NEGATIVE" in decision.reasons


def test_conditional_commitment_is_context_only() -> None:
    decision = route_commitments(
        [_candidate(signals=("FIRST_PERSON_COMMITMENT",))],
        {"C-1": _clause("Khi nào xong em sẽ gửi báo cáo.")},
        active_authorities=frozenset({AuthorityKind.SELF_COMMITMENT}),
    )[0]

    assert decision.route is CommitmentRoute.CONTEXT_ONLY
    assert decision.authority_kind is AuthorityKind.CONDITIONAL


def test_coordination_followup_is_context_only_without_hiding_the_evidence() -> None:
    decision = route_commitments(
        [_candidate(signals=("FIRST_PERSON_COMMITMENT",))],
        {"C-1": _clause("Em sẽ gửi biên bản sau cuộc họp.")},
        active_authorities=frozenset({AuthorityKind.SELF_COMMITMENT}),
    )[0]

    assert decision.route is CommitmentRoute.CONTEXT_ONLY
    assert decision.authority_kind is AuthorityKind.ADMIN_FOLLOWUP


def test_assist_suppresses_only_candidate_backed_non_authoritative_create() -> None:
    meeting = MeetingInput(
        "commitment-router", "Commitment router", "2026-08-21",
        "[09:00:00] Lan: Khi nào xong em sẽ gửi báo cáo.",
    )

    baseline = process_meeting(meeting)
    assist = process_meeting(meeting, commitment_router_mode="assist")

    assert len(baseline.tasks) == 1
    assert assist.tasks == []
    assert assist.diagnostics.commitment_router_suppressed_event_count == 1
    assert assist.diagnostics.commitment_router_route_counts == {"CONTEXT_ONLY": 1}
