"""Parse compact human notes into hints; this module never creates events."""

from __future__ import annotations

import re

from ...models import MeetingNoteInput
from ...preprocessing.unicode_normalizer import normalize_for_match
from .keywords import extract_keywords
from ..models import NoteLineKind, ParsedNoteLine


_ROW_PREFIX = re.compile(r"^\s*(?:[-*•]+|\[[ xX]\]|\d+[.)]|(?:todo|action|ai|follow[- ]?up)\s*[:：])\s*", re.I)
_QUESTION = re.compile(r"\?|\b(?:chua chot|pending|hoi lai|question)\b", re.I)
_CANCEL = re.compile(r"\b(?:bo|huy|drop|cancel)\b", re.I)
_STATE = re.compile(r"\b(?:chuyen|handoff|nhan thay|doi|deadline|dl|ddl|due|eta|han)\b", re.I)
_LABEL = re.compile(r"\btask\s*[- ]?([a-z0-9]+)\b", re.I)
_DEADLINE = re.compile(r"\b(?:dl|ddl|due|deadline|eta|han)\s*[:=-]?\s*([^,;]+)", re.I)
_OWNER = re.compile(r"(?:@([\wÀ-ỹ'-]+)|->\s*([\wÀ-ỹ'-]+)|\(([\wÀ-ỹ'-]+)\)|^\s*([\wÀ-ỹ'-]+)\s*:)", re.I)
_TOPIC = re.compile(r"^(?:chủ\s*đề|chu\s*de|topic|agenda|scope)\s*[:：=-]\s*(.+)$", re.I)
_KEYWORDS = re.compile(r"^(?:kw|keywords?|từ\s*khóa|tu\s*khoa)\s*[:：=-]\s*(.+)$", re.I)


def split_note_rows(content: str) -> list[str]:
    return [row.strip() for row in content.splitlines() if row.strip()]


def _owners(raw: str) -> tuple[str, ...]:
    values = [item for group in _OWNER.findall(raw) for item in group if item]
    return tuple(dict.fromkeys(values))


def classify_note_row(index: int, raw: str) -> ParsedNoteLine:
    text = _ROW_PREFIX.sub("", raw).strip()
    normalized = normalize_for_match(text)
    labels = tuple(f"Task {label.upper()}" for label in _LABEL.findall(text))
    deadlines = tuple(match.strip() for match in _DEADLINE.findall(text))
    owners = _owners(text)
    if not text:
        kind, confidence = NoteLineKind.NOISE, 1.0
    elif _TOPIC.match(text):
        kind, confidence = NoteLineKind.TOPIC, 0.9
    elif _KEYWORDS.match(text):
        # Keyword rows help ranking/compaction, never action extraction.
        kind, confidence = NoteLineKind.TOPIC, 0.45
    elif len(extract_keywords(text)) <= 2 and re.search(r"\b(?:note|notes|meeting|agenda|recap)\b", normalized):
        kind, confidence = NoteLineKind.HEADER, 0.9
    # Keep the raw question mark: normalization intentionally removes punctuation.
    elif _QUESTION.search(text) or _QUESTION.search(normalized):
        kind, confidence = NoteLineKind.QUESTION, 0.95
    elif _CANCEL.search(normalized) or _STATE.search(normalized):
        kind, confidence = NoteLineKind.STATE_HINT, 0.8
    elif labels or owners or deadlines or len(extract_keywords(text)) >= 2:
        kind, confidence = NoteLineKind.ACTION_HINT, 0.65
    else:
        kind, confidence = NoteLineKind.TOPIC, 0.45
    operations = tuple(
        item for item, pattern in (("CANCEL", _CANCEL), ("STATE", _STATE))
        if pattern.search(normalized)
    )
    return ParsedNoteLine(
        line_id=f"NOTE-{index + 1:03d}", raw_text=text, normalized_text=normalized,
        kind=kind, keywords=extract_keywords(text), owner_hints=owners,
        task_labels=labels, deadline_hints=deadlines, operation_hints=operations,
        confidence=confidence,
    )


def parse_meeting_note(note: MeetingNoteInput) -> list[ParsedNoteLine]:
    return [classify_note_row(index, row) for index, row in enumerate(split_note_rows(note.content))]


def meeting_note_topic(note: MeetingNoteInput | None) -> str:
    """Return only an explicitly written note topic, not an inferred action row."""

    if note is None:
        return ""
    for row in split_note_rows(note.content):
        text = _ROW_PREFIX.sub("", row).strip()
        match = _TOPIC.match(text)
        if not match:
            continue
        topic = re.split(r"[.!?]", match.group(1), maxsplit=1)[0].strip(" ,:;.-")
        if 3 <= len(topic) <= 160:
            return topic[:1].upper() + topic[1:]
    return ""
