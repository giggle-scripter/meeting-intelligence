"""Deterministic, span-preserving task action canonicalization."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from backend.app.ai.rule_guards import evaluate_action
from backend.app.annotation.cue_patterns import ACTION_VERBS


class ActionFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_action_span: str = Field(min_length=1)
    canonical_action: str
    verb: str
    object: str
    qualifiers: tuple[str, ...] = ()
    deliverable: str
    method_steps: tuple[str, ...] = ()
    grounded_clause_ids: tuple[str, ...] = ()
    valid: bool
    rejection_reason: str = ""


_LEADING_DISCOURSE_RE = re.compile(
    r"^(?:(?:dạ|vâng|ừm?|ok(?:ay)?|được|vậy|thế|còn)\s*[,;:.!?-]*\s*)+",
    re.IGNORECASE,
)
_LEADING_SCOPE_RE = re.compile(
    r"^(?:(?:còn\s+)?(?:việc|task|phần)\s+|(?:text|cái\s+đó)\s+(?:thì\s+)?)",
    re.IGNORECASE,
)
_LEADING_MODALITY_RE = re.compile(
    r"^(?:(?:em|tôi|mình|anh|chị|we|i)\s+)?(?:sẽ|will|cam\s+kết\s+(?:sẽ\s+)?|"
    r"cố\s+gắng\s+(?:sẽ\s+)?|tranh\s+thủ\s+(?:sẽ\s+)?)\s+",
    re.IGNORECASE,
)
_TRAILING_METHOD_RE = re.compile(
    r"\s*(?:,|;)?\s+(?:và|rồi|sau\s+đó|and\s+then)\s+"
    r"(?P<step>(?:báo(?:\s+lại|\s+cáo)?|gửi(?:\s+lại)?|update|cập\s+nhật)\b.*)$",
    re.IGNORECASE,
)
_TRAILING_QUESTION_RE = re.compile(r"\s*(?:được\s+không|nhé|nhỉ|không)\s*\?*$", re.IGNORECASE)
_TRAILING_TIME_RE = re.compile(
    r"\s*(?:,|;)?\s*(?:trước|vào|đến|deadline|hạn(?:\s+chót)?|by|on)\b.*$",
    re.IGNORECASE,
)


def _verb_and_object(action: str) -> tuple[str, str]:
    normalized_verbs = sorted(ACTION_VERBS, key=len, reverse=True)
    for verb in normalized_verbs:
        match = re.match(rf"^{re.escape(verb)}\b\s*(.*)$", action, re.IGNORECASE)
        if match:
            return verb, match.group(1).strip()
    first, _, remainder = action.partition(" ")
    return first, remainder.strip()


def build_action_frame(raw_action_span: str, grounded_clause_ids: tuple[str, ...] = ()) -> ActionFrame:
    """Remove non-identity discourse while preserving a concrete action/object."""

    action = " ".join(raw_action_span.split()).strip(" .,:;!?-–—")
    action = _LEADING_DISCOURSE_RE.sub("", action)
    action = _LEADING_SCOPE_RE.sub("", action)
    action = _LEADING_MODALITY_RE.sub("", action)
    method_steps = ()
    if match := _TRAILING_METHOD_RE.search(action):
        method_steps = (match.group("step").strip(),)
        action = action[:match.start()].strip()
    action = _TRAILING_TIME_RE.sub("", action)
    action = _TRAILING_QUESTION_RE.sub("", action)
    action = " ".join(action.split()).strip(" .,:;!?-–—")
    if action:
        action = action[:1].upper() + action[1:]
    guard = evaluate_action(action)
    verb, object_text = _verb_and_object(action)
    if verb:
        verb = verb[:1].upper() + verb[1:]
    return ActionFrame(
        raw_action_span=raw_action_span,
        canonical_action=(action if guard.concrete else ""),
        verb=verb,
        object=object_text,
        deliverable=object_text,
        method_steps=method_steps,
        grounded_clause_ids=grounded_clause_ids,
        valid=guard.concrete,
        rejection_reason="" if guard.concrete else guard.reason,
    )
