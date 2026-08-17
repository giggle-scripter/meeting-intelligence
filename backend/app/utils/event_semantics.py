"""Shared semantic guards for provider-authored task mutations."""

from __future__ import annotations

import re

from ..preprocessing.unicode_normalizer import normalize_for_match


_DISCOURSE_SECTION_RE = re.compile(
    r"\b(?:tam\s+dung|dung|ket\s+thuc)\s+"
    r"(?:phan|noi\s+dung|chu\s+de|trao\s+doi|thao\s+luan)\b",
    re.I,
)
_EXPLICIT_TASK_OBJECT_RE = re.compile(
    r"\b(?:task|cong\s+viec|dau\s+viec|ticket|issue)\b",
    re.I,
)
_EXPLICIT_TERMINAL_RE = re.compile(
    r"\b(?:huy|cancel|bo\s+(?:task|cong\s+viec|dau\s+viec)|"
    r"khong\s+(?:can|tiep\s+tuc)\s+lam)\b",
    re.I,
)


def is_discourse_transition_cancel(text: str) -> bool:
    """Return true when stopping a discussion section is not task cancellation."""

    normalized = normalize_for_match(text)
    if not normalized or not _DISCOURSE_SECTION_RE.search(normalized):
        return False
    return not (
        _EXPLICIT_TASK_OBJECT_RE.search(normalized)
        or _EXPLICIT_TERMINAL_RE.search(normalized)
    )
