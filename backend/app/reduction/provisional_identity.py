"""Discover strong references to tasks that pre-date the current meeting.

These references create internal navigation identities only. They are not
public tasks until a later owner, deadline, or active event promotes them.
"""

from __future__ import annotations

import re

from ..annotation.cue_patterns import ACTION_VERBS
from ..models import Clause, TaskEvent
from ..preprocessing.unicode_normalizer import normalize_for_match
from ..utils.ids import make_id


_QUOTED_TASK_RE = re.compile(
    r"\btask\s*(?:c[oó]\s+t[eê]n\s+l[aà]\s*)?[\"“](?P<label>[^\"”]{2,160})[\"”]",
    re.I,
)
_PLAIN_TASK_RE = re.compile(r"\btask\s+(?P<label>[^,;.!?]{1,180})", re.I)
_PLAIN_STOP_RE = re.compile(
    r"\s+(?:đang|đã|deadline|hạn(?:\s+chót)?|owner|assignee|status|"
    r"trạng\s+thái|do|được|vẫn|sẽ|có\s+cần|thế\s+nào|không\s+còn)\b",
    re.I,
)
_ACTIVE_STATE_RE = re.compile(
    r"\b(?:đang\s+(?:ở\s+)?(?:trạng\s+thái\s+)?"
    r"(?:open|active|in[ -]?progress)|"
    r"(?:status|trạng\s+thái)\s*(?:là|:|-)?\s*"
    r"(?:open|active|in[ -]?progress))\b",
    re.I,
)
_VAGUE_REFERENCE_RE = re.compile(
    r"^(?:nay|do|kia|ay|cu|moi|con|rieng|chinh\s+thuc|"
    r"truoc|sau|tiep\s+theo|a|b|c|x|y|z)(?:\b|$)",
    re.I,
)
_TRAILING_FILLER_RE = re.compile(
    r"\s+(?:của\s+(?:em|anh|chị|tôi|mình)|này|đó|kia|ấy)\s*$",
    re.I,
)
_NON_TASK_REFERENCE_RE = re.compile(
    r"^(?:cua\s+(?:em|anh|chi|toi|minh)\b|deu\s+tracking\b|"
    r"phu\s+thuoc\b|ten\s+giong\s+nhau\b|mot\s+lan\s+nua\b|"
    r"dang\s+(?:progress|theo\s+doi|tracking)\b|"
    r"(?:status|trang\s+thai)\b)|"
    r"\bco\b.*\b(?:overlap|phu\s+thuoc)\b.*\bkhong$|"
    r"^(?:\w+\s+){0,3}hoan\s+thanh\s+(?:truoc|sau|vao)\b",
    re.I,
)
_DELIVERABLE_RE = re.compile(
    r"\b(?:api|board|config|dashboard|data|dataset|design|document|draft|"
    r"log|metric|migration|monitoring|parser|pipeline|plan|release|report|runbook|"
    r"sandbox|scenario|script|spec|tai\s+lieu|test(?:\s+case)?|ui|"
    r"wireframe)\b",
    re.I,
)


def is_promotable_task_reference(label: str) -> bool:
    """Return whether a plain reference is safe to promote into public output.

    Provisional IDs remain broad enough for navigation and replay stability.
    Promotion is narrower so a later mutation cannot turn a relation, status
    sentence or question into a public task.
    """

    normalized = normalize_for_match(label).strip()
    if not normalized or _NON_TASK_REFERENCE_RE.search(normalized):
        return False
    if len(normalized.split()) < 2:
        return False
    verbs = sorted(
        (normalize_for_match(item) for item in ACTION_VERBS),
        key=len,
        reverse=True,
    )
    if any(
        normalized == verb
        or normalized.startswith(verb + " ")
        or re.search(
            rf"(?:^|\s[-:]\s|\btask\s+\d+\s*[:-]\s).*\b{re.escape(verb)}\b",
            normalized,
        )
        for verb in verbs
    ):
        return True
    return bool(_DELIVERABLE_RE.search(normalized))


def _clean_reference_label(value: str, *, quoted: bool) -> str:
    label = re.sub(r"\s+", " ", value).strip(" \t\r\n\"“”'`:-–—")
    if not quoted:
        label = _PLAIN_STOP_RE.split(label, maxsplit=1)[0]
    label = _TRAILING_FILLER_RE.sub("", label).strip(" \t\r\n\"“”'`:-–—")
    label = re.sub(
        r"^TASK[-_ ]?\d+\s*[-:–—]\s*",
        "",
        label,
        flags=re.I,
    )
    normalized = normalize_for_match(label)
    if not normalized or _VAGUE_REFERENCE_RE.match(normalized):
        return ""
    meaningful = [token for token in normalized.split() if len(token) > 1]
    if (
        not quoted
        and len(meaningful) < 2
        and not _DELIVERABLE_RE.search(normalized)
    ):
        return ""
    return label[:1].upper() + label[1:]


def extract_provisional_task_references(
    clauses: list[Clause],
    start_sequence: int = 0,
) -> list[TaskEvent]:
    """Return ``TASK_REFERENCE`` events for concrete, explicit task labels."""

    events: list[TaskEvent] = []
    for clause in clauses:
        matches: list[tuple[int, str, bool]] = []
        quoted_spans: list[tuple[int, int]] = []
        for match in _QUOTED_TASK_RE.finditer(clause.text_raw):
            quoted_spans.append(match.span())
            matches.append((match.start(), match.group("label"), True))
        for match in _PLAIN_TASK_RE.finditer(clause.text_raw):
            if any(start <= match.start() < end for start, end in quoted_spans):
                continue
            matches.append((match.start(), match.group("label"), False))

        seen: set[str] = set()
        for _, raw_label, quoted in sorted(matches):
            label = _clean_reference_label(raw_label, quoted=quoted)
            normalized = normalize_for_match(label)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            active = bool(_ACTIVE_STATE_RE.search(clause.text_raw))
            events.append(
                TaskEvent(
                    event_id=make_id("EVENT", start_sequence + len(events) + 1),
                    event_type="TASK_REFERENCE",
                    source_clause_ids=[clause.clause_id],
                    action_text=label,
                    confidence=0.98 if active else 0.92 if quoted else 0.88,
                    extraction_source=(
                        "RULE_REFERENCE_ACTIVE" if active else "RULE_REFERENCE"
                    ),
                    order_index=clause.order_index,
                    anchor_clause_id=clause.clause_id,
                )
            )
    return events
