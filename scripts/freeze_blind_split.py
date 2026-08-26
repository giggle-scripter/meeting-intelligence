"""Freeze independently reviewed meetings into an immutable blind manifest."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path


REQUIRED_FILES = ("metadata.json", "transcript.txt", "expected_output.json")


def build_manifest(
    dataset: Path, *, split_id: str, reviewer: str, frozen_at: str,
) -> dict:
    """Create a reproducible BLIND manifest without modifying source cases."""

    cases: list[dict] = []
    case_ids: set[str] = set()
    corpus = sha256()
    for case_dir in sorted(path for path in dataset.iterdir() if path.is_dir()):
        missing = [name for name in REQUIRED_FILES if not (case_dir / name).is_file()]
        if missing:
            raise ValueError(f"{case_dir.name}: missing {', '.join(missing)}")
        metadata = json.loads((case_dir / "metadata.json").read_text(encoding="utf-8"))
        case_id = str(metadata.get("case_id", "")).strip()
        if not case_id or case_id != case_dir.name:
            raise ValueError(f"{case_dir.name}: metadata case_id must match directory name")
        if case_id in case_ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        ground_truth = metadata.get("ground_truth", {})
        if not ground_truth.get("available") or not str(ground_truth.get("reviewer", "")).strip():
            raise ValueError(f"{case_id}: ground truth must be independently reviewed")
        case_ids.add(case_id)
        hashes: dict[str, str] = {}
        for filename in REQUIRED_FILES:
            source = case_dir / filename
            content = source.read_bytes()
            hashes[filename] = sha256(content).hexdigest()
            corpus.update(f"{case_id}/{filename}\0".encode())
            corpus.update(content)
        cases.append({"case_id": case_id, "sha256": hashes})
    if len(cases) < 20:
        raise ValueError("blind split requires at least 20 independently reviewed meetings")
    return {
        "schema_version": "1.0",
        "split_id": split_id,
        "split_kind": "BLIND",
        "frozen_at": frozen_at,
        "corpus_sha256": corpus.hexdigest(),
        "independence": {
            "tuned_against": False,
            "reviewer": reviewer,
            "frozen_at": frozen_at,
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path, help="Independently reviewed meeting folders")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split-id", required=True)
    parser.add_argument("--reviewer", required=True, help="Independent person who freezes this split")
    parser.add_argument("--frozen-at", required=True, help="ISO-8601 timestamp")
    args = parser.parse_args()

    manifest = build_manifest(
        args.dataset, split_id=args.split_id, reviewer=args.reviewer, frozen_at=args.frozen_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Frozen blind split: cases={len(manifest['cases'])} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
