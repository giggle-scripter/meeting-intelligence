"""Verify generated Power Automate upload packages against their source cases."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.ingestion import parse_meeting_package


def audit_upload_set(source_root: Path, upload_root: Path) -> dict[str, Any]:
    errors: list[str] = []
    expected_rows: dict[str, dict[str, str]] = {}
    expected_files: set[str] = {"README.md", "index.csv"}
    for case_dir in sorted(item for item in source_root.iterdir() if item.is_dir()):
        metadata_path = case_dir / "metadata.json"
        if not metadata_path.is_file():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        case_id = str(metadata.get("case_id") or case_dir.name)
        transcript = (case_dir / metadata.get("transcript_file", "transcript.txt")).read_text(
            encoding="utf-8-sig"
        )
        note_path = case_dir / "meeting_note.txt"
        variants = [(f"{case_id}.txt", "transcript_only", None)]
        if note_path.is_file() and note_path.read_text(encoding="utf-8-sig").strip():
            # The package parser owns outer whitespace and returns the semantic
            # note body without a trailing newline.
            variants.append((f"{case_id}__with-note.txt", "with_note", note_path.read_text(encoding="utf-8-sig").strip()))
        for filename, variant, source_note in variants:
            expected_files.add(filename)
            expected_rows[filename] = {"case_id": case_id, "variant": variant}
            path = upload_root / filename
            if not path.is_file():
                errors.append(f"missing upload package: {filename}")
                continue
            package_transcript, package_note, package_metadata = parse_meeting_package(
                path.read_text(encoding="utf-8-sig")
            )
            if package_transcript != transcript:
                errors.append(f"{filename}: transcript does not match source")
            if (package_note.content if package_note else None) != source_note:
                errors.append(f"{filename}: meeting note does not match source")
            if (
                package_metadata.meeting_id != case_id
                or package_metadata.meeting_title != str(metadata.get("meeting_title", ""))
                or package_metadata.meeting_date != str(metadata.get("meeting_date", ""))
            ):
                errors.append(f"{filename}: embedded metadata does not match source")

    index_path = upload_root / "index.csv"
    index_rows: dict[str, dict[str, str]] = {}
    if not index_path.is_file():
        errors.append("missing upload index.csv")
    else:
        with index_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                filename = str(row.get("file_name", ""))
                if filename in index_rows:
                    errors.append(f"index has duplicate row: {filename}")
                index_rows[filename] = row
        if set(index_rows) != set(expected_rows):
            errors.append("index files do not match source variants")
        for filename, expected in expected_rows.items():
            row = index_rows.get(filename, {})
            if row.get("case_id") != expected["case_id"] or row.get("variant") != expected["variant"]:
                errors.append(f"{filename}: index identity does not match source")

    unexpected = sorted(
        item.name for item in upload_root.iterdir() if item.is_file() and item.name not in expected_files
    )
    if unexpected:
        errors.append("unexpected upload artifacts: " + ", ".join(unexpected))
    return {
        "source_case_count": len({item["case_id"] for item in expected_rows.values()}),
        "upload_variant_count": len(expected_rows),
        "passed": not errors,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/validation"))
    parser.add_argument("--uploads", type=Path, default=Path("data/power_automate_uploads"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit_upload_set(args.source, args.uploads)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "Power Automate upload audit: "
        f"cases={report['source_case_count']} variants={report['upload_variant_count']} "
        f"passed={report['passed']}"
    )
    for error in report["errors"]:
        print(f"ERROR: {error}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
