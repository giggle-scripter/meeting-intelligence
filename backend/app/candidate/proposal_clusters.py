"""Constrained V3 proposal clusters built around one typed source nucleus."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from .evidence_seeds import EvidenceSeed, SeedRole
from .proposal_relations import ProposalRelation, RelationType


class ProposalKind(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    REFERENCE = "REFERENCE"


class ProposalCluster(BaseModel):
    """One action plus only its directly typed, local supporting evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster_id: str
    nucleus_seed_id: str
    nucleus_clause_id: str
    proposal_kind: ProposalKind
    support_seed_ids: tuple[str, ...] = ()
    relation_types: tuple[RelationType, ...] = ()


def _proposal_kind(nucleus: EvidenceSeed) -> ProposalKind:
    if SeedRole.MUTATION in nucleus.roles:
        return ProposalKind.UPDATE
    if SeedRole.ACTION not in nucleus.roles or SeedRole.NEGATIVE in nucleus.roles or SeedRole.RECAP in nucleus.roles:
        return ProposalKind.REFERENCE
    return ProposalKind.CREATE


def build_proposal_clusters(
    seeds: list[EvidenceSeed], relations: list[ProposalRelation]
) -> list[ProposalCluster]:
    """Build one cluster per action seed; unrelated actions are never merged."""

    by_nucleus: dict[str, list[ProposalRelation]] = {}
    for relation in relations:
        by_nucleus.setdefault(relation.nucleus_seed_id, []).append(relation)

    clusters: list[ProposalCluster] = []
    for nucleus in seeds:
        attached = sorted(
            by_nucleus.get(nucleus.seed_id, []),
            key=lambda item: (item.distance, item.support_seed_id, item.relation_type.value),
        )
        clusters.append(
            ProposalCluster(
                cluster_id=f"CLUSTER-{nucleus.clause_id}",
                nucleus_seed_id=nucleus.seed_id,
                nucleus_clause_id=nucleus.clause_id,
                proposal_kind=_proposal_kind(nucleus),
                support_seed_ids=tuple(item.support_seed_id for item in attached),
                relation_types=tuple(item.relation_type for item in attached),
            )
        )
    return clusters
