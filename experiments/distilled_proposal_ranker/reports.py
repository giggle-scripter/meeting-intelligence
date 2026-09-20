"""Locked gate evaluation, paired bootstrap, and aggregate-only Markdown."""

from __future__ import annotations

import json
from pathlib import Path
import platform
import random
from typing import Any

from .contracts import ProtocolSnapshot
from .hashing import atomic_write_json, sha256_file
from .safety import SAFETY_COUNTERS


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _f1(expected: int, actual: int, matched: int) -> float:
    precision = matched / actual if actual else 0.0
    recall = matched / expected if expected else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def paired_bootstrap_ci(rows: list[dict[str, Any]], *, samples: int = 10000, seed: int = 1729) -> tuple[float, float]:
    generator = random.Random(seed)
    deltas = []
    for _ in range(samples):
        selected = [rows[generator.randrange(len(rows))] for _ in rows]
        expected = sum(item["expected"] for item in selected)
        challenger = _f1(expected, sum(item["challenger_actual"] for item in selected), sum(item["challenger_matched"] for item in selected))
        baseline = _f1(expected, sum(item["baseline_actual"] for item in selected), sum(item["baseline_matched"] for item in selected))
        deltas.append(challenger - baseline)
    deltas.sort()
    return deltas[int(0.025 * samples)], deltas[int(0.975 * samples) - 1]


def evaluate_run(repo_root: Path, snapshot: ProtocolSnapshot, run_root: Path) -> dict[str, Any]:
    import numpy as np
    import sklearn
    import torch
    import transformers

    protocol = snapshot.protocol
    failures = []
    required = {
        "manifest": run_root / "run-manifest.json",
        "span": run_root / "reports/span.json",
        "proposal": run_root / "reports/proposal.json",
        "ranker": run_root / "reports/ranker.json",
        "task": run_root / "reports/task.json",
        "safety": run_root / "reports/safety.json",
        "teacher": run_root / "reports/teacher.json",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        return {"schema_version": "distillation-final-report-v1", "readiness": "NOT_READY", "decision": "INVALID_RUN", "failures": [f"missing:{name}" for name in missing]}
    values = {name: _read(path) for name, path in required.items()}
    manifest = values["manifest"]
    integrity_ok = (
        manifest.get("status") == "complete"
        and manifest.get("case_count") == protocol.expected_cases
        and manifest.get("fold_count") == protocol.outer_folds
        and manifest.get("signature", {}).get("protocol_hash") == snapshot.sha256
        and len(values["task"].get("per_case_counts", [])) == protocol.expected_cases
    )
    decision = "PROMISING"
    if not integrity_ok:
        decision = "INVALID_RUN"
        failures.append("integrity")
    safety = values["safety"]
    leakage = safety.get("cross_fold_training_leak_count", -1) + safety.get("gold_feature_leak_count", -1)
    if decision == "PROMISING" and leakage != 0:
        decision = "INVALID_RUN"
        failures.append("leakage")
    span = values["span"]
    if decision == "PROMISING" and (
        span["exact"]["recall"] < protocol.gates.span_exact_recall
        or span["token"]["f1"] < protocol.gates.span_token_f1
    ):
        decision = "SPAN_GATE_FAILED"
        failures.append("span")
    proposal = values["proposal"]
    if decision == "PROMISING" and proposal["recall_at_30"] < protocol.gates.proposal_recall_at_30:
        decision = "CANDIDATE_GATE_FAILED"
        failures.append("candidate")
    task = values["task"]
    global_task = task["global"]
    ranker = values["ranker"]
    if decision == "PROMISING" and (
        ranker.get("pr_auc") is None
        or global_task["task_identity_precision"] < protocol.gates.task_identity_precision
        or global_task["task_identity_recall"] < protocol.gates.task_identity_recall
        or global_task["task_identity_f1"] < protocol.gates.task_identity_f1
        or global_task["task_identity_f1"] - task["baseline_global"]["task_identity_f1"] < 0.0362
    ):
        decision = "RANKER_GATE_FAILED"
        failures.append("ranker_or_e2e")
    minimum_fold_delta = min(item["f1_delta"] for item in task["per_fold"])
    if decision == "PROMISING" and minimum_fold_delta < -protocol.gates.max_fold_f1_regression:
        decision = "RANKER_GATE_FAILED"
        failures.append("fold_regression")
    safety_total = sum(int(safety.get(name, -1)) for name in SAFETY_COUNTERS)
    if decision == "PROMISING" and safety_total != protocol.gates.safety_violations:
        decision = "SAFETY_GATE_FAILED"
        failures.append("safety")
    ci_low, ci_high = paired_bootstrap_ci(task["per_case_counts"])
    final = {
        "schema_version": "distillation-final-report-v1",
        "readiness": "PROMISING" if decision == "PROMISING" else "NOT_READY",
        "decision": decision,
        "development_nested_cv": True,
        "blind_test": False,
        "hashes": {
            "base_commit": protocol.base_commit,
            "branch": manifest["signature"]["branch"],
            "experiment_commit": manifest["signature"]["code_sha"],
            "protocol_sha256": snapshot.sha256,
            "evidence_sha256": sha256_file(repo_root / protocol.evidence_path),
            "dataset_sha256": manifest["signature"]["data_hash"],
            "fold_sha256": sha256_file(run_root / "folds/fold-manifest.json"),
            "trace_manifest_sha256": manifest["signature"]["trace_manifest_hash"],
            "baseline_trace_manifest_sha256": manifest["signature"]["baseline_trace_manifest_hash"],
        },
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "device": "cuda" if torch.cuda.is_available() else "cpu",
            "span_model": protocol.span_model_name,
            "reranker_model": protocol.reranker_model_name,
            "training_seeds": protocol.training_seeds,
        },
        "teacher": values["teacher"],
        "span": span,
        "proposal": proposal,
        "ranker": ranker,
        "task": task,
        "f1_delta_vs_q2": global_task["task_identity_f1"] - task["baseline_global"]["task_identity_f1"],
        "minimum_fold_delta": minimum_fold_delta,
        "bootstrap_f1_delta_ci_95": [ci_low, ci_high],
        "bootstrap_samples": 10000,
        "bootstrap_seed": 1729,
        "safety": safety,
        "safety_violation_count": safety_total,
        "failures": failures,
    }
    return final


