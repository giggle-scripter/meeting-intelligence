"""V2.22 TRAIN34 OOF reranking of existing baseline task proposals.

This package deliberately starts from the task proposals already serialized in
the locked baseline traces.  It never rebuilds action spans or asks a model to
extract new tasks.  A small sparse logistic scorer is fitted once per held-out
meeting fold, using Hungarian identity matches only as fold-local labels.  The
selection threshold and meeting budget are chosen on the fit meetings and are
then applied once to the held-out meetings.
"""

from __future__ import annotations

import argparse
from collections import Counter
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

from backend.app.evaluation import (  # noqa: E402
    _maximum_weight_matching,
    aggregate_results,
    compare_case,
)
from experiments.distilled_proposal_ranker.v2.protocol_bridge_v219 import (  # noqa: E402
    CrossClauseLogisticRanker,
    FEATURE_DIMENSIONS,
)


FOLDS = 5
IDENTITY_GATE = 0.57
CEILING_GATE = 0.57
FIELD_REGRESSION_TOLERANCE = 0.03
DEFAULT_SPLIT = Path(
    "evaluation/runtime/experimental-distillation-v2/kaggle/"
    "v221-neural-meeting-ranker-smoke-01-repair-v3/dataset/snapshot/v29-split.json"
)
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
STOPWORDS = frozenset("anh chi em task viec phan va cho cac the a an to for".split())


@dataclass(frozen=True)
class TaskProposal:
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


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _normalise(value: Any) -> str:
    text = str(value or "").casefold().replace("đ", "d")
    return " ".join(TOKEN_RE.findall(text))


def _tokens(value: Any) -> list[str]:
    return [token for token in _normalise(value).split() if token not in STOPWORDS]


def _hash_index(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=4).digest(), "little") % FEATURE_DIMENSIONS


def _feature_map(values: dict[str, float | str]) -> dict[int, float]:
    result: dict[int, float] = {}
    for name, value in values.items():
        if isinstance(value, str):
            result[_hash_index("cat=" + name + "=" + _normalise(value))] = 1.0
        else:
            result[_hash_index("num=" + name)] = float(value)
    tokens = _tokens(values.get("title", ""))
    for size in (1, 2, 3):
        for start in range(max(0, len(tokens) - size + 1)):
            index = _hash_index("ng=" + " ".join(tokens[start : start + size]))
            result[index] = result.get(index, 0.0) + 1.0
    norm = sum(value * value for value in result.values()) ** 0.5 or 1.0
    return {index: value / norm for index, value in result.items()}


def _clause_map(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("clause_id")): row for row in trace.get("clauses", []) if isinstance(row, dict) and row.get("clause_id")}


def _event_matches(trace: dict[str, Any], task: dict[str, Any]) -> list[dict[str, Any]]:
    title = _normalise(task.get("task_name"))
    assignee = _normalise(task.get("assignee"))
    events = [row for row in trace.get("events_after_deduplication", []) if isinstance(row, dict)]
    exact = [
        row for row in events
        if _normalise(row.get("action_text")) == title
        and (not assignee or _normalise(row.get("assignee")) == assignee)
    ]
    if exact:
        return exact
    # Runtime-only fallback for baseline task names that are abbreviated by a
    # later mutation event.  This carries provenance but does not make labels.
    return [
        row for row in events
        if title and (_normalise(row.get("action_text")) in title or title in _normalise(row.get("action_text")))
        and (not assignee or not row.get("assignee") or _normalise(row.get("assignee")) == assignee)
    ]


