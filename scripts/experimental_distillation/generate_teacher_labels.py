from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from experiments.distilled_proposal_ranker.contracts import TeacherPayload, load_protocol
from experiments.distilled_proposal_ranker.hashing import atomic_write_json, require_runtime_output
from experiments.distilled_proposal_ranker.teacher_client import TeacherClient
from experiments.distilled_proposal_ranker.teacher_consensus import teacher_consensus


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--mode", choices=("disabled", "cache-only", "provider"), required=True)
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    require_runtime_output(root, args.payload_root)
    cache_root = require_runtime_output(root, args.cache_root)
    report_path = require_runtime_output(root, args.report)
    protocol = load_protocol(args.protocol).protocol
    model = os.environ.get("DISTILL_TEACHER_MODEL", "cache-only-no-model")
    client = TeacherClient(args.mode, cache_root)
    valid_responses = 0
    labels = []
    for path in sorted(args.payload_root.glob("outer-*/*.json")):
        payload = TeacherPayload.model_validate_json(path.read_text(encoding="utf-8-sig"))
        responses = []
        for version in protocol.teacher_prompt_versions:
            response = client.get(payload, version, model=model)
            if response is not None:
                responses.append(response)
                valid_responses += 1
        labels.extend(item.__dict__ for item in teacher_consensus(responses))
    status = "DISABLED" if args.mode == "disabled" else "SKIPPED_NO_VALID_CACHE" if not valid_responses else "COMPLETE"
    report = {
        "schema_version": "teacher-run-report-v1",
        "mode": args.mode,
        "model": model,
        "prompt_versions": protocol.teacher_prompt_versions,
        "status": status,
        "valid_response_count": valid_responses,
        "label_count": len(labels),
        "calls": client.calls,
        "estimated_cost_usd": client.estimated_cost,
        "labels": labels,
    }
    atomic_write_json(report_path, report)
    print(json.dumps({key: value for key, value in report.items() if key != "labels"}, sort_keys=True))


if __name__ == "__main__":
    main()
