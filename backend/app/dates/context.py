"""Infer meeting-date context and classify temporal mention purpose."""

from __future__ import annotations

from datetime import date
import re


_ISO_DATE = r"\d{4}-\d{1,2}-\d{1,2}"
_DMY_DATE = r"\d{1,2}[/-]\d{1,2}[/-]\d{4}"
_VI_DATE = r"\d{1,2}\s+tháng\s+\d{1,2}\s+năm\s+\d{4}"
_FULL_DATE = rf"(?:{_ISO_DATE}|{_DMY_DATE}|{_VI_DATE})"

_MEETING_CONTEXT_PATTERNS = (
    re.compile(
        rf"\b(?:hôm\s+nay|today)\s+(?:là|is)\s+(?:ngày\s+)?"
        rf"(?P<date>{_FULL_DATE})\b",
        re.I,
    ),
    re.compile(
        rf"\b(?:ngày\s+họp|meeting\s+date)\s*(?:là|is|:|-)?\s*"
        rf"(?:ngày\s+)?(?P<date>{_FULL_DATE})\b",
        re.I,
    ),
    re.compile(
        rf"\b(?:cuộc\s+họp|meeting)\s+(?:hôm\s+nay\s+)?"
        rf"(?:diễn\s+ra\s+)?(?:vào|on)?\s*(?:ngày\s+)?"
        rf"(?P<date>{_FULL_DATE})\b",
        re.I,
    ),
)

_START_CUE_RE = re.compile(
    r"\b(?:bắt\s+đầu|khởi\s+động|start(?:s|ed|ing)?|commence(?:s|d)?)\b",
    re.I,
)
_START_MENTION_RE = re.compile(r"^\s*(?:từ|from)\b", re.I)
_START_RELATION_RE = re.compile(r"\b(?:từ|from)\s*$", re.I)
_DEADLINE_CUE_RE = re.compile(
    r"\b(?:trước|before|deadline|hạn(?:\s+chót)?|chậm\s+nhất|by|đến|due)\b",
    re.I,
)


def _parse_full_date(value: str) -> str:
    normalized = value.strip().casefold()
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
    dmy = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", normalized)
    vietnamese = re.fullmatch(
        r"(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})",
        normalized,
    )
    try:
        if iso:
            year, month, day = map(int, iso.groups())
        elif dmy or vietnamese:
            day, month, year = map(int, (dmy or vietnamese).groups())
        else:
            return ""
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _context_date_matches(text: str) -> list[tuple[tuple[int, int], str]]:
    matches: list[tuple[tuple[int, int], str]] = []
    for pattern in _MEETING_CONTEXT_PATTERNS:
        for match in pattern.finditer(text):
            resolved = _parse_full_date(match.group("date"))
            if resolved:
                matches.append((match.span("date"), resolved))
    return matches


def infer_meeting_context_date(
    transcript: str,
    meeting_note: str = "",
) -> tuple[str, str]:
    """Return one unambiguous contextual date and its provenance.

    Transcript context has priority over Meeting Note context. Conflicting
    dates within one source are rejected instead of being guessed.
    """

    for text, source in (
        (transcript, "TRANSCRIPT_CONTEXT"),
        (meeting_note, "MEETING_NOTE_CONTEXT"),
    ):
        resolved = {value for _, value in _context_date_matches(text)}
        if len(resolved) == 1:
            return next(iter(resolved)), source
        if len(resolved) > 1:
            return "", ""
    return "", ""


def classify_date_mention_purpose(
    text: str,
    span_start: int,
    span_end: int,
) -> str:
    """Classify a parsed mention as deadline, task start, or meeting context."""

    for context_span, _ in _context_date_matches(text):
        if span_start < context_span[1] and span_end > context_span[0]:
            return "MEETING_DATE"
    # A relative phrase can be part of the same declaration as the explicit
    # date ("Hôm nay là ngày 17/01/2026"). Mark every temporal mention inside
    # that declaration as meeting context, not only the captured full date.
    for pattern in _MEETING_CONTEXT_PATTERNS:
        for match in pattern.finditer(text):
            if span_start < match.end() and span_end > match.start():
                return "MEETING_DATE"

    mention_text = text[span_start:span_end]
    if _DEADLINE_CUE_RE.search(mention_text):
        return "DEADLINE"
    if _START_MENTION_RE.search(mention_text):
        return "START_DATE"

    prefix = text[max(0, span_start - 120):span_start]
    cue_matches = list(_START_CUE_RE.finditer(prefix))
    if cue_matches:
        after_cue = prefix[cue_matches[-1].end():]
        if not _DEADLINE_CUE_RE.search(after_cue):
            return "START_DATE"
    if _START_RELATION_RE.search(prefix):
        return "START_DATE"
    return "DEADLINE"
