"""Locked-input inventory and baseline drift checks."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from backend.app.evaluation import aggregate_results, compare_case

from .contracts import ProtocolSnapshot
from .hashing import atomic_write_json, canonical_json_hash, sha256_file


SUPERVISION_PATH = Path("data/quality/proposal-span-supervision-v3.jsonl")
SUPERVISION_SHA256 = "f22bb5a0fa52b8b33333050be15700b62383bb712085033192988d7f36aaebef"


class IntegrityStop(RuntimeError):
    """A fail-closed runbook stop condition."""


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _validation_cases(repo_root: Path) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for path in sorted((repo_root / "data/validation").glob("*/expected_output.json")):
        cases[path.parent.name] = _read_json(path)
    return cases


def _evidence_case_ids(path: Path) -> tuple[set[str], int]:
    case_ids: set[str] = set()
    row_count = 0
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            row_count += 1
            case_ids.add(row["case_id"])
            if row.get("review_status") != "HUMAN_CONFIRMED" or not row.get("reviewer") or not row.get("reviewed_at"):
                raise IntegrityStop("STOP_TASK_EVIDENCE_GATE_FAILED")
    return case_ids, row_count


def build_trace_manifest(repo_root: Path, relative_root: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    trace_root = repo_root / relative_root
    files = sorted(trace_root.glob("*-v1-*.json"), key=lambda path: path.relative_to(repo_root).as_posix())
    rows: list[dict[str, Any]] = []
    traces: dict[str, dict[str, Any]] = {}
    meeting_ids: list[str] = []
    for path in files:
        trace = _read_json(path)
        meeting_id = trace.get("meeting_id")
        meeting_ids.append(meeting_id)
        traces[meeting_id] = trace
        rows.append(
            {
                "path": path.relative_to(repo_root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    if any(count != 1 for count in Counter(meeting_ids).values()):
        raise IntegrityStop("STOP_TRACE_INVENTORY_INVALID")
    return rows, traces


def run_preflight(repo_root: Path, snapshot: ProtocolSnapshot, output_root: Path) -> dict[str, Any]:
    protocol = snapshot.protocol
    expected_by_case = _validation_cases(repo_root)
    validation_ids = set(expected_by_case)
    evidence_ids, reviewed_count = _evidence_case_ids(repo_root / protocol.evidence_path)
    zero_ids = {case_id for case_id, value in expected_by_case.items() if value.get("tasks", []) == []}
    if (
        len(validation_ids) != protocol.expected_cases
        or len(evidence_ids) != protocol.expected_evidence_cases
        or reviewed_count != protocol.expected_tasks
        or sum(len(item.get("tasks", [])) for item in expected_by_case.values()) != protocol.expected_tasks
        or zero_ids != set(protocol.zero_task_case_ids)
        or validation_ids - evidence_ids != zero_ids
    ):
        raise IntegrityStop("STOP_TRACE_INVENTORY_INVALID")
    if sha256_file(repo_root / SUPERVISION_PATH) != SUPERVISION_SHA256:
        raise IntegrityStop("STOP_SUPERVISION_CORPUS_DRIFT")

    manifests: dict[str, Any] = {}
    trace_sets: dict[str, dict[str, dict[str, Any]]] = {}
    for name, relative in (("pr38", protocol.trace_path), ("pr29", protocol.baseline_trace_path)):
        rows, traces = build_trace_manifest(repo_root, relative)
        if len(rows) != protocol.expected_cases or set(traces) != validation_ids:
            raise IntegrityStop("STOP_TRACE_INVENTORY_INVALID")
        trace_sets[name] = traces
        manifest_hash = canonical_json_hash(rows)
        atomic_write_json(output_root / "preflight" / f"{name}-trace-manifest.json", rows, pretty=False)
        manifests[name] = {"count": len(rows), "sha256": manifest_hash}

    comparisons = [
        compare_case(case_id, expected_by_case[case_id], {"tasks": trace_sets["pr29"][case_id].get("final_tasks", [])})
        for case_id in sorted(validation_ids)
    ]
    baseline = aggregate_results(comparisons)
    expected_counts = (
        baseline["expected_task_count"] == protocol.baseline_expected,
        baseline["actual_task_count"] == protocol.baseline_actual,
        baseline["matched_task_count"] == protocol.baseline_matched,
        baseline["missing_task_count"] == 111,
        baseline["unexpected_task_count"] == 179,
        abs(baseline["task_identity_f1"] - 0.5337620578778135) <= 1e-12,
    )
    if not all(expected_counts):
        raise IntegrityStop("STOP_Q2_BASELINE_DRIFT")
    report = {
        "schema_version": "distilled-proposal-ranker-preflight-v1",
        "passed": True,
        "protocol_sha256": snapshot.sha256,
        "evidence_sha256": sha256_file(repo_root / protocol.evidence_path),
        "supervision_sha256": sha256_file(repo_root / SUPERVISION_PATH),
        "validation_case_count": len(validation_ids),
        "evidence_case_count": len(evidence_ids),
        "zero_task_case_ids": sorted(zero_ids),
        "trace_manifests": manifests,
        "q2_baseline": baseline,
    }
    atomic_write_json(output_root / "preflight" / "preflight.json", report)
    return report
