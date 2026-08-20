"""Stable contracts for deterministic temporal parsing and resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TemporalType(str, Enum):
    ABSOLUTE_DATE = "ABSOLUTE_DATE"
    RELATIVE_DAY = "RELATIVE_DAY"
    RELATIVE_WEEKDAY = "RELATIVE_WEEKDAY"
    DURATION_AFTER_MEETING = "DURATION_AFTER_MEETING"
    DURATION_AFTER_EVENT = "DURATION_AFTER_EVENT"
    DATE_RANGE = "DATE_RANGE"
    UNKNOWN = "UNKNOWN"


class TemporalRelation(str, Enum):
    ON = "ON"
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    WITHIN = "WITHIN"
    FROM = "FROM"


class TemporalUnit(str, Enum):
    CALENDAR_DAY = "CALENDAR_DAY"
    WORKING_DAY = "WORKING_DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"


class TemporalResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNKNOWN_EXPRESSION = "UNKNOWN_EXPRESSION"
    UNRESOLVED_ANCHOR = "UNRESOLVED_ANCHOR"
    AMBIGUOUS_RANGE = "AMBIGUOUS_RANGE"
    INVALID_DATE = "INVALID_DATE"
    UNSUPPORTED_POLICY = "UNSUPPORTED_POLICY"


@dataclass(frozen=True)
class TemporalExpression:
    """Parser output only; it intentionally contains no AI-derived date."""

    expression_id: str
    source_text: str
    type: TemporalType
    relation: TemporalRelation = TemporalRelation.ON
    target_date: str | None = None
    weekday: int | None = None
    duration_value: int | None = None
    duration_unit: TemporalUnit | None = None
    anchor_event: str | None = None
    inclusive: bool | None = None
    parser_version: str = "temporal-parser-v1"


@dataclass(frozen=True)
class TemporalResolution:
    expression_id: str
    status: TemporalResolutionStatus
    resolved_date: str = ""
    reason: str = ""
