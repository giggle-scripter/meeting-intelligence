from __future__ import annotations

from experiments.distilled_proposal_ranker.contracts import GroundedSpan, ProposalRecord
from experiments.distilled_proposal_ranker.hard_negatives import negative_reason, select_hard_negatives


def _proposal(identifier: str, kind: str = "CREATE", reasons: list[str] | None = None, ambiguity: list[str] | None = None) -> ProposalRecord:
    return ProposalRecord(
        proposal_id=identifier,
        input_hash="0" * 64,
        case_id="CASE",
        kind=kind,
        cluster_identity=identifier,
        action_span=GroundedSpan(clause_id="C1", start=0, end=2, text="do"),
        authority_refs=[], acceptance_refs=[], owner_refs=[], deadline_refs=[], negative_refs=[], relations=[],
        deterministic_score=0, deterministic_reasons=reasons or [], neural_span_score=0,
        source_type="LATTICE", ambiguity_flags=ambiguity or [], order_index=0,
    )


def test_hard_negative_taxonomy_prefers_sibling_then_suggestion() -> None:
    positive = _proposal("positive")
    sibling = _proposal("sibling", ambiguity=["SIBLING_AMBIGUITY"])
    suggestion = _proposal("suggestion", reasons=["SUGGESTION"])
    reference = _proposal("reference", kind="REFERENCE")
    selected = select_hard_negatives([reference, suggestion, positive, sibling], positive.proposal_id)
    assert [item.proposal_id for item in selected] == ["sibling", "suggestion", "reference"]
    assert negative_reason(reference) == "REFERENCE_ONLY"


def test_suggestion_near_deadline_remains_negative() -> None:
    proposal = _proposal("suggestion", reasons=["SUGGESTION", "HAS_DEADLINE"])
    assert negative_reason(proposal) == "SUGGESTION_ONLY"
