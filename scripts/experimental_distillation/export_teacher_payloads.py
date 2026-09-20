from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.distilled_proposal_ranker.contracts import load_protocol
from experiments.distilled_proposal_ranker.hashing import atomic_write_json, require_runtime_output
from experiments.distilled_proposal_ranker.teacher_payload import build_teacher_payloads
from experiments.distilled_proposal_ranker.trace_reader import load_trace_set


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--folds", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    output_root = require_runtime_output(root, args.output_root)
    snapshot = load_protocol(args.protocol)
    folds = json.loads(args.folds.read_text(encoding="utf-8-sig"))
    traces = load_trace_set(root / snapshot.protocol.trace_path)
    manifest = []
    for case_id in sorted(traces):
        payloads = build_teacher_payloads(case_id, traces[case_id], folds["outer_fold_by_case"][case_id])
        for payload in payloads:
            relative = Path(f"outer-{payload.outer_fold}") / f"{payload.payload_id}.json"
            if not args.dry_run:
                atomic_write_json(output_root / relative, payload.model_dump(mode="json"))
            manifest.append({"payload_id": payload.payload_id, "input_hash": payload.input_hash, "path": relative.as_posix()})
    atomic_write_json(output_root / "manifest.json", manifest)
    print(json.dumps({"payload_count": len(manifest), "manifest_hash": canonical_manifest_hash(manifest)}, sort_keys=True))


def canonical_manifest_hash(manifest: list[dict]) -> str:
    from experiments.distilled_proposal_ranker.hashing import canonical_json_hash

    return canonical_json_hash(manifest)


if __name__ == "__main__":
    main()
