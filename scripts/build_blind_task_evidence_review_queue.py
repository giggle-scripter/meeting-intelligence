"""Build a reviewer queue for an independently reviewed blind corpus."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"\w+", value.casefold()) if len(token) > 1}


def _context(clauses: list[dict], clause_id: str, radius: int = 2) -> list[dict]:
    index = next((i for i, item in enumerate(clauses) if item["clause_id"] == clause_id), 0)
    return clauses[max(0, index - radius):index + radius + 1]


def _suggested_clause_ids(task: dict, clauses: list[dict]) -> list[str]:
    task_tokens = _tokens(str(task.get("task_name", "")))
    scored = [
        (len(task_tokens & _tokens(str(clause.get("text_raw", "")))), str(clause["clause_id"]))
        for clause in clauses
    ]
    return [clause_id for score, clause_id in sorted(scored, reverse=True)[:3] if score]


def build_queue(dataset: Path, traces: Path) -> tuple[list[dict], list[str]]:
    """Return editable suggestions; none of the fields are review evidence."""

    rows: list[dict] = []
    errors: list[str] = []
    for case_dir in sorted(path for path in dataset.iterdir() if path.is_dir()):
        metadata_path, expected_path = case_dir / "metadata.json", case_dir / "expected_output.json"
        if not metadata_path.is_file() or not expected_path.is_file():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        case_id = str(metadata.get("case_id", ""))
        matches = list(traces.glob(f"{case_id}-v1-*.json"))
        if len(matches) != 1:
            errors.append(f"{case_id}: expected exactly one trace, found {len(matches)}")
            continue
        trace = json.loads(matches[0].read_text(encoding="utf-8"))
        clauses = list(trace.get("clauses", []))
        dates = trace.get("date_mentions", {})
        date_mentions = list(dates.values()) if isinstance(dates, dict) else list(dates)
        tasks = json.loads(expected_path.read_text(encoding="utf-8")).get("tasks", [])
        for index, task in enumerate(tasks):
            suggested = _suggested_clause_ids(task, clauses)
            rows.append({
                "case_id": case_id,
                "expected_task_index": index,
                "expected_task": task,
                "review_status": "SUGGESTED",
                "review_basis": "BLIND_TRANSCRIPT_CONTEXT_ONLY",
                "suggested_clause_ids": suggested,
                "contexts": [_context(clauses, clause_id) for clause_id in suggested] or [clauses],
                "annotations": {key: trace.get("annotations", {}).get(key, {}) for key in suggested},
                "date_mentions": date_mentions,
                "action_evidence": None,
                "authority_evidence": None,
                "owner_evidence": None,
                "deadline_evidence": None,
                "support_clause_ids": [], "recap_clause_ids": [], "mutation_clause_ids": [],
                "reviewer": "", "reviewed_at": "", "review_note": "",
            })
    return rows, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    rows, errors = build_queue(args.dataset, args.traces)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    manifest = {
        "schema_version": "blind-task-evidence-review-queue-v1",
        "row_count": len(rows),
        "queue_sha256": sha256(args.output.read_bytes()).hexdigest(),
        "reviewer_policy": "Suggestions are not ground truth; reviewer must ground all roles in transcript text.",
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Blind task evidence queue: rows={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
