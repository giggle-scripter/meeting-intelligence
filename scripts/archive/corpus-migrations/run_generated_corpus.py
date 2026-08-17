"""Run generated transcript waves as an exploratory corpus."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import fnmatch
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterator

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting


def _metadata(case_dir: Path) -> dict[str, Any]:
    generation_meta = case_dir / "generation_meta.json"
    if generation_meta.exists():
        return json.loads(generation_meta.read_text(encoding="utf-8"))["case"]

    part_meta = next(iter(sorted(case_dir.glob("part_*.meta.json"))), None)
    if part_meta is None:
        raise ValueError("missing generation_meta.json or part metadata")
    return json.loads(part_meta.read_text(encoding="utf-8"))["case"]


def _transcript(case_dir: Path) -> tuple[str, str]:
    single = case_dir / "transcript.txt"
    if single.exists():
        return single.read_text(encoding="utf-8-sig"), single.name

    parts = sorted(case_dir.glob("part_*.txt"))
    if not parts:
        raise ValueError("missing transcript.txt or part files")
    content = "\n\n".join(part.read_text(encoding="utf-8-sig").strip() for part in parts)
    return content + "\n", f"{case_dir.name}.txt"


def iter_cases(root: Path, wave_pattern: str, case_pattern: str) -> Iterator[tuple[dict[str, Any], str, str]]:
    for wave_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if not fnmatch.fnmatch(wave_dir.name, wave_pattern):
            continue
        for case_dir in sorted(path for path in wave_dir.iterdir() if path.is_dir()):
            if not fnmatch.fnmatch(case_dir.name, case_pattern):
                continue
            metadata = _metadata(case_dir)
            transcript, file_name = _transcript(case_dir)
            yield metadata, transcript, file_name


def _payload(metadata: dict[str, Any], transcript: str, file_name: str) -> dict[str, Any]:
    return {
        "meeting_id": metadata["meeting_id"],
        "meeting_title": metadata["title"],
        "meeting_date": metadata["meeting_date"],
        "file_name": file_name,
        "transcript": transcript,
        "speaker_aliases": {},
    }


def _run_api(endpoint: str, api_key: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    response = httpx.post(endpoint, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path("data/generated_transcripts"),
    )
    parser.add_argument("--wave", default="wave_*", help="Wave glob.")
    parser.add_argument("--case", default="*", help="Meeting ID glob.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--endpoint", help="Optional deployed process endpoint.")
    parser.add_argument(
        "--api-key",
        default=os.getenv("POWER_AUTOMATE_API_KEY", ""),
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output", type=Path, help="Optional directory for actual JSON.")
    args = parser.parse_args()

    total = succeeded = failed = task_count = unresolved = 0
    for metadata, transcript, file_name in iter_cases(args.root, args.wave, args.case):
        if args.limit and total >= args.limit:
            break
        total += 1
        meeting_id = metadata["meeting_id"]
        try:
            if args.endpoint:
                result = _run_api(
                    args.endpoint,
                    args.api_key,
                    _payload(metadata, transcript, file_name),
                    args.timeout,
                )
            else:
                result = asdict(
                    process_meeting(
                        MeetingInput(
                            meeting_id,
                            metadata["title"],
                            metadata["meeting_date"],
                            transcript,
                            file_name,
                        )
                    )
                )
            succeeded += 1
            task_count += len(result["tasks"])
            unresolved += len(result["unresolved_window_ids"])
            print(
                f"{meeting_id}: OK tasks={len(result['tasks'])} "
                f"unresolved={len(result['unresolved_window_ids'])}"
            )
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / f"{meeting_id}.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        except Exception as exc:
            failed += 1
            print(f"{meeting_id}: ERROR {type(exc).__name__}: {exc}")

    print(
        f"Cases={total} succeeded={succeeded} failed={failed} "
        f"tasks={task_count} unresolved_windows={unresolved}"
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
