"""Tests for conservative action-dataset derivation and grouped folds."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.build_action_classifier_dataset import (
    _load_case,
    build_dataset,
    build_group_folds,
    map_expected_tasks,
    write_dataset,
)


def _write_case(
    root: Path,
    case_id: str,
    transcript: str,
    tasks: list[dict],
    note: str = "",
) -> None:
    case_dir = root / case_id
    case_dir.mkdir(parents=True)
    (case_dir / "metadata.json").write_text(
        json.dumps(
            {
                "case_id": case_id,
                "meeting_id": case_id,
                "meeting_title": case_id,
                "meeting_date": "2026-08-17",
                "transcript_file": "transcript.txt",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "expected_output.json").write_text(
        json.dumps({"meeting_title": case_id, "tasks": tasks}, ensure_ascii=False),
        encoding="utf-8",
    )
    (case_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
    (case_dir / "meeting_note.txt").write_text(note, encoding="utf-8")


def _mini_corpus(tmp_path: Path) -> Path:
    validation = tmp_path / "validation"
    validation.mkdir()
    _write_case(
        validation,
        "CASE-A",
        "\n".join(
            [
                "[09:00:00] Lan: Khách đang đợi bản cập nhật.",
                "[09:01:00] Minh: Em sẽ gửi proposal trước thứ Sáu.",
                "[09:02:00] Lan: Ok, cảm ơn Minh.",
            ]
        ),
        [
            {
                "task_name": "Gửi proposal",
                "assignee": "Minh",
                "due_date_text": "trước thứ Sáu",
                "evidence": "Em sẽ gửi proposal trước thứ Sáu.",
            }
        ],
        note="Minh gửi proposal trước thứ Sáu.",
    )
    _write_case(
        validation,
        "CASE-B",
        "\n".join(
            [
                "[10:00:00] Hoa: Hay là Tuấn gửi báo cáo nhỉ?",
                "[10:01:00] Tuấn: Nếu khách hỏi thì em có thể gửi.",
                "[10:02:00] Hoa: Deadline task báo cáo đổi thành thứ Sáu.",
            ]
        ),
        [],
    )
    return validation


def test_builder_maps_exact_evidence_and_selects_hard_negatives(tmp_path: Path) -> None:
    validation = _mini_corpus(tmp_path)

    records, folds, report = build_dataset(
        validation,
        fold_count=2,
        include_pipeline_false_creates=False,
    )

    action_records = [record for record in records if record["label"] == "CLEAR_ACTION"]
    assert len(action_records) == 1
    assert action_records[0]["focus"] == "Em sẽ gửi proposal trước thứ Sáu."
    assert action_records[0]["features"]["note_supported"] is True
    assert action_records[0]["semantic_text"].startswith("[PREV]")
    assert {record["label"] for record in records} >= {"CLEAR_ACTION", "NON_ACTION", "UPDATE_ONLY"}
    assert any(record["features"]["is_question"] for record in records)
    assert report["mapping"]["accepted_task_count"] == 1
    assert report["mapping"]["manual_review_task_count"] == 0
    assert folds["group_key"] == "meeting_id"


def test_builder_marks_ambiguous_ground_truth_mapping_for_manual_review(
    tmp_path: Path,
) -> None:
    validation = tmp_path / "validation"
    validation.mkdir()
    repeated = "Em sẽ gửi proposal trước thứ Sáu"
    _write_case(
        validation,
        "CASE-A",
        "\n".join(
            [
                f"[09:00:00] Minh: {repeated}.",
                f"[09:01:00] Lan: {repeated} cho khách.",
            ]
        ),
        [
            {
                "task_name": "Gửi proposal",
                "assignee": "Minh",
                "evidence": repeated,
            }
        ],
    )
    _write_case(
        validation,
        "CASE-B",
        "[10:00:00] Lan: Có nên gửi báo cáo không?",
        [],
    )
    _write_case(
        validation,
        "CASE-C",
        "[11:00:00] Hoa: Nếu khách hỏi thì có thể gửi sau.",
        [],
    )

    records, _, report = build_dataset(
        validation,
        fold_count=2,
        include_pipeline_false_creates=False,
    )

    manual_records = [record for record in records if record["manual_review_required"]]
    assert len(manual_records) == 1
    assert manual_records[0]["label"] is None
    assert manual_records[0]["eligible_for_training"] is False
    assert report["mapping"]["manual_review_task_count"] == 1


def test_group_folds_never_split_one_meeting_between_train_and_validation() -> None:
    records = [
        {
            "record_id": f"record-{meeting}-{index}",
            "meeting_id": meeting,
            "label": "CLEAR_ACTION" if index == 0 else "NON_ACTION",
            "eligible_for_training": True,
        }
        for meeting in ("A", "B", "C", "D", "E")
        for index in range(2)
    ]

    first = build_group_folds(records, fold_count=5, seed=7)
    second = build_group_folds(records, fold_count=5, seed=7)

    assert first == second
    for fold in first["folds"]:
        assert set(fold["train_meeting_ids"]).isdisjoint(fold["validation_meeting_ids"])
        assert set(fold["train_meeting_ids"]) | set(fold["validation_meeting_ids"]) == {
            "A",
            "B",
            "C",
            "D",
            "E",
        }


def test_task_name_fallback_does_not_label_weak_question(tmp_path: Path) -> None:
    validation = tmp_path / "validation"
    validation.mkdir()
    _write_case(
        validation,
        "CASE-A",
        "[09:00:00] Lan: Phần demo có cần chuẩn bị môi trường test không?",
        [
            {
                "task_name": "Chuẩn bị môi trường demo",
                "assignee": "",
                "due_date_text": "",
            }
        ],
    )

    case = _load_case(validation / "CASE-A")
    mapping = map_expected_tasks(case, mapping_threshold=0.72, mapping_margin=0.12)[0]

    assert mapping.accepted is False
    assert mapping.manual_review_required is True


def test_write_dataset_is_stable_and_uses_only_requested_output(tmp_path: Path) -> None:
    validation = _mini_corpus(tmp_path)
    records, folds, report = build_dataset(
        validation,
        fold_count=2,
        include_pipeline_false_creates=False,
    )
    output = tmp_path / "output"

    write_dataset(records, folds, report, output)
    first = {path.name: path.read_bytes() for path in output.iterdir()}
    write_dataset(records, folds, report, output)
    second = {path.name: path.read_bytes() for path in output.iterdir()}

    assert first == second
    assert set(first) == {"train.jsonl", "folds.json", "dataset-report.json"}
