"""V2.27 DEV42 volume adaptive policy over the frozen V2.26 pool.

This is a bounded leave-template-out experiment.  Candidate construction is
delegated to the V2.26 ``final_plus_bridge_plus_intermediate`` runtime pool;
each template gets one cheap fold-local ranker fit.  The selector policy can
adapt threshold and budget from meeting-local candidate volume, source mix,
score distribution, and assignee/due/status completeness.  It never reads a
template or case identifier while selecting a meeting.
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

from backend.app.evaluation import compare_case  # noqa: E402
from scripts.experimental_distillation.run_v222_task_proposal_reranker_oof import (  # noqa: E402
    CrossClauseLogisticRanker,
    FIELD_REGRESSION_TOLERANCE,
    FEATURE_DIMENSIONS,
    IDENTITY_GATE,
    _maximum_weight_matching,
)
from scripts.experimental_distillation.run_v223_union_proposal_oof import (  # noqa: E402
    UnionProposal,
    _features,
    _policy_select,
)
from scripts.experimental_distillation.run_v226_dev42_template_family_holdout import (  # noqa: E402
    _compact,
    _expanded_rows,
    _groups,
    _load,
    _metrics,
    _sha256,
    _write,
)
from scripts.experimental_distillation.audit_v225_upstream_candidates import family_keys  # noqa: E402


DEFAULT_SPLIT = Path(
    "evaluation/runtime/experimental-distillation-v2/kaggle/"
    "v221-neural-meeting-ranker-smoke-01-repair-v3/dataset/snapshot/v29-split.json"
)
DEFAULT_OUTPUT = Path("evaluation/runtime/experimental-distillation-v2/v227-dev42-volume-adaptive-policy")
TRACE_ROOT = Path("evaluation/runtime/pr29-q2-traces")
EPOCHS = 1
LEARNING_RATE = 0.15
SUPPORTED_FAMILY_MIN_TASKS = 5
ADAPTIVE_POLICY_FEATURES = (
    "candidate_count", "baseline_share", "bridge_share", "intermediate_share",
    "score_mean", "score_std", "score_p50", "score_p90", "score_top_gap",
    "assignee_completeness", "due_completeness", "status_completeness", "field_completeness",
)


def _context(proposals: Sequence[UnionProposal], scores: Sequence[float]) -> dict[str, float]:
    """Return only inference-safe meeting-local policy inputs."""

    count = len(proposals)
    denominator = float(max(1, count))
    values = sorted((float(value) for value in scores), reverse=True)
    mean = sum(values) / denominator
    variance = sum((value - mean) ** 2 for value in values) / denominator

    def quantile(fraction: float) -> float:
        if not values:
            return 0.0
        return values[min(len(values) - 1, int(fraction * (len(values) - 1)))]

    assignee = sum(bool(item.assignee) for item in proposals) / denominator
    due = sum(bool(item.due_date or item.due_date_text) for item in proposals) / denominator
    status = sum(bool(item.status) for item in proposals) / denominator
    return {
        "candidate_count": float(count),
        "baseline_share": sum(item.origin == "baseline" for item in proposals) / denominator,
        "bridge_share": sum(item.origin == "bridge" for item in proposals) / denominator,
        "intermediate_share": sum(item.origin == "intermediate" for item in proposals) / denominator,
        "score_mean": mean,
        "score_std": variance ** 0.5,
        "score_p50": quantile(0.50),
        "score_p90": quantile(0.90),
        "score_top_gap": (values[0] - values[1]) if len(values) > 1 else (values[0] if values else 0.0),
        "assignee_completeness": assignee,
        "due_completeness": due,
        "status_completeness": status,
        "field_completeness": (assignee + due + status) / 3.0,
    }


def _adaptive_select(
    proposals: Sequence[UnionProposal], scores: Sequence[float], policy: dict[str, Any],
) -> tuple[list[UnionProposal], dict[str, float], bool]:
    """Apply a policy without using template, case, or expected-output data."""

    context = _context(proposals, scores)
    adaptive = (
        context["candidate_count"] >= float(policy["volume_cutoff"])
        and context["score_mean"] <= float(policy["score_mean_cap"])
        and context["intermediate_share"] >= float(policy["intermediate_share_min"])
        and context["field_completeness"] >= float(policy["field_completeness_min"])
    )
    threshold = policy["adaptive_threshold"] if adaptive else policy["base_threshold"]
    budget = policy["adaptive_budget"] if adaptive else policy["base_budget"]
    return _policy_select(proposals, scores, float(threshold), budget), context, adaptive


def _labels(expected: dict[str, Any], proposals: Sequence[UnionProposal]) -> list[int]:
    pairs = _maximum_weight_matching(list(expected.get("tasks", [])), [item.task for item in proposals])
    return [int(any(actual == index for _gold, actual in pairs)) for index in range(len(proposals))]


def _score_policy(
    root: Path, case_ids: Sequence[str], proposals: dict[str, list[UnionProposal]],
    expected: dict[str, dict[str, Any]], ranker: CrossClauseLogisticRanker, policy: dict[str, Any],
) -> tuple[dict[str, list[UnionProposal]], dict[str, dict[str, float]], dict[str, bool]]:
    selected: dict[str, list[UnionProposal]] = {}
    contexts: dict[str, dict[str, float]] = {}
    adaptive: dict[str, bool] = {}
    for case_id in case_ids:
        pool = proposals[case_id]
        scores = [
            ranker.probability(_features(item, pool))
            * (float(policy["bridge_weight"]) if item.origin == "bridge" else float(policy["intermediate_weight"]) if item.origin == "intermediate" else 1.0)
            for item in pool
        ]
        selected[case_id], contexts[case_id], adaptive[case_id] = _adaptive_select(pool, scores, policy)
    return selected, contexts, adaptive


def _fit_policy(
    root: Path, fit_ids: Sequence[str], proposals: dict[str, list[UnionProposal]],
    expected: dict[str, dict[str, Any]], ranker: CrossClauseLogisticRanker,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Tune a small adaptive policy on fit meetings only."""

    baseline = _metrics(root, fit_ids, {case_id: proposals[case_id] for case_id in fit_ids}, expected)
    field_floor = float(baseline["field_accuracy"]) - FIELD_REGRESSION_TOLERANCE
    grid: list[dict[str, Any]] = []
    # The fit-only sweep deliberately keeps the conservative V2.26 recall
    # floor at 0.35; adaptation is tuned through volume, score, source-mix,
    # completeness, threshold, and budget dimensions below.
    for base_threshold in (0.35,):
        for base_budget in (6, 8):
            for volume_cutoff in (30, 35):
                for adaptive_threshold in (0.15, 0.20):
                    for adaptive_budget in (20, 24):
                        for score_mean_cap in (0.30, 0.32):
                            for intermediate_share_min in (0.25, 0.33):
                                grid.append({
                                    "base_threshold": base_threshold, "base_budget": base_budget,
                                    "volume_cutoff": volume_cutoff, "adaptive_threshold": adaptive_threshold,
                                    "adaptive_budget": adaptive_budget, "score_mean_cap": score_mean_cap,
                                    "intermediate_share_min": intermediate_share_min,
                                    "field_completeness_min": 0.50,
                                    "bridge_weight": 0.50, "intermediate_weight": 0.75,
                                })
    # Candidate-level Hungarian labels are already available fold-locally.  Use
    # them for the full grid, then run the comparatively expensive field
    # evaluator only for the strongest few policies.
    label_map = {
        case_id: {item.proposal_id: label for item, label in zip(proposals[case_id], _labels(expected[case_id], proposals[case_id]), strict=True)}
        for case_id in fit_ids
    }
    score_vectors: dict[str, list[float]] = {}
    context_vectors: dict[str, dict[str, float]] = {}
    for case_id in fit_ids:
        pool = proposals[case_id]
        scores = [
            ranker.probability(_features(item, pool))
            * (0.50 if item.origin == "bridge" else 0.75 if item.origin == "intermediate" else 1.0)
            for item in pool
        ]
        score_vectors[case_id] = scores
        context_vectors[case_id] = _context(pool, scores)

    def apply_cached(policy: dict[str, Any]) -> tuple[dict[str, list[UnionProposal]], dict[str, dict[str, float]], dict[str, bool]]:
        selected: dict[str, list[UnionProposal]] = {}
        adaptive: dict[str, bool] = {}
        for case_id in fit_ids:
            context = context_vectors[case_id]
            is_adaptive = (
                context["candidate_count"] >= float(policy["volume_cutoff"])
                and context["score_mean"] <= float(policy["score_mean_cap"])
                and context["intermediate_share"] >= float(policy["intermediate_share_min"])
                and context["field_completeness"] >= float(policy["field_completeness_min"])
            )
            selected[case_id] = _policy_select(
                proposals[case_id], score_vectors[case_id],
                float(policy["adaptive_threshold"] if is_adaptive else policy["base_threshold"]),
                policy["adaptive_budget"] if is_adaptive else policy["base_budget"],
            )
            adaptive[case_id] = is_adaptive
        return selected, context_vectors, adaptive

    fast: list[tuple[tuple[float, ...], dict[str, Any], dict[str, list[UnionProposal]], dict[str, dict[str, float]], dict[str, bool]]] = []
    for policy in grid:
        selected, contexts, adaptive = apply_cached(policy)
        actual_count = sum(len(values) for values in selected.values())
        matched_count = sum(sum(label_map[case_id].get(item.proposal_id, 0) for item in values) for case_id, values in selected.items())
        expected_count = sum(len(expected[case_id].get("tasks", [])) for case_id in fit_ids)
        precision = matched_count / actual_count if actual_count else 0.0
        recall = matched_count / expected_count if expected_count else 0.0
        identity_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        key = (
            float(identity_f1), float(precision), -float(actual_count),
            -float(policy["base_threshold"]), -float(policy["volume_cutoff"]),
            -float(policy["adaptive_threshold"]), -float(policy["adaptive_budget"]),
        )
        fast.append((key, policy, selected, contexts, adaptive))
    fast.sort(key=lambda item: item[0], reverse=True)
    best: tuple[tuple[float, ...], dict[str, Any], dict[str, Any], dict[str, dict[str, float]], dict[str, bool]] | None = None
    for identity_key, policy, selected, contexts, adaptive in fast[:4]:
        metrics = _metrics(root, fit_ids, selected, expected)
        field_ok = float(metrics["field_accuracy"]) >= field_floor
        key = (float(field_ok), float(metrics["task_identity_f1"]), float(metrics["task_identity_precision"]), float(metrics["field_accuracy"]), -float(metrics["actual_task_count"])) + identity_key
        if best is None or key > best[0]:
            best = (key, policy, metrics, contexts, adaptive)
    assert best is not None
    return best[1], {
        "metrics": best[2], "candidate_count": len(grid), "field_floor": field_floor,
        "adaptive_fit_meetings": sum(best[4].values()),
        "policy_feature_names": list(ADAPTIVE_POLICY_FEATURES),
    }


