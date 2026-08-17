"""Calendar arithmetic without external holiday assumptions."""

from datetime import date, timedelta

WEEKDAY_MAP = {"MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2, "THURSDAY": 3, "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6}


def resolve_weekday_anchor(meeting_date: date, target_weekday: str, week_scope: str, relation: str) -> date:
    target = WEEKDAY_MAP[target_weekday]
    current = meeting_date.weekday()
    if week_scope == "NEXT_CALENDAR_WEEK":
        monday = meeting_date - timedelta(days=current)
        return monday + timedelta(days=7 + target)
    delta = (target - current) % 7
    if relation == "BEFORE_DAY" and delta == 0:
        delta = 7
    return meeting_date + timedelta(days=delta)
