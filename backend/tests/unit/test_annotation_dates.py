import pytest

from backend.app.annotation import annotate_clauses, extract_date_mentions
from backend.app.dates import resolve_date_mention
from backend.app.models import Clause


def clause(text: str) -> Clause:
    return Clause("CLAUSE-1", "SENT-1", "SPK-1", "Nam", 0, 1, text, text.casefold())


@pytest.mark.parametrize(
    ("text", "meeting", "start", "expected"),
    [
        ("Em sẽ gửi trước thứ Sáu", "2026-07-20", "2026-07-20", "2026-07-23"),
        ("Em sẽ gửi trước 18h ngày 24/07/2026", "2026-07-20", "2026-07-20", "2026-07-24"),
        ("I will send it by next Tuesday", "2026-07-20", "2026-07-20", "2026-07-28"),
        ("Em sẽ gửi trước ngày 05/01", "2026-07-20", "2026-07-20", "2027-01-04"),
        ("Em cần 4 ngày lịch", "2026-07-20", "2026-07-21", "2026-07-25"),
        ("Em cần ba ngày làm việc", "2026-07-20", "2026-07-21", ""),
        ("Em sẽ test xong trong ngày mai", "2026-07-20", "2026-07-20", "2026-07-21"),
    ],
)
def test_date_parser_and_resolver(text: str, meeting: str, start: str, expected: str) -> None:
    mentions = extract_date_mentions([clause(text)])
    assert len(mentions) == 1
    assert resolve_date_mention(meeting, start, next(iter(mentions.values()))) == expected


def test_annotation_distinguishes_commitment_from_brainstorm() -> None:
    clauses = [clause("Em sẽ cập nhật access matrix trước thứ Sáu")]
    mentions = extract_date_mentions(clauses)
    annotation = annotate_clauses(clauses, {item.clause_id for item in mentions.values()})["CLAUSE-1"]
    assert {"FIRST_PERSON_COMMITMENT", "ACTION_VERB", "DATE_MENTION"} <= annotation.flags


def test_date_parser_preserves_labelled_deadline_phrase() -> None:
    mentions = extract_date_mentions([
        clause("Deadline cho release plan chi tiết là 21/03, trước release một tuần.")
    ])

    assert next(iter(mentions.values())).raw_text == (
        "Deadline cho release plan chi tiết là 21/03"
    )


def test_date_parser_preserves_simple_deadline_label() -> None:
    mentions = extract_date_mentions([clause("Tích hợp payment, deadline 20/02.")])
    assert next(iter(mentions.values())).raw_text == "deadline 20/02"


def test_date_parser_keeps_timed_relative_boundary_as_one_mention() -> None:
    mention = next(iter(extract_date_mentions([
        clause("Em sẽ commit fix trước 16h hôm nay.")
    ]).values()))

    assert mention.raw_text == "trước 16h hôm nay"
    assert mention.relation == "BEFORE_TIME"


def test_date_parser_distinguishes_task_start_from_deadline() -> None:
    mentions = sorted(extract_date_mentions([clause(
        "Em sẽ triển khai API bắt đầu từ ngày 20/02/2026 và hoàn thành "
        "trước ngày 25/02/2026."
    )]).values(), key=lambda item: item.span_start)

    assert [(item.purpose, item.raw_text) for item in mentions] == [
        ("START_DATE", "từ ngày 20/02/2026"),
        ("DEADLINE", "trước ngày 25/02/2026"),
    ]


def test_meeting_context_date_is_not_a_deadline() -> None:
    mention = next(iter(extract_date_mentions([
        clause("Hôm nay là ngày 17/01/2026, chúng ta bắt đầu cuộc họp.")
    ]).values()))

    assert mention.purpose == "MEETING_DATE"


def test_past_start_word_does_not_reclassify_later_deadline() -> None:
    mention = next(iter(extract_date_mentions([clause(
        "Em mới bắt đầu thu thập số liệu và sẽ gửi báo cáo trước ngày 25/02/2026."
    )]).values()))

    assert mention.purpose == "DEADLINE"
