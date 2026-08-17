"""Convert all generated transcript waves into one immutable dataset schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
from typing import Any


TURN_RE = re.compile(
    r"^(?:T\d+\s+)?\[(\d{2}:\d{2}:\d{2})\]\s*([^:]{1,80}):\s*(.+)$",
    re.IGNORECASE,
)
TIMESTAMPED_NOISE_RE = re.compile(
    r"^(?:T\d+\s+)?\[(\d{2}:\d{2}:\d{2})\]\s*(.+)$",
    re.IGNORECASE,
)
HEADER_RE = re.compile(
    r"^(?:MEETING_ID|PART|CASE_LABELS|MEETING_DATE|PRIMARY_FEATURE|TITLE):",
    re.IGNORECASE,
)
PART_BOUNDARY_RE = re.compile(r"^---\s*(?:END|START)\s+OF\s+PART\b", re.IGNORECASE)
SCHEMA_VERSION = "1.0"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_source(case_dir: Path) -> tuple[dict[str, Any], list[Path], list[dict[str, Any]]]:
    single_meta = case_dir / "generation_meta.json"
    single_transcript = case_dir / "transcript.txt"
    if single_meta.exists() and single_transcript.exists():
        meta = _read_json(single_meta)
        return meta["case"], [single_transcript], [meta]

    transcript_parts = sorted(case_dir.glob("part_*.txt"))
    meta_parts = sorted(case_dir.glob("part_*.meta.json"))
    if not transcript_parts or len(transcript_parts) != len(meta_parts):
        raise ValueError(
            f"{case_dir}: expected matching part_*.txt and part_*.meta.json files"
        )
    metadata = [_read_json(path) for path in meta_parts]
    case = metadata[0]["case"]
    if any(item["case"]["meeting_id"] != case["meeting_id"] for item in metadata):
        raise ValueError(f"{case_dir}: inconsistent meeting IDs across parts")
    return case, transcript_parts, metadata


def _normalize_transcript(paths: list[Path]) -> tuple[str, int]:
    output: list[str] = []
    for path in paths:
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").split("\n"),
            start=1,
        ):
            line = raw_line.strip()
            if not line or HEADER_RE.match(line) or PART_BOUNDARY_RE.match(line):
                continue
            match = TURN_RE.match(line)
            if match:
                timestamp, speaker, text = match.groups()
            else:
                noise_match = TIMESTAMPED_NOISE_RE.match(line)
                if noise_match:
                    timestamp, text = noise_match.groups()
                    speaker = "System"
                else:
                    if not output:
                        raise ValueError(f"{path}:{line_number}: unsupported line: {line!r}")
                    output[-1] = f"{output[-1]} {line}"
                    continue
            if not text.strip():
                raise ValueError(f"{path}:{line_number}: unsupported line: {line!r}")
            output.append(f"[{timestamp}] {speaker.strip()}: {text.strip()}")
    if not output:
        raise ValueError(f"{paths[0].parent}: transcript contains no dialogue turns")
    return "\n".join(output) + "\n", len(output)


def _unique(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(value for value in values if value not in (None, "")))


def _metadata(
    source_root: Path,
    wave_dir: Path,
    case: dict[str, Any],
    transcript_paths: list[Path],
    generation_meta: list[dict[str, Any]],
    normalized_turn_count: int,
) -> dict[str, Any]:
    validations = [item.get("validation", {}) for item in generation_meta]
    labels = [item.strip() for item in str(case.get("labels", "")).split(",") if item.strip()]
    errors = [
        str(error)
        for validation in validations
        for error in validation.get("errors", [])
    ]
    source_files = [
        path.relative_to(source_root).as_posix()
        for path in transcript_paths
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["meeting_id"],
        "meeting_id": case["meeting_id"],
        "meeting_title": case["title"],
        "meeting_date": case["meeting_date"],
        "wave": case["wave"],
        "wave_name": wave_dir.name,
        "labels": labels,
        "objective": case.get("objective", ""),
        "transcript_file": "transcript.txt",
        "transcript_format": "[HH:MM:SS] Speaker: text",
        "part_count": len(transcript_paths),
        "source_files": source_files,
        "generation": {
            "models": _unique([item.get("model") for item in generation_meta]),
            "thinking": any(bool(item.get("thinking")) for item in generation_meta),
            "reasoning_effort": _unique(
                [item.get("reasoning_effort") for item in generation_meta]
            ),
        },
        "validation": {
            "valid": all(bool(item.get("valid")) for item in validations),
            "errors": errors,
            "word_count": sum(int(item.get("word_count", 0)) for item in validations),
            "turn_count": normalized_turn_count,
        },
        "ground_truth": {
            "available": False,
            "expected_output_file": "",
        },
    }


def _index_entry(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": metadata["case_id"],
        "wave": metadata["wave"],
        "wave_name": metadata["wave_name"],
        "meeting_title": metadata["meeting_title"],
        "meeting_date": metadata["meeting_date"],
        "labels": metadata["labels"],
        "path": f"cases/{metadata['case_id']}",
        "part_count": metadata["part_count"],
        "word_count": metadata["validation"]["word_count"],
        "turn_count": metadata["validation"]["turn_count"],
        "valid": metadata["validation"]["valid"],
        "ground_truth_available": metadata["ground_truth"]["available"],
    }


def _readme() -> str:
    return """# Normalized Transcript Dataset

