from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.ingestion import build_meeting_package


def build_upload_set(source_root: Path, output_root: Path) -> int:
    output_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str | int]] = []
    expected_names: set[str] = {"README.md", "index.csv"}

    for case_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        metadata_path = case_dir / "metadata.json"
        expected_path = case_dir / "expected_output.json"
        if not metadata_path.exists():
            continue

        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        transcript_name = metadata.get("transcript_file", "transcript.txt")
        transcript_path = case_dir / transcript_name
        if not transcript_path.exists():
            raise FileNotFoundError(f"Missing transcript: {transcript_path}")

        case_id = str(metadata.get("case_id") or case_dir.name)
        suffix = transcript_path.suffix.lower()
        output_name = f"{case_id}{suffix}"
        note_output_name = f"{case_id}__with-note{suffix}"
        expected_names.add(output_name)
        transcript = transcript_path.read_text(encoding="utf-8-sig")
        note_path = case_dir / "meeting_note.txt"
        note = note_path.read_text(encoding="utf-8-sig") if note_path.exists() else None
        package_args = {
            "meeting_id": case_id,
            "meeting_title": str(metadata.get("meeting_title", "")),
            "meeting_date": str(metadata.get("meeting_date", "")),
        }
        # Upload fixtures are self-contained. Power Automate transports one
        # file instead of maintaining title/date/ID in a parallel data path.
        (output_root / output_name).write_text(
            build_meeting_package(transcript, None, **package_args),
            encoding="utf-8",
        )
        if note and note.strip():
            expected_names.add(note_output_name)
            (output_root / note_output_name).write_text(
                build_meeting_package(transcript, note, **package_args),
                encoding="utf-8",
            )

        expected_task_count = ""
        if expected_path.exists():
            expected = json.loads(expected_path.read_text(encoding="utf-8-sig"))
            expected_task_count = len(expected.get("tasks", []))

        common = {
            "case_id": case_id,
            "meeting_title": str(metadata.get("meeting_title", "")),
            "meeting_date": str(metadata.get("meeting_date", "")),
            "wave": metadata.get("wave", ""),
            "labels": ";".join(metadata.get("labels", [])),
            "expected_task_count": expected_task_count,
        }
        rows.append({"file_name": output_name, "variant": "transcript_only", "meeting_note": "no", **common})
        if note and note.strip():
            rows.append({"file_name": note_output_name, "variant": "with_note", "meeting_note": "yes", **common})

    for existing in output_root.iterdir():
        if existing.is_file() and existing.name not in expected_names:
            existing.unlink()

    index_path = output_root / "index.csv"
    with index_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "file_name",
                "variant",
                "case_id",
                "meeting_title",
                "meeting_date",
                "wave",
                "labels",
                "meeting_note",
                "expected_task_count",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    readme = """# Power Automate Upload Transcripts

Thư mục phẳng dùng để upload vào OneDrive/Power Automate.

- `<case-id>.txt` là package có meeting metadata + transcript gốc.
- `<case-id>__with-note.txt` là package A/B có cùng metadata + Meeting Note + transcript.
- Hai file dùng cùng `case_id` và expected output; phân biệt bằng cột `variant`.
- Nếu case không có `meeting_note.txt`, chỉ tạo bản transcript gốc.
- Backend tách metadata và Meeting Note trước khi parser đọc raw transcript.
- Tên file chính là `case_id`, ví dụ `W2-SHORT-C2-N0-IT-BRST-006.txt`.
- `index.csv` vẫn là manifest/audit; runtime đọc ID/title/date ngay từ file package.
- Dữ liệu gốc tại `data/validation/<case_id>/` không bị thay đổi.

Tạo lại thư mục:

```powershell
python scripts/prepare_power_automate_uploads.py
```
"""
    (output_root / "README.md").write_text(readme, encoding="utf-8")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare flat, case-named transcript files for Power Automate."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/validation"),
        help="Validation case directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/power_automate_uploads"),
        help="Flat upload directory.",
    )
    args = parser.parse_args()

    count = build_upload_set(args.source, args.output)
    print(f"Prepared {count} upload variants in {args.output}")


if __name__ == "__main__":
    main()
