"""V2.23 TRAIN34 OOF ranker over a baseline/bridge proposal union.

The candidate pool is the locked ``final_tasks`` inventory unioned with
inference-valid V2.17 occurrence-to-task bridge candidates.  Pool construction
and identity-aware deduplication consume runtime trace data only.  Expected
tasks are used for fold-local labels and scoring, never for inference
features, candidate construction, or deduplication.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.evaluation import _maximum_weight_matching, aggregate_results, compare_case  # noqa: E402
from experiments.distilled_proposal_ranker.v2.protocol_bridge_v217 import (  # noqa: E402
    Occurrence,
    _normalise,
    bridge_occurrence_to_task,
    retrieve_cross_clause_evidence,
    runtime_occurrences,
)
from scripts.experimental_distillation.run_v222_task_proposal_reranker_oof import (  # noqa: E402
    FOLDS,
    FIELD_REGRESSION_TOLERANCE,
    IDENTITY_GATE,
    FEATURE_DIMENSIONS,
    CrossClauseLogisticRanker,
    _event_matches,
    _feature_map,
    _ledger_matches,
    _load,
    _tokens,
    _write,
)


CEILING_GATE = IDENTITY_GATE
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
STOPWORDS = frozenset("anh chi em task viec phan va cho cac the a an to for".split())


@dataclass(frozen=True)
class UnionProposal:
    proposal_id: str
    case_id: str
    task: dict[str, Any]
    title: str
    assignee: str
    due_date: str
    due_date_text: str
    status: str
    provenance: str
    confidence: float
    context: str
    source_clause_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    origin: str
    bridge_retrieval: str = ""
    bridge_support_count: int = 0
    bridge_cross_clause: bool = False
    cross_source_agreement: bool = False
    source_rank: int = 0
    confidence_rank: int = 0


def _key(task: dict[str, Any]) -> tuple[str, str]:
    return (_normalise(task.get("task_name")), _normalise(task.get("assignee")))


def _meeting_date(repo_root: Path, case_id: str) -> str:
    path = repo_root / "data" / "validation" / case_id / "metadata.json"
    if not path.is_file():
        return "2026-01-01"
    return str(_load(path).get("meeting_date") or "2026-01-01")


def _runtime_occurrences(trace: dict[str, Any]) -> list[Occurrence]:
    """Select one stable, non-negative proposed occurrence per source clause."""

    clauses = {
        str(item.get("clause_id")): str(item.get("text_raw", ""))
        for item in trace.get("clauses", [])
        if isinstance(item, dict) and item.get("clause_id")
    }
    values = [
        item for item in runtime_occurrences(trace)
        if item.state in {"PROPOSED", ""} and not item.negative_signals
    ]
    # The locked PR29 traces predate the V2.17 identity-record field and carry
    # the same runtime spans under action_candidates_v2.  This is still an
    # inference-only source: it contains no expected tasks or gold offsets.
    if not values:
        for record in (trace.get("action_candidates_v2") or {}).get("records", []):
            if not isinstance(record, dict) or str(record.get("state", "")) not in {"PROPOSED", ""}:
                continue
            negatives = tuple(sorted(str(value) for value in record.get("negative_signals", []) if value))
            if negatives:
                continue
            primary = [str(value) for value in record.get("primary_clause_ids", []) if value]
            for span in record.get("action_spans", []):
                if not isinstance(span, dict):
                    continue
                clause_id = str(span.get("clause_id") or (primary[0] if primary else ""))
                try:
                    start, end = int(span.get("start", -1)), int(span.get("end", -1))
                except (TypeError, ValueError):
                    continue
                text = str(span.get("text", ""))
                if clause_id and clause_id in clauses and start >= 0 and end > start and text and clauses[clause_id][start:end] == text:
                    values.append(Occurrence(clause_id, start, end, text, str(record.get("candidate_id", "")), str(record.get("state", "")), negatives))
    chosen: dict[str, Occurrence] = {}
    for value in values:
        previous = chosen.get(value.clause_id)
        if previous is None or (value.end - value.start, -value.start, value.text) > (
            previous.end - previous.start,
            -previous.start,
            previous.text,
        ):
            chosen[value.clause_id] = value
    return sorted(chosen.values(), key=lambda item: (item.clause_id, item.start, item.end, item.text))


def _baseline_rows(case_id: str, trace: dict[str, Any]) -> list[UnionProposal]:
    """Adapt locked final_tasks with runtime provenance, without rebuilding spans."""

    clauses = {
        str(item.get("clause_id")): item
        for item in trace.get("clauses", [])
        if isinstance(item, dict) and item.get("clause_id")
    }
    rows: list[UnionProposal] = []
    for index, raw in enumerate(trace.get("final_tasks", []) or []):
        if not isinstance(raw, dict) or not raw.get("task_name"):
            continue
        task = dict(raw)
        ledger = _ledger_matches(trace, task)
        events = _event_matches(trace, task)
        source_ids: list[str] = []
        event_ids: list[str] = []
        provenance: list[str] = []
        confidences: list[float] = []
        for item in ledger:
            source_ids.extend(str(value) for value in item.get("source_clause_ids", []) if value)
            event_ids.extend(str(value) for value in item.get("event_ids", []) if value)
            provenance.extend(str(value) for value in item.get("extraction_sources", []) if value)
            confidences.append(float(item.get("confidence", 0.0) or 0.0))
        for item in events:
            source_ids.extend(str(value) for value in item.get("source_clause_ids", []) if value)
            if item.get("event_id"):
                event_ids.append(str(item["event_id"]))
            if item.get("extraction_source"):
                provenance.append(str(item["extraction_source"]))
            confidences.append(float(item.get("confidence", 0.0) or 0.0))
        source_ids = list(dict.fromkeys(source_ids))
        event_ids = list(dict.fromkeys(event_ids))
        provenance = list(dict.fromkeys(provenance))
        context = " || ".join(str(clauses[value].get("text_raw", "")) for value in source_ids[:4] if value in clauses)
        if not context:
            context = str(task.get("evidence", ""))
        rows.append(
            UnionProposal(
                proposal_id=f"{case_id}::baseline::{index}",
                case_id=case_id,
                task=task,
                title=str(task.get("task_name", "")),
                assignee=str(task.get("assignee", "")),
                due_date=str(task.get("due_date", "")),
                due_date_text=str(task.get("due_date_text", "")),
                status=str(task.get("status", "")),
                provenance="|".join(provenance) or "unknown",
                confidence=max(confidences, default=0.0),
                context=context,
                source_clause_ids=tuple(source_ids),
                event_ids=tuple(dict.fromkeys(event_ids)),
                origin="baseline",
            )
        )
    return rows


def _bridge_rows(case_id: str, trace: dict[str, Any], meeting_date: str) -> list[UnionProposal]:
    """Build runtime-only V2.17 bridge tasks and retain typed evidence features."""

    clauses = {
        str(item.get("clause_id")): item
        for item in trace.get("clauses", [])
        if isinstance(item, dict) and item.get("clause_id")
    }
    rows: list[UnionProposal] = []
    for index, occurrence in enumerate(_runtime_occurrences(trace)):
        task, evidence = bridge_occurrence_to_task(trace, occurrence, meeting_date=meeting_date)
        if task is None:
            continue
        event = next(
            (
                item for item in trace.get("events_after_deduplication", [])
                if isinstance(item, dict)
                and set(map(str, item.get("source_clause_ids", []))) & set(evidence.support_clause_ids)
            ),
            {},
        )
        source_ids = tuple(dict.fromkeys(str(value) for value in evidence.support_clause_ids if value))
        event_ids = (str(event.get("event_id")),) if event.get("event_id") else ()
        extraction_source = str(event.get("extraction_source", ""))
        context_ids = source_ids or (occurrence.clause_id,)
        context = " || ".join(str(clauses[value].get("text_raw", "")) for value in context_ids[:4] if value in clauses)
        rows.append(
            UnionProposal(
                proposal_id=f"{case_id}::bridge::{index}",
                case_id=case_id,
                task=task,
                title=str(task.get("task_name", "")),
                assignee=str(task.get("assignee", "")),
                due_date=str(task.get("due_date", "")),
                due_date_text=str(task.get("due_date_text", "")),
                status=str(task.get("status", "")),
                provenance="|".join(value for value in ("V217_BRIDGE", evidence.retrieval, extraction_source) if value),
                confidence=float(event.get("confidence", 0.0) or 0.0),
                context=context or occurrence.text,
                source_clause_ids=source_ids or (occurrence.clause_id,),
                event_ids=event_ids,
                origin="bridge",
                bridge_retrieval=evidence.retrieval,
                bridge_support_count=len(source_ids),
                bridge_cross_clause=any(
                    value and value != occurrence.clause_id
                    for value in (source_ids + (evidence.authority_clause_id, evidence.owner_clause_id, evidence.deadline_clause_id))
                ),
            )
        )
    return rows


def _merge_duplicate(left: UnionProposal, right: UnionProposal) -> UnionProposal:
    """Merge same identity deterministically, preserving baseline task values."""

    task = dict(left.task)
    for field in ("assignee", "due_date", "due_date_text", "status", "start_date"):
        if not task.get(field) and right.task.get(field):
            task[field] = right.task[field]
    return UnionProposal(
        **{
            **left.__dict__,
            "task": task,
            "due_date": str(task.get("due_date", "")),
            "due_date_text": str(task.get("due_date_text", "")),
            "status": str(task.get("status", "")),
            "provenance": "|".join(dict.fromkeys((left.provenance + "|" + right.provenance).split("|"))),
            "confidence": max(left.confidence, right.confidence),
            "context": left.context or right.context,
            "source_clause_ids": tuple(dict.fromkeys(left.source_clause_ids + right.source_clause_ids)),
            "event_ids": tuple(dict.fromkeys(left.event_ids + right.event_ids)),
            "origin": "both" if left.origin != right.origin else left.origin,
            "bridge_retrieval": left.bridge_retrieval or right.bridge_retrieval,
            "bridge_support_count": max(left.bridge_support_count, right.bridge_support_count),
            "bridge_cross_clause": left.bridge_cross_clause or right.bridge_cross_clause,
            "cross_source_agreement": left.origin != right.origin or left.cross_source_agreement or right.cross_source_agreement,
        }
    )


def _union_rows(case_id: str, trace: dict[str, Any], meeting_date: str) -> list[UnionProposal]:
    """Create baseline-first, identity-key deduped candidate union."""

    return _dedupe_rows(_baseline_rows(case_id, trace) + _bridge_rows(case_id, trace, meeting_date))


def _dedupe_rows(rows: Sequence[UnionProposal]) -> list[UnionProposal]:
    """Identity-key dedupe for an already materialized runtime candidate pool."""

    result: list[UnionProposal] = []
    by_key: dict[tuple[str, str], int] = {}
    for row in rows:
        key = _key(row.task)
        if not key[0]:
            continue
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = len(result)
            result.append(row)
        else:
            result[previous] = _merge_duplicate(result[previous], row)
    return _rank_fields(result)


def _rank_fields(rows: Sequence[UnionProposal]) -> list[UnionProposal]:
    ranked = sorted(rows, key=lambda item: (-item.confidence, item.proposal_id))
    ranks = {item.proposal_id: index + 1 for index, item in enumerate(ranked)}
    return [
        UnionProposal(**{**item.__dict__, "source_rank": index + 1, "confidence_rank": ranks[item.proposal_id]})
        for index, item in enumerate(sorted(rows, key=lambda item: item.proposal_id))
    ]


def _features(proposal: UnionProposal, pool: Sequence[UnionProposal]) -> dict[int, float]:
    values: dict[str, float | str] = {
        "title": proposal.title,
        "assignee": proposal.assignee,
        "due_date": proposal.due_date,
        "due_date_text": proposal.due_date_text,
        "status": proposal.status,
        "provenance": proposal.provenance,
        "origin": proposal.origin,
        "context": proposal.context,
        "bridge_retrieval": proposal.bridge_retrieval,
        "confidence": min(1.0, max(0.0, proposal.confidence)),
        "bridge_support_count": min(8, proposal.bridge_support_count) / 8.0,
        "bridge_cross_clause": float(proposal.bridge_cross_clause),
        "cross_source_agreement": float(proposal.cross_source_agreement),
        "source_clause_count": min(8, len(proposal.source_clause_ids)) / 8.0,
        "event_count": min(8, len(proposal.event_ids)) / 8.0,
        "source_rank": 1.0 / max(1, proposal.source_rank),
        "confidence_rank": 1.0 / max(1, proposal.confidence_rank),
        "pool_size": min(32, len(pool)) / 32.0,
        "has_assignee": float(bool(proposal.assignee)),
        "has_due_date": float(bool(proposal.due_date)),
        "has_due_date_text": float(bool(proposal.due_date_text)),
        "task_key_present": float(bool(proposal.task.get("task_key"))),
    }
    result = _feature_map(values)
    tokens = [token for token in _tokens(proposal.title) if token not in STOPWORDS]
    for size in (1, 2, 3):
        for start in range(max(0, len(tokens) - size + 1)):
            index = int.from_bytes(hashlib.blake2b(("union-ng=" + " ".join(tokens[start:start + size])).encode("utf-8"), digest_size=4).digest(), "little") % FEATURE_DIMENSIONS
            result[index] = result.get(index, 0.0) + 1.0
    return result


def _labels(expected: dict[str, Any], proposals: Sequence[UnionProposal]) -> list[int]:
    pairs = _maximum_weight_matching(list(expected.get("tasks", [])), [item.task for item in proposals])
    return [int(any(actual == index for _expected, actual in pairs)) for index in range(len(proposals))]


def _oracle_selected(expected: dict[str, Any], proposals: Sequence[UnionProposal]) -> list[UnionProposal]:
    """Return the candidate pool's perfect-selector positives.

    Matching is performed independently for each meeting against its expected
    tasks.  This is a coverage diagnostic only: expected tasks never enter the
    runtime candidate pool or any OOF feature vector.
    """

    labels = _labels(expected, proposals)
    return [proposal for proposal, label in zip(proposals, labels, strict=True) if label]


def _metrics(repo_root: Path, case_ids: Sequence[str], selected: dict[str, list[UnionProposal]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return aggregate_results([
        compare_case(case_id, expected[case_id], {"tasks": [item.task for item in selected.get(case_id, [])]})
        for case_id in sorted(case_ids)
    ])


def _policy_select(proposals: Sequence[UnionProposal], scores: Sequence[float], threshold: float, budget: int | None) -> list[UnionProposal]:
    ranked = sorted(zip(proposals, scores), key=lambda item: (-item[1], item[0].proposal_id))
    ranked = [item for item in ranked if item[1] >= threshold]
    if budget is not None:
        ranked = ranked[:budget]
    return [item[0] for item in ranked]


def _tune_policy(
    train_ids: Sequence[str], proposals: dict[str, list[UnionProposal]], expected: dict[str, dict[str, Any]],
    ranker: CrossClauseLogisticRanker, baseline: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_scores = {case_id: [ranker.probability(_features(item, proposals[case_id])) for item in proposals[case_id]] for case_id in train_ids}
    best_policy: dict[str, Any] | None = None
    best_metrics: dict[str, Any] | None = None
    best_key: tuple[float, ...] | None = None
    field_floor = float(baseline["field_accuracy"]) - FIELD_REGRESSION_TOLERANCE
    for bridge_weight in (0.0, 0.10, 0.20, 0.35, 0.50, 0.75, 1.0):
        scores = {
            case_id: [
                score * (bridge_weight if item.origin == "bridge" else 1.0)
                for item, score in zip(proposals[case_id], raw_scores[case_id], strict=True)
            ]
            for case_id in train_ids
        }
        for threshold in (0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80):
            for budget in (None, 1, 2, 3, 4, 5, 6):
                selected = {case_id: _policy_select(proposals[case_id], scores[case_id], threshold, budget) for case_id in train_ids}
                metrics = _metrics(_REPO_ROOT, train_ids, selected, expected)
                field_ok = float(metrics["field_accuracy"]) >= field_floor
                key = (float(field_ok), float(metrics["task_identity_f1"]), float(metrics["task_identity_precision"]), float(metrics["field_accuracy"]), -float(metrics["actual_task_count"]))
                if best_key is None or key > best_key:
                    best_key, best_policy, best_metrics = key, {"threshold": threshold, "meeting_budget": budget, "bridge_weight": bridge_weight}, metrics
    assert best_policy is not None and best_metrics is not None
    return best_policy, {"metrics": best_metrics, "candidate_count": 343, "field_floor": field_floor}


def _oracle_ceiling(repo_root: Path, case_ids: Sequence[str], proposals: dict[str, list[UnionProposal]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Score the perfect selector over fold-local Hungarian-matchable labels."""

    selected = {
        case_id: _oracle_selected(expected[case_id], proposals[case_id])
        for case_id in case_ids
    }
    metrics = _metrics(repo_root, case_ids, selected, expected)
    return {
        "definition": "perfect selector over fold-local, meeting-local Hungarian-positive candidates",
        "label_scope": "one Hungarian matching per meeting; labels are recomputed inside each TRAIN34 fit/validation fold",
        "expected_tasks": metrics["expected_task_count"],
        "union_candidates": sum(len(proposals[case_id]) for case_id in case_ids),
        "matchable_candidates": metrics["actual_task_count"],
        "actual_task_count": metrics["actual_task_count"],
        "identity_matches": metrics["matched_task_count"],
        "identity_precision": metrics["task_identity_precision"],
        "identity_recall": metrics["task_identity_recall"],
        "identity_f1": metrics["task_identity_f1"],
        "field_accuracy": metrics["field_accuracy"],
        "passed": metrics["task_identity_f1"] >= CEILING_GATE,
    }


