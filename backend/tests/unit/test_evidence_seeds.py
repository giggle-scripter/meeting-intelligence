from backend.app.candidate import SeedRole, build_evidence_seeds
from backend.app.models import Clause, ClauseAnnotation, DateMention

def test_seed_builder_keeps_negative_and_deadline_evidence() -> None:
    clause=Clause("C-1","S","P","Lan",None,None,"Lan sẽ gửi báo cáo trước thứ Sáu.","",[],0)
    mention=DateMention("D-1","C-1","thứ Sáu","ON_DATE","WEEKDAY")
    seed=build_evidence_seeds([clause],{"C-1":ClauseAnnotation("C-1",{"ACTION_VERB","FIRST_PERSON_COMMITMENT","HYPOTHETICAL"})},{"D-1":mention})[0]
    assert {SeedRole.ACTION,SeedRole.AUTHORITY,SeedRole.OWNER,SeedRole.DEADLINE,SeedRole.NEGATIVE} <= set(seed.roles)


def test_seed_builder_recognizes_explicit_short_acceptance_and_commitment() -> None:
    clauses = [
        Clause("C-1", "S-1", "P-1", "Lan", None, None, "Dạ, được ạ.", "", [], 0),
        Clause("C-2", "S-2", "P-2", "Minh", None, None, "Em cam kết đúng hạn.", "", [], 1),
    ]
    seeds = build_evidence_seeds(clauses, {item.clause_id: ClauseAnnotation(item.clause_id) for item in clauses}, {})
    assert SeedRole.ACCEPTANCE in seeds[0].roles
    assert {SeedRole.AUTHORITY, SeedRole.OWNER} <= set(seeds[1].roles)
