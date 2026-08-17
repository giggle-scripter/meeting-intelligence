"""Inspect parser, deduplication and text segmentation output."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from backend.app.models import MeetingInput
from backend.app.pipeline import preprocess_meeting


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    content = args.transcript.read_text(encoding="utf-8-sig")
    stages = preprocess_meeting(MeetingInput(args.transcript.stem, args.transcript.stem, args.date, content, args.transcript.name))
    payload = {key: ([asdict(item) for item in value] if isinstance(value, list) else value) for key, value in stages.items()}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
