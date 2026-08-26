from __future__ import annotations

from pathlib import Path

from experiments.distilled_proposal_ranker.contracts import load_protocol
from experiments.distilled_proposal_ranker.folds import (
    assert_no_fold_leakage,
    assign_folds,
    build_fold_manifest,
)
from experiments.distilled_proposal_ranker.hashing import canonical_json_hash


def test_fold_assignment_is_deterministic_and_grouped() -> None:
    cases = [
        {"case_id": f"C-{index}", "wave": f"W{index % 2}", "length_class": "SHORT", "expected_task_count": index % 3}
        for index in range(20)
    ]
    first = assign_folds(cases, 5, 1729)
    second = assign_folds(list(reversed(cases)), 5, 1729)
    assert first == second
    assert len(first) == len(cases)


def test_real_manifest_has_no_outer_or_inner_case_leakage() -> None:
    root = Path.cwd()
    snapshot = load_protocol(Path("experiments/distilled_proposal_ranker/config/protocol-v1.json"))
    first = build_fold_manifest(root, snapshot)
    second = build_fold_manifest(root, snapshot)
    assert canonical_json_hash(first) == canonical_json_hash(second)
    assert_no_fold_leakage(first)
    assert len(first["outer_fold_by_case"]) == 86
    zero_cases = set(snapshot.protocol.zero_task_case_ids)
    table = {row["case_id"]: row for row in first["cases"]}
    assert all(table[case_id]["expected_task_count"] == 0 for case_id in zero_cases)


def test_same_case_cannot_be_train_and_validation() -> None:
    invalid = {
        "outer_fold_by_case": {"A": 0},
        "outer_folds": [{"train_case_ids": ["A"], "valid_case_ids": ["A"], "inner_fold_by_case": {"A": 0}}],
    }
    try:
        assert_no_fold_leakage(invalid)
    except ValueError as error:
        assert "leak" in str(error)
    else:
        raise AssertionError("leakage was accepted")
