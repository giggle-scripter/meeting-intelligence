from backend.app.ingestion import parse_transcript


def test_generated_header_is_not_parsed_as_dialogue() -> None:
    transcript = """MEETING_ID: CASE-001
CASE_LABELS: assignment,deadline
MEETING_DATE: 2026-02-20
TITLE: Release review

[09:00:00] An: Chào mọi người.
"""

    captions = parse_transcript(transcript, "transcript.txt")

    assert len(captions) == 1
    assert captions[0].speaker_raw == "An"
    assert captions[0].start_ms == 9 * 60 * 60 * 1000


def test_generated_long_turn_prefix_and_part_boundary() -> None:
    transcript = """MEETING_ID: CASE-002
PART: P01/5

T000001 [09:00:00] Bình: Em sẽ chuẩn bị môi trường test.
T000002 [09:00:12] Cường: Tôi sẽ review.
--- END OF PART P01 ---
"""

    captions = parse_transcript(transcript, "part_01.txt")

    assert [item.speaker_raw for item in captions] == ["Bình", "Cường"]
    assert [item.text_raw for item in captions] == [
        "Em sẽ chuẩn bị môi trường test.",
        "Tôi sẽ review.",
    ]
