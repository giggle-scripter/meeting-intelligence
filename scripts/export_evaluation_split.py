"""Export a verified split manifest as evaluator-compatible case CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.quality.validation_gate import load_split_manifest, verify_split_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("data/validation"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    verification = verify_split_manifest(
        load_split_manifest(args.manifest), args.dataset
    )
    if not verification.passed:
        for error in verification.errors:
            print(f"ERROR: {error}")
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id"])
        writer.writeheader()
        writer.writerows({"case_id": case_id} for case_id in verification.case_ids)
    print(f"Exported {len(verification.case_ids)} cases to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
