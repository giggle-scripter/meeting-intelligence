from backend.app.ingestion import (
    build_meeting_package,
    parse_meeting_package,
    split_meeting_package,
)


def test_package_round_trip_keeps_note_out_of_transcript() -> None:
    packaged = build_meeting_package("Lan: Em sẽ cập nhật dashboard.", "note nhanh\n- dashboard: Lan")

    transcript, note = split_meeting_package(packaged)

    assert transcript == "Lan: Em sẽ cập nhật dashboard."
    assert note is not None
    assert note.content == "note nhanh\n- dashboard: Lan"


def test_plain_transcript_is_unchanged_when_note_is_absent() -> None:
    transcript = "An: No action item was agreed."

    parsed, note = split_meeting_package(build_meeting_package(transcript, None))

    assert parsed == transcript
    assert note is None


def test_self_contained_package_round_trips_metadata_without_a_note() -> None:
    transcript = "Lan: Em sẽ cập nhật dashboard."
    packaged = build_meeting_package(
        transcript,
        None,
        meeting_id="M-001",
        meeting_title="Rà soát dashboard",
        meeting_date="2026-08-17",
    )

    parsed, note, metadata = parse_meeting_package(packaged)

    assert parsed == transcript
    assert note is None
    assert metadata.meeting_id == "M-001"
    assert metadata.meeting_title == "Rà soát dashboard"
    assert metadata.meeting_date == "2026-08-17"
