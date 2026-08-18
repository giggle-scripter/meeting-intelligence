from backend.app.models import MeetingInput, MeetingNoteInput
from backend.app.v2.context import parse_note_claims, parse_meeting_note


def test_claim_ids_are_stable_for_bullets_unicode_and_duplicate_rows() -> None:
    meeting = MeetingInput(
        "M-NOTE-CLAIM", "Release", "2026-08-18", "Lan: cập nhật dashboard.",
        meeting_note=MeetingNoteInput("- Lan cập nhật dashboard\n- Lan cập nhật dashboard"),
    )

    first = parse_note_claims(meeting, parse_meeting_note(meeting.meeting_note))
    second = parse_note_claims(meeting, parse_meeting_note(meeting.meeting_note))

    assert [item.note_claim_id for item in first] == [item.note_claim_id for item in second]
    assert len({item.note_claim_id for item in first}) == 2
    assert all(item.source == "SECRETARY" for item in first)


def test_headers_and_questions_do_not_become_claims() -> None:
    meeting = MeetingInput(
        "M-NOTE-NOISE", "Release", "2026-08-18", "", 
        meeting_note=MeetingNoteInput("Chủ đề: Release\n- Có cần chốt owner không?\n- Lan cập nhật dashboard"),
    )

    claims = parse_note_claims(meeting, parse_meeting_note(meeting.meeting_note))

    assert len(claims) == 1
    assert claims[0].text == "Lan cập nhật dashboard"
