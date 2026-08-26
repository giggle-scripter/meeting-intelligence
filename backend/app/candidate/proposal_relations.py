"""Typed, bounded relations between V3 evidence seeds."""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field
from .evidence_seeds import EvidenceSeed, SeedRole

class RelationType(str,Enum):
    AUTHORIZES="AUTHORIZES"; ACCEPTS="ACCEPTS"; OWNS="OWNS"; HAS_DEADLINE="HAS_DEADLINE"; CONTINUES_ACTION="CONTINUES_ACTION"
class ProposalRelation(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    relation_type:RelationType
    nucleus_seed_id:str
    support_seed_id:str
    distance:int=Field(ge=0)
    score:float=Field(ge=0,le=1)

def build_proposal_relations(seeds:list[EvidenceSeed], *, local_clause_radius:int=3)->list[ProposalRelation]:
    """Link only to one local proposal nucleus; never union components."""
    result=[]
    for nucleus in seeds:
        for support in seeds:
            if support.seed_id==nucleus.seed_id: continue
            distance=abs(support.order_index-nucleus.order_index)
            if distance>local_clause_radius: continue
            if SeedRole.AUTHORITY in support.roles:
                typ=RelationType.AUTHORIZES
            elif SeedRole.ACCEPTANCE in support.roles and support.order_index>=nucleus.order_index:
                typ=RelationType.ACCEPTS
            elif SeedRole.DEADLINE in support.roles:
                typ=RelationType.HAS_DEADLINE
            elif SeedRole.OWNER in support.roles:
                typ=RelationType.OWNS
            elif support.speaker_name==nucleus.speaker_name and distance<=1:
                typ=RelationType.CONTINUES_ACTION
            else: continue
            score=round(1.0-(distance/(local_clause_radius+1))*0.25,3)
            result.append(ProposalRelation(relation_type=typ,nucleus_seed_id=nucleus.seed_id,support_seed_id=support.seed_id,distance=distance,score=score))
    return result
