from backend.app.candidate import EvidenceSeed, SeedRole, RelationType, build_proposal_relations
def _seed(i,roles): return EvidenceSeed(seed_id=f"S{i}",clause_id=f"C{i}",turn_id=f"T{i}",speaker_name="Lan",order_index=i,roles=roles)
def test_relations_link_action_to_local_authority_and_deadline():
    rows=build_proposal_relations([_seed(1,(SeedRole.ACTION,)),_seed(2,(SeedRole.AUTHORITY,SeedRole.OWNER)),_seed(3,(SeedRole.DEADLINE,))])
    assert {x.relation_type for x in rows}>={RelationType.AUTHORIZES,RelationType.HAS_DEADLINE}
