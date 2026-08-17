"""Create a deterministic review queue without modifying generated fixtures."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


RECAP_MARKERS = ("chốt lại", "tổng kết", "recap", "final list")


def _load_case(case_dir: Path) -> dict[str, Any] | None:
    metadata_path = case_dir / "metadata.json"
    expected_path = case_dir / "expected_output.json"
    transcript_path = next(iter(case_dir.glob("transcript.*")), None)
    if not metadata_path.exists() or not expected_path.exists() or transcript_path is None:
        return None
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    transcript = transcript_path.read_text(encoding="utf-8-sig")
    reviewed = bool(metadata.get("ground_truth", {}).get("available", False))
    expected_count = len(expected.get("tasks", []))
    suspect_empty = (
        not expected_count
        and any(marker in transcript.casefold() for marker in RECAP_MARKERS)
    )
    priority = 0 if suspect_empty else 1 if metadata.get("wave", 99) <= 2 else 2
    objective = metadata.get("objective", "")
    if isinstance(objective, str) and objective.lstrip().startswith("{"):
        try:
            objective = json.loads(objective)
        except json.JSONDecodeError:
            pass
    if isinstance(objective, dict):
        objective = objective.get("requirements") or objective.get("name") or json.dumps(
            objective,
            ensure_ascii=False,
        )
    return {
        "priority": priority,
        "case_id": metadata["case_id"],
        "review_status": "reviewed" if reviewed else "pending",
        "recap_with_empty_expected": "yes" if suspect_empty else "",
        "wave": metadata.get("wave", ""),
        "labels": ";".join(metadata.get("labels", [])),
        "objective": objective,
        "word_count": metadata.get("validation", {}).get("word_count", ""),
        "expected_task_count": expected_count,
        "transcript_path": transcript_path.as_posix(),
        "expected_output_path": expected_path.as_posix(),
        "reviewer": "",
        "review_notes": "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/ground-truth-review.csv"),
    )
    args = parser.parse_args()

    rows = [
        row
        for case_dir in args.dataset.iterdir()
        if case_dir.is_dir() and (row := _load_case(case_dir)) is not None
    ]
    rows.sort(key=lambda row: (row["priority"], row["wave"], row["case_id"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    print(f"Review queue: {args.output} ({len(rows)} cases)")


if __name__ == "__main__":
    main()
