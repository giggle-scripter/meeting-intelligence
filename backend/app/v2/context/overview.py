"""Deterministic topic overview used only when no human note is supplied."""

from __future__ import annotations

from collections import Counter
import re

from ...models import Clause, MeetingInput
from ...preprocessing.unicode_normalizer import normalize_for_match
from .keywords import extract_keywords
from ..models import TopicHint


_TECHNICAL = re.compile(r"^(?:w\d+-|upload-|[a-z0-9]+(?:-[a-z0-9]+){3,})", re.I)


def build_overview(meeting: MeetingInput, clauses: list[Clause], annotations: dict[str, object], max_topics: int = 12) -> list[TopicHint]:
    candidates: list[tuple[str, tuple[str, ...], str, float]] = []
    title = meeting.meeting_title.strip()
    if title and not _TECHNICAL.match(title):
        candidates.append((title, extract_keywords(title), "TITLE", 0.6))
    for clause in clauses[:40]:
        text = normalize_for_match(clause.text_raw)
        if re.search(r"\b(?:hop.*(?:ve|de)|agenda|chu de|review)\b", text):
            candidates.append((clause.text_raw[:80], extract_keywords(clause.text_raw), "AGENDA", 0.5))
    frequency = Counter(keyword for clause in clauses for keyword in extract_keywords(clause.text_raw))
    for keyword, count in frequency.items():
        if count >= 2:
            candidates.append((keyword, (keyword,), "REPEATED", min(0.8, 0.35 + count * 0.08)))
    topics: list[TopicHint] = []
    seen: set[tuple[str, ...]] = set()
    for label, keywords, source, confidence in sorted(candidates, key=lambda item: item[3], reverse=True):
        key = tuple(keywords)
        if not key or key in seen:
            continue
        seen.add(key)
        topics.append(TopicHint(f"TOPIC-{len(topics) + 1:03d}", label, key[:8], source, confidence))
        if len(topics) == max_topics:
            break
    return topics
