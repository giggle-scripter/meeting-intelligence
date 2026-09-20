"""Semantic event deduplication with evidence union and source authority."""

from __future__ import annotations

from ..models import TaskEvent
from ..preprocessing.unicode_normalizer import normalize_for_match
from ..utils.event_semantics import is_discourse_transition_cancel


SOURCE_PRIORITY = {
    "HUMAN": 5,
    "RULE_FINAL_RECAP": 5,
    "HUMAN_NOTE": 4,
    "RULE_RECAP": 3,
    "RULE_CONTEXT": 2,
    "RULE_NOTE_CUE": 2,
    "RULE_REFERENCE_ACTIVE": 2,
    "RULE_REFERENCE": 2,
    "RULE": 2,
    "AI": 1,
    "AI_CREATE_PROPOSAL": 1,
}
CREATION_EVENTS = {"TASK_CREATE", "TASK_COMMITMENT", "OWNER_ASSIGN"}
REFERENCE_EVENTS = {"TASK_REFERENCE"}
MUTATION_EVENTS = {
    "OWNER_REASSIGN",
    "DEADLINE_SET",
    "DEADLINE_REPLACE",
    "TASK_CANCEL",
    "TASK_REJECT",
}


def _anchor(event: TaskEvent) -> str:
    if event.anchor_clause_id:
        return event.anchor_clause_id
    # Local rule extractors generally put the event clause last, after any
    # antecedent evidence.  Keep events separate when no anchor can be inferred.
    return event.source_clause_ids[-1] if event.source_clause_ids else event.event_id


def semantic_event_key(event: TaskEvent) -> tuple:
    anchor = _anchor(event)
    if event.related_task_id:
        return (
            "TARGETED",
            event.event_type,
            event.related_task_id,
            anchor,
            normalize_for_match(event.assignee),
            event.deadline_mention_id,
        )
    if event.event_type in CREATION_EVENTS:
        if event.extraction_source == "RULE_FINAL_RECAP":
            # One explicit final-list row may contain sibling tasks with the
            # same owner/tokens.  Their identity is intentionally preserved.
            return ("FINAL_LIST", event.event_id)
        return (
            "CREATE",
            event.event_type,
            normalize_for_match(event.action_text),
            anchor,
        )
    if event.event_type in REFERENCE_EVENTS:
        return (
            "REFERENCE",
            normalize_for_match(event.action_text),
            anchor,
        )
    return (
        "UNTARGETED_MUTATION",
        event.event_type,
        anchor,
        normalize_for_match(event.related_task_hint or event.action_text),
        normalize_for_match(event.assignee),
        event.deadline_mention_id,
    )


def _quality(event: TaskEvent) -> tuple[int, float]:
    return SOURCE_PRIORITY.get(event.extraction_source, 0), event.confidence


def deduplicate_events(events: list[TaskEvent]) -> list[TaskEvent]:
    selected: dict[tuple, TaskEvent] = {}
    for event in events:
        # Provider output may confuse a meeting-section transition such as
        # "tạm dừng phần tài liệu ở đây" with cancellation of the underlying
        # deliverable.  Keep explicit task cancellation, but fail closed for
        # discourse-only transitions.  This also protects paid trace replay.
        if (
            event.extraction_source == "AI"
            and event.event_type == "TASK_CANCEL"
            and is_discourse_transition_cancel(event.action_text)
        ):
            continue
        key = semantic_event_key(event)
        previous = selected.get(key)
        if previous is None:
            selected[key] = event
            continue
        source_ids = list(
            dict.fromkeys(previous.source_clause_ids + event.source_clause_ids)
        )
        if _quality(event) > _quality(previous):
            event.source_clause_ids = source_ids
            if not event.anchor_clause_id:
                event.anchor_clause_id = previous.anchor_clause_id
            selected[key] = event
        else:
            previous.source_clause_ids = source_ids
            if not previous.anchor_clause_id:
                previous.anchor_clause_id = event.anchor_clause_id

    # A same-clause recap/assignment already carries its own target identity and
    # deadline.  A provider deadline mutation at that anchor is redundant and
    # can incorrectly pull the recap into an older, broader identity.
    local_positive_deadlines = {
        (_anchor(event), event.deadline_mention_id)
        for event in selected.values()
        if event.event_type in CREATION_EVENTS
        and event.extraction_source != "AI"
        and event.deadline_mention_id
    }
    for key, event in list(selected.items()):
        if (
            event.extraction_source == "AI"
            and event.event_type in {"DEADLINE_SET", "DEADLINE_REPLACE"}
            and event.deadline_mention_id
            and (_anchor(event), event.deadline_mention_id)
            in local_positive_deadlines
        ):
            del selected[key]

    # A local rule may identify the mutation kind and anchor but fail to bind a
    # target. When AI resolves that exact mutation to one supplied ledger ID,
    # keeping the unbound rule event would falsely leave the mutation
    # unresolved after the target-backed event has already been applied.
    targeted_by_anchor: dict[tuple[str, str], list[tuple[tuple, TaskEvent]]] = {}
    for key, event in selected.items():
        if event.event_type in MUTATION_EVENTS and event.related_task_id:
            targeted_by_anchor.setdefault(
                (event.event_type, _anchor(event)), []
            ).append((key, event))

    for key, event in list(selected.items()):
        if event.event_type not in MUTATION_EVENTS or event.related_task_id:
            continue
        candidates = targeted_by_anchor.get((event.event_type, _anchor(event)), [])
        target_ids = {candidate.related_task_id for _, candidate in candidates}
        if len(target_ids) != 1:
            continue
        _, resolved = max(candidates, key=lambda item: _quality(item[1]))
        resolved.source_clause_ids = list(
            dict.fromkeys(resolved.source_clause_ids + event.source_clause_ids)
        )
        if (
            resolved.extraction_source == "AI"
            and event.extraction_source != "AI"
            and event.event_type in {"DEADLINE_SET", "DEADLINE_REPLACE"}
            and event.deadline_mention_id == resolved.deadline_mention_id
        ):
            resolved.corroborated_by_rule = True
        del selected[key]
    return sorted(selected.values(), key=lambda item: (item.order_index, item.event_id))
