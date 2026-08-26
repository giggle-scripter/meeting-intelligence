"""Wide, typed evidence seeds for V3 proposal construction (shadow only)."""
from __future__ import annotations

from enum import Enum
import re
from pydantic import BaseModel, ConfigDict, Field
from backend.app.models import Clause, ClauseAnnotation, DateMention


class SeedRole(str, Enum):
    ACTION="ACTION"; AUTHORITY="AUTHORITY"; ACCEPTANCE="ACCEPTANCE"; OWNER="OWNER"; DEADLINE="DEADLINE"; TASK_REFERENCE="TASK_REFERENCE"; RECAP="RECAP"; MUTATION="MUTATION"; NEGATIVE="NEGATIVE"


class EvidenceSeed(BaseModel):
    model_config=ConfigDict(extra="forbid", frozen=True)
    seed_id: str
    clause_id: str
    turn_id: str
    speaker_name: str
    roles: tuple[SeedRole, ...]=Field(min_length=1)
    flags: tuple[str, ...]=()
    date_mention_ids: tuple[str, ...]=()
    rule_score: float=0.0


_NEGATIVE={"ROOT_QUESTION","SUGGESTION_ONLY","HYPOTHETICAL","PAST_COMPLETED","PROGRESS_UPDATE","FUTURE_DISCUSSION","ADMIN_FOLLOWUP","REJECTION","CANCELLATION"}
_MUTATION={"CORRECTION","CANCELLATION","REJECTION"}
_RECAP=re.compile(r"\b(?:tổng kết|chốt lại|recap|trạng thái cuối)\b",re.I)
_REFERENCE=re.compile(r"\b(?:task|công việc|phần đó|việc đó|deadline|lỗi|ý em|integration spec)\b",re.I)
_EXPLICIT_ACTION=re.compile(r"\b(?:tập trung|debug|khảo sát|thiết lập|cấu hình|triển khai)\b",re.I)


def build_evidence_seeds(clauses:list[Clause], annotations:dict[str,ClauseAnnotation], mentions:dict[str,DateMention]) -> list[EvidenceSeed]:
    dates:dict[str,list[str]]={}
    for item in mentions.values():
        if item.purpose=="DEADLINE": dates.setdefault(item.clause_id,[]).append(item.date_mention_id)
    result=[]
    for clause in clauses:
        annotation=annotations[clause.clause_id]; flags=set(annotation.flags); roles=set()
        if "ACTION_VERB" in flags or _EXPLICIT_ACTION.search(clause.text_raw): roles.add(SeedRole.ACTION)
        if flags & {"DIRECT_ASSIGNMENT","FIRST_PERSON_COMMITMENT"}: roles|={SeedRole.AUTHORITY,SeedRole.OWNER}
        if "CONFIRMATION" in flags: roles.add(SeedRole.ACCEPTANCE)
        if dates.get(clause.clause_id): roles.add(SeedRole.DEADLINE)
        if flags & _MUTATION: roles.add(SeedRole.MUTATION)
        if flags & _NEGATIVE: roles.add(SeedRole.NEGATIVE)
        if _RECAP.search(clause.text_raw): roles.add(SeedRole.RECAP)
        if _REFERENCE.search(clause.text_raw): roles.add(SeedRole.TASK_REFERENCE)
        if roles:
            result.append(EvidenceSeed(seed_id=f"SEED-{clause.clause_id}",clause_id=clause.clause_id,turn_id=clause.sentence_id,speaker_name=clause.speaker_name,roles=tuple(sorted(roles,key=lambda x:x.value)),flags=tuple(sorted(flags)),date_mention_ids=tuple(sorted(dates.get(clause.clause_id,[]))),rule_score=annotation.rule_confidence))
    return result
