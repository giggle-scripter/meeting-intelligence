from pathlib import Path

from backend.app.quality.validation_gate import (
    load_split_manifest,
    task_f1,
    verify_evaluation_report,
    verify_split_manifest,
)


def test_locked_manifest_matches_reviewed_validation_corpus() -> None:
    manifest = load_split_manifest(Path("data/evaluation_splits/locked_validation_v1.json"))

    result = verify_split_manifest(manifest, Path("data/validation"))

    assert result.passed
    assert result.split_kind == "LOCKED_VALIDATION"
    assert len(result.case_ids) == 20


def test_report_must_execute_exactly_the_locked_cases() -> None:
    case_ids = ("A", "B")
    report = {
        "case_execution": [{"case_id": "A"}, {"case_id": "B"}],
        "execution_errors": [],
        "metrics": {"case_count": 2},
    }

    assert verify_evaluation_report(report, case_ids) == []
    assert verify_evaluation_report(
        {**report, "case_execution": [{"case_id": "A"}]}, case_ids
    ) == ["evaluation report cases do not match split manifest"]


def test_task_f1_uses_identity_metrics() -> None:
    assert task_f1({"task_identity_precision": 0.5, "task_identity_recall": 0.5}) == 0.5
