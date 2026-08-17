"""Apply one event to a linked task state."""

import re

from ..models import TaskEvent, TaskState
from ..preprocessing.unicode_normalizer import normalize_for_match


def apply_event(state: TaskState, event: TaskEvent) -> TaskState:
    if (
        event.event_type in {"TASK_CREATE", "TASK_COMMITMENT", "OWNER_ASSIGN"}
        and event.action_text
        and event.extraction_source != "HUMAN_NOTE"
        and "HUMAN_NOTE" in state.extraction_sources
    ):
        current = normalize_for_match(state.task_name)
        incoming = normalize_for_match(event.action_text)
        if (
            current
            and current in incoming
            and len(incoming.split()) > len(current.split())
            and not re.search(
                r"\?|[\"“”].*[\"“”]|\b(?:có\s+vẻ|giống\s+nhau|hay\s+là|"
                r"maybe|similar|duplicate)\b",
                event.action_text,
                re.I,
            )
        ):
            # The trusted note establishes the task; a corroborating transcript
            # clause may retain a more specific object without invalidating it.
            state.task_name = event.action_text
    if event.event_type in {"OWNER_ASSIGN", "OWNER_REASSIGN"} and event.assignee:
        state.assignee = event.assignee
        state.status = "REASSIGNED" if state.status != "UNKNOWN" else "PROPOSED"
        if event.deadline_mention_id:
            state.deadline_mention_id = event.deadline_mention_id
    elif event.event_type in {"DEADLINE_SET", "DEADLINE_REPLACE"} and event.deadline_mention_id:
        state.deadline_mention_id = event.deadline_mention_id
    elif event.event_type == "TASK_CANCEL":
        state.status = "CANCELLED"
    elif event.event_type == "TASK_REJECT":
        state.status = "REJECTED"
    elif event.event_type in {"TASK_CREATE", "TASK_COMMITMENT"}:
        state.status = "CONFIRMED"
        if event.assignee:
            state.assignee = event.assignee
        if event.deadline_mention_id:
            state.deadline_mention_id = event.deadline_mention_id
    for clause_id in event.source_clause_ids:
        if clause_id not in state.source_clause_ids:
            state.source_clause_ids.append(clause_id)
    state.last_order_index = event.order_index
    state.confidence = max(state.confidence, event.confidence)
    state.extraction_sources.add(event.extraction_source)
    return state
