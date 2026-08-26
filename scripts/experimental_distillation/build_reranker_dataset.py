from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.distilled_proposal_ranker.contracts import GroundedSpan, ProposalRecord
from experiments.distilled_proposal_ranker.hashing import atomic_write_json, require_runtime_output
from experiments.distilled_proposal_ranker.reranker_dataset import label_training_proposals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--gold-spans", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    proposals = [ProposalRecord.model_validate(item) for item in json.loads(args.proposals.read_text(encoding="utf-8-sig"))]
    gold = [GroundedSpan.model_validate(item) for item in json.loads(args.gold_spans.read_text(encoding="utf-8-sig"))]
    labels = label_training_proposals(proposals, gold)
    atomic_write_json(require_runtime_output(root, args.output), [item.__dict__ for item in labels])


if __name__ == "__main__":
    main()
