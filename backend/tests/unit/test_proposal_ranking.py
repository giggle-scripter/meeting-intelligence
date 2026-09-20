from backend.app.candidate import (
    EvidenceSeed,
    GroundedSpan,
    ProposalCluster,
    ProposalDecision,
    ProposalKind,
    ProposalRelation,
    ProposalSpanIdentity,
    RelationType,
    SeedRole,
    build_ranked_proposals,
)


def _seed(roles: tuple[SeedRole, ...]) -> EvidenceSeed:
    return EvidenceSeed(seed_id="S1", clause_id="C1", turn_id="T1", speaker_name="Lan", order_index=1, roles=roles)


def _identity() -> ProposalSpanIdentity:
    return ProposalSpanIdentity(
        cluster_id="CLUSTER-C1", primary_clause_id="C1", proposal_kind=ProposalKind.CREATE,
        state="PROPOSED", identity_key="PID-1", normalized_action="viết báo cáo",
        action_span=GroundedSpan(clause_id="C1", start=0, end=13, text="viết báo cáo"),
    )


def test_ranker_selects_concrete_grounded_action_with_authority() -> None:
    cluster = ProposalCluster(cluster_id="CLUSTER-C1", nucleus_seed_id="S1", nucleus_clause_id="C1", proposal_kind=ProposalKind.CREATE)
    relation = ProposalRelation(relation_type=RelationType.AUTHORIZES, nucleus_seed_id="S1", support_seed_id="S2", distance=1, score=1.0)
    record = build_ranked_proposals([_identity()], [cluster], [relation], [_seed((SeedRole.ACTION, SeedRole.AUTHORITY))])[0]

    assert record.decision is ProposalDecision.SELECTED
    assert record.score >= 0.75
    assert record.canonical_action == "Viết báo cáo"


def test_ranker_holds_out_action_without_nucleus_authority() -> None:
    cluster = ProposalCluster(cluster_id="CLUSTER-C1", nucleus_seed_id="S1", nucleus_clause_id="C1", proposal_kind=ProposalKind.CREATE)
    relation = ProposalRelation(relation_type=RelationType.AUTHORIZES, nucleus_seed_id="S1", support_seed_id="S2", distance=1, score=1.0)
    record = build_ranked_proposals([_identity()], [cluster], [relation], [_seed((SeedRole.ACTION,))])[0]

    assert record.decision is ProposalDecision.HELD_OUT


def test_ranker_holds_out_negative_nucleus_even_with_grounded_span() -> None:
    cluster = ProposalCluster(cluster_id="CLUSTER-C1", nucleus_seed_id="S1", nucleus_clause_id="C1", proposal_kind=ProposalKind.CREATE)
    record = build_ranked_proposals([_identity()], [cluster], [], [_seed((SeedRole.ACTION, SeedRole.NEGATIVE))])[0]

    assert record.decision is ProposalDecision.HELD_OUT
    assert record.score == 0.0
