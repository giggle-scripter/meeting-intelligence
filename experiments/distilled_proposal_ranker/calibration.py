"""Inner-OOF-only temperature scaling and fixed threshold/top-k selection."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import torch

from .metrics import prf


def fit_temperature(logits: list[float], labels: list[float]) -> float:
    if not logits or len({int(value >= 0.5) for value in labels}) < 2:
        return 1.0
    values = torch.tensor(logits, dtype=torch.float64)
    targets = torch.tensor(labels, dtype=torch.float64)
    log_temperature = torch.nn.Parameter(torch.zeros((), dtype=torch.float64))
    optimizer = torch.optim.LBFGS([log_temperature], lr=0.1, max_iter=100, line_search_fn="strong_wolfe")

    def closure() -> torch.Tensor:
        optimizer.zero_grad()
        temperature = log_temperature.exp().clamp(0.05, 20.0)
        loss = torch.nn.functional.binary_cross_entropy_with_logits(values / temperature, targets)
        loss.backward()
        return loss

    optimizer.step(closure)
    return float(log_temperature.detach().exp().clamp(0.05, 20.0))


def calibrate_probabilities(logits: list[float], temperature: float) -> list[float]:
    return [1.0 / (1.0 + math.exp(-value / temperature)) for value in logits]


@dataclass(frozen=True)
class Selection:
    threshold: float
    top_k: int
    precision: float
    recall: float
    f1: float
    precision_constraint_met: bool


def choose_threshold_topk(
    case_ids: list[str],
    probabilities: list[float],
    labels: list[float],
    *,
    threshold_grid: list[float],
    precision_floor: float = 0.48,
    top_k_grid: tuple[int, ...] = (3, 5, 8),
) -> Selection:
    if not (len(case_ids) == len(probabilities) == len(labels)):
        raise ValueError("inner OOF calibration arrays differ in length")
    candidates: list[Selection] = []
    for top_k in top_k_grid:
        by_case: dict[str, list[int]] = {}
        for index, case_id in enumerate(case_ids):
            by_case.setdefault(case_id, []).append(index)
        for threshold in threshold_grid:
            selected: set[int] = set()
            for indices in by_case.values():
                ranked = sorted(indices, key=lambda index: (-probabilities[index], index))
                selected.update(index for index in ranked[:top_k] if probabilities[index] >= threshold)
            expected = sum(value >= 0.8 for value in labels)
            matched = sum(labels[index] >= 0.8 for index in selected)
            values = prf(matched, len(selected), expected)
            candidates.append(Selection(threshold, top_k, values["precision"], values["recall"], values["f1"], values["precision"] >= precision_floor))
    valid = [item for item in candidates if item.precision_constraint_met]
    if valid:
        return max(valid, key=lambda item: (item.f1, item.recall, item.threshold, -item.top_k))
    return max(candidates, key=lambda item: (item.precision, item.recall, item.threshold, -item.top_k))


def expected_calibration_error(probabilities: list[float], labels: list[float], bins: int = 10) -> float:
    if not probabilities:
        return 0.0
    result = 0.0
    for lower in np.linspace(0.0, 1.0, bins, endpoint=False):
        upper = lower + 1.0 / bins
        indices = [index for index, value in enumerate(probabilities) if lower <= value < upper or (upper >= 1.0 and value == 1.0)]
        if not indices:
            continue
        confidence = sum(probabilities[index] for index in indices) / len(indices)
        accuracy = sum(labels[index] for index in indices) / len(indices)
        result += len(indices) / len(probabilities) * abs(confidence - accuracy)
    return result
