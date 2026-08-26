"""Typed, bounded relations between V3 evidence seeds."""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field
from .evidence_seeds import EvidenceSeed, SeedRole

class RelationType(str,Enum):
    AUTHORIZES="AUTHORIZES"; ACCEPTS="ACCEPTS"; OWNS="OWNS"; HAS_DEADLINE="HAS_DEADLINE"; CONTINUES_ACTION="CONTINUES_ACTION"
class RelationScope(str,Enum):
    LOCAL="LOCAL"; RESPONSE="RESPONSE"; SAME_SPEAKER_FOLLOW_UP="SAME_SPEAKER_FOLLOW_UP"
class ProposalRelation(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    relation_type:RelationType
    nucleus_seed_id:str
    support_seed_id:str
    distance:int=Field(ge=0)
    score:float=Field(ge=0,le=1)
    scope:RelationScope=RelationScope.LOCAL

def build_proposal_relations(seeds:list[EvidenceSeed], *, local_clause_radius:int=3, response_clause_radius:int=3, same_speaker_follow_up_radius:int=24)->list[ProposalRelation]:
    """Link typed evidence with bounded local, response, and causal follow-up rules."""
    result=[]
    action_or_reference_orders={item.order_index for item in seeds if SeedRole.ACTION in item.roles or SeedRole.TASK_REFERENCE in item.roles}
    for nucleus in seeds:
        for support in seeds:
            if support.seed_id==nucleus.seed_id: continue
            distance=abs(support.order_index-nucleus.order_index)
            scope=RelationScope.LOCAL
            if distance<=local_clause_radius and SeedRole.AUTHORITY in support.roles:
                typ=RelationType.AUTHORIZES
            elif distance<=local_clause_radius and SeedRole.DEADLINE in support.roles:
                typ=RelationType.HAS_DEADLINE
            elif distance<=response_clause_radius and SeedRole.ACCEPTANCE in support.roles and support.order_index>=nucleus.order_index:
                typ=RelationType.ACCEPTS; scope=RelationScope.RESPONSE
            elif distance<=local_clause_radius and SeedRole.OWNER in support.roles:
                typ=RelationType.OWNS
            elif distance<=local_clause_radius and support.speaker_name==nucleus.speaker_name and distance<=1:
                typ=RelationType.CONTINUES_ACTION
            elif support.order_index>nucleus.order_index and distance<=same_speaker_follow_up_radius and support.speaker_name==nucleus.speaker_name and not any(nucleus.order_index < order < support.order_index for order in action_or_reference_orders):
                if SeedRole.AUTHORITY in support.roles: typ=RelationType.AUTHORIZES
                elif SeedRole.DEADLINE in support.roles: typ=RelationType.HAS_DEADLINE
                elif SeedRole.OWNER in support.roles: typ=RelationType.OWNS
                else: continue
                scope=RelationScope.SAME_SPEAKER_FOLLOW_UP
            else: continue
            radius=local_clause_radius if scope is RelationScope.LOCAL else (response_clause_radius if scope is RelationScope.RESPONSE else same_speaker_follow_up_radius)
            score=round(1.0-(distance/(radius+1))*0.25,3)
            result.append(ProposalRelation(relation_type=typ,nucleus_seed_id=nucleus.seed_id,support_seed_id=support.seed_id,distance=distance,score=score,scope=scope))
            if SeedRole.DEADLINE in support.roles and typ is not RelationType.HAS_DEADLINE:
                result.append(ProposalRelation(relation_type=RelationType.HAS_DEADLINE,nucleus_seed_id=nucleus.seed_id,support_seed_id=support.seed_id,distance=distance,score=score,scope=scope))
            if SeedRole.ACCEPTANCE in support.roles and typ is not RelationType.ACCEPTS and support.order_index>=nucleus.order_index and distance<=response_clause_radius:
                result.append(ProposalRelation(relation_type=RelationType.ACCEPTS,nucleus_seed_id=nucleus.seed_id,support_seed_id=support.seed_id,distance=distance,score=score,scope=RelationScope.RESPONSE))
    return result
