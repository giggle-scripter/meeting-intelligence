from backend.app.candidate import EvidenceSeed, SeedRole, RelationScope, RelationType, build_proposal_relations
def _seed(i,roles): return EvidenceSeed(seed_id=f"S{i}",clause_id=f"C{i}",turn_id=f"T{i}",speaker_name="Lan",order_index=i,roles=roles)
def test_relations_link_action_to_local_authority_and_deadline():
    rows=build_proposal_relations([_seed(1,(SeedRole.ACTION,)),_seed(2,(SeedRole.AUTHORITY,SeedRole.OWNER)),_seed(3,(SeedRole.DEADLINE,))])
    assert {x.relation_type for x in rows}>={RelationType.AUTHORIZES,RelationType.HAS_DEADLINE}
    assert any(item.nucleus_seed_id == "S1" for item in rows)


def test_relations_link_bounded_same_speaker_follow_up_without_crossing_new_action():
    action = _seed(1, (SeedRole.ACTION,))
    deadline = _seed(12, (SeedRole.DEADLINE,))
    rows = build_proposal_relations([action, deadline])
    assert any(item.relation_type is RelationType.HAS_DEADLINE and item.scope is RelationScope.SAME_SPEAKER_FOLLOW_UP for item in rows)

    new_action = _seed(4, (SeedRole.ACTION,))
    blocked = build_proposal_relations([action, new_action, deadline])
    assert not any(item.nucleus_seed_id == "S1" and item.support_seed_id == "S12" for item in blocked)


def test_relations_keep_deadline_relation_when_clause_is_also_an_acceptance() -> None:
    rows = build_proposal_relations([
        _seed(1, (SeedRole.ACTION,)),
        _seed(2, (SeedRole.ACCEPTANCE, SeedRole.DEADLINE)),
    ])
    assert {item.relation_type for item in rows} >= {RelationType.ACCEPTS, RelationType.HAS_DEADLINE}