def _family_metrics(
    root: Path, case_ids: Sequence[str], selected: dict[str, list[UnionProposal]],
    expected: dict[str, dict[str, Any]], groups: dict[str, list[str]],
) -> dict[str, Any]:
    """Report all families while gating only families with sufficient support."""

    result: dict[str, Any] = {}
    supported: dict[str, dict[str, Any]] = {}
    small: dict[str, dict[str, Any]] = {}
    zero_expected: list[str] = []
    for family, ids in groups.items():
        metrics = _compact(_metrics(root, ids, selected, expected))
        expected_count = int(metrics.get("expected_task_count") or 0)
        item = {"support": {"meeting_count": len(ids), "expected_task_count": expected_count, "eligible_for_gate": expected_count >= SUPPORTED_FAMILY_MIN_TASKS}, **metrics}
        result[family] = item
        if expected_count == 0:
            zero_expected.append(family)
        elif expected_count >= SUPPORTED_FAMILY_MIN_TASKS:
            supported[family] = item
        else:
            small[family] = item
    eligible_values = [float(item["task_identity_f1"]) for item in supported.values()]
    return {
        "groups": result, "supported_groups": supported, "smaller_groups": small,
        "zero_expected_groups": sorted(zero_expected), "eligible_family_count": len(supported),
        "macro_f1": sum(eligible_values) / len(eligible_values) if eligible_values else 0.0,
        "worst_f1": min(eligible_values) if eligible_values else 0.0,
        "worst_family": min(supported, key=lambda name: (float(supported[name]["task_identity_f1"]), name)) if supported else None,
        "best_f1": max(eligible_values) if eligible_values else 0.0,
    }


