"""Fail-closed task target resolution for V2."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata

from ..models import TaskEntity, TaskEventV2


def _normal(value: str) -> str:
    value = "".join(
        char for char in unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
        if not unicodedata.combining(char)
    )
    return " ".join(re.findall(r"\w+", value))


def _tokens(value: str) -> set[str]:
    return set(_normal(value).split()) - {"task", "viec", "phan", "nay", "do"}


class TaskResolver:
    """Resolve only explicit or sufficiently evidenced task references.

    In particular, owner, recency and active-task count are never fallback
    identifiers.  A caller can surface ``None`` as UNRESOLVED_REFERENCE or ask
    a constrained second-pass model to choose from ``candidate_entities``.
    """

    def resolve(self, event: TaskEventV2, entities: dict[str, TaskEntity]) -> str | None:
        if event.target_task_id in entities:
            return event.target_task_id
        label = _normal(event.explicit_task_label)
        if label:
            matches = [
                entity.task_id for entity in entities.values()
                if label in {_normal(item) for item in entity.explicit_labels}
            ]
            return matches[0] if len(matches) == 1 else None
        hint = _normal(event.target_action_hint)
        if not hint:
            return None
        exact = [
            entity.task_id for entity in entities.values()
            if hint == _normal(entity.canonical_action)
            or hint in {_normal(alias) for alias in entity.aliases}
        ]
        if len(exact) == 1:
            return exact[0]
        # A fuzzy match must be both strong and unambiguous, and must share an
        # explicit object token.  This is intentionally stricter than V1.
        ranked: list[tuple[float, str]] = []
        hint_tokens = _tokens(hint)
        for entity in entities.values():
            candidate = _normal(entity.canonical_action)
            score = SequenceMatcher(None, hint, candidate).ratio()
            if hint_tokens and hint_tokens & _tokens(candidate):
                ranked.append((score, entity.task_id))
        ranked.sort(reverse=True)
        if not ranked or ranked[0][0] < 0.80:
            return None
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.15:
            return None
        return ranked[0][1]

    @staticmethod
    def candidate_entities(event: TaskEventV2, entities: dict[str, TaskEntity], limit: int = 5) -> list[TaskEntity]:
        """Return only a bounded, auditable candidate list for second-pass AI."""

        hint = _normal(event.target_action_hint)
        if not hint:
            return []
        ranked = sorted(
            entities.values(),
            key=lambda entity: SequenceMatcher(None, hint, _normal(entity.canonical_action)).ratio(),
            reverse=True,
        )
        return ranked[:limit]
