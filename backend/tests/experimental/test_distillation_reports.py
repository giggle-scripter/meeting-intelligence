from __future__ import annotations

from experiments.distilled_proposal_ranker.reports import paired_bootstrap_ci, render_markdown


def test_paired_bootstrap_is_deterministic() -> None:
    rows = [
        {"expected": 2, "challenger_actual": 2, "challenger_matched": 2, "baseline_actual": 2, "baseline_matched": 1},
        {"expected": 1, "challenger_actual": 1, "challenger_matched": 0, "baseline_actual": 1, "baseline_matched": 0},
    ]
    assert paired_bootstrap_ci(rows, samples=100, seed=1729) == paired_bootstrap_ci(rows, samples=100, seed=1729)


def test_markdown_contains_only_aggregate_fields() -> None:
    report = {
        "readiness": "NOT_READY", "decision": "SPAN_GATE_FAILED",
        "hashes": {
            "protocol_sha256": "a" * 64,
            "branch": "experimental/distilled-proposal-ranker-v1",
            "base_commit": "a04e815",
            "experiment_commit": "b" * 40,
        },
        "teacher": {"mode": "cache-only", "model": "cache-only-no-model", "prompt_versions": ["teacher-a-v1", "teacher-b-v1"], "status": "SKIPPED_NO_VALID_CACHE", "calls": 0, "estimated_cost_usd": 0.0},
        "span": {"token": {"f1": 0.5}, "exact": {"precision": 0.5, "recall": 0.5, "f1": 0.5}, "source_recall": {"lattice": 0.5, "neural": 0.6, "union": 0.7}},
        "proposal": {"recall_at_30": 0.8}, "ranker": {"pr_auc": 0.4, "selected_count": 10},
        "task": {"global": {"expected_task_count": 277, "actual_task_count": 300, "matched_task_count": 170, "unexpected_task_count": 130, "missing_task_count": 107, "task_identity_precision": 0.5, "task_identity_recall": 0.6, "task_identity_f1": 0.55}, "per_fold": [{"outer_fold": 0, "challenger": {"task_identity_precision": 0.5, "task_identity_recall": 0.6, "task_identity_f1": 0.55}, "f1_delta": 0.01}], "seed_summary": {"individual": {"17": {"task_identity_precision": 0.5, "task_identity_recall": 0.6, "task_identity_f1": 0.55}}, "task_identity_f1_mean": 0.55, "task_identity_f1_std": 0.0}},
        "f1_delta_vs_q2": 0.01, "bootstrap_f1_delta_ci_95": [-0.01, 0.03], "minimum_fold_delta": -0.01,
        "safety_violation_count": 0, "environment": {"python": "3.11"}, "failures": ["span"],
    }
    markdown = render_markdown(report)
    assert "development nested-CV" in markdown
    assert "transcript" not in markdown.casefold()
    assert "SPAN_GATE_FAILED" in markdown
