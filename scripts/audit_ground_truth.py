"""Audit validation fixtures before they are accepted as reviewed ground truth."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.annotation.date_parser import extract_date_mentions
from backend.app.dates import resolve_date_mention
from backend.app.models import MeetingInput
from backend.app.models import Clause
from backend.app.pipeline import process_meeting
from backend.app.preprocessing.unicode_normalizer import normalize_for_match, normalize_text


ACTION_STOPWORDS = {
    "va", "và", "cho", "voi", "với", "cua", "của", "phan", "phần", "task",
    "viec", "việc", "the", "a", "an", "to", "for", "and",
}
RECAP_MARKERS = ("chốt lại", "tổng kết", "recap", "final list")
ASSIGNEE_SEPARATOR = re.compile(r"\s*[;,]\s*")


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"\w+", normalize_for_match(value))
        if len(token) > 1 and token not in ACTION_STOPWORDS
    }


def _date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _resolve_due_text(meeting_date: str, start_date: str, raw_text: str) -> str | None:
    """Resolve one unambiguous due-date expression with the production parser."""

    if not raw_text:
        return None
    normalized = normalize_for_match(raw_text)
    if (
        "ngay lam viec" in normalized
        or re.search(r"\bthu \w+\s*-\s*thu \w+\b", normalized)
        or re.search(r"\btu thu \w+\s+den thu \w+\b", normalized)
        or re.search(r"\bthu \w+\s+\d{1,2}\s*(?:h|gio)\b", normalized)
    ):
        return None
    clause = Clause(
        clause_id="AUDIT-DATE",
        sentence_id="AUDIT-SENTENCE",
        speaker_id="",
        speaker_name="",
        start_ms=None,
        end_ms=None,
        text_raw=raw_text,
        text_normalized=normalize_text(raw_text),
    )
    mentions = list(extract_date_mentions([clause]).values())
    if len(mentions) != 1:
        return None
    return resolve_date_mention(meeting_date, start_date, mentions[0])


def audit_case(case_dir: Path) -> dict[str, Any]:
    metadata = json.loads((case_dir / "metadata.json").read_text(encoding="utf-8"))
    expected = json.loads(
        (case_dir / "expected_output.json").read_text(encoding="utf-8")
    )
    transcript_path = next(iter(case_dir.glob("transcript.*")))
    transcript = transcript_path.read_text(encoding="utf-8-sig")
    normalized_transcript = normalize_for_match(transcript)
    transcript_tokens = _tokens(transcript)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()

    for index, task in enumerate(expected.get("tasks", []), 1):
        label = f"task[{index}]"
        task_name = str(task.get("task_name", "")).strip()
        assignee = str(task.get("assignee", "")).strip()
        identity = (
            normalize_for_match(task_name),
            normalize_for_match(assignee),
        )
        if identity in identities:
            errors.append({"field": label, "issue": "duplicate_task_identity"})
        identities.add(identity)

        if not task_name:
            errors.append({"field": f"{label}.task_name", "issue": "empty"})
        action_tokens = _tokens(task_name)
        if action_tokens:
            overlap = len(action_tokens & transcript_tokens) / len(action_tokens)
            if overlap < 0.6:
                errors.append(
                    {
                        "field": f"{label}.task_name",
                        "issue": "low_transcript_token_overlap",
                        "value": task_name,
                    }
                )
        for assignee_name in filter(None, ASSIGNEE_SEPARATOR.split(assignee)):
            if normalize_for_match(assignee_name) not in normalized_transcript:
                errors.append(
                    {
                        "field": f"{label}.assignee",
                        "issue": "not_found_in_transcript",
                        "value": assignee_name,
                    }
                )

        start_raw = str(task.get("start_date", ""))
        due_raw = str(task.get("due_date", ""))
        start = _date(start_raw)
        due = _date(due_raw)
        if start_raw and start is None:
            errors.append(
                {"field": f"{label}.start_date", "issue": "invalid_iso_date"}
            )
        if due_raw and due is None:
            errors.append({"field": f"{label}.due_date", "issue": "invalid_iso_date"})
        if start and due and due < start:
            errors.append(
                {
                    "field": f"{label}.due_date",
                    "issue": "before_start_date",
                    "value": due_raw,
                }
            )

        due_text = str(task.get("due_date_text", "")).strip()
        if due_text and normalize_for_match(due_text) not in normalized_transcript:
            warnings.append(
                {
                    "field": f"{label}.due_date_text",
                    "issue": "not_exactly_found_in_transcript",
                    "value": due_text,
                }
            )
        resolved_due = _resolve_due_text(
            metadata["meeting_date"],
            start_raw or metadata["meeting_date"],
            due_text,
        )
        if resolved_due is not None and resolved_due != due_raw:
            warnings.append(
                {
                    "field": f"{label}.due_date",
                    "issue": "does_not_match_due_date_text",
                    "value": f"expected={due_raw}; resolved={resolved_due}",
                }
            )

    actual = process_meeting(
        MeetingInput(
            metadata["case_id"],
            metadata["meeting_title"],
            metadata["meeting_date"],
            transcript,
            transcript_path.name,
        )
    )
    if (
        not expected.get("tasks")
        and actual.tasks
        and any(marker in transcript.casefold() for marker in RECAP_MARKERS)
    ):
        warnings.append(
            {
                "field": "tasks",
                "issue": "empty_expected_with_recap_candidates",
                "value": str(len(actual.tasks)),
            }
        )

    return {
        "case_id": metadata["case_id"],
        "wave": metadata.get("wave"),
        "expected_task_count": len(expected.get("tasks", [])),
        "errors": errors,
        "warnings": warnings,
        "audit_passed": not errors and not warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("evaluation/ground-truth-audit.json"),
    )
    args = parser.parse_args()
    cases = [
        audit_case(case_dir)
        for case_dir in sorted(args.dataset.iterdir())
        if case_dir.is_dir()
        and (case_dir / "metadata.json").exists()
        and (case_dir / "expected_output.json").exists()
    ]
    failed = [case for case in cases if not case["audit_passed"]]
    report = {
        "case_count": len(cases),
        "passed_case_count": len(cases) - len(failed),
        "failed_case_count": len(failed),
        "cases": cases,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Ground-truth audit: {report['passed_case_count']}/{report['case_count']} "
        f"passed; report={args.report}"
    )
    for case in failed:
        print(
            f"{case['case_id']}: errors={len(case['errors'])}, "
            f"warnings={len(case['warnings'])}"
        )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
