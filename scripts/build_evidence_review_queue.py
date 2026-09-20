"""Convert a runtime quality-attribution report into a reviewer work queue."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.quality.review_models import AttributionRecord
from backend.app.quality.review_queue import build_review_queue


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("attribution_report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.attribution_report.read_text(encoding="utf-8"))
    records = [AttributionRecord.model_validate(item) for item in payload.get("records", [])]
    queue = build_review_queue(records)
    output = {
        "schema_version": "quality-review-queue-v1",
        "source_report": str(args.attribution_report),
        "review_status": "NEEDS_REVIEW",
        "items": [item.model_dump(mode="json") for item in queue],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=(
                "queue_id", "case_id", "queue_kind", "task_name", "assignee",
                "suggested_taxonomy", "review_status", "evidence_clause_ids",
            ))
            writer.writeheader()
            for item in queue:
                writer.writerow({
                    "queue_id": item.queue_id, "case_id": item.case_id,
                    "queue_kind": item.queue_kind, "task_name": item.task.get("task_name", ""),
                    "assignee": item.task.get("assignee", ""),
                    "suggested_taxonomy": item.suggested_taxonomy,
                    "review_status": item.review_status.value,
                    "evidence_clause_ids": ";".join(evidence.clause_id for evidence in item.evidence),
                })
    print(f"Review queue: items={len(queue)} output={args.output}")


if __name__ == "__main__":
    main()
