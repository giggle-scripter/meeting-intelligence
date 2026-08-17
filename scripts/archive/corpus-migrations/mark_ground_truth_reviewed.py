"""Mark one fixture or an audited dataset as reviewed ground truth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Mark every child case containing metadata and expected output.",
    )
    args = parser.parse_args()

    case_dirs = (
        sorted(path for path in args.case_dir.iterdir() if path.is_dir())
        if args.all
        else [args.case_dir]
    )
    reviewed = 0
    for case_dir in case_dirs:
        metadata_path = case_dir / "metadata.json"
        expected_path = case_dir / "expected_output.json"
        if not metadata_path.exists() or not expected_path.exists():
            if args.all:
                continue
            raise SystemExit(
                "case_dir must contain metadata.json and expected_output.json"
            )

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["ground_truth"] = {
            "available": True,
            "expected_output_file": "expected_output.json",
            "reviewer": args.reviewer,
            "review_method": "full_transcript_and_final_state_audit",
            "review_policy_version": "1.0",
            "review_notes": args.notes,
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        reviewed += 1
        print(f"Reviewed: {metadata.get('case_id', case_dir.name)}")
    print(f"Marked {reviewed} case(s) as reviewed.")


if __name__ == "__main__":
    main()
