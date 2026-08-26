"""Gate reviewed task evidence against the validation inventory and raw traces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.quality.oracle import (
    validate_grounded_task_evidence,
    validate_task_evidence_coverage,
)


def _expected_keys(dataset: Path) -> set[tuple[str, int]]:
    result: set[tuple[str, int]] = set()
    for case in dataset.iterdir():
        expected_path = case / "expected_output.json"
        if not case.is_dir() or not expected_path.is_file():
            continue
        case_id = json.loads((case / "metadata.json").read_text(encoding="utf-8"))["case_id"]
        tasks = json.loads(expected_path.read_text(encoding="utf-8")).get("tasks", [])
        result.update((case_id, index) for index, _ in enumerate(tasks))
    return result


def _load_traces(trace_directory: Path, case_ids: set[str]) -> tuple[dict[str, dict], list[str]]:
    traces: dict[str, dict] = {}
    errors: list[str] = []
    for case_id in sorted(case_ids):
        matches = list(trace_directory.glob(f"{case_id}-v1-*.json"))
        if len(matches) != 1:
            errors.append(f"{case_id}: expected exactly one trace, found {len(matches)}")
            continue
        traces[case_id] = json.loads(matches[0].read_text(encoding="utf-8"))
    return traces, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=Path("data/quality/task-evidence-v2.jsonl"))
    parser.add_argument("--dataset", type=Path, default=Path("data/validation"))
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.evidence.read_text(encoding="utf-8").splitlines() if line.strip()]
    expected_keys = _expected_keys(args.dataset)
    case_ids = {str(row.get("case_id", "")) for row in rows if row.get("case_id")}
    traces, errors = _load_traces(args.traces, case_ids)
    errors.extend(validate_task_evidence_coverage(rows, expected_keys))
    errors.extend(validate_grounded_task_evidence(rows, traces))
    payload = {
        "schema_version": "task-evidence-gate-v1",
        "passed": not errors,
        "expected_task_count": len(expected_keys),
        "reviewed_task_count": len(rows),
        "trace_count": len(traces),
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Task evidence gate: passed={payload['passed']} errors={len(errors)}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
