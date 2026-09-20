from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.distilled_proposal_ranker.contracts import load_protocol
from experiments.distilled_proposal_ranker.hashing import atomic_write_json, require_runtime_output
from experiments.distilled_proposal_ranker.span_dataset import build_span_examples, read_evidence
from experiments.distilled_proposal_ranker.trace_reader import load_trace_set


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--folds", type=Path, required=True)
    parser.add_argument("--outer-fold", type=int, required=True)
    parser.add_argument("--view", choices=("train", "valid", "inference"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    output = require_runtime_output(root, args.output)
    snapshot = load_protocol(args.protocol)
    folds = json.loads(args.folds.read_text(encoding="utf-8-sig"))
    fold = folds["outer_folds"][args.outer_fold]
    case_ids = set(fold["train_case_ids"] if args.view == "train" else fold["valid_case_ids"])
    include_gold = case_ids if args.view != "inference" else set()
    traces = load_trace_set(root / snapshot.protocol.trace_path)
    examples = build_span_examples(
        {case_id: traces[case_id] for case_id in sorted(case_ids)},
        read_evidence(root / snapshot.protocol.evidence_path),
        folds["outer_fold_by_case"],
        include_gold_case_ids=include_gold,
        label_weights=snapshot.protocol.label_weights.model_dump(),
    )
    atomic_write_json(output, [item.model_dump(mode="json") for item in examples])


if __name__ == "__main__":
    main()
