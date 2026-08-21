"""Create a review-only, stage-level quality attribution report for fixtures."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.evaluation import aggregate_results, compare_case
from backend.app.models import MeetingInput, MeetingNoteInput
from backend.app.pipeline import process_meeting_by_version
from backend.app.quality import attribute_case, attribute_expected_tasks, summarize_attribution


def _trace_path(directory: Path, meeting_id: str) -> Path:
    safe_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in meeting_id)
    return directory / f"{safe_id or 'meeting'}-v1-{sha256(meeting_id.encode('utf-8')).hexdigest()[:10]}.json"


def _load_case(case_dir: Path, *, with_notes: bool) -> tuple[dict[str, Any], str, str, dict[str, Any], str | None] | None:
    metadata_path = case_dir / "metadata.json"
    expected_path = case_dir / "expected_output.json"
    transcript_path = next(iter(case_dir.glob("transcript.*")), None)
    if not metadata_path.exists() or not expected_path.exists() or transcript_path is None:
        return None
    note_path = case_dir / "meeting_note.txt"
    return (
        json.loads(metadata_path.read_text(encoding="utf-8")),
        transcript_path.read_text(encoding="utf-8-sig"),
        transcript_path.name,
        json.loads(expected_path.read_text(encoding="utf-8")),
        note_path.read_text(encoding="utf-8") if with_notes and note_path.exists() else None,
    )


def analyze_dataset(
    dataset: Path,
    *,
    trace_directory: Path,
    with_notes: bool = True,
    action_candidate_builder_mode: str = "off",
    action_candidate_builder_version: str = "action-candidate-v2",
) -> dict[str, Any]:
    records = []
    comparisons = []
    action_candidate_kind_counts: Counter[str] = Counter()
    action_candidate_state_counts: Counter[str] = Counter()
    action_candidate_count = 0
    action_candidate_error_count = 0
    case_count = 0
    for case_dir in sorted(path for path in dataset.iterdir() if path.is_dir()):
        loaded = _load_case(case_dir, with_notes=with_notes)
        if loaded is None:
            continue
        metadata, transcript, file_name, expected, note = loaded
        case_id = str(metadata["case_id"])
        result = process_meeting_by_version(
            MeetingInput(
                case_id, metadata["meeting_title"], metadata["meeting_date"],
                transcript, file_name,
                MeetingNoteInput(note, "Thư ký", "SECRETARY") if note else None,
            ),
            pipeline_version="v1", trace_enabled=True,
            trace_directory=str(trace_directory),
            action_candidate_builder_mode=action_candidate_builder_mode,
            action_candidate_builder_version=action_candidate_builder_version,
        )
        actual = asdict(result)
        comparison = compare_case(case_id, expected, actual)
        trace = json.loads(_trace_path(trace_directory, case_id).read_text(encoding="utf-8"))
        candidate_trace = trace.get("action_candidates_v2", {})
        candidate_records = candidate_trace.get("records", [])
        action_candidate_count += len(candidate_records)
        action_candidate_error_count += candidate_trace.get("error_count", 0)
        action_candidate_kind_counts.update(
            item["candidate_kind"] for item in candidate_records
        )
        action_candidate_state_counts.update(item["state"] for item in candidate_records)
        records.extend(attribute_expected_tasks(case_id, expected, trace, metadata))
        records.extend(attribute_case(case_id, expected, actual, comparison, trace, metadata))
        comparisons.append(comparison)
        case_count += 1
    return {
        "schema_version": "quality-attribution-v1",
        "with_meeting_notes": with_notes,
        "action_candidate_builder_mode": action_candidate_builder_mode,
        "action_candidate_builder_version": action_candidate_builder_version,
        "action_candidate_summary": {
            "count": action_candidate_count,
            "kind_counts": dict(sorted(action_candidate_kind_counts.items())),
            "state_counts": dict(sorted(action_candidate_state_counts.items())),
            "error_count": action_candidate_error_count,
        },
        "case_count": case_count,
        "final_metrics": aggregate_results(comparisons),
        "attribution_summary": summarize_attribution(records),
        "records": [record.model_dump(mode="json") for record in records],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--trace-directory", type=Path, default=Path("evaluation/runtime/quality-traces"))
    parser.add_argument("--without-meeting-notes", action="store_true")
    parser.add_argument(
        "--action-candidate-builder-mode", choices=("off", "shadow"), default="off"
    )
    parser.add_argument(
        "--action-candidate-builder-version", default="action-candidate-v2"
    )
    args = parser.parse_args()
    report = analyze_dataset(
        args.dataset, trace_directory=args.trace_directory,
        with_notes=not args.without_meeting_notes,
        action_candidate_builder_mode=args.action_candidate_builder_mode,
        action_candidate_builder_version=args.action_candidate_builder_version,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=(
                "record_id", "case_id", "wave", "kind", "task_name", "assignee",
                "suggested_taxonomy", "review_status", "confidence",
                "source_clause_ids", "candidate_window_ids", "provenance_event_ids",
                "provenance_task_ids",
            ))
            writer.writeheader()
            for record in report["records"]:
                writer.writerow({
                    "record_id": record["record_id"], "case_id": record["case_id"],
                    "wave": record["wave"], "kind": record["kind"],
                    "task_name": record["task"].get("task_name", ""),
                    "assignee": record["task"].get("assignee", ""),
                    "suggested_taxonomy": record["suggested_taxonomy"],
                    "review_status": record["review_status"], "confidence": record["confidence"],
                    "source_clause_ids": ";".join(item["clause_id"] for item in record["suggested_source_evidence"]),
                    "candidate_window_ids": ";".join(record["candidate_window_ids"]),
                    "provenance_event_ids": ";".join(record["provenance_event_ids"]),
                    "provenance_task_ids": ";".join(record["provenance_task_ids"]),
                })
    summary = report["attribution_summary"]
    print(
        f"Quality attribution: cases={report['case_count']} records={summary['record_count']} "
        f"missing-source-coverage={summary['missing_source_coverage']['rate']:.3f}"
    )
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