Dataset này là bản chuẩn hóa từ `data/generated_transcripts`. Dữ liệu nguồn
không bị thay đổi.

## Cấu trúc

```text
normalized_transcripts/
  README.md
  index.json
  cases/
    <case_id>/
      metadata.json
      transcript.txt
```

Mọi case dùng cùng một schema. `transcript.txt` luôn có định dạng:

```text
[HH:MM:SS] Speaker: nội dung
```

Wave 1-3 được bỏ header generation. Wave 4-5 được ghép các part theo thứ tự,
bỏ `T000001` và part boundary nhưng giữ timestamp, speaker và nguyên văn lời
thoại.

## metadata.json

- `case_id`, `meeting_id`: business ID của meeting.
- `meeting_title`, `meeting_date`: input cho API.
- `wave`, `wave_name`, `labels`, `objective`: đặc tính test.
- `part_count`, `source_files`: truy vết về dữ liệu nguồn.
- `generation`: model và cấu hình sinh dữ liệu.
- `validation`: trạng thái, word count và turn count.
- `ground_truth`: hiện là `available=false`; corpus chưa có expected tasks.

## index.json

Index chứa toàn bộ case và đường dẫn tương đối. Dùng index để lọc theo wave,
label, meeting date hoặc độ dài mà không cần scan từng thư mục.

## Dùng với API

```powershell
dotnet run --project clients\\csharp\\MeetingTaskPipeline.Client -- `
  "data\\normalized_transcripts\\cases\\<case-id>\\transcript.txt" `
  "<case-id>" "<meeting-title>" "<YYYY-MM-DD>"
```

Để dùng với Power Automate, upload `transcript.txt` và map ba field
`meeting_id`, `meeting_title`, `meeting_date` từ `metadata.json`.

## Ground truth

Đây là exploratory/stress corpus, chưa phải regression dataset. Muốn đánh giá
precision/recall, review một case và tạo `expected_output.json` trong dataset
validation riêng. Không sửa transcript chuẩn hóa để làm output dễ pass.
"""


def convert(source_root: Path, output_root: Path, overwrite: bool) -> None:
    if output_root.exists():
        if not overwrite:
            raise FileExistsError(
                f"{output_root} already exists; rerun with --overwrite"
            )
        source_resolved = source_root.resolve()
        output_resolved = output_root.resolve()
        if output_resolved == source_resolved or source_resolved in output_resolved.parents:
            raise ValueError("output directory must not be inside the source directory")
        shutil.rmtree(output_root)

    cases_root = output_root / "cases"
    cases_root.mkdir(parents=True)
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for wave_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        for case_dir in sorted(path for path in wave_dir.iterdir() if path.is_dir()):
            case, transcript_paths, generation_meta = _case_source(case_dir)
            case_id = case["meeting_id"]
            if case_id in seen_ids:
                raise ValueError(f"duplicate case ID: {case_id}")
            seen_ids.add(case_id)

            transcript, turn_count = _normalize_transcript(transcript_paths)
            metadata = _metadata(
                source_root,
                wave_dir,
                case,
                transcript_paths,
                generation_meta,
                turn_count,
            )
            case_output = cases_root / case_id
            case_output.mkdir()
            (case_output / "transcript.txt").write_text(transcript, encoding="utf-8")
            (case_output / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            entries.append(_index_entry(metadata))

    entries.sort(key=lambda item: (item["wave"], item["case_id"]))
    index = {
        "schema_version": SCHEMA_VERSION,
        "dataset_name": "normalized-generated-transcripts",
        "source": "../generated_transcripts",
        "case_count": len(entries),
        "ground_truth_available": False,
        "cases": entries,
    }
    (output_root / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_root / "README.md").write_text(_readme(), encoding="utf-8")
    print(f"Normalized {len(entries)} cases into {output_root}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/generated_transcripts"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/normalized_transcripts"),
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    convert(args.source, args.output, args.overwrite)


if __name__ == "__main__":
    main()
