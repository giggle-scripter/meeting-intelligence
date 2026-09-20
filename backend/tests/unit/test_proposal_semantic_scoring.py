from backend.app.candidate import GroundedSpan, ProposalKind, ProposalSpanIdentity, build_semantic_proposal_scores


def _identity(text: str) -> ProposalSpanIdentity:
    return ProposalSpanIdentity(
        identity_key="PID-1", cluster_id="CLUSTER-C1", primary_clause_id="C1",
        proposal_kind=ProposalKind.CREATE, state="PROPOSED",
        action_span=GroundedSpan(clause_id="C1", start=0, end=len(text), text=text),
    )


def test_semantic_score_rewards_specific_concrete_action() -> None:
    score = build_semantic_proposal_scores([_identity("viết báo cáo kiểm thử API")])[0]
    assert score.score >= 0.9
    assert "SPECIFIC_OBJECT" in score.reasons


def test_semantic_score_rejects_action_without_object() -> None:
    score = build_semantic_proposal_scores([_identity("hoàn thành")])[0]
    assert score.score == 0.0
