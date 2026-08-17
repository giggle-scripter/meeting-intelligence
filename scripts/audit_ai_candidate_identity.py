"""Provider-free preflight for V1 AI task candidate identity safety."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.ai.schemas import AiEventResponse
from backend.app.models import MeetingInput, MeetingNoteInput
from backend.app.pipeline import process_meeting
from backend.app.reduction.task_ledger import candidate_aliases_conflict


class CapturingAiClient:
    enabled = True

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def extract_events(self, payload: dict) -> AiEventResponse:
        self.payloads.append(payload)
        return AiEventResponse(events=[])


def _case_ids(dataset: Path, case_csv: Path | None) -> list[str]:
    if case_csv is None:
        return sorted(path.name for path in dataset.iterdir() if path.is_dir())
    with case_csv.open(encoding="utf-8-sig", newline="") as handle:
        return [row["case_id"].strip() for row in csv.DictReader(handle)]


def _load_meeting(case_dir: Path, *, with_note: bool) -> MeetingInput:
    metadata = json.loads(
        (case_dir / "metadata.json").read_text(encoding="utf-8")
    )
    note_path = case_dir / "meeting_note.txt"
    note = (
        MeetingNoteInput(
            note_path.read_text(encoding="utf-8"),
            "Thư ký",
            "SECRETARY",
        )
        if with_note and note_path.exists()
        else None
    )
    return MeetingInput(
        metadata["meeting_id"],
        metadata["meeting_title"],
        metadata["meeting_date"],
        (case_dir / "transcript.txt").read_text(encoding="utf-8"),
        meeting_note=note,
    )


def audit(
    dataset: Path,
    *,
    case_csv: Path | None = None,
    with_note: bool = True,
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    execution_errors: list[dict[str, str]] = []
    total_requests = 0
    total_candidate_entries = 0
    total_conflicts = 0
    empty_candidate_requests = 0
    duplicate_id_requests = 0

    for case_id in _case_ids(dataset, case_csv):
        client = CapturingAiClient()
        case_conflicts: set[str] = set()
        case_duplicate_requests = 0
        case_empty_requests = 0
        try:
            process_meeting(
                _load_meeting(dataset / case_id, with_note=with_note),
                client,
            )
        except Exception as exc:  # pragma: no cover - defensive CLI boundary
            execution_errors.append(
                {"case_id": case_id, "error": f"{type(exc).__name__}: {exc}"}
            )
            continue

        for payload in client.payloads:
            candidates = list(payload.get("candidate_tasks", []))
            total_requests += 1
            total_candidate_entries += len(candidates)
            if not candidates:
                empty_candidate_requests += 1
                case_empty_requests += 1
            task_ids = [str(item.get("task_id", "")) for item in candidates]
            if len(task_ids) != len(set(task_ids)):
                duplicate_id_requests += 1
                case_duplicate_requests += 1
            for item in candidates:
                if candidate_aliases_conflict(
                    str(item.get("canonical_action", "")),
                    {str(alias) for alias in item.get("aliases", [])},
                ):
                    total_conflicts += 1
                    case_conflicts.add(str(item.get("task_id", "")))

        cases.append(
            {
                "case_id": case_id,
                "ai_request_count": len(client.payloads),
                "conflicting_task_ids": sorted(case_conflicts),
                "empty_candidate_request_count": case_empty_requests,
                "duplicate_task_id_request_count": case_duplicate_requests,
            }
        )

    passed = not (
        execution_errors
        or total_conflicts
        or empty_candidate_requests
        or duplicate_id_requests
    )
    return {
        "mode": "provider_free_candidate_identity_audit",
        "case_count": len(cases),
        "with_meeting_notes": with_note,
        "ai_request_count": total_requests,
        "candidate_entry_count": total_candidate_entries,
        "conflicting_candidate_occurrence_count": total_conflicts,
        "empty_candidate_request_count": empty_candidate_requests,
        "duplicate_task_id_request_count": duplicate_id_requests,
        "execution_error_count": len(execution_errors),
        "passed": passed,
        "execution_errors": execution_errors,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--case-csv", type=Path)
    parser.add_argument("--without-meeting-notes", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    report = audit(
        args.dataset,
        case_csv=args.case_csv,
        with_note=not args.without_meeting_notes,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "Candidate identity audit: "
        f"cases={report['case_count']} requests={report['ai_request_count']} "
        f"conflicts={report['conflicting_candidate_occurrence_count']} "
        f"empty={report['empty_candidate_request_count']} "
        f"duplicates={report['duplicate_task_id_request_count']}"
    )
    print(f"Report: {args.report}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
