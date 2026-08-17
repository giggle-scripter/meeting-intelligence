"""Select a compact, diverse ground-truth review batch with greedy set cover."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def _length_bucket(word_count: int) -> str:
    if word_count <= 1_000:
        return "short"
    if word_count <= 3_000:
        return "medium"
    return "long"


def _load_case(case_dir: Path) -> dict[str, Any] | None:
    metadata_path = case_dir / "metadata.json"
    expected_path = case_dir / "expected_output.json"
    transcript_path = next(iter(case_dir.glob("transcript.*")), None)
    if not metadata_path.exists() or not expected_path.exists() or transcript_path is None:
        return None
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    word_count = int(metadata.get("validation", {}).get("word_count", 0))
    labels = set(metadata.get("labels", []))
    features = {f"wave:{metadata.get('wave', '')}", f"length:{_length_bucket(word_count)}"}
    features.update(f"label:{label}" for label in labels)
    return {
        "case_id": metadata["case_id"],
        "wave": metadata.get("wave", ""),
        "labels": sorted(labels),
        "word_count": word_count,
        "expected_task_count": len(expected.get("tasks", [])),
        "features": features,
        "transcript_path": transcript_path.as_posix(),
        "expected_output_path": expected_path.as_posix(),
    }


def _feature_weight(feature: str) -> float:
    if feature.startswith("wave:"):
        return 10.0
    if feature.startswith("length:"):
        return 6.0
    return 4.0


def select_batch(cases: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    uncovered = set().union(*(case["features"] for case in cases))
    remaining = list(cases)
    while remaining and len(selected) < limit:
        def rank(case: dict[str, Any]) -> tuple[float, int, str]:
            coverage = sum(
                _feature_weight(feature)
                for feature in case["features"]
                if feature in uncovered
            )
            brevity_bonus = max(0.0, 3.0 - case["word_count"] / 2_500)
            return (coverage + brevity_bonus, -case["word_count"], case["case_id"])

        chosen = max(remaining, key=rank)
        selected.append(chosen)
        uncovered.difference_update(chosen["features"])
        remaining.remove(chosen)
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/ground-truth-batch-01.csv"),
    )
    args = parser.parse_args()
    if args.limit <= 0:
        raise SystemExit("--limit must be greater than zero")

    cases = [
        case
        for case_dir in args.dataset.iterdir()
        if case_dir.is_dir() and (case := _load_case(case_dir)) is not None
    ]
    selected = select_batch(cases, min(args.limit, len(cases)))
    covered = set().union(*(case["features"] for case in selected)) if selected else set()
    universe = set().union(*(case["features"] for case in cases)) if cases else set()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "review_order",
        "case_id",
        "wave",
        "word_count",
        "labels",
        "expected_task_count",
        "transcript_path",
        "expected_output_path",
        "review_status",
        "reviewer",
        "review_notes",
    ]
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for order, case in enumerate(selected, 1):
            writer.writerow(
                {
                    "review_order": order,
                    "case_id": case["case_id"],
                    "wave": case["wave"],
                    "word_count": case["word_count"],
                    "labels": ";".join(case["labels"]),
                    "expected_task_count": case["expected_task_count"],
                    "transcript_path": case["transcript_path"],
                    "expected_output_path": case["expected_output_path"],
                    "review_status": "pending",
                    "reviewer": "",
                    "review_notes": "",
                }
            )
    print(
        f"Review batch: {args.output} ({len(selected)} cases, "
        f"{len(covered)}/{len(universe)} features covered)"
    )


if __name__ == "__main__":
    main()
