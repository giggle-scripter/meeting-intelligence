"""Build a split-preserving supervision corpus from reviewed action evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path


def _load_traces(directory: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in directory.glob("*-v1-*.json"):
        trace = json.loads(path.read_text(encoding="utf-8"))
        result[str(trace["meeting_id"])] = trace
    return result


def _waves(value: str) -> set[str]:
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def build_rows(
    evidence_rows: list[dict], traces: dict[str, dict], *, train_waves: set[str], calibration_waves: set[str], test_waves: set[str],
) -> tuple[list[dict], list[str]]:
    exact_gold = {
        (row["case_id"], item["clause_id"], item["start"], item["end"], item["text"])
        for row in evidence_rows for item in [row["action_evidence"]]
    }
    source_gold = {(row["case_id"], row["action_evidence"]["clause_id"]) for row in evidence_rows}
    rows: list[dict] = []
    errors: list[str] = []
    for case_id, trace in sorted(traces.items()):
        wave = case_id.split("-", 1)[0].upper()
        split = (
            "train" if wave in train_waves else
            "calibration" if wave in calibration_waves else
            "test" if wave in test_waves else
            "excluded"
        )
        if split == "excluded":
            continue
        clusters = {item["cluster_id"]: item for item in trace.get("proposal_clusters_v3", {}).get("records", [])}
        seeds = {item["seed_id"]: item for item in trace.get("proposal_evidence_seeds_v3", {}).get("records", [])}
        semantic = {
            (item["identity_key"], item["primary_clause_id"]): item
            for item in trace.get("proposal_semantic_scores_v3", {}).get("records", [])
        }
        ranking = {
            (item["identity_key"], item["primary_clause_id"]): item
            for item in trace.get("proposal_ranking_v3", {}).get("records", [])
        }
        for identity in trace.get("proposal_span_identities_v3", {}).get("records", []):
            span = identity.get("action_span")
            if not span:
                continue
            key = (identity["identity_key"], identity["primary_clause_id"])
            cluster = clusters.get(identity["cluster_id"])
            if cluster is None or cluster["nucleus_seed_id"] not in seeds:
                errors.append(f"{case_id}: identity {identity['identity_key']} has unresolved source")
                continue
            nucleus = seeds[cluster["nucleus_seed_id"]]
            semantic_item = semantic.get(key, {})
            ranked = ranking.get(key, {})
            exact_key = (case_id, span["clause_id"], span["start"], span["end"], span["text"])
            rows.append({
                "schema_version": "proposal-span-supervision-v3",
                "case_id": case_id,
                "wave": wave,
                "split": split,
                "identity_key": identity["identity_key"],
                "cluster_id": identity["cluster_id"],
                "primary_clause_id": identity["primary_clause_id"],
                "span": span,
                "span_variant": identity.get("span_variant", "FULL_BOUNDARY"),
                "nucleus_roles": nucleus["roles"],
                "nucleus_flags": nucleus["flags"],
                "semantic_score": semantic_item.get("score", 0.0),
                "semantic_reasons": semantic_item.get("reasons", []),
                "ranking_score": ranked.get("score", 0.0),
                "ranking_reasons": ranked.get("reasons", []),
                "exact_span_label": int(exact_key in exact_gold),
                "source_clause_label": int((case_id, span["clause_id"]) in source_gold),
                "label_source": "HUMAN_CONFIRMED_TASK_EVIDENCE",
            })
    return rows, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=Path("data/quality/task-evidence-v2.jsonl"))
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/quality/proposal-span-supervision-v3.jsonl"))
    parser.add_argument("--train-waves", default="W1,W2,W3")
    parser.add_argument("--calibration-waves", default="W4")
    parser.add_argument("--test-waves", default="W5")
    args = parser.parse_args()
    train_waves = _waves(args.train_waves)
    calibration_waves = _waves(args.calibration_waves)
    test_waves = _waves(args.test_waves)
    if train_waves & calibration_waves or train_waves & test_waves or calibration_waves & test_waves:
        raise ValueError("train, calibration, and test waves must not overlap")
    evidence = [json.loads(line) for line in args.evidence.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows, errors = build_rows(
        evidence, _load_traces(args.traces), train_waves=train_waves,
        calibration_waves=calibration_waves, test_waves=test_waves,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    counts = Counter((row["split"], row["exact_span_label"]) for row in rows)
    manifest = {
        "schema_version": "proposal-span-supervision-manifest-v3",
        "train_waves": sorted(train_waves),
        "calibration_waves": sorted(calibration_waves),
        "test_waves": sorted(test_waves),
        "row_count": len(rows),
        "label_counts": {f"{split}:{label}": count for (split, label), count in sorted(counts.items())},
        "sha256": sha256(args.output.read_bytes()).hexdigest(),
        "errors": errors,
    }
    manifest_path = args.output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Proposal supervision corpus: rows={len(rows)} errors={len(errors)} output={args.output}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