def render_markdown(report: dict[str, Any]) -> str:
    task = report["task"]["global"]
    span = report["span"]
    proposal = report["proposal"]
    ranker = report["ranker"]
    teacher = report["teacher"]
    lines = [
        "# Distilled proposal ranker V1 results",
        "",
        "This is a development nested-CV estimate, not a blind test, production proof, or unbiased generalization estimate.",
        "",
        f"- Readiness: `{report['readiness']}`",
        f"- Decision: `{report['decision']}`",
        f"- Protocol SHA256: `{report['hashes']['protocol_sha256']}`",
        f"- Branch/base/experiment commit: `{report['hashes']['branch']}` / `{report['hashes']['base_commit']}` / `{report['hashes']['experiment_commit']}`",
        f"- Teacher: `{teacher['mode']}` / `{teacher['status']}`; model `{teacher['model']}`; prompts `{', '.join(teacher['prompt_versions'])}`; calls `{teacher['calls']}`; estimated cost `${teacher['estimated_cost_usd']:.6f}`",
        f"- Span token F1: `{span['token']['f1']:.6f}`",
        f"- Span exact precision/recall/F1: `{span['exact']['precision']:.6f}` / `{span['exact']['recall']:.6f}` / `{span['exact']['f1']:.6f}`",
        f"- Lattice/neural/union recall: `{span['source_recall']['lattice']:.6f}` / `{span['source_recall']['neural']:.6f}` / `{span['source_recall']['union']:.6f}`",
        f"- Proposal recall@30: `{proposal['recall_at_30']:.6f}`; PR-AUC: `{ranker.get('pr_auc')}`; selected: `{ranker['selected_count']}`",
        f"- Task expected/actual/matched/unexpected/missing: `{task['expected_task_count']}` / `{task['actual_task_count']}` / `{task['matched_task_count']}` / `{task['unexpected_task_count']}` / `{task['missing_task_count']}`",
        f"- Task precision/recall/F1: `{task['task_identity_precision']:.6f}` / `{task['task_identity_recall']:.6f}` / `{task['task_identity_f1']:.6f}`",
        f"- Delta vs Q2: `{report['f1_delta_vs_q2']:+.6f}`; paired bootstrap 95% CI: `[{report['bootstrap_f1_delta_ci_95'][0]:+.6f}, {report['bootstrap_f1_delta_ci_95'][1]:+.6f}]`",
        f"- Minimum per-fold delta: `{report['minimum_fold_delta']:+.6f}`",
        f"- Safety violations: `{report['safety_violation_count']}`",
        "",
        "## Environment",
        "",
        *[f"- {key}: `{value}`" for key, value in report["environment"].items()],
        "",
        "## Per-fold task identity",
        "",
        "| Fold | Precision | Recall | F1 | Delta vs Q2 fold |",
        "|---:|---:|---:|---:|---:|",
    ]
    for item in report["task"]["per_fold"]:
        current = item["challenger"]
        lines.append(f"| {item['outer_fold']} | {current['task_identity_precision']:.6f} | {current['task_identity_recall']:.6f} | {current['task_identity_f1']:.6f} | {item['f1_delta']:+.6f} |")
    seed_summary = report["task"]["seed_summary"]
    lines.extend(
        [
            "",
            "## Per-seed task identity",
            "",
            f"Mean/std F1: `{seed_summary['task_identity_f1_mean']:.6f}` / `{seed_summary['task_identity_f1_std']:.6f}`",
            "",
            "| Seed | Precision | Recall | F1 |",
            "|---:|---:|---:|---:|",
        ]
    )
    for seed, current in seed_summary["individual"].items():
        lines.append(f"| {seed} | {current['task_identity_precision']:.6f} | {current['task_identity_recall']:.6f} | {current['task_identity_f1']:.6f} |")
    lines.extend(["", "## Failures", "", *(f"- `{item}`" for item in report["failures"] or ["none"])])
    return "\n".join(lines) + "\n"
