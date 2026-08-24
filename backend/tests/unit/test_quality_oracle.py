from backend.app.quality.oracle import build_oracle_report, validate_review_bundle


def _baseline_records() -> list[dict]:
    return [
        {"record_id": "M-1", "kind": "MISSING"},
        {"record_id": "U-1", "kind": "UNEXPECTED"},
        {"record_id": "F-1", "kind": "FIELD_ERROR"},
    ]


def _bundle() -> dict:
    return {
        "schema_version": "quality-oracle-review-v2",
        "expected_tasks": [{
            "case_id": "CASE", "expected_task_index": 0, "review_status": "HUMAN_CONFIRMED", "reviewer": "QA",
            "source_clause_ids": ["C-1"], "authority": "DIRECT_ASSIGNMENT",
            "action_spans": [{"clause_id": "C-1", "start": 0, "end": 4}],
        }],
        "error_reviews": [
            {"attribution_record_id": "M-1", "kind": "MISSING", "category": "SOURCE_CANDIDATE_ROUTED_DROP", "review_status": "HUMAN_CONFIRMED", "reviewer": "QA"},
            {"attribution_record_id": "U-1", "kind": "UNEXPECTED", "category": "FALSE_SOURCE_CANDIDATE", "review_status": "HUMAN_CONFIRMED", "reviewer": "QA"},
            {"attribution_record_id": "F-1", "kind": "FIELD_ERROR", "category": "FIELD_DUE_DATE_MISMATCH", "review_status": "HUMAN_CONFIRMED", "reviewer": "QA"},
        ],
    }


def test_review_bundle_requires_every_baseline_error_and_grounded_expected_task() -> None:
    assert validate_review_bundle(_bundle(), expected_task_count=1, baseline_records=_baseline_records()) == []

    incomplete = _bundle()
    incomplete["error_reviews"].pop()
    errors = validate_review_bundle(incomplete, expected_task_count=1, baseline_records=_baseline_records())

    assert "FIELD_ERROR reviews missing 1 baseline records" in errors


def test_review_bundle_handles_repeated_legacy_record_ids_by_occurrence() -> None:
    records = _baseline_records() + [{"record_id": "U-1", "kind": "UNEXPECTED"}]
    duplicated = _bundle()
    duplicated["error_reviews"].append({
        "attribution_record_id": "U-1", "kind": "UNEXPECTED",
        "category": "FALSE_SOURCE_CANDIDATE", "review_status": "HUMAN_CONFIRMED", "reviewer": "QA",
    })

    assert validate_review_bundle(duplicated, expected_task_count=1, baseline_records=records) == []


def test_oracle_reports_stage_specific_accounting_ceiling() -> None:
    report = build_oracle_report(
        {"expected_task_count": 10, "actual_task_count": 12, "matched_task_count": 6},
        _bundle(),
    )

    candidate = next(item for item in report["stage_ceilings"] if item["stage"] == "candidate")
    authority = next(item for item in report["stage_ceilings"] if item["stage"] == "authority")
    assert candidate["matched_task_count"] == 7
    assert candidate["actual_task_count"] == 13
    assert authority["matched_task_count"] == 6
    assert authority["actual_task_count"] == 11
