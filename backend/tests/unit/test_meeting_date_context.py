from backend.app.dates import infer_meeting_context_date


def test_infers_meeting_date_from_transcript_context() -> None:
    assert infer_meeting_context_date(
        "Thư ký: Hôm nay là ngày 17/01/2026. Chúng ta bắt đầu họp."
    ) == ("2026-01-17", "TRANSCRIPT_CONTEXT")


def test_uses_meeting_note_context_when_transcript_has_no_context_date() -> None:
    assert infer_meeting_context_date(
        "Lan: Em sẽ triển khai API.",
        "Ngày họp: 18 tháng 1 năm 2026\n- Lan triển khai API.",
    ) == ("2026-01-18", "MEETING_NOTE_CONTEXT")


def test_transcript_context_has_priority_over_meeting_note_context() -> None:
    assert infer_meeting_context_date(
        "Meeting date: 2026-01-19.",
        "Ngày họp: 20/01/2026",
    ) == ("2026-01-19", "TRANSCRIPT_CONTEXT")


def test_conflicting_context_dates_are_not_guessed() -> None:
    assert infer_meeting_context_date(
        "Ngày họp: 17/01/2026. Biên bản lại ghi ngày họp: 18/01/2026."
    ) == ("", "")
