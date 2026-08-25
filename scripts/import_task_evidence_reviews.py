"""Import reviewed JSONL rows into the versioned task-evidence source of truth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewed_jsonl", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/quality/task-evidence-v2.jsonl"))
    args = parser.parse_args()
    raw_rows = [json.loads(line) for line in args.reviewed_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = []
    for row in raw_rows:
        action = {
            "clause_id": row.get("action_clause_id", ""), "text": row.get("action_text", ""),
            "start": int(row.get("action_start", -1)), "end": int(row.get("action_end", -1)),
        }
        rows.append({
            "case_id": row.get("case_id"), "expected_task_index": row.get("expected_task_index"),
            "action_evidence": action,
            "authority_evidence": {"clause_id": row.get("authority_clause_id", ""), "type": row.get("authority_type", "")},
            "owner_evidence": ({"clause_id": row.get("owner_clause_id"), "participant": row.get("owner_participant"), "basis": "EXPLICIT_MENTION"} if row.get("owner_participant") else None),
            "deadline_evidence": ({"clause_id": row.get("deadline_clause_id"), "mention_id": row.get("deadline_mention_id")} if row.get("deadline_mention_id") else None),
            "support_clause_ids": row.get("support_clause_ids", []), "recap_clause_ids": row.get("recap_clause_ids", []), "mutation_clause_ids": row.get("mutation_clause_ids", []),
            "proposal_kind": row.get("proposal_kind", "CREATE"), "review_status": row.get("review_status"),
            "reviewer": row.get("reviewer", ""), "reviewed_at": row.get("reviewed_at", ""),
            "review_note": row.get("review_note", ""), "review_basis": row.get("review_basis", ""),
        })
    keys = {(row.get("case_id"), row.get("expected_task_index")) for row in rows}
    if len(keys) != len(rows):
        raise ValueError("duplicate task evidence key")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"Imported task evidence: rows={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
