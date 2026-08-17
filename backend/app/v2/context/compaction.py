"""Select bounded AI context without deleting or rewriting transcript clauses."""

from __future__ import annotations

from ...models import Clause
from ..models import ClauseRelevance, RelevanceClass


_CLASS_SCORE = {
    RelevanceClass.MANDATORY: 100,
    RelevanceClass.LIKELY: 60,
    RelevanceClass.CONTEXT: 10,
    RelevanceClass.NOISE: -100,
}


def compact_context_clause_ids(
    context_clause_ids: list[str] | tuple[str, ...],
    focus_clause_ids: list[str] | tuple[str, ...],
    clauses_by_id: dict[str, Clause],
    relevance: dict[str, ClauseRelevance],
    *,
    max_clauses: int = 56,
    neighbor_radius: int = 2,
) -> tuple[str, ...]:
    """Keep focus/safety clauses, then rank topic and nearby support.

    The return value only controls an AI payload. The original transcript and
    the rule-only input are never changed. If safety clauses exceed the budget,
    all safety clauses are retained and the caller can split the provider call.
    """

    if max_clauses <= 0:
        raise ValueError("max_clauses must be greater than zero")
    ordered = [clause_id for clause_id in context_clause_ids if clause_id in clauses_by_id]
    position = {clause_id: index for index, clause_id in enumerate(ordered)}
    focus = {clause_id for clause_id in focus_clause_ids if clause_id in position}
    mandatory = {
        clause_id for clause_id in ordered
        if relevance.get(clause_id)
        and relevance[clause_id].relevance_class is RelevanceClass.MANDATORY
    }
    required = focus | mandatory
    if len(required) >= max_clauses:
        return tuple(clause_id for clause_id in ordered if clause_id in required)

    focus_positions = [position[clause_id] for clause_id in focus]
    focus_topics = {
        topic_id for clause_id in focus
        for topic_id in relevance.get(clause_id, ClauseRelevance(clause_id, RelevanceClass.CONTEXT, 0, ())).topic_ids
    }

    def priority(clause_id: str) -> tuple[int, int]:
        item = relevance.get(clause_id)
        class_score = _CLASS_SCORE[item.relevance_class] if item else 0
        index = position[clause_id]
        distance = min((abs(index - value) for value in focus_positions), default=999)
        nearby = 35 if distance <= neighbor_radius else max(0, 20 - distance * 2)
        topic = 25 if item and focus_topics.intersection(item.topic_ids) else 0
        grounded = 20 if item and item.note_line_ids else 0
        return class_score + nearby + topic + grounded, -index

    selected = set(required)
    candidates = [clause_id for clause_id in ordered if clause_id not in selected]
    for clause_id in sorted(candidates, key=priority, reverse=True):
        if len(selected) >= max_clauses:
            break
        selected.add(clause_id)
    return tuple(clause_id for clause_id in ordered if clause_id in selected)
