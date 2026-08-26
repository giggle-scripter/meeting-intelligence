from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.distilled_proposal_ranker.contracts import RerankerExample, load_protocol
from experiments.distilled_proposal_ranker.hashing import canonical_json_hash, require_model_output, sha256_file
from experiments.distilled_proposal_ranker.reranker_model import load_reranker
from experiments.distilled_proposal_ranker.reranker_training import train_reranker


def _examples(path: Path) -> list[RerankerExample]:
    return [RerankerExample.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8-sig"))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--valid", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--learning-rate", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    output = require_model_output(root, args.output)
    snapshot = load_protocol(args.protocol)
    protocol = snapshot.protocol
    if args.seed not in protocol.training_seeds or args.learning_rate not in protocol.reranker_learning_rates:
        raise ValueError("training choice is outside locked protocol")
    model, tokenizer = load_reranker(protocol.reranker_model_name)
    result = train_reranker(
        model, tokenizer, _examples(args.train), _examples(args.valid), seed=args.seed,
        learning_rate=args.learning_rate, weight_decay=protocol.weight_decay,
        warmup_ratio=protocol.warmup_ratio, max_epochs=protocol.max_epochs,
        patience=protocol.early_stopping_patience, gradient_clip_norm=protocol.gradient_clip_norm,
        max_length=protocol.reranker_max_length, checkpoint_dir=output,
        manifest_values={
            "protocol_hash": snapshot.sha256, "train_data_hash": sha256_file(args.train),
            "valid_data_hash": sha256_file(args.valid), "model_name": protocol.reranker_model_name,
            "code_hash": canonical_json_hash({"reranker_model": sha256_file(Path("experiments/distilled_proposal_ranker/reranker_model.py"))}),
        },
    )
    print(json.dumps(result.__dict__, sort_keys=True))


if __name__ == "__main__":
    main()
