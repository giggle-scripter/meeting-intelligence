"""Export editable, context-rich task-evidence suggestions for human review."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path


def _context(clauses: list[dict], clause_id: str, radius: int = 2) -> list[dict]:
    index = next((i for i, item in enumerate(clauses) if item["clause_id"] == clause_id), 0)
    return clauses[max(0, index - radius):index + radius + 1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("suggestions", type=Path)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.suggestions.read_text(encoding="utf-8"))
    rows = []
    for item in source["expected_tasks"]:
        case_id = item["case_id"]
        trace_path = next(args.traces.glob(f"{case_id}-v1-*.json"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        suggestion_ids = item.get("source_clause_ids", [])
        rows.append({
            "case_id": case_id,
            "expected_task_index": item["expected_task_index"],
            "expected_task": item["expected_task"],
            "review_status": "SUGGESTED",
            "review_basis": "LEXICAL_TRACE_SUGGESTION_ONLY",
            "suggested_clause_ids": suggestion_ids,
            "contexts": [_context(trace["clauses"], clause_id) for clause_id in suggestion_ids],
            "annotations": {key: trace.get("annotations", {}).get(key, {}) for key in suggestion_ids},
            "date_mentions": list(trace.get("date_mentions", {}).values()),
            "action_evidence": None,
            "authority_evidence": None,
            "owner_evidence": None,
            "deadline_evidence": None,
            "support_clause_ids": [], "recap_clause_ids": [], "mutation_clause_ids": [],
            "reviewer": "", "reviewed_at": "", "review_note": "",
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    manifest = {"schema_version": "task-evidence-review-queue-v2", "suggestions_sha256": sha256(args.suggestions.read_bytes()).hexdigest(), "row_count": len(rows), "reviewer_policy": "HUMAN_CONFIRMED requires reviewer, reviewed_at, and grounded role evidence"}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Task evidence queue: rows={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
