from backend.app.dates.temporal import (
    TemporalResolutionStatus,
    parse_temporal_expression,
    resolve_temporal_expression,
)


def test_resolver_handles_weekday_rollover_and_strict_before() -> None:
    friday = resolve_temporal_expression(
        parse_temporal_expression("thứ sáu"), meeting_date="2026-07-20"
    )
    before_monday = resolve_temporal_expression(
        parse_temporal_expression("trước thứ hai"), meeting_date="2026-07-20"
    )

    assert friday.resolved_date == "2026-07-24"
    assert before_monday.resolved_date == "2026-07-13"


def test_resolver_uses_weekdays_only_and_leap_year_calendar_math() -> None:
    business = resolve_temporal_expression(
        parse_temporal_expression("cần 1 ngày làm việc"), meeting_date="2024-03-01"
    )
    relative = resolve_temporal_expression(
        parse_temporal_expression("ngày mai"), meeting_date="2024-02-28"
    )

    assert business.resolved_date == "2024-03-04"
    assert relative.resolved_date == "2024-02-29"


def test_resolver_requires_exact_event_anchor_and_never_picks_range_end() -> None:
    missing = resolve_temporal_expression(
        parse_temporal_expression("2 ngày sau khi EVT-9"), meeting_date="2026-01-01"
    )
    anchored = resolve_temporal_expression(
        parse_temporal_expression("2 ngày sau khi EVT-9"),
        meeting_date="2026-01-01", event_anchors={"EVT-9": "2026-01-30"},
    )
    date_range = resolve_temporal_expression(
        parse_temporal_expression("từ thứ hai đến thứ tư"), meeting_date="2026-01-01"
    )

    assert missing.status is TemporalResolutionStatus.UNRESOLVED_ANCHOR
    assert anchored.resolved_date == "2026-02-01"
    assert date_range.status is TemporalResolutionStatus.AMBIGUOUS_RANGE
