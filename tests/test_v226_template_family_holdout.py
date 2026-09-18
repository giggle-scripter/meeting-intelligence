from __future__ import annotations

import pytest

from scripts.experimental_distillation.run_v226_dev42_template_family_holdout import (
    _groups,
    _stability,
)


def test_v226_template_folds_are_deterministic_and_hold_out_nested_families() -> None:
    case_ids = [
        "W3-MED-C4-N2-PROD-INT-019",
        "W3-MED-C4-N2-OPS-INT-001",
        "W3-MED-C4-N2-IT-FPC-001",
        "W3-MED-C4-N2-PROD-INT-002",
    ]
    templates = _groups(case_ids, "template")
    families = _groups(case_ids, "family")
    assert list(templates) == ["FPC", "INT"]
    assert list(families) == ["IT-FPC", "OPS-INT", "PROD-INT"]
    for valid_ids in templates.values():
        valid_families = {"-".join(item.split("-")[4:6]) for item in valid_ids}
        fit_families = {
            "-".join(item.split("-")[4:6])
            for item in case_ids
            if item not in valid_ids
        }
        assert not valid_families & fit_families


def test_v226_stability_reports_macro_worst_and_spread() -> None:
    folds = [
        {"fold": 0, "reranked": {"task_identity_f1": 0.2}},
        {"fold": 1, "reranked": {"task_identity_f1": 0.8}},
        {"fold": 2, "reranked": {"task_identity_f1": 0.5}},
    ]
    report = _stability(folds)
    assert report["fold_count"] == 3
    assert report["macro_f1"] == 0.5
    assert report["worst_fold_f1"] == 0.2
    assert report["worst_fold"] == 0
    assert report["spread"] == pytest.approx(0.6)
