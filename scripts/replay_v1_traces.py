"""Replay captured V1 events through the current deterministic reduction path.

This tool deliberately never constructs an AI client.  It is intended for
testing linker, deduplication, ledger, and reconciliation changes against a
fixed provider run without spending tokens or depending on network access.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.evaluation import aggregate_results, compare_case
from backend.app.models import Clause, DateMention, PipelineDiagnostics, TaskEvent
from backend.app.output import build_pipeline_result
from backend.app.pipeline import (
    _apply_final_active_snapshot,
    _final_active_recap_labels,
    _meeting_closes_without_active_tasks,
)
from backend.app.reduction import (
    build_deterministic_reconciliation_operations,
    deduplicate_events,
    reconcile_ledger,
    reduce_task_events_to_ledger,
)


def _case_directory(dataset: Path, case_id: str) -> Path:
    direct = dataset / case_id
    if direct.is_dir():
        return direct
    matches = list(dataset.glob(f"**/{case_id}"))
    if len(matches) != 1:
        raise FileNotFoundError(f"Cannot find validation case {case_id!r}")
    return matches[0]


def _load_case(dataset: Path, case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    case_dir = _case_directory(dataset, case_id)
    metadata = json.loads((case_dir / "metadata.json").read_text(encoding="utf-8"))
    expected = json.loads((case_dir / "expected_output.json").read_text(encoding="utf-8"))
    return metadata, expected


def _deserialize_clause(payload: dict[str, Any]) -> Clause:
    return Clause(**payload)


def _deserialize_mention(payload: dict[str, Any]) -> DateMention:
    return DateMention(**payload)


def _sum_diagnostics(items: list[dict[str, int]]) -> dict[str, int]:
    keys = {
        key
        for item in items
        for key, value in item.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    return {key: sum(int(item.get(key, 0)) for item in items) for key in sorted(keys)}


def replay_trace(trace: dict[str, Any], dataset: Path) -> tuple[Any, dict[str, int]]:
    case_id = str(trace["meeting_id"])
    metadata, expected = _load_case(dataset, case_id)
    events = [TaskEvent(**item) for item in trace.get("events_before_deduplication", [])]
    deduplicated = deduplicate_events(events)
    ledger = reduce_task_events_to_ledger(deduplicated)
    reconciliation = reconcile_ledger(
        ledger,
        build_deterministic_reconciliation_operations(ledger),
    )
    reduction_diagnostics: dict[str, int] = dict(ledger.diagnostics)
    reduction_diagnostics["legacy_ai_creation_event_count"] = sum(
        event.extraction_source == "AI"
        and event.event_type in {"TASK_CREATE", "TASK_COMMITMENT", "OWNER_ASSIGN"}
        for event in deduplicated
    )
    reduction_diagnostics["ai_event_count"] = sum(
        event.extraction_source == "AI" for event in deduplicated
    )
    reduction_diagnostics["ai_missing_related_task_id_count"] = sum(
        event.extraction_source == "AI" and not event.related_task_id.strip()
        for event in deduplicated
    )
    reduction_diagnostics["legacy_ai_only_task_count"] = sum(
        task.extraction_sources == {"AI"} for task in ledger.tasks.values()
    )
    # Event IDs originate in multiple extractors and legacy traces can reuse
    # one ID. Reading history through an ID dictionary can therefore mistake a
    # cancellation for a later positive event. The applied ledger transition
    # is collision-safe and is the invariant this replay needs to test.
    reopened_count = sum(
        task.terminal_order_index is not None
        and task.status not in {"CANCELLED", "REJECTED"}
        for task in ledger.tasks.values()
    )
    reduction_diagnostics["terminal_task_reopened_count"] = reopened_count
    clauses = [_deserialize_clause(item) for item in trace.get("clauses", [])]
    clauses_by_id = {item.clause_id: item for item in clauses}
    states = reconciliation.ledger.to_task_states()
    recap_scope = str(trace.get("recap_scope", "NONE"))
    states = _apply_final_active_snapshot(
        states,
        _final_active_recap_labels(clauses),
        recap_scope,
    )
    closes_without_active_tasks = _meeting_closes_without_active_tasks(clauses)
    no_active_reason = None
    if closes_without_active_tasks:
        if any(
            "CANCELLATION" in set(item.get("flags", []))
            for item in trace.get("annotations", {}).values()
        ):
            no_active_reason = "cancelled"
        states = []

    mentions = {
        key: _deserialize_mention(value)
        for key, value in trace.get("date_mentions", {}).items()
    }
    actual = asdict(
        build_pipeline_result(
            metadata.get("meeting_title", case_id),
            metadata["meeting_date"],
            states,
            clauses_by_id,
            mentions,
            PipelineDiagnostics(),
            [],
            no_active_reason=no_active_reason,
            meeting_note_present=(
                _case_directory(dataset, case_id) / "meeting_note.txt"
            ).exists(),
        )
    )
    comparison = compare_case(case_id, expected, actual)
    reduction_diagnostics.update(
        {
            "event_count_before_deduplication": len(events),
            "event_count_after_deduplication": len(deduplicated),
        }
    )
    return comparison, reduction_diagnostics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", required=True, type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("data/validation"))
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--contract-mode",
        choices=("legacy", "native"),
        default="legacy",
        help="Legacy replay is safety-only; native replay is a blocking contract gate.",
    )
    parser.add_argument("--minimum-precision", type=float)
    parser.add_argument("--minimum-recall", type=float)
    parser.add_argument("--maximum-unexpected", type=int)
    args = parser.parse_args()

    trace_paths = sorted(args.trace_dir.glob("*.json"))
    comparisons = []
    case_diagnostics: dict[str, dict[str, int]] = {}
    errors: list[dict[str, str]] = []
    for trace_path in trace_paths:
        try:
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            comparison, diagnostics = replay_trace(trace, args.dataset)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            errors.append({"trace": trace_path.name, "error": str(exc)})
            continue
        comparisons.append(comparison)
        case_diagnostics[comparison.case_id] = diagnostics
        print(f"{comparison.case_id}: {'PASS' if comparison.passed else 'FAIL'}")

    waves = sorted({item.case_id.split("-", 1)[0] for item in comparisons})
    metrics_by_wave = {
        wave: aggregate_results(
            [item for item in comparisons if item.case_id.startswith(f"{wave}-")]
        )
        for wave in waves
    }
    report = {
        "mode": "trace_replay",
        "source_trace_count": len(trace_paths),
        "completed_case_count": len(comparisons),
        "metrics_are_partial": bool(errors) or len(comparisons) != len(trace_paths),
        "metrics": aggregate_results(comparisons),
        "metrics_by_wave": metrics_by_wave,
        "reconciliation_diagnostics": _sum_diagnostics(list(case_diagnostics.values())),
        "execution_errors": errors,
        "cases": [asdict(item) for item in comparisons],
        "case_diagnostics": case_diagnostics,
    }
    contract_safety = {
        "mode": args.contract_mode,
        "blocking": args.contract_mode == "native",
        "execution_error_count": len(errors),
        "legacy_ai_creation_event_count": report["reconciliation_diagnostics"].get(
            "legacy_ai_creation_event_count", 0
        ),
        "unauthorized_creation_blocked_count": report[
            "reconciliation_diagnostics"
        ].get("unauthorized_creation_blocked_count", 0),
        "legacy_ai_only_task_count": report["reconciliation_diagnostics"].get(
            "legacy_ai_only_task_count", 0
        ),
        "unknown_task_id_rejection_count": report[
            "reconciliation_diagnostics"
        ].get("ledger_unknown_task_id_rejection_count", 0),
        "terminal_task_reopened_count": report[
            "reconciliation_diagnostics"
        ].get("terminal_task_reopened_count", 0),
        "ai_event_count": report["reconciliation_diagnostics"].get(
            "ai_event_count", 0
        ),
        "ai_missing_related_task_id_count": report[
            "reconciliation_diagnostics"
        ].get("ai_missing_related_task_id_count", 0),
    }
    contract_safety["passed"] = (
        not errors
        and contract_safety["legacy_ai_only_task_count"] == 0
        and contract_safety["terminal_task_reopened_count"] == 0
    )
    if args.contract_mode == "native":
        contract_safety["passed"] = (
            contract_safety["passed"]
            and contract_safety["legacy_ai_creation_event_count"] == 0
            and contract_safety["ai_missing_related_task_id_count"] == 0
            and contract_safety["unknown_task_id_rejection_count"] == 0
        )
    report["contract_safety"] = contract_safety
    if args.contract_mode == "legacy":
        report["legacy_safety"] = dict(contract_safety)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    metrics = report["metrics"]
    unexpected = sum(len(item.unexpected_tasks) for item in comparisons)
    quality_failures: list[str] = []
    if (
        args.minimum_precision is not None
        and metrics["task_precision"] + 1e-12 < args.minimum_precision
    ):
        quality_failures.append(
            f"precision {metrics['task_precision']:.4f} < {args.minimum_precision:.4f}"
        )
    if (
        args.minimum_recall is not None
        and metrics["task_recall"] + 1e-12 < args.minimum_recall
    ):
        quality_failures.append(
            f"recall {metrics['task_recall']:.4f} < {args.minimum_recall:.4f}"
        )
    if (
        args.maximum_unexpected is not None
        and unexpected > args.maximum_unexpected
    ):
        quality_failures.append(
            f"unexpected {unexpected} > {args.maximum_unexpected}"
        )
    report["quality_gate"] = {
        "blocking": args.contract_mode == "native",
        "minimum_precision": args.minimum_precision,
        "minimum_recall": args.minimum_recall,
        "maximum_unexpected": args.maximum_unexpected,
        "failures": quality_failures,
        "passed": not quality_failures,
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Replayed {len(comparisons)}/{len(trace_paths)} traces | "
        f"precision={metrics['task_precision']:.4f} "
        f"recall={metrics['task_recall']:.4f} unexpected={unexpected}"
    )
    print(f"Report: {args.report}")
    if errors or (
        args.contract_mode == "native"
        and (not contract_safety["passed"] or quality_failures)
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
