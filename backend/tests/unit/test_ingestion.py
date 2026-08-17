from backend.app.ingestion import parse_transcript
from backend.app.preprocessing.speaker_normalizer import canonical_speaker


def test_parse_vtt_voice_tag() -> None:
    captions = parse_transcript("""WEBVTT

00:00:01.000 --> 00:00:03.000
<v Nam>Tôi sẽ gửi báo cáo.
""", "meeting.vtt")
    assert len(captions) == 1
    assert captions[0].speaker_raw == "Nam"
    assert captions[0].start_ms == 1000


def test_parse_srt_and_plain_text() -> None:
    srt = """1
00:00:01,000 --> 00:00:03,000
Linh: I will review the API.
"""
    assert parse_transcript(srt, "meeting.srt")[0].speaker_raw == "Linh"
    plain = parse_transcript("[00:00:02] Phương: Em sẽ chuẩn bị hồ sơ.", "meeting.txt")
    assert plain[0].start_ms == 2000
    assert plain[0].text_raw == "Em sẽ chuẩn bị hồ sơ."


def test_canonical_speaker_removes_honorific_but_preserves_plain_name() -> None:
    assert canonical_speaker("Anh Minh")[1] == "Minh"
    assert canonical_speaker("Chị Lan")[1] == "Lan"
    assert canonical_speaker("Anh")[1] == "Anh"
