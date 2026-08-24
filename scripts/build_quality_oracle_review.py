"""Review the Q2 trace into a complete, reproducible V2 evidence bundle.

The command works only on an attribution report generated with traces.  It
never touches production output or validation expectations.  The resulting
bundle contains explicit clause IDs and offsets, so a later proposal/identity
experiment cannot quietly train on ungrounded task names.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.quality.oracle import EXPECTED_SCHEMA_VERSION, build_oracle_report


def _normalize(value: Any) -> str:
    text = str(value or "").casefold().replace("đ", "d")
    text = "".join(
        char for char in unicodedata.normalize("NFD", text) if not unicodedata.combining(char)
    )
    return " ".join(text.split())


def _trace_for(traces: Path, case_id: str) -> dict[str, Any]:
    matches = sorted(traces.glob(f"{case_id}-v1-*.json"))
    if len(matches) != 1:
        raise ValueError(f"{case_id}: expected exactly one trace, found {len(matches)}")
    return json.loads(matches[0].read_text(encoding="utf-8"))


def _authority(flags: set[str]) -> str:
    if "DIRECT_ASSIGNMENT" in flags:
        return "DIRECT_ASSIGNMENT"
    if "FIRST_PERSON_COMMITMENT" in flags:
        return "SELF_COMMITMENT"
    if "CONFIRMATION" in flags:
        return "EXPLICIT_ACCEPTANCE"
    if "ACTION_VERB" in flags:
        return "TASK_DECISION"
    return "TRANSCRIPT_GROUNDED_ACTION"


def _owner_evidence(task: dict[str, Any], source_ids: list[str], clauses: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    owner = str(task.get("assignee") or "").strip()
    if not owner:
        return None
    owner_norm = _normalize(owner)
    for clause_id in source_ids:
        clause = clauses[clause_id]
        text = str(clause.get("text_raw") or "")
        if owner_norm in _normalize(text) or owner_norm == _normalize(clause.get("speaker_name")):
            return {"clause_id": clause_id, "owner": owner, "basis": "EXPLICIT_OR_SPEAKER"}
    return {"clause_id": source_ids[0], "owner": owner, "basis": "TASK_FINAL_STATE"}


def _deadline_ids(task: dict[str, Any], dates: dict[str, dict[str, Any]]) -> list[str]:
    target = _normalize(task.get("due_date_text"))
    if not target:
        return []
    matches = [
        mention_id for mention_id, mention in dates.items()
        if target in _normalize(mention.get("raw_text"))
        or _normalize(mention.get("raw_text")) in target
    ]
    return sorted(matches)


def _expected_reviews(report: dict[str, Any], traces: Path, dataset: Path) -> list[dict[str, Any]]:
    report_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in report.get("records", []):
        if row.get("kind") == "EXPECTED_EVIDENCE":
            report_rows[str(row["case_id"])].append(row)
    result: list[dict[str, Any]] = []
    for case_dir in sorted(path for path in dataset.iterdir() if path.is_dir()):
        case_id = case_dir.name
        expected = json.loads((case_dir / "expected_output.json").read_text(encoding="utf-8"))
        rows = report_rows[case_id]
        tasks = list(expected.get("tasks", []))
        if len(rows) != len(tasks):
            raise ValueError(f"{case_id}: expected-record count does not match expected tasks")
        trace = _trace_for(traces, case_id)
        clauses = {str(item["clause_id"]): item for item in trace.get("clauses", [])}
        annotations = dict(trace.get("annotations", {}))
        dates = dict(trace.get("date_mentions", {}))
        for index, (task, row) in enumerate(zip(tasks, rows, strict=True)):
            evidence = list(row.get("suggested_source_evidence", []))
            source_ids = [str(item["clause_id"]) for item in evidence if item.get("clause_id") in clauses]
            if not source_ids:
                raise ValueError(f"{case_id}[{index}]: no grounded source suggestion")
            primary = source_ids[0]
            source_text = str(clauses[primary].get("text_raw") or "")
            flags = set(annotations.get(primary, {}).get("flags", []))
            result.append({
                "case_id": case_id,
                "expected_task_index": index,
                "expected_task": task,
                "source_clause_ids": source_ids,
                "action_spans": [{"clause_id": primary, "start": 0, "end": len(source_text), "text": source_text}],
                "authority": _authority(flags),
                "owner_evidence": _owner_evidence(task, source_ids, clauses),
                "deadline_mention_ids": _deadline_ids(task, dates),
                "lifecycle_clause_ids": [],
                "recap_clause_ids": [primary] if "tổng kết" in _normalize(source_text) else [],
                "review_status": "SUGGESTED",
                "review_basis": "LEXICAL_TRACE_SUGGESTION_ONLY",
            })
    return result


def _error_reviews(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for record in report.get("records", []):
        kind = str(record.get("kind"))
        if kind not in {"MISSING", "UNEXPECTED", "FIELD_ERROR"}:
            continue
        rows.append({
            "attribution_record_id": record["record_id"],
            "case_id": record["case_id"],
            "kind": kind,
            "task": record.get("task", {}),
            "category": record.get("suggested_taxonomy", ""),
            "source_clause_ids": [
                item["clause_id"] for item in record.get("suggested_source_evidence", [])
            ],
            "review_status": "SUGGESTED",
            "review_basis": "Q2_TRACE_HEURISTIC_SUGGESTION_ONLY",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("attribution_report", type=Path)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path("data/validation"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--oracle-output", type=Path, required=True)
    args = parser.parse_args()

    report_bytes = args.attribution_report.read_bytes()
    report = json.loads(report_bytes)
    bundle = {
        "schema_version": EXPECTED_SCHEMA_VERSION,
        "baseline_attribution_sha256": sha256(report_bytes).hexdigest(),
        "baseline_config": {
            "action_candidate_builder_mode": report.get("action_candidate_builder_mode"),
            "commitment_router_mode": report.get("commitment_router_mode"),
            "with_meeting_notes": report.get("with_meeting_notes"),
        },
        "expected_tasks": _expected_reviews(report, args.traces, args.dataset),
        "error_reviews": _error_reviews(report),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    oracle = build_oracle_report(report["final_metrics"], bundle)
    oracle["baseline_attribution_sha256"] = bundle["baseline_attribution_sha256"]
    args.oracle_output.parent.mkdir(parents=True, exist_ok=True)
    args.oracle_output.write_text(json.dumps(oracle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Quality review: expected={len(bundle['expected_tasks'])} "
        f"errors={len(bundle['error_reviews'])} output={args.output}"
    )


if __name__ == "__main__":
    main()
