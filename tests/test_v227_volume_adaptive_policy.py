from __future__ import annotations

import pytest

from scripts.experimental_distillation.run_v227_dev42_volume_adaptive_policy import (
    ADAPTIVE_POLICY_FEATURES,
    _context,
    _stability,
)


def test_v227_stability_excludes_zero_expected_folds() -> None:
    folds = [
        {"fold": 0, "reranked": {"expected_task_count": 0, "task_identity_f1": 0.0}},
        {"fold": 1, "reranked": {"expected_task_count": 5, "task_identity_f1": 0.4}},
        {"fold": 2, "reranked": {"expected_task_count": 8, "task_identity_f1": 0.8}},
    ]
    report = _stability(folds)
    assert report["fold_count"] == 3
    assert report["supported_fold_count"] == 2
    assert report["excluded_zero_expected_folds"] == [0]
    assert report["macro_f1"] == pytest.approx(0.6)
    assert report["supported_expected_task_count"] == 13


def test_v227_policy_context_has_only_runtime_safe_features() -> None:
    class Proposal:
        origin = "intermediate"
        assignee = "A"
        due_date = ""
        due_date_text = ""
        status = "Proposed"

    context = _context([Proposal(), Proposal()], [0.2, 0.4])
    assert set(context) == set(ADAPTIVE_POLICY_FEATURES)
    assert context["candidate_count"] == 2
    assert context["intermediate_share"] == 1.0
    assert context["assignee_completeness"] == 1.0
