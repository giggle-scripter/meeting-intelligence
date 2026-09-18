"""V2.25 DEV42-only upstream proposal-candidate ceiling audit.

This audit reads only the locked PR29 runtime traces for development42 and the
development42 expected outputs for scoring.  It never reads diagnostic,
final-dev, or outer labels, and it does not fit a model.  Runtime candidate
objects are normalized into task-shaped records, deduplicated without gold,
then scored with the shared identity-first evaluator.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.evaluation import _maximum_weight_matching, aggregate_results, compare_case  # noqa: E402
from backend.app.dates.resolver import resolve_date_mention  # noqa: E402
from experiments.distilled_proposal_ranker.v2.protocol_bridge_v217 import (  # noqa: E402
    _date_mentions,
)
from scripts.experimental_distillation.run_v223_union_proposal_oof import _bridge_rows  # noqa: E402


IDENTITY_GATE = 0.57
FIELD_TOLERANCE = 0.03
TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def norm(value: Any) -> str:
    """Evaluator-compatible accent-insensitive, whitespace-stable text key."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("đ", "d")
    text = "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))
    return " ".join(TOKEN_RE.findall(text))


def identity_key(task: dict[str, Any]) -> tuple[str, str]:
    return norm(task.get("task_name")), norm(task.get("assignee"))


def meeting_date(repo_root: Path, case_id: str) -> str:
    metadata = repo_root / "data" / "validation" / case_id / "metadata.json"
    if metadata.is_file():
        return str(load(metadata).get("meeting_date") or "2026-01-01")
    return "2026-01-01"


def resolved_deadline(trace: dict[str, Any], mention_id: str, case_id: str, repo_root: Path) -> tuple[str, str]:
    mention = _date_mentions(trace).get(str(mention_id or ""))
    if mention is None:
        return "", ""
    return resolve_date_mention(meeting_date(repo_root, case_id), meeting_date(repo_root, case_id), mention), str(mention.raw_text or "")


@dataclass(frozen=True)
class RuntimeRow:
    case_id: str
    task: dict[str, Any]
    source: str
    ordinal: int
    quality: tuple[float, int, int, str]
    provenance: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return identity_key(self.task)


SOURCE_PRIORITY = {
    "accepted_final": 0,
    "intermediate_state": 1,
    "event_stage": 2,
    "occurrence_bridge": 3,
    "action_candidates": 4,
    "rejected_candidates": 5,
}


def task_from_state(state: dict[str, Any], trace: dict[str, Any], case_id: str, repo_root: Path) -> dict[str, Any]:
    due_date, due_text = resolved_deadline(trace, str(state.get("deadline_mention_id", "")), case_id, repo_root)
    return {
        "task_name": str(state.get("task_name", "")),
        "assignee": str(state.get("assignee", "")),
        "start_date": meeting_date(repo_root, case_id),
        "due_date": due_date,
        "due_date_text": due_text,
        "deadline_mention_id": str(state.get("deadline_mention_id", "")),
        "status": "Proposed",
    }


def event_task(event: dict[str, Any], trace: dict[str, Any], case_id: str, repo_root: Path) -> dict[str, Any]:
    due_date, due_text = resolved_deadline(trace, str(event.get("deadline_mention_id", "")), case_id, repo_root)
    return {
        "task_name": str(event.get("action_text", "")),
        "assignee": str(event.get("assignee", "")),
        "start_date": meeting_date(repo_root, case_id),
        "due_date": due_date,
        "due_date_text": due_text,
        "deadline_mention_id": str(event.get("deadline_mention_id", "")),
        "status": "Proposed",
    }


