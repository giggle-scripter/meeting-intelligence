from backend.app.candidate import (
    EvidenceSeed,
    ProposalKind,
    SeedRole,
    build_proposal_clusters,
    build_proposal_relations,
)


def _seed(index: int, roles: tuple[SeedRole, ...], speaker: str = "Lan") -> EvidenceSeed:
    return EvidenceSeed(
        seed_id=f"S{index}",
        clause_id=f"C{index}",
        turn_id=f"T{index}",
        speaker_name=speaker,
        order_index=index,
        roles=roles,
    )


def test_clusters_keep_action_nuclei_separate_and_attach_local_support() -> None:
    first = _seed(1, (SeedRole.ACTION,))
    second = _seed(6, (SeedRole.ACTION,))
    authority = _seed(2, (SeedRole.AUTHORITY, SeedRole.OWNER))
    clusters = build_proposal_clusters(
        [first, authority, second], build_proposal_relations([first, authority, second])
    )

    assert [item.action_seed_id for item in clusters] == ["S1", "S6"]
    assert clusters[0].support_seed_ids == ("S2",)
    assert clusters[1].support_seed_ids == ()
    assert clusters[0].proposal_kind is ProposalKind.CREATE