def _source_reports(repo_root: Path, train_ids: Sequence[str], baseline: dict[str, list[UnionProposal]], bridge: dict[str, list[UnionProposal]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Score each source before union dedupe, then leave union metrics separate."""

    return {
        "baseline": _metrics(repo_root, train_ids, baseline, expected),
        "bridge": _metrics(repo_root, train_ids, bridge, expected),
    }


def run(root: Path, output: Path, *, split_path: Path | None = None, repo_root: Path | None = None) -> dict[str, Any]:
    root, output, repo_root = root.resolve(), output.resolve(), (repo_root or _REPO_ROOT).resolve()
    split_path = (split_path or repo_root / "evaluation/runtime/experimental-distillation-v2/kaggle/v221-neural-meeting-ranker-smoke-01-repair-v3/dataset/snapshot/v29-split.json").resolve()
    split = _load(split_path)
    train_ids = sorted(str(value) for value in split.get("train_case_ids", []))
    if len(train_ids) != 34 or any(split.get(key) for key in ("calibration_opened", "diagnostic_opened", "final_dev_opened", "outer_validation_opened")):
        raise ValueError("STOP_TRAIN34_SCOPE")
    trace_root = root / "evaluation/runtime/pr29-q2-traces"
    traces = {case_id: _load(next(trace_root.glob(f"{case_id}-v1-*.json"))) for case_id in train_ids}
    expected = {case_id: _load(repo_root / "data/validation" / case_id / "expected_output.json") for case_id in train_ids}
    baseline_rows = {case_id: _baseline_rows(case_id, traces[case_id]) for case_id in train_ids}
    bridge_rows = {case_id: _bridge_rows(case_id, traces[case_id], _meeting_date(repo_root, case_id)) for case_id in train_ids}
    proposals = {case_id: _dedupe_rows(baseline_rows[case_id] + bridge_rows[case_id]) for case_id in train_ids}
    source_metrics = _source_reports(repo_root, train_ids, baseline_rows, bridge_rows, expected)
    union_baseline = _metrics(repo_root, train_ids, proposals, expected)
    ceiling = _oracle_ceiling(repo_root, train_ids, proposals, expected)
    base_scope = {"train_meetings": 34, "folds": FOLDS, "calibration_opened": False, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0}
    if not ceiling["passed"]:
        report = {"schema_version": "v223-train34-union-proposal-oof-v1", "status": "STOP_CEILING", "scope": {**base_scope, "training_performed": False, "model_fit_count": 0}, "sources": source_metrics, "union_baseline": union_baseline, "oracle_ceiling": ceiling}
        _write(output / "coverage-ceiling.json", ceiling); _write(output / "metrics.json", report); _write(output / "status.json", {"schema_version": "v223-status-v1", "status": "STOP_CEILING", "training_performed": False, "model_fit_count": 0, "metrics_persisted": True})
        md = "# V2.23 TRAIN34 union proposal OOF\n\n"
        md += f"STOP_CEILING: the deduped union Hungarian identity ceiling is **{ceiling['identity_f1']:.4f}** (P **{ceiling['identity_precision']:.4f}**, R **{ceiling['identity_recall']:.4f}**, field accuracy **{ceiling['field_accuracy']:.4f}**) from **{ceiling['actual_task_count']}** candidates, below {CEILING_GATE:.2f}; no model was fit.\n\n"
        md += "| Source | Candidates | Matched | P | R | F1 | Field accuracy |\n|---|---:|---:|---:|---:|---:|---:|\n"
        for source, metrics in source_metrics.items():
            md += f"| {source} | {metrics['actual_task_count']} | {metrics['matched_task_count']} | {metrics['task_identity_precision']:.4f} | {metrics['task_identity_recall']:.4f} | {metrics['task_identity_f1']:.4f} | {metrics['field_accuracy']:.4f} |\n"
        (output / "report.md").write_text(md, encoding="utf-8")
        return report

    fold_map = {case_id: index % FOLDS for index, case_id in enumerate(train_ids)}
    folds: list[dict[str, Any]] = []
    reranked_selected: dict[str, list[UnionProposal]] = {}
    for fold in range(FOLDS):
        valid_ids = [case_id for case_id in train_ids if fold_map[case_id] == fold]
        fit_ids = [case_id for case_id in train_ids if case_id not in valid_ids]
        groups = []
        for case_id in fit_ids:
            labels = _labels(expected[case_id], proposals[case_id])
            groups.append([(_features(item, proposals[case_id]), label) for item, label in zip(proposals[case_id], labels, strict=True)])
        ranker = CrossClauseLogisticRanker().fit(groups, epochs=1, learning_rate=0.15)
        fit_baseline = _metrics(repo_root, fit_ids, proposals, expected)
        policy, fit_report = _tune_policy(fit_ids, proposals, expected, ranker, fit_baseline)
        valid_scores = {
            case_id: [
                ranker.probability(_features(item, proposals[case_id]))
                * (float(policy["bridge_weight"]) if item.origin == "bridge" else 1.0)
                for item in proposals[case_id]
            ]
            for case_id in valid_ids
        }
        selected = {case_id: _policy_select(proposals[case_id], valid_scores[case_id], policy["threshold"], policy["meeting_budget"]) for case_id in valid_ids}
        reranked_selected.update(selected)
        folds.append({"fold": fold, "fit_meetings": len(fit_ids), "valid_meetings": len(valid_ids), "valid_case_ids": valid_ids, "policy": policy, "fit": fit_report, "union_baseline": _metrics(repo_root, valid_ids, {case_id: proposals[case_id] for case_id in valid_ids}, expected), "reranked": _metrics(repo_root, valid_ids, selected, expected)})

    reranked = _metrics(repo_root, train_ids, reranked_selected, expected)
    field_ok = reranked["field_accuracy"] >= union_baseline["field_accuracy"] - FIELD_REGRESSION_TOLERANCE
    identity_gate = {"threshold": IDENTITY_GATE, "value": reranked["task_identity_f1"], "passed": reranked["task_identity_f1"] >= IDENTITY_GATE}
    report = {
        "schema_version": "v223-train34-union-proposal-oof-v1", "status": "complete",
        "scope": {**base_scope, "training_performed": True, "model_fit_count": FOLDS},
        "method": {"candidate_sources": ["locked baseline final_tasks", "inference-valid V2.17 cross-clause bridge tasks"], "dedupe": "normalized task_name + assignee, baseline-first deterministic merge; no gold", "features": ["source/provenance", "task fields", "confidence", "bridge evidence", "cross-source agreement", "meeting-relative ranks"], "labels": "fold-local Hungarian identity matches", "model": "CrossClauseLogisticRanker sparse hashed features", "epochs_per_fold": 1, "threshold_tuning": "fit meetings only"},
        "sources": source_metrics, "union_baseline": union_baseline, "oracle_ceiling": ceiling, "reranked": reranked, "folds": folds,
        "gates": {"coverage_ceiling": ceiling["passed"], "task_identity_f1": identity_gate, "field_accuracy_no_material_regression": {"baseline": union_baseline["field_accuracy"], "value": reranked["field_accuracy"], "tolerance": FIELD_REGRESSION_TOLERANCE, "passed": field_ok}},
        "recommendation": {"readiness": "PROMISING" if identity_gate["passed"] and field_ok else "NOT_READY", "reason": "OOF identity gate and field regression gate"},
    }
    _write(output / "coverage-ceiling.json", ceiling); _write(output / "metrics.json", report); _write(output / "fold-policies.json", {"schema_version": "v223-fold-policies-v1", "folds": [{"fold": item["fold"], "valid_meetings": item["valid_meetings"], "policy": item["policy"]} for item in folds]}); _write(output / "status.json", {"schema_version": "v223-status-v1", "status": "complete", "training_performed": True, "model_fit_count": FOLDS, "metrics_persisted": True, "calibration_opened": False, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0})
    md = "# V2.23 TRAIN34 union proposal OOF\n\nFive-fold meeting-group OOF over locked baseline `final_tasks` unioned with inference-valid V2.17 bridge tasks. Candidate construction and dedupe are gold-free.\n\n"
    md += f"Oracle ceiling (perfect selector over fold-local, meeting-local Hungarian-positive candidates): **{ceiling['identity_matches']}/{ceiling['expected_tasks']}** matched, recall **{ceiling['identity_recall']:.4f}**, F1 **{ceiling['identity_f1']:.4f}** from **{ceiling['matchable_candidates']}** selectable candidates. Union baseline: **{union_baseline['actual_task_count']}** tasks, P **{union_baseline['task_identity_precision']:.4f}**, R **{union_baseline['task_identity_recall']:.4f}**, F1 **{union_baseline['task_identity_f1']:.4f}**, field accuracy **{union_baseline['field_accuracy']:.4f}**.\n\n"
    md += f"Reranked OOF: **{reranked['actual_task_count']}** tasks, P **{reranked['task_identity_precision']:.4f}**, R **{reranked['task_identity_recall']:.4f}**, F1 **{reranked['task_identity_f1']:.4f}**, field accuracy **{reranked['field_accuracy']:.4f}**. Gate >= {IDENTITY_GATE:.2f}: **{'PASS' if identity_gate['passed'] else 'FAIL'}**; field regression: **{'PASS' if field_ok else 'FAIL'}**. Recommendation: **{report['recommendation']['readiness']}**.\n\n"
    md += "| Source | Candidates | Matched | P | R | F1 | Field accuracy |\n|---|---:|---:|---:|---:|---:|---:|\n"
    for source, metrics in source_metrics.items():
        md += f"| {source} | {metrics['actual_task_count']} | {metrics['matched_task_count']} | {metrics['task_identity_precision']:.4f} | {metrics['task_identity_recall']:.4f} | {metrics['task_identity_f1']:.4f} | {metrics['field_accuracy']:.4f} |\n"
    (output / "report.md").write_text(md, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", type=Path)
    args = parser.parse_args()
    try:
        report = run(args.root, args.output, split_path=args.split, repo_root=args.root)
    except BaseException as error:
        args.output.mkdir(parents=True, exist_ok=True)
        _write(args.output / "status.json", {"schema_version": "v223-status-v1", "status": "STOP", "error": repr(error), "training_performed": False, "metrics_persisted": False})
        print(json.dumps({"status": "STOP", "error": repr(error)}, sort_keys=True))
        return 2
    print(json.dumps({"status": report["status"], "union_baseline_f1": report["union_baseline"]["task_identity_f1"], "reranked_f1": report.get("reranked", {}).get("task_identity_f1"), "readiness": report.get("recommendation", {}).get("readiness")}, sort_keys=True))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
