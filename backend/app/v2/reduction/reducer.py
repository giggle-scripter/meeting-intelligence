"""Single-threaded, idempotent V2 event reducer."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...utils.hashing import stable_hash
from ..models import TaskEntity, TaskEventV2, TaskOperation, TaskStatus
from ..resolution import TaskResolver


OPERATION_PRIORITY = {
    TaskOperation.CREATE: 0,
    TaskOperation.CONFIRM: 1,
    TaskOperation.ASSIGN: 2,
    TaskOperation.REASSIGN: 3,
    TaskOperation.RENAME: 4,
    TaskOperation.SET_DEADLINE: 5,
    TaskOperation.REPLACE_DEADLINE: 6,
    TaskOperation.CLEAR_DEADLINE: 7,
    TaskOperation.CANCEL: 8,
    TaskOperation.REJECT: 9,
    TaskOperation.REOPEN: 10,
}


@dataclass
class ReductionResult:
    entities: dict[str, TaskEntity] = field(default_factory=dict)
    unresolved_event_ids: list[str] = field(default_factory=list)
    applied_event_ids: list[str] = field(default_factory=list)

    @property
    def active_entities(self) -> list[TaskEntity]:
        return [entity for entity in self.entities.values() if entity.status is TaskStatus.ACTIVE]


def _append_evidence(entity: TaskEntity, event: TaskEventV2) -> None:
    for clause_id in event.source_clause_ids:
        if clause_id not in entity.source_clause_ids:
            entity.source_clause_ids.append(clause_id)
    if event.event_id not in entity.event_history:
        entity.event_history.append(event.event_id)
    entity.last_updated_order = event.global_order


def _create_task_id(meeting_id: str, event: TaskEventV2) -> str:
    identity = event.explicit_task_label or event.anchor_clause_id
    return f"TASK-{stable_hash(f'{meeting_id}|{identity}', length=20)}"


def _apply_mutation(entity: TaskEntity, event: TaskEventV2) -> bool:
    """Apply a linked mutation; return false for invalid state transitions."""

    if event.event_id in entity.event_history:
        return True
    if event.operation is TaskOperation.CONFIRM:
        pass
    elif event.operation is TaskOperation.ASSIGN:
        if entity.assignee:
            return False
        entity.assignee = event.assignee_patch or entity.assignee
    elif event.operation is TaskOperation.REASSIGN:
        if not event.assignee_patch:
            return False
        entity.assignee = event.assignee_patch
    elif event.operation is TaskOperation.RENAME:
        if not event.action_patch:
            return False
        if entity.canonical_action not in entity.aliases:
            entity.aliases.append(entity.canonical_action)
        entity.canonical_action = event.action_patch
    elif event.operation is TaskOperation.SET_DEADLINE:
        if entity.deadline_mention_id or not event.deadline_mention_id:
            return False
        entity.deadline_mention_id = event.deadline_mention_id
    elif event.operation is TaskOperation.REPLACE_DEADLINE:
        if not event.deadline_mention_id:
            return False
        entity.deadline_mention_id = event.deadline_mention_id
    elif event.operation is TaskOperation.CLEAR_DEADLINE:
        entity.deadline_mention_id = ""
    elif event.operation is TaskOperation.CANCEL:
        entity.status = TaskStatus.CANCELLED
    elif event.operation is TaskOperation.REJECT:
        entity.status = TaskStatus.REJECTED
    elif event.operation is TaskOperation.REOPEN:
        if entity.status not in {TaskStatus.CANCELLED, TaskStatus.REJECTED}:
            return False
        entity.status = TaskStatus.ACTIVE
    else:
        return False
    _append_evidence(entity, event)
    return True


def reduce_task_events_v2(meeting_id: str, events: list[TaskEventV2], resolver: TaskResolver | None = None) -> ReductionResult:
    """Reduce in transcript order; only CREATE can add an entity."""

    resolver = resolver or TaskResolver()
    result = ReductionResult()
    for event in sorted(events, key=lambda item: (item.global_order, OPERATION_PRIORITY[item.operation], item.event_id)):
        if event.operation is TaskOperation.CREATE:
            task_id = _create_task_id(meeting_id, event)
            if task_id in result.entities:
                # A retried provider response is idempotent.  It never produces
                # a second task for the same stable create anchor.
                if event.event_id not in result.entities[task_id].event_history:
                    _append_evidence(result.entities[task_id], event)
                result.applied_event_ids.append(event.event_id)
                continue
            entity = TaskEntity(
                task_id=task_id,
                canonical_action=event.action_patch,
                aliases=[],
                explicit_labels={event.explicit_task_label} if event.explicit_task_label else set(),
                assignee=event.assignee_patch,
                deadline_mention_id=event.deadline_mention_id,
                created_order=event.global_order,
                last_updated_order=event.global_order,
            )
            _append_evidence(entity, event)
            result.entities[task_id] = entity
            result.applied_event_ids.append(event.event_id)
            continue
        task_id = resolver.resolve(event, result.entities)
        if task_id is None or not _apply_mutation(result.entities[task_id], event):
            result.unresolved_event_ids.append(event.event_id)
            continue
        result.applied_event_ids.append(event.event_id)
    return result