def candidate_task(record: dict[str, Any], trace: dict[str, Any], case_id: str, repo_root: Path) -> dict[str, Any]:
    """Adapt one action-candidate object using runtime evidence only."""

    source_ids = {str(x) for x in record.get("primary_clause_ids", []) if x}
    source_ids.update(str(x) for x in record.get("support_clause_ids", []) if x)
    events = [x for x in trace.get("events_after_deduplication", []) if isinstance(x, dict)]
    linked = [x for x in events if source_ids.intersection(str(y) for y in x.get("source_clause_ids", []))]
    linked.sort(key=lambda x: (float(x.get("confidence", 0.0) or 0.0), -int(x.get("order_index", 0) or 0)), reverse=True)
    event = linked[0] if linked else {}
    spans = [x for x in record.get("action_spans", []) if isinstance(x, dict) and x.get("text")]
    span_text = str(spans[0].get("text", "")) if spans else ""
    mention_id = str(record.get("deadline_mention_ids", [""])[0] if record.get("deadline_mention_ids") else event.get("deadline_mention_id", ""))
    due_date, due_text = resolved_deadline(trace, mention_id, case_id, repo_root)
    return {
        "task_name": str(event.get("action_text", "") or span_text).strip(),
        "assignee": str(event.get("assignee", "") or "").strip(),
        "start_date": meeting_date(repo_root, case_id),
        "due_date": due_date,
        "due_date_text": due_text,
        "deadline_mention_id": mention_id,
        "status": "Proposed",
    }


def rows_for_stage(case_id: str, trace: dict[str, Any], repo_root: Path) -> dict[str, list[RuntimeRow]]:
    stages: dict[str, list[RuntimeRow]] = defaultdict(list)
    for i, raw in enumerate(trace.get("final_tasks", []) or []):
        if isinstance(raw, dict) and raw.get("task_name"):
            stages["accepted_final"].append(RuntimeRow(case_id, dict(raw), "accepted_final", i, (0.95, 0, -i, str(raw.get("task_name", ""))), ("final_tasks",)))
    for i, raw in enumerate(trace.get("task_states", []) or []):
        if not isinstance(raw, dict) or raw.get("status") not in {"CONFIRMED", "PROVISIONAL"} or not raw.get("task_name"):
            continue
        task = task_from_state(raw, trace, case_id, repo_root)
        stages["intermediate_state"].append(RuntimeRow(case_id, task, "intermediate_state", i, (float(raw.get("confidence", 0.0) or 0.0), 0, -i, str(task["task_name"])), ("task_states", str(raw.get("status", "")))))
    event_types = {"TASK_COMMITMENT", "TASK_CREATE", "OWNER_ASSIGN"}
    for i, raw in enumerate(trace.get("events_after_deduplication", []) or []):
        if not isinstance(raw, dict) or raw.get("event_type") not in event_types or not raw.get("action_text"):
            continue
        task = event_task(raw, trace, case_id, repo_root)
        stages["event_stage"].append(RuntimeRow(case_id, task, "event_stage", i, (float(raw.get("confidence", 0.0) or 0.0), 0, -i, str(task["task_name"])), ("events_after_deduplication", str(raw.get("event_type", "")))))
    records = [x for x in (trace.get("action_candidates_v2") or {}).get("records", []) if isinstance(x, dict)]
    decisions = {str(x.get("candidate_id")): x for x in (trace.get("commitment_router_v2") or {}).get("decisions", []) if isinstance(x, dict)}
    for i, raw in enumerate(records):
        task = candidate_task(raw, trace, case_id, repo_root)
        if not task.get("task_name"):
            continue
        decision = decisions.get(str(raw.get("candidate_id")), {})
        route = str(decision.get("route", ""))
        rejected = bool(raw.get("negative_signals")) or route in {"DROP", "CONTEXT_ONLY"} or str(raw.get("state", "")) in {"REFERENCE_ONLY"}
        target = "rejected_candidates" if rejected else "action_candidates"
        confidence = float(raw.get("confidence", 0.0) or 0.0)
        stages[target].append(RuntimeRow(case_id, task, target, i, (confidence, 0, -i, str(task["task_name"])), ("action_candidates_v2", str(raw.get("candidate_kind", "")), str(raw.get("state", "")), route)))
    # Reconstruct the V2.17 occurrence bridge from runtime fields only.  This
    # helper deliberately does not accept expected tasks or evidence labels.
    for i, row in enumerate(_bridge_rows(case_id, trace, meeting_date(repo_root, case_id))):
        stages["occurrence_bridge"].append(RuntimeRow(case_id, dict(row.task), "occurrence_bridge", i, (float(row.confidence), 0, -i, row.title), ("runtime_occurrence_bridge", row.bridge_retrieval)))
    return stages


