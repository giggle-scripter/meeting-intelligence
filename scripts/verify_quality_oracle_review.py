"""Block V2 work if its reviewed Q2 baseline is incomplete or stale."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.quality.oracle import validate_grounded_task_evidence, validate_review_bundle


def _expected_count(dataset: Path) -> int:
    return sum(
        len(json.loads((case / "expected_output.json").read_text(encoding="utf-8")).get("tasks", []))
        for case in dataset.iterdir()
        if case.is_dir() and (case / "expected_output.json").is_file()
    )


def _q7_errors(report: dict) -> list[str]:
    required_modes = {
        "action_classifier_mode": "shadow",
        "candidate_router_mode": "shadow",
        "action_candidate_builder_mode": "shadow",
        "commitment_router_mode": "shadow",
        "ai_quality_uplift_mode": "shadow",
    }
    errors = [
        f"Q7 report {name} must be {value}"
        for name, value in required_modes.items()
        if report.get(name) != value
    ]
    diagnostics = report.get("pipeline_diagnostics", {})
    for name in ("action_classifier_error_count", "candidate_router_error_count"):
        if int(diagnostics.get(name, 0)) != 0:
            errors.append(f"Q7 report {name} must be 0")
    if report.get("execution_errors"):
        errors.append("Q7 report has execution errors")
    if report.get("metrics_are_partial"):
        errors.append("Q7 report is partial")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--attribution-report", type=Path, required=True)
    parser.add_argument("--q7-report", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path("data/validation"))
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = json.loads(args.review.read_text(encoding="utf-8"))
    report = json.loads(args.attribution_report.read_text(encoding="utf-8"))
    q7_report = json.loads(args.q7_report.read_text(encoding="utf-8"))
    errors = validate_review_bundle(
        bundle,
        expected_task_count=_expected_count(args.dataset),
        baseline_records=list(report.get("records", [])),
    )
    errors.extend(_q7_errors(q7_report))
    traces = {}
    for row in bundle.get("expected_tasks", []):
        case_id = row.get("case_id", "")
        matches = list(args.traces.glob(f"{case_id}-v1-*.json"))
        if len(matches) == 1:
            traces[case_id] = json.loads(matches[0].read_text(encoding="utf-8"))
    errors.extend(validate_grounded_task_evidence(bundle.get("expected_tasks", []), traces))
    payload = {
        "schema_version": "quality-oracle-review-gate-v2",
        "passed": not errors,
        "expected_task_count": _expected_count(args.dataset),
        "error_review_count": len(bundle.get("error_reviews", [])),
        "q7_classifier_error_count": int(
            q7_report.get("pipeline_diagnostics", {}).get("action_classifier_error_count", 0)
        ),
        "q7_candidate_router_error_count": int(
            q7_report.get("pipeline_diagnostics", {}).get("candidate_router_error_count", 0)
        ),
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Quality oracle review: passed={payload['passed']} errors={len(errors)}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
