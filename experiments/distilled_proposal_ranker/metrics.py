"""Deterministic span and ranking metric primitives."""

from __future__ import annotations

from typing import Iterable

from .contracts import GroundedSpan


def prf(matched: int, predicted: int, expected: int) -> dict[str, float]:
    precision = matched / predicted if predicted else 0.0
    recall = matched / expected if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def token_prf(predictions: Iterable[list[int]], labels: Iterable[list[int]]) -> dict[str, float]:
    true_positive = predicted_positive = expected_positive = 0
    for predicted, expected in zip(predictions, labels, strict=True):
        for left, right in zip(predicted, expected, strict=True):
            if right == -100:
                continue
            left_positive = left in (1, 2)
            right_positive = right in (1, 2)
            predicted_positive += int(left_positive)
            expected_positive += int(right_positive)
            true_positive += int(left_positive and right_positive)
    return prf(true_positive, predicted_positive, expected_positive)


def exact_span_prf(predicted: Iterable[tuple[str, GroundedSpan]], expected: Iterable[tuple[str, GroundedSpan]]) -> dict[str, float]:
    predicted_set = {(case_id, item.clause_id, item.start, item.end, item.text) for case_id, item in predicted}
    expected_set = {(case_id, item.clause_id, item.start, item.end, item.text) for case_id, item in expected}
    return prf(len(predicted_set & expected_set), len(predicted_set), len(expected_set))


def ranking_metrics(
    case_ids: list[str],
    probabilities: list[float],
    labels: list[float],
    *,
    expected_positive_count: int | None = None,
) -> dict[str, float | None]:
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

    binary = [int(value >= 0.8) for value in labels]
    both_classes = len(set(binary)) == 2
    result: dict[str, float | None] = {
        "pr_auc": float(average_precision_score(binary, probabilities)) if both_classes else None,
        "roc_auc": float(roc_auc_score(binary, probabilities)) if both_classes else None,
        "brier_score": float(brier_score_loss(binary, probabilities)),
    }
    calibration_error = 0.0
    for bin_index in range(10):
        lower = bin_index / 10
        upper = (bin_index + 1) / 10
        indices = [
            index for index, value in enumerate(probabilities)
            if lower <= value < upper or (bin_index == 9 and value == 1.0)
        ]
        if indices:
            confidence = sum(probabilities[index] for index in indices) / len(indices)
            accuracy = sum(binary[index] for index in indices) / len(indices)
            calibration_error += len(indices) / len(probabilities) * abs(confidence - accuracy)
    result["expected_calibration_error"] = calibration_error
    by_case: dict[str, list[int]] = {}
    for index, case_id in enumerate(case_ids):
        by_case.setdefault(case_id, []).append(index)
    expected = expected_positive_count if expected_positive_count is not None else sum(binary)
    for k in (1, 3, 5, 10, 30):
        selected = set()
        for indices in by_case.values():
            selected.update(sorted(indices, key=lambda index: (-probabilities[index], index))[:k])
        result[f"recall_at_{k}"] = sum(binary[index] for index in selected) / expected if expected else 0.0
    return result