def merge_rows(rows: Sequence[RuntimeRow]) -> list[RuntimeRow]:
    """Gold-free deterministic identity dedupe, preserving best fields."""

    ordered = sorted(rows, key=lambda row: (SOURCE_PRIORITY.get(row.source, 99), row.ordinal, row.key, row.task.get("task_name", "")))
    out: list[RuntimeRow] = []
    by_key: dict[tuple[str, str], int] = {}
    for row in ordered:
        key = row.key
        if not key[0]:
            continue
        if key not in by_key:
            by_key[key] = len(out)
            out.append(row)
            continue
        index = by_key[key]
        previous = out[index]
        task = dict(previous.task)
        for field in ("assignee", "start_date", "due_date", "due_date_text", "deadline_mention_id", "status"):
            if not task.get(field) and row.task.get(field):
                task[field] = row.task[field]
        out[index] = RuntimeRow(previous.case_id, task, previous.source, previous.ordinal, previous.quality, tuple(dict.fromkeys(previous.provenance + row.provenance)))
    return out


def source_metrics(case_ids: Sequence[str], rows_by_case: dict[str, list[RuntimeRow]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    comparisons = [compare_case(cid, expected[cid], {"tasks": [x.task for x in rows_by_case.get(cid, [])]}) for cid in sorted(case_ids)]
    return aggregate_results(comparisons)


def oracle_metrics(case_ids: Sequence[str], rows_by_case: dict[str, list[RuntimeRow]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Score a perfect selector over this source's identity-matchable rows."""

    selected: dict[str, list[RuntimeRow]] = {}
    for cid in case_ids:
        tasks = [row.task for row in rows_by_case.get(cid, [])]
        pairs = _maximum_weight_matching(list(expected[cid].get("tasks", [])), tasks)
        selected[cid] = [rows_by_case[cid][actual_index] for _, actual_index in pairs]
    score = source_metrics(case_ids, selected, expected)
    score["definition"] = "perfect selector over per-meeting Hungarian identity-matchable candidates"
    score["oracle_identity_recall"] = score["task_identity_recall"]
    score["oracle_identity_f1"] = score["task_identity_f1"]
    score["matchable_candidates"] = score["matched_task_count"]
    score["candidate_count"] = sum(len(rows_by_case.get(cid, [])) for cid in case_ids)
    score["passed_gate"] = float(score["oracle_identity_f1"]) >= IDENTITY_GATE
    return score


def family_keys(case_id: str) -> dict[str, str]:
    parts = case_id.split("-")
    # The corpus uses W/LENGTH/CLUSTER/N/DOMAIN/TEMPLATE[/MODIFIER]/INDEX.
    # Template is the semantic scenario token; family is domain+template.
    template_index = 5 if len(parts) > 5 else -1
    template = parts[template_index] if template_index >= 0 else "unknown"
    family = "-".join(parts[4:6]) if len(parts) >= 6 else "unknown"
    return {"template": template, "family": family}


def grouped_metrics(case_ids: Sequence[str], key: str, rows: dict[str, dict[str, list[RuntimeRow]]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[str]] = defaultdict(list)
    for cid in case_ids:
        groups[family_keys(cid)[key]].append(cid)
    result: dict[str, Any] = {}
    for group, ids in sorted(groups.items()):
        result[group] = {source: source_metrics(ids, by_case, expected) for source, by_case in rows.items()}
    group_names = sorted(groups)
    for source in rows:
        vals = [float(result[g][source]["task_identity_f1"]) for g in group_names]
        result.setdefault("_summary", {})[source] = {
            "groups": len(vals),
            "macro_f1": sum(vals) / len(vals) if vals else 0.0,
            "min_f1": min(vals) if vals else 0.0,
            "max_f1": max(vals) if vals else 0.0,
        }
    return result


def grouped_oracle_metrics(case_ids: Sequence[str], key: str, rows: dict[str, dict[str, list[RuntimeRow]]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[str]] = defaultdict(list)
    for cid in case_ids:
        groups[family_keys(cid)[key]].append(cid)
    result: dict[str, Any] = {}
    for group, ids in sorted(groups.items()):
        result[group] = {source: oracle_metrics(ids, by_case, expected) for source, by_case in rows.items()}
    group_names = sorted(groups)
    for source in rows:
        vals = [float(result[g][source]["oracle_identity_f1"]) for g in group_names]
        result.setdefault("_summary", {})[source] = {
            "groups": len(vals),
            "macro_oracle_f1": sum(vals) / len(vals) if vals else 0.0,
            "min_oracle_f1": min(vals) if vals else 0.0,
            "max_oracle_f1": max(vals) if vals else 0.0,
        }
    return result


def compact_partition(value: dict[str, Any]) -> dict[str, Any]:
    """Keep group output small while retaining identity/field evidence."""

    observed_fields = ("task_identity_precision", "task_identity_recall", "task_identity_f1", "field_accuracy", "actual_task_count", "matched_task_count")
    oracle_fields = ("oracle_identity_recall", "oracle_identity_f1", "field_accuracy", "candidate_count", "matchable_candidates")
    result: dict[str, Any] = {}
    for group, sources in value.items():
        if group == "_summary":
            result[group] = sources
            continue
        result[group] = {}
        for source, metrics in sources.items():
            fields = oracle_fields if "oracle_identity_f1" in metrics else observed_fields
            result[group][source] = {field: metrics[field] for field in fields if field in metrics}
    return result


def run(repo_root: Path, output: Path) -> dict[str, Any]:
    audit_dir = repo_root / "evaluation/runtime/experimental-distillation-v2/v224-dev42-crossfit-gate"
    split_audit = load(audit_dir / "split-access-audit.json")
    case_ids = sorted(str(x) for x in split_audit["access"]["development42"]["case_ids_read"])
    if len(case_ids) != 42 or split_audit["access"].get("diagnostic9", {}).get("case_ids_read") is None:
        raise ValueError("STOP_DEV42_SCOPE")
    trace_root = repo_root / "evaluation/runtime/pr29-q2-traces"
    traces: dict[str, dict[str, Any]] = {}
    for cid in case_ids:
        paths = sorted(trace_root.glob(f"{cid}-v1-*.json"))
        if len(paths) != 1:
            raise ValueError(f"STOP_TRACE_INVENTORY:{cid}:{len(paths)}")
        traces[cid] = load(paths[0])
    expected = {cid: load(repo_root / "data/validation" / cid / "expected_output.json") for cid in case_ids}
    stage_rows = {cid: rows_for_stage(cid, traces[cid], repo_root) for cid in case_ids}
    stage_names = ["accepted_final", "intermediate_state", "event_stage", "occurrence_bridge", "action_candidates", "rejected_candidates"]
    raw: dict[str, dict[str, list[RuntimeRow]]] = {name: {} for name in stage_names}
    dedup: dict[str, dict[str, list[RuntimeRow]]] = {name: {} for name in stage_names}
    for name in stage_names:
        for cid in case_ids:
            values = stage_rows[cid].get(name, [])
            raw[name][cid] = values
            dedup[name][cid] = merge_rows(values)
    unions: dict[str, dict[str, list[RuntimeRow]]] = {
        "final_plus_bridge": {},
        "final_plus_intermediate": {},
        "final_plus_bridge_plus_intermediate": {},
        "all_runtime_stages": {},
    }
    for cid in case_ids:
        unions["final_plus_bridge"][cid] = merge_rows(dedup["accepted_final"][cid] + dedup["occurrence_bridge"][cid])
        unions["final_plus_intermediate"][cid] = merge_rows(dedup["accepted_final"][cid] + dedup["intermediate_state"][cid])
        unions["final_plus_bridge_plus_intermediate"][cid] = merge_rows(dedup["accepted_final"][cid] + dedup["occurrence_bridge"][cid] + dedup["intermediate_state"][cid])
        unions["all_runtime_stages"][cid] = merge_rows([row for name in stage_names for row in dedup[name][cid]])
    all_sources = {**dedup, **unions}
    metrics = {name: source_metrics(case_ids, by_case, expected) for name, by_case in all_sources.items()}
    oracle = {name: oracle_metrics(case_ids, by_case, expected) for name, by_case in all_sources.items()}
    inventory = {}
    for name in all_sources:
        source_rows = raw[name] if name in raw else all_sources[name]
        dedup_rows = dedup[name] if name in dedup else all_sources[name]
        inventory[name] = {
            "raw_count": sum(len(source_rows[cid]) for cid in case_ids),
            "dedup_count": sum(len(dedup_rows[cid]) for cid in case_ids),
            "nonempty_meetings": sum(bool(source_rows[cid]) for cid in case_ids),
            "mean_per_meeting": sum(len(source_rows[cid]) for cid in case_ids) / len(case_ids),
        }
    grouped = {
        "leave_template_out": {
            "observed": compact_partition(grouped_metrics(case_ids, "template", all_sources, expected)),
            "oracle": compact_partition(grouped_oracle_metrics(case_ids, "template", all_sources, expected)),
        },
        "leave_family_out": {
            "observed": compact_partition(grouped_metrics(case_ids, "family", all_sources, expected)),
            "oracle": compact_partition(grouped_oracle_metrics(case_ids, "family", all_sources, expected)),
        },
    }
    baseline = metrics["final_plus_bridge"]
    expanded = []
    # Only unions that strictly expand the locked V2.24 baseline are eligible
    # for the recommendation.  ``final_plus_intermediate`` is reported as an
    # alternate replacement, but it drops the bridge and is not an expansion.
    for name in ("final_plus_bridge_plus_intermediate", "all_runtime_stages"):
        item = metrics[name]
        oracle_item = oracle[name]
        integrity_ok = float(oracle_item["field_accuracy"]) >= float(oracle["final_plus_bridge"]["field_accuracy"]) - FIELD_TOLERANCE if name != "final_plus_bridge" else True
        volume_ok = inventory[name]["mean_per_meeting"] <= 20.0
        expanded.append({"source": name, "observed_identity_f1": item["task_identity_f1"], "oracle_identity_f1": oracle_item["oracle_identity_f1"], "oracle_recall": oracle_item["oracle_identity_recall"], "field_accuracy": oracle_item["field_accuracy"], "mean_per_meeting": inventory[name]["mean_per_meeting"], "reasonable_volume": volume_ok, "field_integrity": integrity_ok, "ceiling_supports_gate": float(oracle_item["oracle_identity_f1"]) >= IDENTITY_GATE})
    eligible = [x for x in expanded if x["ceiling_supports_gate"] and x["reasonable_volume"] and x["field_integrity"]]
    recommendation = {
        "readiness": "EXPANSION_SUPPORTED" if eligible else "NO_EXPANDED_GENERATOR_SUPPORTED",
        "smallest_expansion": eligible[0]["source"] if eligible else None,
        "gate": IDENTITY_GATE,
        "baseline_union": "final_plus_bridge",
        "reason": "Oracle ceiling and volume/field integrity checks are audit-only; no selector or model was fit.",
    }
    report = {
        "schema_version": "v225-dev42-upstream-candidate-audit-v1",
        "status": "complete",
        "scope": {"development_meetings": 42, "diagnostic_labels_read": False, "final_dev_labels_read": False, "outer_labels_read": False, "gold_used_for": "identity scoring only", "training_performed": False, "teacher_calls": 0, "provider_calls": 0, "kaggle_calls": 0},
        "inputs": {"trace_directory": "evaluation/runtime/pr29-q2-traces", "split_access_audit": str((audit_dir / "split-access-audit.json").relative_to(repo_root)), "normalization": "NFKC/casefold/đ->d/NFD accent strip/regex token join", "dedupe_key": "normalized task_name + normalized assignee", "dedupe_order": "accepted_final, intermediate_state, event_stage, occurrence_bridge, action_candidates, rejected_candidates; first row wins and missing fields are filled from later rows", "stage_mapping": {"accepted/final": "accepted_final from final_tasks", "intermediate": "intermediate_state from CONFIRMED/PROVISIONAL task_states plus event_stage from create/commitment/owner events", "occurrence bridge": "occurrence_bridge reconstructed from runtime action candidates/events/date mentions", "candidates": "action_candidates from non-rejected action_candidates_v2 records", "rejected": "rejected_candidates from negative-signal, reference-only, drop, or context-only action candidates", "later proposal stages": "task_create_proposals, ai_quality_uplift_v1, ai_mutation_router, and candidate_router_shadow contain aggregate/empty arrays in locked PR29 traces; no proposal objects are fabricated"}},
        "stage_inventory": inventory,
        "metrics": metrics,
        "oracle_metrics": oracle,
        "partitions": grouped,
        "expanded_generator_checks": expanded,
        "recommendation": recommendation,
    }
    dump(output / "audit.json", report)
    lines = ["# V2.25 DEV42 upstream proposal-candidate audit", "", "Runtime-only candidate inventories from locked PR29 traces; development42 gold is used only after candidate construction for shared evaluator scoring. Diagnostic, final-dev, and outer labels remained closed.", "", "| Source/union | Candidates | Observed F1 | Oracle R | Oracle F1 | Oracle field accuracy | Mean/meeting |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name in all_sources:
        item = metrics[name]
        ceiling = oracle[name]
        lines.append(f"| {name} | {inventory[name]['dedup_count']} | {item['task_identity_f1']:.4f} | {ceiling['oracle_identity_recall']:.4f} | {ceiling['oracle_identity_f1']:.4f} | {ceiling['field_accuracy']:.4f} | {inventory[name]['mean_per_meeting']:.2f} |")
    lines += ["", f"Recommendation: **{recommendation['readiness']}**; smallest eligible expansion: `{recommendation['smallest_expansion']}`.", "", "Leave-template and leave-family observed and oracle group metrics are persisted in `audit.json`; these are deterministic group holdouts with no fitting or threshold selection."]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    dump(output / "status.json", {"schema_version": "v225-status-v1", "status": "complete", "training_performed": False, "diagnostic_labels_read": False, "final_dev_labels_read": False, "outer_labels_read": False, "metrics_persisted": True})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args.root.resolve(), args.output.resolve())
    except BaseException as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        dump(args.output / "status.json", {"schema_version": "v225-status-v1", "status": "STOP", "error": repr(exc), "training_performed": False, "metrics_persisted": False})
        print(json.dumps({"status": "STOP", "error": repr(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": report["status"], "baseline_union_f1": report["metrics"]["final_plus_bridge"]["task_identity_f1"], "all_runtime_f1": report["metrics"]["all_runtime_stages"]["task_identity_f1"], "recommendation": report["recommendation"]["readiness"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
