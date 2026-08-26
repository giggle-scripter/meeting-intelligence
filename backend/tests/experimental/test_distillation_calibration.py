from __future__ import annotations

import pytest

from experiments.distilled_proposal_ranker.calibration import (
    calibrate_probabilities,
    choose_threshold_topk,
    expected_calibration_error,
    fit_temperature,
)


def test_temperature_scaling_is_positive_and_deterministic() -> None:
    logits = [-3.0, -1.0, 1.0, 3.0]
    labels = [0.0, 0.0, 1.0, 1.0]
    first = fit_temperature(logits, labels)
    second = fit_temperature(logits, labels)
    assert first == pytest.approx(second)
    assert 0.05 <= first <= 20.0
    probabilities = calibrate_probabilities(logits, first)
    assert probabilities == sorted(probabilities)
    assert expected_calibration_error(probabilities, labels) >= 0.0


def test_threshold_and_topk_use_only_supplied_inner_oof_rows() -> None:
    selection = choose_threshold_topk(
        ["A", "A", "B", "B"],
        [0.9, 0.8, 0.7, 0.1],
        [1.0, 0.0, 1.0, 0.0],
        threshold_grid=[0.2, 0.5, 0.85],
    )
    assert selection.threshold in {0.2, 0.5, 0.85}
    assert selection.top_k in {3, 5, 8}
    assert selection.precision_constraint_met
    with pytest.raises(ValueError, match="arrays"):
        choose_threshold_topk(["inner"], [0.9, 0.2], [1.0], threshold_grid=[0.5])


def test_no_precision_feasible_marks_constraint_failure() -> None:
    selection = choose_threshold_topk(["A"], [0.9], [0.0], threshold_grid=[0.2, 0.5])
    assert selection.precision_constraint_met is False
    assert selection.threshold == 0.5