def _ledger_matches(trace: dict[str, Any], task: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = ((trace.get("task_ledger") or {}).get("ledger") or {})
    title = _normalise(task.get("task_name"))
    assignee = _normalise(task.get("assignee"))
    return [
        row for row in ledger.values() if isinstance(row, dict)
        and (_normalise(row.get("canonical_action")) == title or title in {_normalise(x) for x in row.get("aliases", [])})
        and (not assignee or not row.get("assignees") or assignee in {_normalise(x) for x in row.get("assignees", [])})
    ]


def _proposal_rows(case_id: str, trace: dict[str, Any]) -> list[TaskProposal]:
    clauses = _clause_map(trace)
    result: list[TaskProposal] = []
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
        for row in ledger:
            source_ids.extend(str(x) for x in row.get("source_clause_ids", []) if x)
            event_ids.extend(str(x) for x in row.get("event_ids", []) if x)
            provenance.extend(str(x) for x in row.get("extraction_sources", []) if x)
            confidences.append(float(row.get("confidence", 0.0) or 0.0))
        for row in events:
            source_ids.extend(str(x) for x in row.get("source_clause_ids", []) if x)
            event_ids.extend(str(row.get("event_id", "")) for _ in [0] if row.get("event_id"))
            provenance.extend([str(row.get("extraction_source", ""))] if row.get("extraction_source") else [])
            confidences.append(float(row.get("confidence", 0.0) or 0.0))
        source_ids = list(dict.fromkeys(source_ids)); event_ids = list(dict.fromkeys(event_ids)); provenance = list(dict.fromkeys(provenance))
        context_ids = source_ids[:4]
        context_parts = [str(clauses[item].get("text_raw", "")) for item in context_ids if item in clauses]
        if not context_parts:
            context_parts = [str(task.get("evidence", ""))]
        result.append(TaskProposal(
            proposal_id=f"{case_id}::task::{index}", case_id=case_id, task=task,
            title=str(task.get("task_name", "")), assignee=str(task.get("assignee", "")),
            due_date=str(task.get("due_date", "")), due_date_text=str(task.get("due_date_text", "")),
            status=str(task.get("status", "")), provenance="|".join(provenance) or "unknown",
            confidence=max(confidences, default=0.0), context=" || ".join(context_parts),
            source_clause_ids=tuple(source_ids), event_ids=tuple(event_ids),
        ))
    return result


def _features(proposal: TaskProposal, pool_size: int) -> dict[int, float]:
    task = proposal.task
    return _feature_map({
        "title": proposal.title,
        "assignee": proposal.assignee,
        "due_date": proposal.due_date,
        "due_date_text": proposal.due_date_text,
        "status": proposal.status,
        "provenance": proposal.provenance,
        "context": proposal.context,
        "confidence": min(1.0, max(0.0, proposal.confidence)),
        "has_assignee": float(bool(proposal.assignee)),
        "has_due_date": float(bool(proposal.due_date)),
        "has_due_date_text": float(bool(proposal.due_date_text)),
        "source_clause_count": min(8, len(proposal.source_clause_ids)) / 8.0,
        "event_count": min(8, len(proposal.event_ids)) / 8.0,
        "pool_size": min(32, pool_size) / 32.0,
        "task_key_present": float(bool(task.get("task_key"))),
    })


def _labels(expected: dict[str, Any], proposals: Sequence[TaskProposal]) -> list[int]:
    expected_tasks = list(expected.get("tasks", []))
    actual_tasks = [item.task for item in proposals]
    pairs = _maximum_weight_matching(expected_tasks, actual_tasks)
    return [int(any(actual_index == index for _expected_index, actual_index in pairs)) for index in range(len(proposals))]


def _folds(case_ids: Iterable[str]) -> dict[str, int]:
    return {case_id: index % FOLDS for index, case_id in enumerate(sorted(case_ids))}


def _metrics(repo_root: Path, case_ids: Sequence[str], selected: dict[str, list[TaskProposal]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    comparisons = [compare_case(case_id, expected[case_id], {"tasks": [item.task for item in selected.get(case_id, [])]}) for case_id in sorted(case_ids)]
    return aggregate_results(comparisons)


def _policy_select(proposals: Sequence[TaskProposal], scores: Sequence[float], threshold: float, budget: int | None) -> list[TaskProposal]:
    ranked = sorted(zip(proposals, scores), key=lambda item: (-item[1], item[0].proposal_id))
    ranked = [item for item in ranked if item[1] >= threshold]
    if budget is not None:
        ranked = ranked[:budget]
    return [item[0] for item in ranked]


def _tune_policy(
    repo_root: Path, train_ids: Sequence[str], proposals: dict[str, list[TaskProposal]], expected: dict[str, dict[str, Any]],
    ranker: CrossClauseLogisticRanker,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scores = {
        case_id: [ranker.probability(_features(item, len(proposals[case_id]))) for item in proposals[case_id]]
        for case_id in train_ids
    }
    frontier: list[dict[str, Any]] = []
    best_policy: dict[str, Any] | None = None
    best_metrics: dict[str, Any] | None = None
    best_key: tuple[float, ...] | None = None
    for threshold in (0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80):
        for budget in (None, 1, 2, 3, 4, 5, 6):
            selected = {case_id: _policy_select(proposals[case_id], scores[case_id], threshold, budget) for case_id in train_ids}
            metrics = _metrics(repo_root, train_ids, selected, expected)
            key = (float(metrics["task_identity_f1"]), float(metrics["task_identity_precision"]), float(metrics["field_accuracy"]), -float(metrics["actual_task_count"]))
            frontier.append({"threshold": threshold, "meeting_budget": budget, **{name: metrics[name] for name in ("task_identity_precision", "task_identity_recall", "task_identity_f1", "actual_task_count", "field_accuracy")}})
            if best_key is None or key > best_key:
                best_key, best_policy, best_metrics = key, {"threshold": threshold, "meeting_budget": budget}, metrics
    assert best_policy is not None and best_metrics is not None
    frontier.sort(key=lambda row: (-row["task_identity_f1"], -row["task_identity_precision"], -row["field_accuracy"], row["actual_task_count"], row["threshold"], str(row["meeting_budget"])))
    return best_policy, {"metrics": best_metrics, "frontier": frontier[:8], "candidate_count": len(frontier)}


def run(root: Path, output: Path, *, split_path: Path | None = None, repo_root: Path | None = None) -> dict[str, Any]:
    root, output, repo_root = root.resolve(), output.resolve(), (repo_root or _REPO_ROOT).resolve()
    split_path = (split_path or repo_root / DEFAULT_SPLIT).resolve()
    split = _load(split_path)
    train_ids = sorted(str(item) for item in split.get("train_case_ids", []))
    if len(train_ids) != 34 or split.get("calibration_opened") or split.get("diagnostic_opened") or split.get("final_dev_opened") or split.get("outer_validation_opened"):
        raise ValueError("STOP_TRAIN34_SCOPE")
    trace_root = root / "evaluation/runtime/pr29-q2-traces"
    traces = {case_id: _load(next(trace_root.glob(f"{case_id}-v1-*.json"))) for case_id in train_ids}
    expected = {case_id: _load(repo_root / "data/validation" / case_id / "expected_output.json") for case_id in train_ids}
    proposals = {case_id: _proposal_rows(case_id, traces[case_id]) for case_id in train_ids}
    baseline_selected = proposals
    baseline = _metrics(repo_root, train_ids, baseline_selected, expected)
    ceiling = {
        "expected_tasks": baseline["expected_task_count"], "baseline_proposals": baseline["actual_task_count"],
        "identity_matches": baseline["matched_task_count"], "identity_recall": baseline["task_identity_recall"],
        "identity_f1": baseline["task_identity_f1"], "passed": baseline["task_identity_f1"] >= CEILING_GATE,
    }
    if not ceiling["passed"]:
        report = {"schema_version": "v222-train34-task-proposal-reranker-oof-v1", "status": "STOP_CEILING", "scope": {"train_meetings": 34, "folds": FOLDS, "calibration_opened": False, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "training_performed": False}, "coverage_ceiling": ceiling, "baseline": baseline}
        _write(output / "coverage-ceiling.json", ceiling); _write(output / "metrics.json", report); _write(output / "status.json", {"schema_version": "v222-status-v1", "status": "STOP_CEILING", "training_performed": False, "metrics_persisted": True})
        (output / "report.md").write_text(f"# V2.22 TRAIN34 task proposal reranker OOF\n\nSTOP_CEILING: baseline proposal identity F1 {baseline['task_identity_f1']:.4f} is below {CEILING_GATE:.2f}; no model was fit.\n", encoding="utf-8")
        return report

    fold_map = _folds(train_ids)
    folds: list[dict[str, Any]] = []
    reranked_selected: dict[str, list[TaskProposal]] = {}
    baseline_fold_reports: list[dict[str, Any]] = []
    reranked_fold_reports: list[dict[str, Any]] = []
    fit_count = 0
    for fold in range(FOLDS):
        valid_ids = sorted(case_id for case_id in train_ids if fold_map[case_id] == fold)
        fit_ids = sorted(set(train_ids) - set(valid_ids))
        groups = []
        for case_id in fit_ids:
            labels = _labels(expected[case_id], proposals[case_id])
            groups.append([(_features(item, len(proposals[case_id])), label) for item, label in zip(proposals[case_id], labels, strict=True)])
        ranker = CrossClauseLogisticRanker().fit(groups, epochs=1, learning_rate=0.15)
        fit_count += 1
        policy, fit_report = _tune_policy(repo_root, fit_ids, proposals, expected, ranker)
        valid_scores = {case_id: [ranker.probability(_features(item, len(proposals[case_id]))) for item in proposals[case_id]] for case_id in valid_ids}
        valid_selected = {case_id: _policy_select(proposals[case_id], valid_scores[case_id], policy["threshold"], policy["meeting_budget"]) for case_id in valid_ids}
        reranked_selected.update(valid_selected)
        baseline_metrics = _metrics(repo_root, valid_ids, {case_id: baseline_selected[case_id] for case_id in valid_ids}, expected)
        reranked_metrics = _metrics(repo_root, valid_ids, valid_selected, expected)
        baseline_fold_reports.append(baseline_metrics); reranked_fold_reports.append(reranked_metrics)
        folds.append({"fold": fold, "fit_meetings": len(fit_ids), "valid_meetings": len(valid_ids), "policy": policy, "fit": fit_report, "baseline": baseline_metrics, "reranked": reranked_metrics, "valid_case_ids": valid_ids})

    reranked = _metrics(repo_root, train_ids, reranked_selected, expected)
    field_ok = reranked["field_accuracy"] >= baseline["field_accuracy"] - FIELD_REGRESSION_TOLERANCE
    gate = {"threshold": IDENTITY_GATE, "value": reranked["task_identity_f1"], "passed": reranked["task_identity_f1"] >= IDENTITY_GATE}
    report = {
        "schema_version": "v222-train34-task-proposal-reranker-oof-v1", "status": "complete",
        "scope": {"train_meetings": 34, "folds": FOLDS, "calibration_opened": False, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "training_performed": True, "model_fit_count": fit_count, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0},
        "method": {"candidate_source": "locked baseline trace final_tasks", "features": ["title", "assignee", "due_date", "status", "provenance", "confidence", "context"], "labels": "fold-local Hungarian identity matches against expected tasks", "model": "CrossClauseLogisticRanker sparse hashed features", "epochs_per_fold": 1, "fold_assignment": "sorted_case_id_round_robin", "threshold_tuning": "fit meetings only"},
        "coverage_ceiling": ceiling, "baseline": baseline, "reranked": reranked, "folds": folds,
        "gates": {"coverage_ceiling": ceiling["passed"], "task_identity_f1": gate, "field_accuracy_no_material_regression": {"baseline": baseline["field_accuracy"], "value": reranked["field_accuracy"], "tolerance": FIELD_REGRESSION_TOLERANCE, "passed": field_ok}},
        "recommendation": {"readiness": "PROMISING" if gate["passed"] and field_ok else "NOT_READY", "reason": "OOF identity gate and field regression gate"},
    }
    compact_folds = [{"fold": item["fold"], "valid_meetings": item["valid_meetings"], "policy": item["policy"], "baseline": {k: item["baseline"][k] for k in ("expected_task_count", "actual_task_count", "matched_task_count", "task_identity_precision", "task_identity_recall", "task_identity_f1", "field_accuracy")}, "reranked": {k: item["reranked"][k] for k in ("expected_task_count", "actual_task_count", "matched_task_count", "task_identity_precision", "task_identity_recall", "task_identity_f1", "field_accuracy")}} for item in folds]
    _write(output / "coverage-ceiling.json", ceiling)
    _write(output / "fold-policies.json", {"schema_version": "v222-fold-policies-v1", "folds": compact_folds})
    _write(output / "metrics.json", report)
    _write(output / "status.json", {"schema_version": "v222-status-v1", "status": "complete", "training_performed": True, "model_fit_count": fit_count, "metrics_persisted": True, "calibration_opened": False, "diagnostic_opened": False, "final_dev_opened": False, "outer_validation_opened": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0})
    md = "# V2.22 TRAIN34 task proposal reranker OOF\n\n"
    md += "Five-fold meeting-group OOF over the 34 locked TRAIN34 meetings. Candidate proposals are the existing baseline `final_tasks`; no action spans were reconstructed. Labels are fold-local Hungarian identity matches against expected tasks. One cheap sparse logistic fit was run per fold, and thresholds/budgets were tuned only on fit meetings.\n\n"
    md += f"Coverage ceiling: **{ceiling['identity_matches']}/{ceiling['expected_tasks']}** matched, recall **{ceiling['identity_recall']:.4f}**, F1 **{ceiling['identity_f1']:.4f}**.\n\n"
    md += f"Baseline: **{baseline['actual_task_count']}** tasks, P **{baseline['task_identity_precision']:.4f}**, R **{baseline['task_identity_recall']:.4f}**, F1 **{baseline['task_identity_f1']:.4f}**; field accuracy **{baseline['field_accuracy']:.4f}**.\n\n"
    md += f"Reranked OOF: **{reranked['actual_task_count']}** tasks, P **{reranked['task_identity_precision']:.4f}**, R **{reranked['task_identity_recall']:.4f}**, F1 **{reranked['task_identity_f1']:.4f}**; field accuracy **{reranked['field_accuracy']:.4f}**. Assignee **{reranked.get('assignee_accuracy')}**, due date **{reranked.get('due_date_accuracy')}**, due-date text **{reranked.get('due_date_text_exact_accuracy')}**, state **{reranked.get('state_link_accuracy')}**.\n\n"
    md += f"OOF gate >= {IDENTITY_GATE:.2f}: **{'PASS' if gate['passed'] else 'FAIL'}**. Field regression gate: **{'PASS' if field_ok else 'FAIL'}**. Recommendation: **{report['recommendation']['readiness']}**.\n\n"
    md += "| Fold | Baseline F1 | Reranked F1 | Baseline count | Reranked count |\n|---:|---:|---:|---:|---:|\n"
    md += "\n".join(f"| {item['fold']} | {item['baseline']['task_identity_f1']:.4f} | {item['reranked']['task_identity_f1']:.4f} | {item['baseline']['actual_task_count']} | {item['reranked']['actual_task_count']} |" for item in folds) + "\n"
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
        _write(args.output / "status.json", {"schema_version": "v222-status-v1", "status": "STOP", "error": repr(error), "training_performed": False, "metrics_persisted": False})
        print(json.dumps({"status": "STOP", "error": repr(error)}, sort_keys=True))
        return 2
    print(json.dumps({"status": report["status"], "baseline_f1": report["baseline"]["task_identity_f1"], "reranked_f1": report.get("reranked", {}).get("task_identity_f1"), "readiness": report.get("recommendation", {}).get("readiness")}, sort_keys=True))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
