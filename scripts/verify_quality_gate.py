"""Validate immutable evaluation splits and produce an auditable release gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.quality.validation_gate import (
    load_split_manifest,
    task_f1,
    verify_evaluation_report,
    verify_split_manifest,
)


def _check(
    manifest_path: Path,
    dataset: Path,
    evaluation_report: Path | None,
) -> dict:
    manifest = load_split_manifest(manifest_path)
    verification = verify_split_manifest(manifest, dataset)
    errors = list(verification.errors)
    metrics: dict = {}
    if evaluation_report is not None:
        report = json.loads(evaluation_report.read_text(encoding="utf-8"))
        errors.extend(verify_evaluation_report(report, verification.case_ids))
        metrics = report.get("reviewed_metrics") or report.get("metrics") or {}
    return {
        "split_id": verification.split_id,
        "split_kind": verification.split_kind,
        "case_count": len(verification.case_ids),
        "manifest_verified": verification.passed,
        "evaluation_report": str(evaluation_report) if evaluation_report else None,
        "metrics": metrics,
        "task_identity_f1": task_f1(metrics) if metrics else None,
        "errors": errors,
        "passed": not errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--locked-manifest", type=Path, required=True)
    parser.add_argument("--locked-dataset", type=Path, default=Path("data/validation"))
    parser.add_argument("--locked-report", type=Path, required=True)
    parser.add_argument("--blind-manifest", type=Path)
    parser.add_argument("--blind-dataset", type=Path, default=Path("data/blind_test"))
    parser.add_argument("--blind-report", type=Path)
    parser.add_argument("--require-blind", action="store_true")
    parser.add_argument("--min-blind-precision", type=float, default=0.80)
    parser.add_argument("--min-blind-recall", type=float, default=0.80)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    locked = _check(args.locked_manifest, args.locked_dataset, args.locked_report)
    blind = None
    release_errors = list(locked["errors"])
    if args.blind_manifest or args.blind_report:
        if not (args.blind_manifest and args.blind_report):
            release_errors.append("blind manifest and blind report must be supplied together")
        else:
            blind = _check(args.blind_manifest, args.blind_dataset, args.blind_report)
            release_errors.extend(blind["errors"])
            metrics = blind["metrics"]
            if float(metrics.get("task_identity_precision", 0.0)) < args.min_blind_precision:
                release_errors.append("blind precision is below release threshold")
            if float(metrics.get("task_identity_recall", 0.0)) < args.min_blind_recall:
                release_errors.append("blind recall is below release threshold")
    elif args.require_blind:
        release_errors.append("blind validation is required for this gate")

    payload = {
        "schema_version": "1.0",
        "locked_validation": locked,
        "blind_validation": blind,
        "release_ready": not release_errors,
        "release_errors": release_errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Locked split: cases={locked['case_count']} passed={locked['passed']}")
    print(f"Release ready: {payload['release_ready']}")
    return 0 if not release_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
