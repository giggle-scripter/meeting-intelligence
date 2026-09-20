"""Evaluate selected V3 shadow proposals against exact reviewed action evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _traces(directory: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in directory.glob("*-v1-*.json"):
        trace = json.loads(path.read_text(encoding="utf-8"))
        result[str(trace["meeting_id"])] = trace
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=Path("data/quality/task-evidence-v2.jsonl"))
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--waves", default="", help="Comma-separated wave IDs, for example W1,W2,W3")
    args = parser.parse_args()

    waves = {item.strip().upper() for item in args.waves.split(",") if item.strip()}
    rows = [json.loads(line) for line in args.evidence.read_text(encoding="utf-8").splitlines() if line.strip()]
    if waves:
        rows = [row for row in rows if row["case_id"].split("-", 1)[0].upper() in waves]
    gold = {
        (row["case_id"], item["clause_id"], item["start"], item["end"], item["text"])
        for row in rows for item in [row["action_evidence"]]
    }
    selected: set[tuple[str, str, int, int, str]] = set()
    errors: list[str] = []
    for case_id, trace in _traces(args.traces).items():
        if waves and case_id.split("-", 1)[0].upper() not in waves:
            continue
        identities = {
            (item["identity_key"], item["primary_clause_id"]): item
            for item in trace.get("proposal_span_identities_v3", {}).get("records", [])
        }
        for ranked in trace.get("proposal_ranking_v3", {}).get("records", []):
            if ranked["decision"] != "SELECTED":
                continue
            identity = identities.get((ranked["identity_key"], ranked["primary_clause_id"]))
            if identity is None or not identity.get("action_span"):
                errors.append(f"{case_id}: selected ranking lacks a grounded span")
                continue
            span = identity["action_span"]
            selected.add((case_id, span["clause_id"], span["start"], span["end"], span["text"]))
    matched = gold & selected
    precision = len(matched) / len(selected) if selected else 0.0
    recall = len(matched) / len(gold) if gold else 1.0
    payload = {
        "schema_version": "action-proposal-v3-exact-evidence-ranking-v1",
        "passed": not errors,
        "waves": sorted(waves),
        "expected_evidence_count": len(gold),
        "selected_evidence_count": len(selected),
        "matched_evidence_count": len(matched),
        "exact_evidence_precision": round(precision, 6),
        "exact_evidence_recall": round(recall, 6),
        "exact_evidence_f1": round((2 * precision * recall / (precision + recall)) if precision + recall else 0.0, 6),
        "errors": errors,
        "interpretation": "This is exact source-action evidence F1, not task identity F1 or production quality.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "V3 exact evidence ranking: "
        f"precision={payload['exact_evidence_precision']:.3f} "
        f"recall={payload['exact_evidence_recall']:.3f} f1={payload['exact_evidence_f1']:.3f}"
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
