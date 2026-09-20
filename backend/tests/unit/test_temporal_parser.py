import pytest

from backend.app.dates.temporal import (
    TemporalRelation,
    TemporalType,
    TemporalUnit,
    parse_temporal_expression,
)


@pytest.mark.parametrize(
    ("text", "kind", "target"),
    [
        ("chậm nhất 2026-02-28", TemporalType.ABSOLUTE_DATE, "2026-02-28"),
        ("vào 28/02/2026", TemporalType.ABSOLUTE_DATE, "2026-02-28"),
        ("vào ngày 28 tháng 2", TemporalType.ABSOLUTE_DATE, "--02-28"),
        ("xong ngày mai", TemporalType.RELATIVE_DAY, None),
    ],
)
def test_parser_supports_exact_and_relative_days(text, kind, target) -> None:
    expression = parse_temporal_expression(text, source_id="DATE-1")

    assert expression.expression_id.startswith("TEMP-")
    assert expression.type is kind
    assert expression.target_date == target


def test_parser_marks_duration_and_event_anchor_without_resolving_it() -> None:
    meeting_duration = parse_temporal_expression("cần 2 ngày làm việc")
    event_duration = parse_temporal_expression("3 ngày làm việc sau khi EVT-42")

    assert meeting_duration.type is TemporalType.DURATION_AFTER_MEETING
    assert meeting_duration.duration_value == 2
    assert meeting_duration.duration_unit is TemporalUnit.WORKING_DAY
    assert event_duration.type is TemporalType.DURATION_AFTER_EVENT
    assert event_duration.relation is TemporalRelation.AFTER
    assert event_duration.anchor_event == "evt-42"


def test_parser_keeps_ranges_ambiguous_and_unknown_unsupported_language() -> None:
    assert parse_temporal_expression("từ thứ hai đến thứ tư").type is TemporalType.DATE_RANGE
    assert parse_temporal_expression("sớm thôi khi phù hợp").type is TemporalType.UNKNOWN
