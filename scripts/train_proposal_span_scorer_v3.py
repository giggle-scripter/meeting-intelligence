"""Train and evaluate a small, deterministic span scorer on the fixed corpus split."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import re


def _features(row: dict) -> tuple[str, ...]:
    span = row["span"]["text"]
    tokens = re.findall(r"\w+", span.casefold())
    features = {f"variant={row.get('span_variant', 'FULL_BOUNDARY')}", f"verb={tokens[0] if tokens else ''}"}
    features.add(f"token_count={min(len(tokens), 8)}")
    features.add(f"semantic_bucket={int(float(row.get('semantic_score', 0.0)) * 4)}")
    features.add(f"ranking_bucket={int(float(row.get('ranking_score', 0.0)) * 4)}")
    features.update(f"role={item}" for item in row.get("nucleus_roles", []))
    features.update(f"flag={item}" for item in row.get("nucleus_flags", []))
    return tuple(sorted(features))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))


def _score(features: tuple[str, ...], weights: dict[str, float], intercept: float) -> float:
    return _sigmoid(intercept + sum(weights.get(item, 0.0) for item in features))


def _fit(rows: list[dict], *, epochs: int = 80, learning_rate: float = 0.04, l2: float = 0.002) -> tuple[dict[str, float], float]:
    positives = sum(int(row["exact_span_label"]) for row in rows)
    if not positives or positives == len(rows):
        raise ValueError("training split needs both positive and negative labels")
    positive_weight = (len(rows) - positives) / positives
    weights: dict[str, float] = {}
    intercept = 0.0
    prepared = [(_features(row), int(row["exact_span_label"])) for row in rows]
    for _ in range(epochs):
        for features, label in prepared:
            probability = _score(features, weights, intercept)
            sample_weight = positive_weight if label else 1.0
            error = sample_weight * (label - probability)
            intercept += learning_rate * error
            for name in features:
                weights[name] = weights.get(name, 0.0) + learning_rate * (error - l2 * weights.get(name, 0.0))
    return {name: round(value, 8) for name, value in weights.items()}, round(intercept, 8)


def _metrics(rows: list[dict], weights: dict[str, float], intercept: float, threshold: float) -> dict[str, float | int]:
    predicted = [_score(_features(row), weights, intercept) >= threshold for row in rows]
    labels = [bool(row["exact_span_label"]) for row in rows]
    true_positive = sum(prediction and label for prediction, label in zip(predicted, labels))
    selected = sum(predicted)
    positives = sum(labels)
    precision = true_positive / selected if selected else 0.0
    recall = true_positive / positives if positives else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"selected": selected, "positives": positives, "matched": true_positive, "precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path("data/quality/proposal-span-supervision-v3.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/quality/proposal-span-scorer-v3.json"))
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.corpus.read_text(encoding="utf-8").splitlines() if line.strip()]
    train = [row for row in rows if row["split"] == "train"]
    heldout = [row for row in rows if row["split"] == "held_out"]
    weights, intercept = _fit(train)
    candidates = [index / 20 for index in range(1, 20)]
    threshold = max(candidates, key=lambda value: (_metrics(train, weights, intercept, value)["f1"], value))
    payload = {
        "schema_version": "proposal-span-logistic-scorer-v3",
        "corpus_sha256": sha256(args.corpus.read_bytes()).hexdigest(),
        "training_split": "W1,W2,W3",
        "heldout_split": "W4,W5",
        "feature_count": len(weights),
        "threshold": threshold,
        "intercept": intercept,
        "weights": dict(sorted(weights.items())),
        "train_metrics": _metrics(train, weights, intercept, threshold),
        "heldout_metrics": _metrics(heldout, weights, intercept, threshold),
        "heldout_was_not_used_for_training_or_threshold_selection": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Proposal span scorer: threshold={threshold:.2f} train_f1={payload['train_metrics']['f1']:.3f} heldout_f1={payload['heldout_metrics']['f1']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
