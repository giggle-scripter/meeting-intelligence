"""Conservative shadow ranking for span-grounded proposal identities."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .action_canonicalization import build_action_frame
from .evidence_seeds import EvidenceSeed, SeedRole
from .proposal_clusters import ProposalCluster
from .proposal_relations import ProposalRelation, RelationType
from .proposal_span_identity import ProposalSpanIdentity


class ProposalDecision(str, Enum):
    SELECTED = "SELECTED"
    HELD_OUT = "HELD_OUT"


class RankedProposal(BaseModel):
    """A ranking decision that cannot be consumed by the event ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity_key: str
    cluster_id: str
    primary_clause_id: str
    canonical_action: str = ""
    score: float = Field(ge=0.0, le=1.0)
    decision: ProposalDecision
    reasons: tuple[str, ...] = ()
    duplicate_group_key: str = ""


def build_ranked_proposals(
    identities: list[ProposalSpanIdentity],
    clusters: list[ProposalCluster],
    relations: list[ProposalRelation],
    seeds: list[EvidenceSeed],
    *,
    selection_threshold: float = 0.90,
) -> list[RankedProposal]:
    """Rank only concrete CREATE spans; references remain visible but held out."""

    clusters_by_id = {item.cluster_id: item for item in clusters}
    seeds_by_id = {item.seed_id: item for item in seeds}
    relation_types: dict[str, set[RelationType]] = {}
    for relation in relations:
        relation_types.setdefault(relation.nucleus_seed_id, set()).add(relation.relation_type)

    result: list[RankedProposal] = []
    for identity in identities:
        cluster = clusters_by_id.get(identity.cluster_id)
        if cluster is None:
            raise ValueError(f"identity {identity.identity_key} has an unknown cluster")
        nucleus = seeds_by_id.get(cluster.nucleus_seed_id)
        if nucleus is None:
            raise ValueError(f"cluster {cluster.cluster_id} has an unknown nucleus seed")
        frame = build_action_frame(
            identity.action_span.text if identity.action_span else "",
            (identity.primary_clause_id,),
        ) if identity.action_span else None
        links = relation_types.get(nucleus.seed_id, set())
        score = 0.0
        reasons: list[str] = []
        if identity.state == "PROPOSED" and identity.action_span:
            score += 0.55; reasons.append("GROUNDED_SPAN")
        if frame and frame.valid:
            score += 0.20; reasons.append("CONCRETE_ACTION")
        if RelationType.AUTHORIZES in links or RelationType.ACCEPTS in links:
            score += 0.15; reasons.append("AUTHORITY_LINK")
        if RelationType.OWNS in links:
            score += 0.05; reasons.append("OWNER_LINK")
        if RelationType.HAS_DEADLINE in links:
            score += 0.05; reasons.append("DEADLINE_LINK")
        has_nucleus_authority = SeedRole.AUTHORITY in nucleus.roles
        if has_nucleus_authority:
            reasons.append("NUCLEUS_AUTHORITY")
        if SeedRole.NEGATIVE in nucleus.roles or SeedRole.RECAP in nucleus.roles:
            score = 0.0; reasons.append("NON_CREATE_GUARD")
        score = round(min(score, 1.0), 3)
        selected = bool(
            frame and frame.valid and identity.state == "PROPOSED" and has_nucleus_authority
            and score >= selection_threshold
        )
        canonical = frame.canonical_action if frame and frame.valid else ""
        result.append(
            RankedProposal(
                identity_key=identity.identity_key,
                cluster_id=identity.cluster_id,
                primary_clause_id=identity.primary_clause_id,
                canonical_action=canonical,
                score=score,
                decision=ProposalDecision.SELECTED if selected else ProposalDecision.HELD_OUT,
                reasons=tuple(reasons),
                duplicate_group_key=canonical.casefold() if canonical else "",
            )
        )
    return result
