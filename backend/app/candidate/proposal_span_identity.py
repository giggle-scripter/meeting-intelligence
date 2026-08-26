"""Exact action spans and deterministic identities for shadow proposal clusters."""

from __future__ import annotations

from hashlib import sha256
import re

from pydantic import BaseModel, ConfigDict

from backend.app.models import Clause, ClauseAnnotation

from .action_candidates import GroundedSpan, extract_action_span
from .evidence_seeds import EvidenceSeed
from .proposal_clusters import ProposalCluster, ProposalKind


class ProposalSpanIdentity(BaseModel):
    """A non-emitting identity record rooted in one source clause."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster_id: str
    primary_clause_id: str
    proposal_kind: ProposalKind
    state: str
    identity_key: str
    normalized_action: str = ""
    action_span: GroundedSpan | None = None


def _normalize_action(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,.;:!?").casefold()


def _identity_key(kind: ProposalKind, clause_id: str, normalized_action: str) -> str:
    material = f"{kind.value}|{normalized_action or clause_id}"
    return "PID-" + sha256(material.encode("utf-8")).hexdigest()[:16]


def build_proposal_span_identities(
    clusters: list[ProposalCluster],
    seeds: list[EvidenceSeed],
    clauses: list[Clause],
    annotations: dict[str, ClauseAnnotation],
) -> list[ProposalSpanIdentity]:
    """Extract exact spans without broad fallback text or output side effects."""

    clauses_by_id = {item.clause_id: item for item in clauses}
    seed_ids = {item.seed_id for item in seeds}
    result: list[ProposalSpanIdentity] = []
    for cluster in clusters:
        if cluster.nucleus_seed_id not in seed_ids:
            raise ValueError(f"cluster {cluster.cluster_id} has an unknown nucleus seed")
        clause = clauses_by_id[cluster.nucleus_clause_id]
        span = extract_action_span(clause, annotations[clause.clause_id])
        normalized = _normalize_action(span.text) if span else ""
        is_create = cluster.proposal_kind is ProposalKind.CREATE and span is not None
        kind = ProposalKind.CREATE if is_create else ProposalKind.REFERENCE
        result.append(
            ProposalSpanIdentity(
                cluster_id=cluster.cluster_id,
                primary_clause_id=clause.clause_id,
                proposal_kind=kind,
                state="PROPOSED" if is_create else "REFERENCE",
                identity_key=_identity_key(kind, clause.clause_id, normalized),
                normalized_action=normalized,
                action_span=span,
            )
        )
    return result
