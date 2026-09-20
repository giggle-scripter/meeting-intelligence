"""Measure V3 shadow graph coverage against human-confirmed task evidence."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path


def _trace_by_case(directory: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in directory.glob("*-v1-*.json"):
        trace = json.loads(path.read_text(encoding="utf-8"))
        result[str(trace["meeting_id"])] = trace
    return result


def _rate(matched: int, total: int) -> float:
    return round(matched / total, 6) if total else 1.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=Path("data/quality/task-evidence-v2.jsonl"))
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--waves", default="")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.evidence.read_text(encoding="utf-8").splitlines() if line.strip()]
    requested_waves = {item.strip().upper() for item in args.waves.split(",") if item.strip()}
    if requested_waves:
        rows = [row for row in rows if str(row["case_id"]).split("-", 1)[0].upper() in requested_waves]
    traces = _trace_by_case(args.traces)
    counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    errors: list[str] = []
    for row in rows:
        case_id = str(row["case_id"])
        wave = case_id.split("-", 1)[0]
        metric = counters[wave]
        metric["expected"] += 1
        trace = traces.get(case_id)
        if trace is None:
            errors.append(f"{case_id}: missing trace")
            continue
        seeds = {item["clause_id"]: item for item in trace.get("proposal_evidence_seeds_v3", {}).get("records", [])}
        clusters = {item["nucleus_clause_id"] for item in trace.get("proposal_clusters_v3", {}).get("records", [])}
        relations = trace.get("proposal_relations_v3", {}).get("records", [])
        span_identities = trace.get("proposal_span_identities_v3", {}).get("records", [])
        action_clause = row["action_evidence"]["clause_id"]
        authority_clause = row["authority_evidence"]["clause_id"]
        deadline = row.get("deadline_evidence") or {}
        deadline_clause = deadline.get("clause_id")
        if action_clause in seeds:
            metric["seed_source_matched"] += 1
        if action_clause in clusters:
            metric["cluster_source_matched"] += 1
        action = row["action_evidence"]
        if any(
            item["primary_clause_id"] == action_clause and item.get("action_span")
            for item in span_identities
        ):
            metric["span_clause_matched"] += 1
        if any(
            item["primary_clause_id"] == action_clause
            and item.get("action_span") == {
                "clause_id": action_clause, "start": action["start"],
                "end": action["end"], "text": action["text"],
            }
            for item in span_identities
        ):
            metric["span_exact_matched"] += 1
        if action_clause == authority_clause or any(
            item["nucleus_seed_id"] == seeds.get(action_clause, {}).get("seed_id")
            and item["support_seed_id"] == seeds.get(authority_clause, {}).get("seed_id")
            and item["relation_type"] in {"AUTHORIZES", "ACCEPTS"}
            for item in relations
        ):
            metric["authority_link_matched"] += 1
        if deadline_clause:
            metric["deadline_expected"] += 1
            if action_clause == deadline_clause or any(
                item["nucleus_seed_id"] == seeds.get(action_clause, {}).get("seed_id")
                and item["support_seed_id"] == seeds.get(deadline_clause, {}).get("seed_id")
                and item["relation_type"] == "HAS_DEADLINE"
                for item in relations
            ):
                metric["deadline_link_matched"] += 1

    def summarize(metric: dict[str, int]) -> dict[str, int | float]:
        total = metric["expected"]
        return {
            "expected": total,
            "seed_source_matched": metric["seed_source_matched"],
            "seed_source_recall": _rate(metric["seed_source_matched"], total),
            "cluster_source_matched": metric["cluster_source_matched"],
            "cluster_source_recall": _rate(metric["cluster_source_matched"], total),
            "span_clause_matched": metric["span_clause_matched"],
            "span_clause_recall": _rate(metric["span_clause_matched"], total),
            "span_exact_matched": metric["span_exact_matched"],
            "span_exact_recall": _rate(metric["span_exact_matched"], total),
            "authority_link_matched": metric["authority_link_matched"],
            "authority_link_recall": _rate(metric["authority_link_matched"], total),
            "deadline_expected": metric["deadline_expected"],
            "deadline_link_matched": metric["deadline_link_matched"],
            "deadline_link_recall": _rate(metric["deadline_link_matched"], metric["deadline_expected"]),
        }

    by_wave = {wave: summarize(metric) for wave, metric in sorted(counters.items())}
    totals: dict[str, int] = defaultdict(int)
    for metric in counters.values():
        for key, value in metric.items():
            totals[key] += value
    payload = {
        "schema_version": "action-proposal-v3-shadow-audit-v1",
        "passed": not errors,
        "waves": sorted(requested_waves) if requested_waves else "ALL",
        "traces": len(traces),
        "overall": summarize(totals),
        "by_wave": by_wave,
        "errors": errors,
        "interpretation": "Coverage is a shadow-graph diagnostic, not an output-quality or F1 claim.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "V3 shadow audit: "
        f"seed={payload['overall']['seed_source_recall']:.3f} "
        f"cluster={payload['overall']['cluster_source_recall']:.3f} "
        f"exact_span={payload['overall']['span_exact_recall']:.3f} "
        f"authority={payload['overall']['authority_link_recall']:.3f}"
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
