from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.distilled_proposal_ranker.contracts import SpanExample, load_protocol
from experiments.distilled_proposal_ranker.hashing import canonical_json_hash, require_model_output, sha256_file
from experiments.distilled_proposal_ranker.span_model import load_span_model
from experiments.distilled_proposal_ranker.span_training import train_span_model


def _examples(path: Path) -> list[SpanExample]:
    return [SpanExample.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8-sig"))]


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
    if args.seed not in protocol.training_seeds or args.learning_rate not in protocol.span_learning_rates:
        raise ValueError("training choice is outside locked protocol")
    model, tokenizer = load_span_model(protocol.span_model_name)
    train = _examples(args.train)
    valid = _examples(args.valid)
    result = train_span_model(
        model,
        tokenizer,
        train,
        valid,
        seed=args.seed,
        learning_rate=args.learning_rate,
        weight_decay=protocol.weight_decay,
        warmup_ratio=protocol.warmup_ratio,
        max_epochs=protocol.max_epochs,
        patience=protocol.early_stopping_patience,
        gradient_clip_norm=protocol.gradient_clip_norm,
        max_length=protocol.span_max_length,
        checkpoint_dir=output,
        manifest_values={
            "protocol_hash": snapshot.sha256,
            "train_data_hash": sha256_file(args.train),
            "valid_data_hash": sha256_file(args.valid),
            "model_name": protocol.span_model_name,
            "code_hash": canonical_json_hash({"span_model": sha256_file(Path("experiments/distilled_proposal_ranker/span_model.py"))}),
        },
    )
    print(json.dumps(result.__dict__, sort_keys=True))


if __name__ == "__main__":
    main()
