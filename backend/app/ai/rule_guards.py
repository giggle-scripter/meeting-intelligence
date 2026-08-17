"""Pure safety guards shared by deterministic CREATE validation."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..annotation.cue_patterns import ACTION_VERBS
from ..preprocessing.unicode_normalizer import normalize_for_match


@dataclass(frozen=True)
class ActionGuardResult:
    concrete: bool
    normalized_action: str
    reason: str


_PRONOUN_ONLY = {
    "viec do", "phan nay", "phan do", "task nay", "task do",
    "it", "this", "that", "the task",
}
_VAGUE = {
    "will do", "handle this", "send it", "cap nhat lai", "check lai",
    "hoan thanh", "lam theo", "viet chi tiet", "gui changes som",
    "gui loi moi", "cap quyen cho em", "tao trong qua trinh chuan bi du lieu",
    "an danh can than", "sua theo", "update gi khong",
}
_VAGUE_PREFIXES = (
    "cap nhat lai", "update again", "cap nhat tinh hinh", "update status",
    "theo doi tien do", "track progress", "gui build khi xong",
    "send build when done",
)
_NON_OBJECT = {
    "em", "anh", "chi", "toi", "minh", "ban", "lai", "them", "som",
    "ngay", "mai", "nay", "nhe", "please", "it", "this", "that",
}


def evaluate_action(action: str) -> ActionGuardResult:
    """Require an action verb plus a concrete deliverable/object.

    This does not infer missing objects from Meeting Note or nearby text; that
    remains AI/context work. The reason is trace/test-only and never public.
    """

    normalized = normalize_for_match(action).strip()
    if not normalized:
        return ActionGuardResult(False, normalized, "EMPTY")
    if normalized in _PRONOUN_ONLY:
        return ActionGuardResult(False, normalized, "PRONOUN_ONLY")
    if normalized in _VAGUE or normalized.startswith(_VAGUE_PREFIXES):
        return ActionGuardResult(False, normalized, "VAGUE_ADMIN")
    verbs = sorted((normalize_for_match(item) for item in ACTION_VERBS), key=len, reverse=True)
    verb = next((item for item in verbs if normalized == item or normalized.startswith(item + " ")), "")
    if not verb:
        return ActionGuardResult(False, normalized, "NO_ACTION_VERB")
    object_words = re.findall(r"\w+", normalized[len(verb):])
    if not object_words or not any(word not in _NON_OBJECT for word in object_words):
        return ActionGuardResult(False, normalized, "NO_OBJECT")
    return ActionGuardResult(True, normalized, "CONCRETE")
