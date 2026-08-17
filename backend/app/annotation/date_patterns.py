"""Ordered date patterns, from most specific to most general."""

import re

WEEKDAY = r"(?:thứ hai|thứ ba|thứ tư|thứ năm|thứ sáu|thứ bảy|chủ nhật|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
VIETNAMESE_DATE = r"(?:\d{1,2}\s+tháng\s+\d{1,2}(?:\s+năm\s+\d{4})?)"
DATE = rf"(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{4}})?|{VIETNAMESE_DATE})"
RELATIVE = r"(?:hôm nay|ngày mai|ngày kia|today|tomorrow|day after tomorrow)"
ANCHOR = rf"(?:ngày\s+)?(?:{DATE}|{RELATIVE}|(?:next\s+{WEEKDAY})|{WEEKDAY}(?:\s+(?:tuần này|tuần sau|tuần tới|this week|next week))?)"

ORDERED_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("AMBIGUOUS", "AMBIGUOUS", re.compile(rf"\b{WEEKDAY}\s+(?:hoặc|hay|or)\s+{WEEKDAY}\b", re.I)),
    ("AMBIGUOUS", "AMBIGUOUS", re.compile(r"\b(?:trong|during)\s+(?:tuần này|tuần sau|tuần tới|this week|next week)\b", re.I)),
    ("ON_DATE", "ANCHOR", re.compile(rf"\b{WEEKDAY}\s+{DATE}\b", re.I)),
    ("ON_DATE", "ANCHOR", re.compile(rf"\bdeadline\s+(?:là\s+)?{ANCHOR}", re.I)),
    ("ON_DATE", "RELATIVE_DAY", re.compile(r"\b(?:trong\s+)?(?:sáng|trưa|chiều|tối)\s+(?:nay|mai)\b", re.I)),
    ("ON_DATE", "RELATIVE_DAY", re.compile(r"\bcuối\s+ngày\s+(?:hôm\s+nay|mai|today|tomorrow)\b", re.I)),
    ("BEFORE_TIME", "ANCHOR", re.compile(rf"\b(?:trước|before)\s+(?:sáng|trưa|chiều|tối)\s+{ANCHOR}", re.I)),
    ("BEFORE_TIME", "ANCHOR", re.compile(rf"\b(?:trước|before)\s+(?:\d{{1,2}}(?::\d{{2}})?\s*(?:h|giờ|am|pm)|cuối ngày|end of day|eod)\s+{ANCHOR}", re.I)),
    ("BEFORE_DAY", "ANCHOR", re.compile(rf"\b(?:trước|before|prior to|không đến)\s+{ANCHOR}", re.I)),
    ("ON_OR_BEFORE", "ANCHOR", re.compile(rf"\b(?:chậm nhất|không muộn hơn|no later than|by)\s+{ANCHOR}", re.I)),
    ("ON_DATE", "ANCHOR", re.compile(rf"\b(?:từ|from)\s+{ANCHOR}", re.I)),
    ("ON_DATE", "ANCHOR", re.compile(rf"\b(?:vào|đến|on|due on)\s+{ANCHOR}", re.I)),
    ("ON_DATE", "RELATIVE_DAY", re.compile(rf"\b(?:trong\s+)?{RELATIVE}\b", re.I)),
    ("DURATION", "WORKING_DAY_DURATION", re.compile(r"\b(?:trong vòng|cần|within|need)\s+(\d+|một|hai|ba|bốn|năm|one|two|three|four|five)\s+(?:ngày làm việc|working days?)\b", re.I)),
    ("DURATION", "CALENDAR_DURATION", re.compile(r"\b(?:trong vòng|cần|within|need)\s+(\d+|một|hai|ba|bốn|năm|one|two|three|four|five)\s+(?:ngày(?: lịch)?|calendar days?)\b", re.I)),
    ("AFTER_EVENT", "EVENT_DEPENDENT", re.compile(r"\b(?:\d+|một|hai|ba|bốn|năm|one|two|three|four|five)\s+(?:ngày làm việc|working days?)\s+(?:sau khi|after)\s+[^,.;]+", re.I)),
    ("AFTER_EVENT", "EVENT_DEPENDENT", re.compile(r"\b(?:sau khi|after)\s+[^,.;]+(?:trong vòng\s+\w+\s+ngày|within\s+\w+\s+days?)?", re.I)),
    ("ON_DATE", "ANCHOR", re.compile(rf"\b(?:ngày\s+)?{DATE}\b", re.I)),
]

NUMBER_WORDS = {"một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
WEEKDAYS = {"thứ hai": "MONDAY", "thứ ba": "TUESDAY", "thứ tư": "WEDNESDAY", "thứ năm": "THURSDAY", "thứ sáu": "FRIDAY", "thứ bảy": "SATURDAY", "chủ nhật": "SUNDAY", "monday": "MONDAY", "tuesday": "TUESDAY", "wednesday": "WEDNESDAY", "thursday": "THURSDAY", "friday": "FRIDAY", "saturday": "SATURDAY", "sunday": "SUNDAY"}
