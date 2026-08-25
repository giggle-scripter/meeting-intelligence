"""Import reviewed JSONL rows into the versioned task-evidence source of truth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewed_jsonl", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/quality/task-evidence-v2.jsonl"))
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.reviewed_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    keys = {(row.get("case_id"), row.get("expected_task_index")) for row in rows}
    if len(keys) != len(rows):
        raise ValueError("duplicate task evidence key")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"Imported task evidence: rows={len(rows)} output={args.output}")


if __name__ == "__main__":
    main()
