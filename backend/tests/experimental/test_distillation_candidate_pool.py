from __future__ import annotations

import pytest

from experiments.distilled_proposal_ranker.candidate_pool import build_candidate_pool, validate_prediction
from experiments.distilled_proposal_ranker.contracts import GroundedSpan, SpanPrediction


def _prediction(start: int = 0, end: int = 3, text: str = "Làm") -> SpanPrediction:
    return SpanPrediction(
        prediction_id=f"p-{start}-{end}",
        input_hash="0" * 64,
        case_id="CASE",
        target_clause_id="C1",
        start=start,
        end=end,
        text=text,
        start_logit=1,
        end_logit=1,
        span_score=0.9,
        no_action_score=0.1,
        model_name="fake",
        outer_fold=0,
        training_seed=17,
        checkpoint_hash="1" * 64,
    )


def _trace() -> dict:
    return {
        "meeting_id": "CASE",
        "clauses": [{"clause_id": "C1", "text_raw": "Làm báo cáo", "order_index": 0}],
        "annotations": {"C1": {"flags": []}},
        "proposal_span_identities_v3": {"records": [{
            "cluster_id": "CL1", "identity_key": "I1", "proposal_kind": "CREATE",
            "action_span": {"clause_id": "C1", "start": 0, "end": 3, "text": "Làm"},
        }]},
        "proposal_ranking_v3": {"records": [{"cluster_id": "CL1", "identity_key": "I1", "score": 0.4, "reasons": ["GROUNDED_SPAN"]}]},
        "proposal_evidence_seeds_v3": {"records": []},
        "proposal_clusters_v3": {"records": []},
        "proposal_relations_v3": {"records": []},
    }


def test_union_deduplicates_exact_span_and_marks_both() -> None:
    pool = build_candidate_pool("CASE", _trace(), [_prediction()])
    assert len(pool) == 1
    assert pool[0].source_type == "BOTH"


def test_prediction_outside_clause_and_duplicate_are_rejected() -> None:
    with pytest.raises(ValueError, match="outside"):
        validate_prediction(_prediction(0, 20, "Làm báo cáo plus"), _trace())
    with pytest.raises(ValueError, match="duplicate"):
        build_candidate_pool("CASE", _trace(), [_prediction(), _prediction()])


def test_outer_valid_gold_is_never_injected() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        build_candidate_pool("CASE", _trace(), [], gold_spans=[GroundedSpan(clause_id="C1", start=0, end=3, text="Làm")], evaluation=True)


def test_neural_only_span_inherits_grounded_relations_from_its_clause_seed() -> None:
    trace = _trace()
    trace["proposal_span_identities_v3"]["records"] = []
    trace["clauses"].append({"clause_id": "C2", "text_raw": "Tôi nhận", "order_index": 1})
    trace["annotations"]["C2"] = {"flags": ["FIRST_PERSON_COMMITMENT"]}
    trace["proposal_evidence_seeds_v3"]["records"] = [
        {"seed_id": "S1", "clause_id": "C1", "order_index": 0, "roles": ["ACTION"], "date_mention_ids": []},
        {"seed_id": "S2", "clause_id": "C2", "order_index": 1, "roles": ["ACCEPTANCE", "AUTHORITY"], "date_mention_ids": []},
    ]
    trace["proposal_relations_v3"]["records"] = [
        {"relation_type": "ACCEPTS", "nucleus_seed_id": "S1", "support_seed_id": "S2", "distance": 1}
    ]
    proposal = build_candidate_pool("CASE", trace, [_prediction()])[0]
    assert proposal.source_type == "NEURAL"
    assert proposal.acceptance_refs == ["C2"]
    assert proposal.relations[0].chronology == "AFTER"


def test_top_60_prune_is_deterministic() -> None:
    trace = _trace()
    trace["clauses"][0]["text_raw"] = "a" * 70
    trace["proposal_span_identities_v3"]["records"] = []
    predictions = [
        _prediction(0, 3, "Làm").model_copy(
            update={"prediction_id": str(index), "span_score": index / 100, "start": index, "end": index + 1, "text": "a"}
        )
        for index in range(70)
    ]
    first = build_candidate_pool("CASE", trace, predictions)
    second = build_candidate_pool("CASE", trace, list(reversed(predictions)))
    assert len(first) == 60
    assert [item.proposal_id for item in first] == [item.proposal_id for item in second]
