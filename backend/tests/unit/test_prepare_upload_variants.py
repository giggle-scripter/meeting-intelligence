import csv
import json

from backend.app.ingestion.meeting_package import METADATA_START, NOTE_START
from backend.app.ingestion import parse_meeting_package
from scripts.prepare_power_automate_uploads import build_upload_set


def test_prepare_upload_set_keeps_raw_and_adds_note_variant(tmp_path) -> None:
    source = tmp_path / "validation"
    case = source / "CASE-001"
    case.mkdir(parents=True)
    (case / "metadata.json").write_text(
        json.dumps({
            "case_id": "CASE-001",
            "meeting_title": "Release",
            "meeting_date": "2026-08-03",
            "transcript_file": "transcript.txt",
        }),
        encoding="utf-8",
    )
    (case / "expected_output.json").write_text('{"tasks": []}', encoding="utf-8")
    transcript = "Lan: Em sẽ cập nhật dashboard."
    (case / "transcript.txt").write_text(transcript, encoding="utf-8")
    (case / "meeting_note.txt").write_text("note nhanh\n- dashboard: Lan", encoding="utf-8")
    output = tmp_path / "uploads"

    variant_count = build_upload_set(source, output)

    assert variant_count == 2
    raw_package = (output / "CASE-001.txt").read_text(encoding="utf-8")
    note_package = (output / "CASE-001__with-note.txt").read_text(encoding="utf-8")
    assert raw_package.startswith(METADATA_START)
    assert NOTE_START not in raw_package
    assert note_package.startswith(METADATA_START)
    assert NOTE_START in note_package
    parsed_transcript, parsed_note, parsed_metadata = parse_meeting_package(
        note_package
    )
    assert parsed_transcript == transcript
    assert parsed_note and parsed_note.content == "note nhanh\n- dashboard: Lan"
    assert parsed_metadata.meeting_id == "CASE-001"
    assert parsed_metadata.meeting_title == "Release"
    assert parsed_metadata.meeting_date == "2026-08-03"
    with (output / "index.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["variant"] for row in rows] == ["transcript_only", "with_note"]
    assert {row["case_id"] for row in rows} == {"CASE-001"}
