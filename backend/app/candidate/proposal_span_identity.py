"""Exact action spans and deterministic identities for shadow proposal clusters."""

from __future__ import annotations

from hashlib import sha256
import re

from pydantic import BaseModel, ConfigDict

from backend.app.models import Clause, ClauseAnnotation

from .action_candidates import GroundedSpan, extract_action_spans
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
    span_variant: str = "NO_SPAN"


def _normalize_action(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,.;:!?").casefold()


def _identity_key(kind: ProposalKind, clause_id: str, normalized_action: str) -> str:
    material = f"{kind.value}|{normalized_action or clause_id}"
    return "PID-" + sha256(material.encode("utf-8")).hexdigest()[:16]


def build_action_span_lattice(spans: tuple[GroundedSpan, ...], *, max_prefix_tokens: int = 7) -> list[tuple[GroundedSpan, str]]:
    """Return full spans plus bounded token-prefix alternatives from the same text."""

    result: list[tuple[GroundedSpan, str]] = []
    seen: set[tuple[str, int, int]] = set()
    for span in spans:
        candidates: list[tuple[GroundedSpan, str]] = [(span, "FULL_BOUNDARY")]
        tokens = list(re.finditer(r"\S+", span.text))
        for token_count in range(2, min(len(tokens), max_prefix_tokens) + 1):
            end = tokens[token_count - 1].end()
            value = span.text[:end].strip(" ,.;:!?")
            if value and value != span.text:
                candidates.append((GroundedSpan(clause_id=span.clause_id, start=span.start, end=span.start + len(value), text=value), "TOKEN_PREFIX"))
        for candidate, variant in candidates:
            key = (candidate.clause_id, candidate.start, candidate.end)
            if key not in seen:
                seen.add(key)
                result.append((candidate, variant))
    return result


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
        spans = build_action_span_lattice(extract_action_spans(clause, annotations[clause.clause_id]))
        if spans:
            for span, variant in spans:
                normalized = _normalize_action(span.text)
                is_create = cluster.proposal_kind is ProposalKind.CREATE
                result.append(
                    ProposalSpanIdentity(
                        cluster_id=cluster.cluster_id,
                        primary_clause_id=clause.clause_id,
                        proposal_kind=ProposalKind.CREATE if is_create else ProposalKind.REFERENCE,
                        state="PROPOSED" if is_create else "REFERENCE",
                        identity_key=_identity_key(ProposalKind.CREATE if is_create else ProposalKind.REFERENCE, clause.clause_id, normalized),
                        normalized_action=normalized,
                        action_span=span,
                        span_variant=variant,
                    )
                )
        else:
            result.append(
                ProposalSpanIdentity(
                    cluster_id=cluster.cluster_id,
                    primary_clause_id=clause.clause_id,
                    proposal_kind=ProposalKind.REFERENCE,
                    state="REFERENCE",
                    identity_key=_identity_key(ProposalKind.REFERENCE, clause.clause_id, ""),
                    span_variant="NO_SPAN",
                )
            )
    return result
