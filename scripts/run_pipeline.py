"""Run the complete pipeline for one local transcript file."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from backend.app.models import MeetingInput
from backend.app.pipeline import process_meeting


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--meeting-id", default="local-meeting")
    parser.add_argument("--title", default="Local Meeting")
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    content = args.transcript.read_text(encoding="utf-8-sig")
    result = process_meeting(MeetingInput(args.meeting_id, args.title, args.date, content, args.transcript.name))
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
