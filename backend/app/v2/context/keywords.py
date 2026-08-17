"""Topic keywords are ranking signals, never transcript deletion rules."""

from __future__ import annotations

import re

from ...preprocessing.unicode_normalizer import normalize_for_match


STOPWORDS = frozenset({
    "anh", "chi", "em", "toi", "minh", "ban", "va", "la", "cho", "voi", "cua",
    "task", "viec", "phan", "team", "meeting", "hom", "nay", "mai", "lam", "gui",
    "cap nhat", "kiem tra", "duoc", "vay", "thi", "mot", "nhung", "the", "this",
    "that", "with", "from", "into", "the", "and", "for", "will", "todo", "action",
})
DATE_OR_TIME = re.compile(r"^(?:\d+|dl|ddl|due|deadline|eta|han|thu|ngay|tuan|am|pm)$")


def extract_keywords(value: str, limit: int = 8) -> tuple[str, ...]:
    tokens = re.findall(r"\w+", normalize_for_match(value))
    selected: list[str] = []
    for token in tokens:
        if len(token) < 3 or token in STOPWORDS or DATE_OR_TIME.match(token):
            continue
        if token not in selected:
            selected.append(token)
        if len(selected) >= limit:
            break
    return tuple(selected)


def keyword_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    return len(set(left) & set(right)) / min(len(set(left)), len(set(right)))
