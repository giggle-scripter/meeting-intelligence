"""Extract non-overlapping temporal mentions using ordered regex rules."""

import re

from ..models import Clause, DateMention
from ..dates.context import classify_date_mention_purpose
from ..utils.ids import make_id
from .date_patterns import NUMBER_WORDS, ORDERED_PATTERNS, WEEKDAYS


def _overlaps(span: tuple[int, int], used: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in used)


def _populate_anchor(mention: DateMention) -> None:
    normalized = mention.raw_text.casefold()
    iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", normalized)
    full = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", normalized)
    short = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?![/-]\d)\b", normalized)
    vietnamese = re.search(
        r"\b(\d{1,2})\s+tháng\s+(\d{1,2})(?:\s+năm\s+(\d{4}))?\b",
        normalized,
    )
    if iso:
        mention.explicit_year, mention.explicit_month, mention.explicit_day = map(int, iso.groups())
        mention.date_type = "ABSOLUTE_DATE"
    elif full:
        mention.explicit_day, mention.explicit_month, mention.explicit_year = map(int, full.groups())
        mention.date_type = "ABSOLUTE_DATE"
    elif vietnamese:
        day, month, year = vietnamese.groups()
        mention.explicit_day = int(day)
        mention.explicit_month = int(month)
        mention.explicit_year = int(year) if year else None
        mention.date_type = "ABSOLUTE_DATE" if year else "DAY_MONTH"
    elif short:
        mention.explicit_day, mention.explicit_month = map(int, short.groups())
        mention.date_type = "DAY_MONTH"
    elif "day after tomorrow" in normalized or "ngày kia" in normalized:
        mention.relative_day_offset = 2
        mention.date_type = "RELATIVE_DAY"
    elif "tomorrow" in normalized or "ngày mai" in normalized or re.search(r"\b(?:sáng|trưa|chiều|tối)\s+mai\b", normalized):
        mention.relative_day_offset = 1
        mention.date_type = "RELATIVE_DAY"
    elif "today" in normalized or "hôm nay" in normalized or re.search(r"\b(?:sáng|trưa|chiều|tối)\s+nay\b", normalized):
        mention.relative_day_offset = 0
        mention.date_type = "RELATIVE_DAY"
    else:
        for label, weekday in WEEKDAYS.items():
            if re.search(rf"\b{re.escape(label)}\b", normalized):
                mention.target_weekday = weekday
                mention.week_scope = "NEXT_CALENDAR_WEEK" if any(scope in normalized for scope in ("tuần sau", "tuần tới", "next week", f"next {label}")) else "NEXT_OCCURRENCE"
                mention.date_type = "WEEKDAY_NEXT" if mention.week_scope == "NEXT_CALENDAR_WEEK" else "WEEKDAY"
                break


def extract_date_mentions(clauses: list[Clause]) -> dict[str, DateMention]:
    mentions: dict[str, DateMention] = {}
    sequence = 0
    for clause in clauses:
        used: list[tuple[int, int]] = []
        for relation, date_type, pattern in ORDERED_PATTERNS:
            for match in pattern.finditer(clause.text_normalized):
                if _overlaps(match.span(), used):
                    continue
                sequence += 1
                raw_text = clause.text_raw[match.start():match.end()] if len(clause.text_raw) >= match.end() else match.group(0)
                span_start = match.start()
                labelled_weekday_date = re.match(
                    r"deadline\s+((?:thứ\s+\w+|chủ\s+nhật|"
                    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
                    r"\s+(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{4})?|"
                    r"\d{1,2}\s+tháng\s+\d{1,2}(?:\s+năm\s+\d{4})?))$",
                    raw_text.strip(),
                    re.I,
                )
                if labelled_weekday_date:
                    raw_text = labelled_weekday_date.group(1)
                    span_start = match.end() - len(raw_text)
                current_weekday = re.match(
                    r"deadline\s+là\s+((?:thứ\s+\w+|chủ\s+nhật)\s+tuần\s+này)$",
                    raw_text.strip(),
                    re.I,
                )
                if current_weekday:
                    raw_text = current_weekday.group(1)
                    span_start = match.end() - len(raw_text)
                prefix = clause.text_raw[:match.end()]
                labelled = None
                raw_is_weekday_date = re.match(
                    r"(?:thứ\s+\w+|chủ\s+nhật|monday|tuesday|wednesday|"
                    r"thursday|friday|saturday|sunday)\s+\d",
                    raw_text.strip(),
                    re.I,
                )
                if (
                    not labelled_weekday_date
                    and not current_weekday
                    and not raw_is_weekday_date
                ):
                    labelled = re.search(
                        r"\bdeadline(?:\s+(?:cho|for)\s+[^,;.]{2,100}?\s+"
                        r"(?:là|is))?\s+" + re.escape(raw_text.strip()) + r"$",
                        prefix,
                        re.I,
                    )
                if labelled:
                    raw_text = labelled.group(0)
                    span_start = labelled.start()
                mention = DateMention(
                    make_id("DATE", sequence),
                    clause.clause_id,
                    raw_text.strip(),
                    relation,
                    date_type,
                    span_start=span_start,
                    span_end=match.end(),
                    purpose=classify_date_mention_purpose(
                        clause.text_raw,
                        span_start,
                        match.end(),
                    ),
                )
                if date_type in {"CALENDAR_DURATION", "WORKING_DAY_DURATION"}:
                    number = match.group(1).casefold()
                    mention.duration_days = int(number) if number.isdigit() else NUMBER_WORDS.get(number)
                elif date_type in {"EVENT_DEPENDENT", "AMBIGUOUS"}:
                    pass
                else:
                    _populate_anchor(mention)
                mentions[mention.date_mention_id] = mention
                used.append(match.span())
    return mentions
