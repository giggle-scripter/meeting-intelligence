"""V2.26 DEV42 expanded proposal reranker with template/family holdout OOF.

The candidate generator is frozen to the V2.25 eligible expansion:
``final_plus_bridge_plus_intermediate``.  Candidate construction uses only
locked PR29 runtime traces.  Expected outputs are read only for fold-local
Hungarian identity labels and scoring.  Each template is a validation fold,
which also holds out every nested domain/template family in that fold.

No diagnostic, final-dev, outer-validation, teacher, provider, or Kaggle
inputs are opened by this runner.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.evaluation import aggregate_results, compare_case  # noqa: E402
from scripts.experimental_distillation.audit_v225_upstream_candidates import (  # noqa: E402
    RuntimeRow,
    family_keys,
    load as _load_v225,
    merge_rows,
    rows_for_stage,
)
from scripts.experimental_distillation.run_v222_task_proposal_reranker_oof import (  # noqa: E402
    CrossClauseLogisticRanker,
    FIELD_REGRESSION_TOLERANCE,
    FEATURE_DIMENSIONS,
    IDENTITY_GATE,
    _maximum_weight_matching,
    _feature_map,
    _tokens,
)
from scripts.experimental_distillation.run_v223_union_proposal_oof import (  # noqa: E402
    UnionProposal,
    _features,
    _policy_select,
)


DEFAULT_SPLIT = Path(
    "evaluation/runtime/experimental-distillation-v2/kaggle/"
    "v221-neural-meeting-ranker-smoke-01-repair-v3/dataset/snapshot/v29-split.json"
)
DEFAULT_OUTPUT = Path("evaluation/runtime/experimental-distillation-v2/v226-dev42-template-family-holdout")
TRACE_ROOT = Path("evaluation/runtime/pr29-q2-traces")
EPOCHS = 1
LEARNING_RATE = 0.15
WORST_FOLD_F1_FLOOR = 0.25


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compact(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "case_count", "expected_task_count", "actual_task_count", "matched_task_count",
        "unexpected_task_count", "missing_task_count", "task_identity_precision",
        "task_identity_recall", "task_identity_f1", "field_accuracy", "assignee_accuracy",
        "due_date_accuracy", "due_date_text_exact_accuracy", "state_link_accuracy",
    )
    return {key: metrics.get(key) for key in keys}


def _row_to_proposal(row: RuntimeRow) -> UnionProposal:
    """Adapt a V2.25 runtime row without consulting expected output."""

    task = dict(row.task)
    source = row.source
    origin = {"accepted_final": "baseline", "occurrence_bridge": "bridge", "intermediate_state": "intermediate"}.get(source, source)
    provenance = "|".join(row.provenance) or source
    return UnionProposal(
        proposal_id=f"{row.case_id}::{source}::{row.ordinal}",
        case_id=row.case_id,
        task=task,
        title=str(task.get("task_name", "")),
        assignee=str(task.get("assignee", "")),
        due_date=str(task.get("due_date", "")),
        due_date_text=str(task.get("due_date_text", "")),
        status=str(task.get("status", "")),
        provenance=provenance,
        confidence=float(row.quality[0]),
        context="",
        source_clause_ids=(),
        event_ids=(),
        origin=origin,
    )


def _expanded_rows(case_id: str, trace: dict[str, Any], repo_root: Path) -> list[UnionProposal]:
    """Build the frozen, baseline-first V2.25 eligible expansion."""

    stages = rows_for_stage(case_id, trace, repo_root)
    # merge_rows applies V2.25's stable source priority and identity-only
    # dedupe.  In particular, it does not inspect expected output.
    runtime = merge_rows(
        stages.get("accepted_final", [])
        + stages.get("occurrence_bridge", [])
        + stages.get("intermediate_state", [])
    )
    proposals = [_row_to_proposal(row) for row in runtime]
    # Keep V2.23's deterministic confidence/proposal ranks for features.
    ranked = sorted(proposals, key=lambda item: item.proposal_id)
    confidence_order = sorted(ranked, key=lambda item: (-item.confidence, item.proposal_id))
    ranks = {item.proposal_id: index + 1 for index, item in enumerate(confidence_order)}
    return [
        UnionProposal(**{**item.__dict__, "source_rank": index + 1, "confidence_rank": ranks[item.proposal_id]})
        for index, item in enumerate(ranked)
    ]


def _metrics(root: Path, case_ids: Sequence[str], selected: dict[str, list[UnionProposal]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return aggregate_results([
        compare_case(case_id, expected[case_id], {"tasks": [item.task for item in selected.get(case_id, [])]})
        for case_id in sorted(case_ids)
    ])


def _labels(expected: dict[str, Any], proposals: Sequence[UnionProposal]) -> list[int]:
    pairs = _maximum_weight_matching(list(expected.get("tasks", [])), [item.task for item in proposals])
    return [int(any(actual == index for _gold, actual in pairs)) for index in range(len(proposals))]


def _oracle_ceiling(root: Path, case_ids: Sequence[str], proposals: dict[str, list[UnionProposal]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    selected = {
        case_id: [item for item, label in zip(proposals[case_id], _labels(expected[case_id], proposals[case_id]), strict=True) if label]
        for case_id in case_ids
    }
    metrics = _metrics(root, case_ids, selected, expected)
    return {
        "definition": "perfect selector over expanded runtime candidates with one meeting-local Hungarian match",
        "candidate_source": "final_plus_bridge_plus_intermediate",
        "expected_task_count": metrics["expected_task_count"],
        "candidate_count": sum(len(proposals[case_id]) for case_id in case_ids),
        "matchable_candidates": metrics["actual_task_count"],
        "identity_matches": metrics["matched_task_count"],
        "identity_precision": metrics["task_identity_precision"],
        "identity_recall": metrics["task_identity_recall"],
        "identity_f1": metrics["task_identity_f1"],
        "field_accuracy": metrics["field_accuracy"],
        "passed_gate": float(metrics["task_identity_f1"]) >= IDENTITY_GATE,
    }


def _source_metrics(root: Path, case_ids: Sequence[str], proposals: dict[str, list[UnionProposal]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "expanded_pool": _metrics(root, case_ids, proposals, expected),
        "baseline_only": _metrics(root, case_ids, {cid: [x for x in proposals[cid] if x.origin == "baseline"] for cid in case_ids}, expected),
        "bridge_only": _metrics(root, case_ids, {cid: [x for x in proposals[cid] if x.origin == "bridge"] for cid in case_ids}, expected),
        "intermediate_only": _metrics(root, case_ids, {cid: [x for x in proposals[cid] if x.origin == "intermediate"] for cid in case_ids}, expected),
    }


def _fit_policy(root: Path, fit_ids: Sequence[str], proposals: dict[str, list[UnionProposal]], expected: dict[str, dict[str, Any]], ranker: CrossClauseLogisticRanker) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline = _metrics(root, fit_ids, {cid: proposals[cid] for cid in fit_ids}, expected)
    field_floor = float(baseline["field_accuracy"]) - FIELD_REGRESSION_TOLERANCE
    raw_scores = {cid: [ranker.probability(_features(item, proposals[cid])) for item in proposals[cid]] for cid in fit_ids}
    best: tuple[tuple[float, ...], dict[str, Any], dict[str, Any]] | None = None
    tried = 0
    # Small, deterministic policy grid. Every dimension is selected from fit
    # meetings only; intermediate gets its own weight because it is the V2.26
    # expansion source.
    for bridge_weight in (0.50, 0.75, 1.0):
        for intermediate_weight in (0.50, 0.75, 1.0):
            for threshold in (0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80):
                for budget in (None, 1, 2, 3, 4, 5, 6):
                    tried += 1
                    scores = {
                        cid: [
                            score * (bridge_weight if item.origin == "bridge" else intermediate_weight if item.origin == "intermediate" else 1.0)
                            for item, score in zip(proposals[cid], raw_scores[cid], strict=True)
                        ]
                        for cid in fit_ids
                    }
                    selected = {cid: _policy_select(proposals[cid], scores[cid], threshold, budget) for cid in fit_ids}
                    metrics = _metrics(root, fit_ids, selected, expected)
                    field_ok = float(metrics["field_accuracy"]) >= field_floor
                    key = (
                        float(field_ok), float(metrics["task_identity_f1"]), float(metrics["task_identity_precision"]),
                        float(metrics["field_accuracy"]), -float(metrics["actual_task_count"]),
                        -float(bridge_weight), -float(intermediate_weight), -float(threshold),
                    )
                    candidate = (key, {"threshold": threshold, "meeting_budget": budget, "bridge_weight": bridge_weight, "intermediate_weight": intermediate_weight}, metrics)
                    if best is None or key > best[0]:
                        best = candidate
    assert best is not None
    return best[1], {"metrics": best[2], "candidate_count": tried, "field_floor": field_floor}


def _score(ranker: CrossClauseLogisticRanker, case_ids: Sequence[str], proposals: dict[str, list[UnionProposal]], policy: dict[str, Any]) -> dict[str, list[UnionProposal]]:
    selected: dict[str, list[UnionProposal]] = {}
    for cid in case_ids:
        scores = [
            ranker.probability(_features(item, proposals[cid]))
            * (float(policy["bridge_weight"]) if item.origin == "bridge" else float(policy["intermediate_weight"]) if item.origin == "intermediate" else 1.0)
            for item in proposals[cid]
        ]
        selected[cid] = _policy_select(proposals[cid], scores, float(policy["threshold"]), policy["meeting_budget"])
    return selected


def _groups(case_ids: Sequence[str], key: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for cid in case_ids:
        grouped[family_keys(cid)[key]].append(cid)
    return {name: sorted(values) for name, values in sorted(grouped.items())}


def _stability(folds: Sequence[dict[str, Any]]) -> dict[str, Any]:
    values = [float(item["reranked"]["task_identity_f1"]) for item in folds]
    return {
        "fold_count": len(values),
        "macro_f1": sum(values) / len(values) if values else 0.0,
        "worst_fold_f1": min(values) if values else 0.0,
        "best_fold_f1": max(values) if values else 0.0,
        "spread": (max(values) - min(values)) if values else 0.0,
        "worst_fold": min(folds, key=lambda item: (float(item["reranked"]["task_identity_f1"]), item["fold"]))["fold"] if folds else None,
    }


def run(root: Path, output: Path, *, split_path: Path | None = None) -> dict[str, Any]:
    root, output = root.resolve(), output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"immutable output already exists: {output}")
    split_path = (split_path or root / DEFAULT_SPLIT).resolve()
    split = _load(split_path)
    development = sorted(str(x) for x in split.get("train_case_ids", []) + split.get("calibration_case_ids", []))
    diagnostic = sorted(str(x) for x in split.get("diagnostic_case_ids", []))
    if len(development) != 42 or len(set(development)) != 42 or len(diagnostic) != 9 or set(development) & set(diagnostic):
        raise ValueError("STOP_DEV42_SCOPE")
    if split.get("final_dev_opened") or split.get("outer_validation_opened"):
        raise ValueError("STOP_CLOSED_SPLIT")

    trace_root = (root / TRACE_ROOT).resolve()
    traces: dict[str, dict[str, Any]] = {}
    expected: dict[str, dict[str, Any]] = {}
    trace_paths: dict[str, Path] = {}
    expected_paths: dict[str, Path] = {}
    for cid in development:
        paths = sorted(trace_root.glob(f"{cid}-v1-*.json"))
        if len(paths) != 1:
            raise ValueError(f"STOP_TRACE_INVENTORY:{cid}:{len(paths)}")
        trace_paths[cid] = paths[0]
        traces[cid] = _load(paths[0])
        expected_paths[cid] = (root / "data/validation" / cid / "expected_output.json").resolve()
        expected[cid] = _load(expected_paths[cid])
    proposals = {cid: _expanded_rows(cid, traces[cid], root) for cid in development}
    volume = {
        "candidate_count": sum(len(values) for values in proposals.values()),
        "mean_per_meeting": sum(len(values) for values in proposals.values()) / len(development),
        "min_per_meeting": min(len(values) for values in proposals.values()),
        "max_per_meeting": max(len(values) for values in proposals.values()),
    }
    ceiling = _oracle_ceiling(root, development, proposals, expected)
    expanded_baseline = _metrics(root, development, proposals, expected)
    source_metrics = _source_metrics(root, development, proposals, expected)
    if not ceiling["passed_gate"]:
        report = {"schema_version": "v226-dev42-template-family-holdout-v1", "status": "STOP_CEILING", "scope": {"development_meetings": 42, "folds": 0, "training_performed": False, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0}, "candidate_ceiling": ceiling, "volume": volume, "expanded_pool_baseline": _compact(expanded_baseline), "source_metrics": {key: _compact(value) for key, value in source_metrics.items()}}
        _write(output / "metrics.json", report)
        _write(output / "status.json", {"schema_version": "v226-status-v1", "status": "STOP_CEILING", "training_performed": False, "metrics_persisted": True})
        return report

    # Leave-template-out is deterministic and stronger than random meeting
    # folds: all families nested under the heldout template are unseen in fit.
    template_groups = _groups(development, "template")
    family_groups = _groups(development, "family")
    folds: list[dict[str, Any]] = []
    oof_selected: dict[str, list[UnionProposal]] = {}
    for index, (template, valid_ids) in enumerate(template_groups.items()):
        valid_families = sorted({family_keys(cid)["family"] for cid in valid_ids})
        fit_ids = [cid for cid in development if cid not in set(valid_ids)]
        fit_families = {family_keys(cid)["family"] for cid in fit_ids}
        if set(valid_families) & fit_families:
            raise ValueError("STOP_FAMILY_LEAKAGE")
        groups = []
        for cid in fit_ids:
            labels = _labels(expected[cid], proposals[cid])
            groups.append([(_features(item, proposals[cid]), label) for item, label in zip(proposals[cid], labels, strict=True)])
        ranker = CrossClauseLogisticRanker().fit(groups, epochs=EPOCHS, learning_rate=LEARNING_RATE)
        policy, tuning = _fit_policy(root, fit_ids, proposals, expected, ranker)
        selected = _score(ranker, valid_ids, proposals, policy)
        oof_selected.update(selected)
        folds.append({
            "fold": index, "holdout_template": template, "fit_meetings": len(fit_ids), "valid_meetings": len(valid_ids),
            "fit_case_ids": fit_ids, "valid_case_ids": valid_ids, "fit_families": sorted(fit_families), "valid_families": valid_families,
            "policy": policy, "tuning": {"metrics": _compact(tuning["metrics"]), "candidate_count": tuning["candidate_count"], "field_floor": tuning["field_floor"]},
            "expanded_pool_baseline": _compact(_metrics(root, valid_ids, {cid: proposals[cid] for cid in valid_ids}, expected)),
            "reranked": _compact(_metrics(root, valid_ids, selected, expected)),
        })

    reranked = _metrics(root, development, oof_selected, expected)
    stability = _stability(folds)
    family_stability = _family_metrics(root, development, oof_selected, expected, family_groups)
    baseline_f1 = float(expanded_baseline["task_identity_f1"])
    field_ok = float(reranked["field_accuracy"]) >= float(expanded_baseline["field_accuracy"]) - FIELD_REGRESSION_TOLERANCE
    identity_ok = float(reranked["task_identity_f1"]) >= IDENTITY_GATE
    worst_ok = float(family_stability["worst_f1"]) >= WORST_FOLD_F1_FLOOR
    report = {
        "schema_version": "v226-dev42-template-family-holdout-v1", "status": "complete",
        "scope": {"development_meetings": 42, "train_meetings": 34, "calibration_meetings": 8, "folds": len(folds), "fold_unit": "template", "training_performed": True, "model_fit_count": len(folds), "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0, "execution": "local_cpu"},
        "method": {"candidate_source": "final_plus_bridge_plus_intermediate", "candidate_generator": "V2.25 rows_for_stage + merge_rows", "dedupe": "normalized task_name + assignee, deterministic V2.25 priority", "labels": "one fold-local meeting-local Hungarian identity match", "model": "CrossClauseLogisticRanker sparse hashed features", "feature_dimensions": FEATURE_DIMENSIONS, "epochs_per_fit": EPOCHS, "learning_rate": LEARNING_RATE, "fold_assignment": "sorted template keys; all nested families held out", "policy_tuning": "fit meetings only; threshold, budget, bridge weight, intermediate weight"},
        "candidate_ceiling": ceiling, "volume": volume, "source_metrics": {key: _compact(value) for key, value in source_metrics.items()}, "expanded_pool_baseline": _compact(expanded_baseline), "reranked_oof": _compact(reranked),
        "stability": {"macro": stability, "aggregate": {"identity_f1": reranked["task_identity_f1"], "field_accuracy": reranked["field_accuracy"]}, "family_count": len(family_groups), "family_metrics": family_stability},
        "folds": folds,
        "gates": {"aggregate_identity_f1": {"threshold": IDENTITY_GATE, "value": reranked["task_identity_f1"], "passed": identity_ok}, "worst_family_f1_defensible": {"threshold": WORST_FOLD_F1_FLOOR, "value": family_stability["worst_f1"], "family": family_stability["worst_family"], "passed": worst_ok}, "field_accuracy_no_material_regression": {"baseline": expanded_baseline["field_accuracy"], "value": reranked["field_accuracy"], "tolerance": FIELD_REGRESSION_TOLERANCE, "passed": field_ok}, "passed": identity_ok and worst_ok and field_ok},
        "split_access": {"development_case_ids": development, "diagnostic_case_ids_read": [], "final_dev_case_ids_read": [], "outer_validation_case_ids_read": [], "template_count": len(template_groups), "family_count": len(family_groups)},
        "source_hashes": {"split": {"path": str(split_path), "sha256": _sha256(split_path)}, "runner": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__).resolve())}, "v225_source": {"path": str((_REPO_ROOT / "scripts/experimental_distillation/audit_v225_upstream_candidates.py").resolve()), "sha256": _sha256((_REPO_ROOT / "scripts/experimental_distillation/audit_v225_upstream_candidates.py").resolve())}, "traces": {cid: {"path": str(trace_paths[cid]), "sha256": _sha256(trace_paths[cid])} for cid in development}, "expected_outputs": {cid: {"path": str(expected_paths[cid]), "sha256": _sha256(expected_paths[cid])} for cid in development}},
        "leakage_audit": {"gold_features": False, "labels_used_for_candidate_construction": False, "diagnostic_labels_used_for_fit": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0},
        "recommendation": "PROMISING" if identity_ok and worst_ok and field_ok else "NOT_READY",
    }
    _write(output / "metrics.json", report)
    _write(output / "coverage-ceiling.json", ceiling)
    _write(output / "fold-policies.json", {"schema_version": "v226-fold-policies-v1", "fold_unit": "template", "folds": [{"fold": x["fold"], "holdout_template": x["holdout_template"], "valid_families": x["valid_families"], "policy": x["policy"]} for x in folds]})
    _write(output / "split-access-audit.json", report["split_access"])
    _write(output / "status.json", {"schema_version": "v226-status-v1", "status": "complete", "training_performed": True, "model_fit_count": len(folds), "metrics_persisted": True, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0})
    lines = ["# V2.26 DEV42 template/family holdout expanded proposal reranker", "", "The frozen `final_plus_bridge_plus_intermediate` runtime candidate pool is evaluated with one fold-local cheap ranker per template. Every nested domain/template family is absent from that fold's fit set. Diagnostic9, final-dev, outer labels, teacher/provider calls, and Kaggle remain closed.", "", f"Candidate ceiling: **{ceiling['identity_matches']}/{ceiling['expected_task_count']}** matched, P **{ceiling['identity_precision']:.4f}**, R **{ceiling['identity_recall']:.4f}**, F1 **{ceiling['identity_f1']:.4f}**, field accuracy **{ceiling['field_accuracy']:.4f}** from **{volume['candidate_count']}** candidates ({volume['mean_per_meeting']:.2f}/meeting).", "", f"Expanded pool baseline: P **{expanded_baseline['task_identity_precision']:.4f}**, R **{expanded_baseline['task_identity_recall']:.4f}**, F1 **{baseline_f1:.4f}**, field accuracy **{expanded_baseline['field_accuracy']:.4f}**.", "", f"Template holdout reranked OOF: P **{reranked['task_identity_precision']:.4f}**, R **{reranked['task_identity_recall']:.4f}**, F1 **{reranked['task_identity_f1']:.4f}**, field accuracy **{reranked['field_accuracy']:.4f}**. Macro fold F1 **{stability['macro_f1']:.4f}**, worst fold **{stability['worst_fold_f1']:.4f}** (fold {stability['worst_fold']}), worst family **{family_stability['worst_f1']:.4f}** ({family_stability['worst_family']}), aggregate gate **{'PASS' if identity_ok else 'FAIL'}**, worst-family stability **{'PASS' if worst_ok else 'FAIL'}**, field regression **{'PASS' if field_ok else 'FAIL'}**.", "", "| Fold | Heldout template | Families | Baseline F1 | Reranked F1 | Field accuracy |", "|---:|---|---:|---:|---:|---:|"]
    lines.extend(f"| {x['fold']} | {x['holdout_template']} | {len(x['valid_families'])} | {x['expanded_pool_baseline']['task_identity_f1']:.4f} | {x['reranked']['task_identity_f1']:.4f} | {x['reranked']['field_accuracy']:.4f} |" for x in folds)
    lines.extend(["", f"Recommendation: **{report['recommendation']}**."])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _family_metrics(root: Path, case_ids: Sequence[str], selected: dict[str, list[UnionProposal]], expected: dict[str, dict[str, Any]], groups: dict[str, list[str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    vals: list[float] = []
    for family, ids in groups.items():
        metrics = _compact(_metrics(root, ids, selected, expected))
        result[family] = metrics
        vals.append(float(metrics["task_identity_f1"]))
    worst_family = min(result, key=lambda name: (float(result[name]["task_identity_f1"]), name)) if result else None
    return {"groups": result, "macro_f1": sum(vals) / len(vals) if vals else 0.0, "worst_f1": min(vals) if vals else 0.0, "worst_family": worst_family, "best_f1": max(vals) if vals else 0.0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--split", type=Path)
    args = parser.parse_args()
    try:
        report = run(args.root, args.output, split_path=args.split)
    except BaseException as error:
        args.output.mkdir(parents=True, exist_ok=True)
        _write(args.output / "status.json", {"schema_version": "v226-status-v1", "status": "STOP", "error": repr(error), "training_performed": False, "metrics_persisted": False, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False})
        print(json.dumps({"status": "STOP", "error": repr(error)}, sort_keys=True))
        return 2
    print(json.dumps({"status": report["status"], "candidate_ceiling_f1": report["candidate_ceiling"]["identity_f1"], "reranked_f1": report.get("reranked_oof", {}).get("task_identity_f1"), "recommendation": report.get("recommendation")}, sort_keys=True))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
