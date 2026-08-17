"""Print candidate windows and rule annotations for one transcript."""

import argparse
import json
from pathlib import Path

from backend.app.annotation import annotate_clauses, extract_date_mentions
from backend.app.candidate import build_candidate_windows, merge_windows
from backend.app.models import MeetingInput
from backend.app.pipeline import preprocess_meeting


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    content = args.transcript.read_text(encoding="utf-8-sig")
    stages = preprocess_meeting(MeetingInput(args.transcript.stem, args.transcript.stem, args.date, content, args.transcript.name))
    mentions = extract_date_mentions(stages["clauses"])
    annotations = annotate_clauses(stages["clauses"], {item.clause_id for item in mentions.values()})
    windows = merge_windows(build_candidate_windows(stages["clauses"], annotations))
    print(json.dumps([window.__dict__ for window in windows], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
