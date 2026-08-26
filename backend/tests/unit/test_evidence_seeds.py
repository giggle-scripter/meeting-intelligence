from backend.app.candidate import SeedRole, build_evidence_seeds
from backend.app.models import Clause, ClauseAnnotation, DateMention

def test_seed_builder_keeps_negative_and_deadline_evidence() -> None:
    clause=Clause("C-1","S","P","Lan",None,None,"Lan sẽ gửi báo cáo trước thứ Sáu.","",[],0)
    mention=DateMention("D-1","C-1","thứ Sáu","ON_DATE","WEEKDAY")
    seed=build_evidence_seeds([clause],{"C-1":ClauseAnnotation("C-1",{"ACTION_VERB","FIRST_PERSON_COMMITMENT","HYPOTHETICAL"})},{"D-1":mention})[0]
    assert {SeedRole.ACTION,SeedRole.AUTHORITY,SeedRole.OWNER,SeedRole.DEADLINE,SeedRole.NEGATIVE} <= set(seed.roles)