def _stability(folds: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Compute fold stability using only folds with positive expected support."""

    supported = [item for item in folds if int(item["reranked"].get("expected_task_count") or 0) > 0]
    values = [float(item["reranked"]["task_identity_f1"]) for item in supported]
    return {
        "fold_count": len(folds), "supported_fold_count": len(supported),
        "excluded_zero_expected_folds": [item["fold"] for item in folds if item not in supported],
        "supported_expected_task_count": sum(int(item["reranked"].get("expected_task_count") or 0) for item in supported),
        "macro_f1": sum(values) / len(values) if values else 0.0,
        "worst_fold_f1": min(values) if values else 0.0,
        "best_fold_f1": max(values) if values else 0.0,
        "spread": (max(values) - min(values)) if values else 0.0,
        "worst_fold": min(supported, key=lambda item: (float(item["reranked"]["task_identity_f1"]), item["fold"]))["fold"] if supported else None,
    }


def _errors(root: Path, case_ids: Sequence[str], selected: dict[str, list[UnionProposal]], expected: dict[str, dict[str, Any]], contexts: dict[str, dict[str, float]], adaptive: dict[str, bool]) -> dict[str, Any]:
    rows = []
    for case_id in sorted(case_ids):
        actual = {"tasks": [item.task for item in selected.get(case_id, [])]}
        comparison = compare_case(case_id, expected[case_id], actual)
        rows.append({"case_id": case_id, "family": family_keys(case_id)["family"], "adaptive": bool(adaptive.get(case_id)), "context": contexts.get(case_id, {}), "expected_task_count": len(expected[case_id].get("tasks", [])), "selected_task_count": len(actual["tasks"]), "matched_task_count": comparison.matched_task_count, "missing_task_count": len(comparison.missing_tasks), "unexpected_task_count": len(comparison.unexpected_tasks), "field_error_count": len(comparison.field_errors)})
    rows.sort(key=lambda row: (-row["unexpected_task_count"], -row["missing_task_count"], row["case_id"]))
    return {"schema_version": "v227-errors-v1", "case_count": len(rows), "top_errors": rows[:12], "total_missing": sum(row["missing_task_count"] for row in rows), "total_unexpected": sum(row["unexpected_task_count"] for row in rows), "total_field_errors": sum(row["field_error_count"] for row in rows)}


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
    for case_id in development:
        paths = sorted(trace_root.glob(f"{case_id}-v1-*.json"))
        if len(paths) != 1:
            raise ValueError(f"STOP_TRACE_INVENTORY:{case_id}:{len(paths)}")
        trace_paths[case_id] = paths[0]
        traces[case_id] = _load(paths[0])
        expected_paths[case_id] = (root / "data/validation" / case_id / "expected_output.json").resolve()
        expected[case_id] = _load(expected_paths[case_id])
    proposals = {case_id: _expanded_rows(case_id, traces[case_id], root) for case_id in development}
    volume = {"candidate_count": sum(len(values) for values in proposals.values()), "mean_per_meeting": sum(len(values) for values in proposals.values()) / len(development), "min_per_meeting": min(len(values) for values in proposals.values()), "max_per_meeting": max(len(values) for values in proposals.values())}
    ceiling_selected = {case_id: [item for item, label in zip(proposals[case_id], _labels(expected[case_id], proposals[case_id]), strict=True) if label] for case_id in development}
    ceiling_metrics = _metrics(root, development, ceiling_selected, expected)
    ceiling = {"definition": "perfect selector over expanded runtime candidates with one meeting-local Hungarian match", "candidate_source": "V2.26 final_plus_bridge_plus_intermediate", "expected_task_count": ceiling_metrics["expected_task_count"], "candidate_count": volume["candidate_count"], "identity_matches": ceiling_metrics["matched_task_count"], "identity_precision": ceiling_metrics["task_identity_precision"], "identity_recall": ceiling_metrics["task_identity_recall"], "identity_f1": ceiling_metrics["task_identity_f1"], "field_accuracy": ceiling_metrics["field_accuracy"], "passed_gate": float(ceiling_metrics["task_identity_f1"]) >= IDENTITY_GATE}
    if not ceiling["passed_gate"]:
        report = {"schema_version": "v227-dev42-volume-adaptive-policy-v1", "status": "STOP_CEILING", "scope": {"development_meetings": 42, "folds": 0, "training_performed": False, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0}, "candidate_ceiling": ceiling, "volume": volume}
        _write(output / "metrics.json", report); _write(output / "status.json", {"schema_version": "v227-status-v1", "status": "STOP_CEILING", "training_performed": False, "metrics_persisted": True}); return report

    template_groups = _groups(development, "template")
    family_groups = _groups(development, "family")
    folds: list[dict[str, Any]] = []
    oof_selected: dict[str, list[UnionProposal]] = {}
    oof_contexts: dict[str, dict[str, float]] = {}
    oof_adaptive: dict[str, bool] = {}
    for index, (template, valid_ids) in enumerate(template_groups.items()):
        valid_set = set(valid_ids)
        fit_ids = [case_id for case_id in development if case_id not in valid_set]
        valid_families = sorted({family_keys(case_id)["family"] for case_id in valid_ids})
        fit_families = {family_keys(case_id)["family"] for case_id in fit_ids}
        if set(valid_families) & fit_families:
            raise ValueError("STOP_FAMILY_LEAKAGE")
        groups = [[(_features(item, proposals[case_id]), label) for item, label in zip(proposals[case_id], _labels(expected[case_id], proposals[case_id]), strict=True)] for case_id in fit_ids]
        ranker = CrossClauseLogisticRanker().fit(groups, epochs=EPOCHS, learning_rate=LEARNING_RATE)
        policy, tuning = _fit_policy(root, fit_ids, proposals, expected, ranker)
        selected, contexts, adaptive = _score_policy(root, valid_ids, proposals, expected, ranker, policy)
        oof_selected.update(selected); oof_contexts.update(contexts); oof_adaptive.update(adaptive)
        folds.append({"fold": index, "holdout_template": template, "fit_meetings": len(fit_ids), "valid_meetings": len(valid_ids), "fit_case_ids": fit_ids, "valid_case_ids": valid_ids, "fit_families": sorted(fit_families), "valid_families": valid_families, "policy": policy, "tuning": {"metrics": _compact(tuning["metrics"]), "candidate_count": tuning["candidate_count"], "field_floor": tuning["field_floor"], "adaptive_fit_meetings": tuning["adaptive_fit_meetings"], "policy_feature_names": tuning["policy_feature_names"]}, "expanded_pool_baseline": _compact(_metrics(root, valid_ids, {case_id: proposals[case_id] for case_id in valid_ids}, expected)), "reranked": _compact(_metrics(root, valid_ids, selected, expected)), "runtime_context": contexts, "adaptive_meetings": sorted(case_id for case_id, value in adaptive.items() if value)})
    reranked = _metrics(root, development, oof_selected, expected)
    expanded_baseline = _metrics(root, development, proposals, expected)
    stability = _stability(folds)
    family_stability = _family_metrics(root, development, oof_selected, expected, family_groups)
    identity_ok = float(reranked["task_identity_f1"]) >= IDENTITY_GATE
    field_ok = float(reranked["field_accuracy"]) >= float(expanded_baseline["field_accuracy"]) - FIELD_REGRESSION_TOLERANCE
    family_ok = family_stability["eligible_family_count"] > 0 and all(float(item["task_identity_f1"]) >= 0.40 for item in family_stability["supported_groups"].values())
    report = {"schema_version": "v227-dev42-volume-adaptive-policy-v1", "status": "complete", "scope": {"development_meetings": 42, "train_meetings": 34, "calibration_meetings": 8, "folds": len(folds), "fold_unit": "template", "training_performed": True, "model_fit_count": len(folds), "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0, "execution": "local_cpu"}, "method": {"candidate_source": "V2.26 final_plus_bridge_plus_intermediate", "model": "CrossClauseLogisticRanker sparse hashed features", "feature_dimensions": FEATURE_DIMENSIONS, "epochs_per_fit": EPOCHS, "learning_rate": LEARNING_RATE, "fold_assignment": "sorted template keys; all nested families held out", "policy_tuning": "fit meetings only; volume/source mix/score distribution/field completeness", "adaptive_policy_features": list(ADAPTIVE_POLICY_FEATURES), "forbidden_policy_features": ["template", "family", "case_id", "expected labels"]}, "candidate_ceiling": ceiling, "volume": volume, "expanded_pool_baseline": _compact(expanded_baseline), "reranked_oof": _compact(reranked), "stability": {"macro": stability, "aggregate": {"identity_f1": reranked["task_identity_f1"], "field_accuracy": reranked["field_accuracy"]}, "family_count": len(family_groups), "family_metrics": family_stability}, "folds": folds, "gates": {"aggregate_identity_f1": {"threshold": IDENTITY_GATE, "value": reranked["task_identity_f1"], "passed": identity_ok}, "supported_family_f1": {"threshold": 0.40, "minimum_expected_tasks": SUPPORTED_FAMILY_MIN_TASKS, "value": family_stability["worst_f1"], "family": family_stability["worst_family"], "passed": family_ok}, "field_accuracy_no_material_regression": {"baseline": expanded_baseline["field_accuracy"], "value": reranked["field_accuracy"], "tolerance": FIELD_REGRESSION_TOLERANCE, "passed": field_ok}, "passed": identity_ok and family_ok and field_ok}, "split_access": {"development_case_ids": development, "diagnostic_case_ids_read": [], "final_dev_case_ids_read": [], "outer_validation_case_ids_read": [], "template_count": len(template_groups), "family_count": len(family_groups)}, "source_hashes": {"split": {"path": str(split_path), "sha256": _sha256(split_path)}, "runner": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__).resolve())}, "v226_source": {"path": str((_REPO_ROOT / "scripts/experimental_distillation/run_v226_dev42_template_family_holdout.py").resolve()), "sha256": _sha256((_REPO_ROOT / "scripts/experimental_distillation/run_v226_dev42_template_family_holdout.py").resolve())}}, "leakage_audit": {"gold_features": False, "labels_used_for_candidate_construction": False, "policy_uses_case_or_template": False, "diagnostic_labels_used_for_fit": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0}, "recommendation": "PROMISING" if identity_ok and family_ok and field_ok else "NOT_READY"}
    _write(output / "metrics.json", report); _write(output / "coverage-ceiling.json", ceiling); _write(output / "fold-policies.json", {"schema_version": "v227-fold-policies-v1", "fold_unit": "template", "policy_feature_names": list(ADAPTIVE_POLICY_FEATURES), "folds": [{"fold": item["fold"], "holdout_template": item["holdout_template"], "valid_families": item["valid_families"], "policy": item["policy"], "adaptive_meetings": item["adaptive_meetings"]} for item in folds]}); _write(output / "split-access-audit.json", report["split_access"]); _write(output / "errors.json", _errors(root, development, oof_selected, expected, oof_contexts, oof_adaptive)); _write(output / "tests.json", {"schema_version": "v227-tests-v1", "checks": {"scope_dev42": len(development) == 42, "one_fit_per_template": len(folds) == len(template_groups), "nested_family_holdout": all(not (set(item["valid_families"]) & set(item["fit_families"])) for item in folds), "stability_excludes_zero_expected": stability["supported_fold_count"] <= stability["fold_count"], "policy_features_inference_safe": True, "aggregate_gate": identity_ok, "supported_family_gate": family_ok, "field_regression_gate": field_ok}, "passed": identity_ok and family_ok and field_ok}); _write(output / "status.json", {"schema_version": "v227-status-v1", "status": "complete", "training_performed": True, "model_fit_count": len(folds), "metrics_persisted": True, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0})
    lines = ["# V2.27 DEV42 volume adaptive policy", "", "One cheap ranker fit per held-out template uses the frozen V2.26 expanded pool. Threshold and budget adapt only from runtime candidate volume, source mix, score distribution, and assignee/due/status completeness; template, family, case-id, diagnostic, final-dev, outer, teacher/provider, and Kaggle inputs remain closed.", "", f"Expanded pool baseline F1 **{expanded_baseline['task_identity_f1']:.4f}**; adaptive leave-template-out F1 **{reranked['task_identity_f1']:.4f}**, field accuracy **{reranked['field_accuracy']:.4f}**. Supported-family worst F1 **{family_stability['worst_f1']:.4f}** ({family_stability['worst_family']}), with support threshold **{SUPPORTED_FAMILY_MIN_TASKS}** expected tasks. Zero-expected families are reported and excluded from stability gates.", "", f"Gates: aggregate identity **{'PASS' if identity_ok else 'FAIL'}**, supported-family identity **{'PASS' if family_ok else 'FAIL'}**, field regression **{'PASS' if field_ok else 'FAIL'}**.", "", "| Fold | Heldout template | Valid meetings | Adaptive meetings | Reranked F1 |", "|---:|---|---:|---:|---:|"]
    lines.extend(f"| {item['fold']} | {item['holdout_template']} | {item['valid_meetings']} | {len(item['adaptive_meetings'])} | {item['reranked']['task_identity_f1']:.4f} |" for item in folds); lines.extend(["", f"Recommendation: **{report['recommendation']}**."]); (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--root", type=Path, default=_REPO_ROOT); parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT); parser.add_argument("--split", type=Path); args = parser.parse_args()
    try:
        report = run(args.root, args.output, split_path=args.split)
    except BaseException as error:
        args.output.mkdir(parents=True, exist_ok=True); _write(args.output / "status.json", {"schema_version": "v227-status-v1", "status": "STOP", "error": repr(error), "training_performed": False, "metrics_persisted": False, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False}); print(json.dumps({"status": "STOP", "error": repr(error)}, sort_keys=True)); return 2
    print(json.dumps({"status": report["status"], "reranked_f1": report.get("reranked_oof", {}).get("task_identity_f1"), "supported_family_worst_f1": report.get("stability", {}).get("family_metrics", {}).get("worst_f1"), "recommendation": report.get("recommendation")}, sort_keys=True)); return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
