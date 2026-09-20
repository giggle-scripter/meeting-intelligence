"""Deterministic temporal arithmetic with explicit trusted anchors only."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

from ..validators import parse_iso_date
from .anchors import MEETING_DATE_ANCHOR, resolve_trusted_anchor
from .contracts import (
    TemporalExpression, TemporalRelation, TemporalResolution,
    TemporalResolutionStatus, TemporalType, TemporalUnit,
)


WORKING_DAY_POLICY = "weekdays-only-v1"


def _result(expression: TemporalExpression, status: TemporalResolutionStatus, value: date | None = None, reason: str = "") -> TemporalResolution:
    return TemporalResolution(expression.expression_id, status, value.isoformat() if value else "", reason)


def _add_working_days(anchor: date, amount: int) -> date:
    direction = 1 if amount >= 0 else -1
    remaining = abs(amount)
    candidate = anchor
    while remaining:
        candidate += timedelta(days=direction)
        if candidate.weekday() < 5:
            remaining -= 1
    return candidate


def _resolve_anchor(expression: TemporalExpression, meeting: date, event_anchors: Mapping[str, str] | None) -> tuple[date | None, str]:
    if expression.anchor_event == MEETING_DATE_ANCHOR:
        return meeting, ""
    if expression.anchor_event == "MEETING_DATE_NEXT_WEEK":
        return meeting + timedelta(days=7), ""
    anchor_value = resolve_trusted_anchor(expression.anchor_event, event_anchors)
    if not anchor_value:
        return None, "missing exact trusted anchor"
    try:
        return parse_iso_date(anchor_value), ""
    except ValueError:
        return None, "invalid trusted anchor date"


def resolve_temporal_expression(
    expression: TemporalExpression,
    *,
    meeting_date: str,
    event_anchors: Mapping[str, str] | None = None,
    working_day_policy: str = WORKING_DAY_POLICY,
) -> TemporalResolution:
    if working_day_policy != WORKING_DAY_POLICY:
        return _result(expression, TemporalResolutionStatus.UNSUPPORTED_POLICY, reason="unsupported working-day policy")
    try:
        meeting = parse_iso_date(meeting_date)
    except ValueError:
        return _result(expression, TemporalResolutionStatus.UNRESOLVED_ANCHOR, reason="invalid meeting date")
    if expression.type is TemporalType.UNKNOWN:
        return _result(expression, TemporalResolutionStatus.UNKNOWN_EXPRESSION)
    if expression.type is TemporalType.DATE_RANGE:
        return _result(expression, TemporalResolutionStatus.AMBIGUOUS_RANGE, reason="ranges have no automatic deadline endpoint")
    if expression.type is TemporalType.ABSOLUTE_DATE:
        if not expression.target_date:
            return _result(expression, TemporalResolutionStatus.INVALID_DATE)
        try:
            if expression.target_date.startswith("--"):
                month, day = map(int, expression.target_date[2:].split("-"))
                resolved = date(meeting.year, month, day)
                if resolved < meeting:
                    resolved = date(meeting.year + 1, month, day)
            else:
                resolved = parse_iso_date(expression.target_date)
        except ValueError:
            return _result(expression, TemporalResolutionStatus.INVALID_DATE)
        if expression.relation is TemporalRelation.BEFORE:
            resolved -= timedelta(days=1)
        return _result(expression, TemporalResolutionStatus.RESOLVED, resolved)
    anchor, anchor_error = _resolve_anchor(expression, meeting, event_anchors)
    if anchor is None:
        return _result(expression, TemporalResolutionStatus.UNRESOLVED_ANCHOR, reason=anchor_error)
    if expression.type is TemporalType.RELATIVE_WEEKDAY:
        if expression.weekday is None:
            return _result(expression, TemporalResolutionStatus.UNKNOWN_EXPRESSION)
        if expression.relation is TemporalRelation.BEFORE:
            delta = (anchor.weekday() - expression.weekday) % 7 or 7
            return _result(expression, TemporalResolutionStatus.RESOLVED, anchor - timedelta(days=delta))
        delta = (expression.weekday - anchor.weekday()) % 7
        return _result(expression, TemporalResolutionStatus.RESOLVED, anchor + timedelta(days=delta))
    if expression.type in {TemporalType.RELATIVE_DAY, TemporalType.DURATION_AFTER_MEETING, TemporalType.DURATION_AFTER_EVENT}:
        if expression.duration_value is None or expression.duration_unit is None:
            return _result(expression, TemporalResolutionStatus.UNKNOWN_EXPRESSION)
        if expression.duration_unit is TemporalUnit.WORKING_DAY:
            resolved = _add_working_days(anchor, expression.duration_value)
        elif expression.duration_unit is TemporalUnit.CALENDAR_DAY:
            resolved = anchor + timedelta(days=expression.duration_value)
        else:
            return _result(expression, TemporalResolutionStatus.UNKNOWN_EXPRESSION)
        return _result(expression, TemporalResolutionStatus.RESOLVED, resolved)
    return _result(expression, TemporalResolutionStatus.UNKNOWN_EXPRESSION)
