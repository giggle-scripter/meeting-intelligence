"""Resolve structured DateMention objects deterministically."""

from datetime import date, timedelta
import re

from ..models import DateMention
from .calendar_utils import resolve_weekday_anchor
from .validators import parse_iso_date


def _anchor(meeting: date, mention: DateMention) -> date | None:
    if mention.date_type == "RELATIVE_DAY":
        return meeting + timedelta(days=mention.relative_day_offset or 0)
    if mention.date_type in {"WEEKDAY", "WEEKDAY_NEXT"} and mention.target_weekday:
        target = {
            "MONDAY": 0,
            "TUESDAY": 1,
            "WEDNESDAY": 2,
            "THURSDAY": 3,
            "FRIDAY": 4,
            "SATURDAY": 5,
            "SUNDAY": 6,
        }[mention.target_weekday]
        if (
            target == meeting.weekday()
            and mention.week_scope == "NEXT_OCCURRENCE"
            and re.search(r"\b(?:hạn(?:\s+chót)?|deadline)\b", mention.raw_text, re.I)
        ):
            return meeting + timedelta(days=7)
        return resolve_weekday_anchor(meeting, mention.target_weekday, mention.week_scope, mention.relation)
    if mention.explicit_day and mention.explicit_month:
        year = mention.explicit_year or meeting.year
        try:
            candidate = date(year, mention.explicit_month, mention.explicit_day)
        except ValueError:
            return None
        if mention.explicit_year is None and candidate < meeting:
            try:
                candidate = date(year + 1, mention.explicit_month, mention.explicit_day)
            except ValueError:
                return None
        return candidate
    return None


def resolve_date_mention(meeting_date: str, start_date: str, mention: DateMention | None) -> str:
    if mention is None:
        return ""
    meeting = parse_iso_date(meeting_date)
    start = parse_iso_date(start_date or meeting_date)
    if mention.date_type in {"EVENT_DEPENDENT", "WORKING_DAY_DURATION", "AMBIGUOUS", "NONE"}:
        return ""
    if mention.date_type == "CALENDAR_DURATION" and mention.duration_days is not None:
        return (start + timedelta(days=mention.duration_days)).isoformat()
    anchor = _anchor(meeting, mention)
    if anchor is None:
        return ""
    if mention.relation == "BEFORE_DAY":
        anchor -= timedelta(days=1)
    return anchor.isoformat()
