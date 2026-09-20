"""Gold-free union of V3 lattice and cross-fitted neural action spans."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .contracts import GroundedSpan, ProposalRecord, SpanPrediction, TypedRelation
from .hashing import canonical_json_hash, sha256_bytes
from .trace_reader import records


def validate_prediction(prediction: SpanPrediction, trace: dict[str, Any]) -> None:
    clauses = {item["clause_id"]: item["text_raw"] for item in trace.get("clauses", [])}
    if prediction.target_clause_id not in clauses:
        raise ValueError("unknown clause ID")
    text = clauses[prediction.target_clause_id]
    if prediction.start < 0 or prediction.end <= prediction.start or prediction.end > len(text):
        raise ValueError("prediction outside target clause")
    if text[prediction.start : prediction.end] != prediction.text or not prediction.text.strip(" \t\r\n.,;:!?"):
        raise ValueError("prediction is not exact grounded text")


def _relation_support(trace: dict[str, Any], cluster_id: str, clause_id: str) -> tuple[list[TypedRelation], dict[str, list[str]]]:
    seeds = {item["seed_id"]: item for item in records(trace.get("proposal_evidence_seeds_v3"))}
    clusters = {item["cluster_id"]: item for item in records(trace.get("proposal_clusters_v3"))}
    cluster = clusters.get(cluster_id, {})
    nucleus_id = cluster.get("nucleus_seed_id")
    if nucleus_id is None:
        matching_seeds = sorted(
            (item for item in seeds.values() if item.get("clause_id") == clause_id),
            key=lambda item: item["seed_id"],
        )
        nucleus_id = matching_seeds[0]["seed_id"] if matching_seeds else None
    nucleus = seeds.get(nucleus_id, {})
    nucleus_clause = nucleus.get("clause_id", cluster.get("nucleus_clause_id", ""))
    result: list[TypedRelation] = []
    refs: dict[str, list[str]] = defaultdict(list)
    for relation in records(trace.get("proposal_relations_v3")):
        if relation.get("nucleus_seed_id") != nucleus_id:
            continue
        support = seeds.get(relation.get("support_seed_id"), {})
        support_clause = support.get("clause_id")
        if not support_clause:
            continue
        relation_type = relation.get("relation_type", "")
        distance = int(relation.get("distance", 0))
        result.append(
            TypedRelation(
                relation_type=relation_type,
                nucleus_clause_id=nucleus_clause,
                support_clause_id=support_clause,
                distance=distance,
                chronology="AFTER" if support.get("order_index", 0) > nucleus.get("order_index", 0) else "BEFORE",
            )
        )
        if relation_type == "AUTHORIZES":
            refs["authority"].append(support_clause)
        elif relation_type == "ACCEPTS":
            refs["acceptance"].append(support_clause)
        elif relation_type == "HAS_OWNER":
            refs["owner"].append(support_clause)
        elif relation_type == "HAS_DEADLINE":
            refs["deadline"].extend(support.get("date_mention_ids", []))
    result.sort(key=lambda item: (item.relation_type, item.distance, item.support_clause_id))
    return result, refs


def build_candidate_pool(
    case_id: str,
    trace: dict[str, Any],
    neural_predictions: Iterable[SpanPrediction],
    *,
    max_proposals: int = 60,
    gold_spans: Iterable[GroundedSpan] | None = None,
    evaluation: bool = True,
) -> list[ProposalRecord]:
    if evaluation and gold_spans:
        raise ValueError("outer-valid gold span injection is forbidden")
    clauses = {item["clause_id"]: item for item in trace.get("clauses", [])}
    ranking = {
        (item.get("identity_key"), item.get("cluster_id")): item
        for item in records(trace.get("proposal_ranking_v3"))
    }
    candidates: dict[tuple[str, int, int, str], dict[str, Any]] = {}
    for item in records(trace.get("proposal_span_identities_v3")):
        span = item.get("action_span")
        if not span:
            continue
        clause = clauses.get(span.get("clause_id"))
        if not clause or clause["text_raw"][span["start"] : span["end"]] != span["text"]:
            continue
        key = (span["clause_id"], span["start"], span["end"], span["text"])
        rank = ranking.get((item.get("identity_key"), item.get("cluster_id")), {})
        candidates[key] = {
            "cluster": item.get("cluster_id", item.get("identity_key", "")),
            "kind": item.get("proposal_kind", "UNRESOLVED"),
            "lattice": True,
            "neural": False,
            "neural_score": 0.0,
            "deterministic_score": float(rank.get("score", 0.0)),
            "reasons": list(rank.get("reasons", [])),
        }
    seen_predictions: set[tuple[str, int, int, str]] = set()
    for prediction in neural_predictions:
        if prediction.case_id != case_id:
            continue
        validate_prediction(prediction, trace)
        key = (prediction.target_clause_id, prediction.start, prediction.end, prediction.text)
        if key in seen_predictions:
            raise ValueError("duplicate exact neural span")
        seen_predictions.add(key)
        value = candidates.setdefault(
            key,
            {
                "cluster": f"NEURAL-{prediction.target_clause_id}-{prediction.start}-{prediction.end}",
                "kind": "UNRESOLVED",
                "lattice": False,
                "neural": True,
                "neural_score": prediction.span_score,
                "deterministic_score": 0.0,
                "reasons": [],
            },
        )
        value["neural"] = True
        value["neural_score"] = max(value["neural_score"], prediction.span_score)
    proposals: list[ProposalRecord] = []
    for (clause_id, start, end, text), value in candidates.items():
        relations, refs = _relation_support(trace, value["cluster"], clause_id)
        annotation_flags = set(trace.get("annotations", {}).get(clause_id, {}).get("flags", []))
        negative_refs = [clause_id] if annotation_flags & {
            "ROOT_QUESTION", "SUGGESTION_ONLY", "HYPOTHETICAL", "PAST_COMPLETED", "PROGRESS_UPDATE", "RECAP_ITEM", "MUTATION_ONLY"
        } else []
        source = "BOTH" if value["lattice"] and value["neural"] else "LATTICE" if value["lattice"] else "NEURAL"
        input_value = {"case_id": case_id, "clause_id": clause_id, "start": start, "end": end, "text": text, "cluster": value["cluster"]}
        proposals.append(
            ProposalRecord(
                proposal_id=sha256_bytes("\0".join(map(str, input_value.values())).encode()),
                input_hash=canonical_json_hash(input_value),
                case_id=case_id,
                kind=value["kind"] if value["kind"] in {"CREATE", "UPDATE", "REFERENCE", "DROP", "UNRESOLVED"} else "UNRESOLVED",
                cluster_identity=value["cluster"],
                action_span=GroundedSpan(clause_id=clause_id, start=start, end=end, text=text),
                authority_refs=sorted(set(refs["authority"])),
                acceptance_refs=sorted(set(refs["acceptance"])),
                owner_refs=sorted(set(refs["owner"])),
                deadline_refs=sorted(set(refs["deadline"])),
                negative_refs=negative_refs,
                relations=relations,
                deterministic_score=value["deterministic_score"],
                deterministic_reasons=value["reasons"],
                neural_span_score=value["neural_score"],
                source_type=source,
                ambiguity_flags=[],
                order_index=int(clauses[clause_id].get("order_index", 0)),
            )
        )
    source_rank = {"BOTH": 0, "NEURAL": 1, "LATTICE": 2}
    proposals.sort(
        key=lambda item: (
            source_rank[item.source_type],
            not bool(item.authority_refs or item.acceptance_refs),
            -item.neural_span_score,
            -item.deterministic_score,
            item.order_index,
            item.action_span.start,
            item.action_span.end,
            item.proposal_id,
        )
    )
    return proposals[:max_proposals]
