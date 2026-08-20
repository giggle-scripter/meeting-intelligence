"""Small, deterministic grammar for supported Vietnamese and English dates."""

from __future__ import annotations

from hashlib import sha256
import re

from .anchors import MEETING_DATE_ANCHOR
from .contracts import (
    TemporalExpression,
    TemporalRelation,
    TemporalType,
    TemporalUnit,
)


WEEKDAYS = {
    "thứ hai": 0, "thứ ba": 1, "thứ tư": 2, "thứ năm": 3,
    "thứ sáu": 4, "thứ bảy": 5, "chủ nhật": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
NUMBER_WORDS = {
    "một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
}
_WEEKDAY_RE = "|".join(sorted((re.escape(value) for value in WEEKDAYS), key=len, reverse=True))
_NUMBER_RE = r"\d+|một|hai|ba|bốn|năm|one|two|three|four|five"


def _expression_id(source_text: str, source_id: str | None) -> str:
    stable_source = source_id or source_text.casefold().strip()
    return "TEMP-" + sha256(stable_source.encode("utf-8")).hexdigest()[:16]


def _relation(text: str) -> TemporalRelation:
    if re.search(r"\b(?:trước|before|prior to)\b", text, re.I):
        return TemporalRelation.BEFORE
    if re.search(r"\b(?:sau khi|after)\b", text, re.I):
        return TemporalRelation.AFTER
    if re.search(r"\b(?:trong vòng|within|cần)\b", text, re.I):
        return TemporalRelation.WITHIN
    if re.search(r"\b(?:từ|from)\b", text, re.I):
        return TemporalRelation.FROM
    return TemporalRelation.ON


def _number(value: str) -> int:
    return int(value) if value.isdigit() else NUMBER_WORDS[value.casefold()]


def _unknown(source_text: str, source_id: str | None, parser_version: str) -> TemporalExpression:
    return TemporalExpression(
        expression_id=_expression_id(source_text, source_id), source_text=source_text,
        type=TemporalType.UNKNOWN, parser_version=parser_version,
    )


def parse_temporal_expression(
    source_text: str,
    *,
    source_id: str | None = None,
    parser_version: str = "temporal-parser-v1",
) -> TemporalExpression:
    """Parse only the documented grammar, returning UNKNOWN for everything else."""

    text = source_text.strip()
    normalized = text.casefold()
    expression_id = _expression_id(text, source_id)
    common = {"expression_id": expression_id, "source_text": text, "parser_version": parser_version}
    if not text:
        return _unknown(text, source_id, parser_version)

    range_match = re.search(rf"\b(?:từ|from)\s+({_WEEKDAY_RE})\s+(?:đến|to|through|until)\s+({_WEEKDAY_RE})\b", normalized, re.I)
    if range_match:
        return TemporalExpression(type=TemporalType.DATE_RANGE, relation=TemporalRelation.FROM,
                                  weekday=WEEKDAYS[range_match.group(1)], inclusive=True, **common)

    iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", normalized)
    full = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", normalized)
    vietnamese = re.search(r"\b(\d{1,2})\s+tháng\s+(\d{1,2})(?:\s+năm\s+(\d{4}))?\b", normalized)
    short = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?![/-]\d)\b", normalized)
    relation = _relation(normalized)
    if iso:
        year, month, day = map(int, iso.groups())
        return TemporalExpression(type=TemporalType.ABSOLUTE_DATE, relation=relation,
                                  target_date=f"{year:04d}-{month:02d}-{day:02d}", **common)
    if full:
        day, month, year = map(int, full.groups())
        return TemporalExpression(type=TemporalType.ABSOLUTE_DATE, relation=relation,
                                  target_date=f"{year:04d}-{month:02d}-{day:02d}", **common)
    if vietnamese:
        day, month, year_text = vietnamese.groups()
        target = f"{int(year_text):04d}-{int(month):02d}-{int(day):02d}" if year_text else f"--{int(month):02d}-{int(day):02d}"
        return TemporalExpression(type=TemporalType.ABSOLUTE_DATE, relation=relation,
                                  target_date=target, **common)
    if short:
        day, month = map(int, short.groups())
        return TemporalExpression(type=TemporalType.ABSOLUTE_DATE, relation=relation,
                                  target_date=f"--{month:02d}-{day:02d}", **common)

    relative_days = ((r"\b(?:ngày kia|day after tomorrow)\b", 2),
                     (r"\b(?:ngày mai|tomorrow)\b", 1),
                     (r"\b(?:hôm nay|today)\b", 0))
    for pattern, offset in relative_days:
        if re.search(pattern, normalized, re.I):
            return TemporalExpression(type=TemporalType.RELATIVE_DAY, relation=relation,
                                      duration_value=offset, duration_unit=TemporalUnit.CALENDAR_DAY,
                                      anchor_event=MEETING_DATE_ANCHOR, **common)

    duration = re.search(
        rf"\b({_NUMBER_RE})\s+(ngày làm việc|working days?|ngày(?: lịch)?|calendar days?)\s+(?:sau khi|after)\s+([^,.;]+)",
        normalized, re.I,
    )
    if duration:
        value, unit_text, anchor = duration.groups()
        unit = TemporalUnit.WORKING_DAY if "làm việc" in unit_text or "working" in unit_text else TemporalUnit.CALENDAR_DAY
        return TemporalExpression(type=TemporalType.DURATION_AFTER_EVENT, relation=TemporalRelation.AFTER,
                                  duration_value=_number(value), duration_unit=unit,
                                  anchor_event=anchor.strip(), **common)

    duration = re.search(rf"\b(?:trong vòng|cần|within|need)\s+({_NUMBER_RE})\s+(ngày làm việc|working days?|ngày(?: lịch)?|calendar days?)\b", normalized, re.I)
    if duration:
        value, unit_text = duration.groups()
        unit = TemporalUnit.WORKING_DAY if "làm việc" in unit_text or "working" in unit_text else TemporalUnit.CALENDAR_DAY
        return TemporalExpression(type=TemporalType.DURATION_AFTER_MEETING, relation=TemporalRelation.WITHIN,
                                  duration_value=_number(value), duration_unit=unit,
                                  anchor_event=MEETING_DATE_ANCHOR, **common)

    weekday = re.search(rf"\b({_WEEKDAY_RE})\b", normalized, re.I)
    if weekday:
        value = weekday.group(1)
        if "tuần sau" in normalized or "tuần tới" in normalized or "next week" in normalized:
            anchor = "MEETING_DATE_NEXT_WEEK"
        else:
            anchor = MEETING_DATE_ANCHOR
        return TemporalExpression(type=TemporalType.RELATIVE_WEEKDAY, relation=relation,
                                  weekday=WEEKDAYS[value], anchor_event=anchor,
                                  inclusive=relation is not TemporalRelation.BEFORE, **common)
    return _unknown(text, source_id, parser_version)
