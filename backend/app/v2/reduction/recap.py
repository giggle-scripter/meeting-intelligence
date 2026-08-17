"""Reconcile recap rows without granting partial recaps destructive authority."""

from ..models import RecapScope, RecapSnapshot, TaskEntity, TaskStatus
from ..resolution import TaskResolver


def reconcile_snapshot(snapshot: RecapSnapshot, entities: dict[str, TaskEntity], resolver: TaskResolver | None = None) -> list[str]:
    """Update matching rows and return unmatched rows for explicit review.

    A PARTIAL snapshot never removes an unmentioned task.  A COMPLETE snapshot
    also does not close tasks unless the source was explicitly classified as
    complete by the extractor; even then only active entities are affected.
    """

    resolver = resolver or TaskResolver()
    unmatched: list[str] = []
    linked_ids: set[str] = set()
    for index, row in enumerate(snapshot.rows):
        # Re-use the resolver's explicit-label and exact-alias lookup via a
        # minimal event-shaped object; no owner-only linking is possible.
        from ..models import TaskEventV2, TaskOperation
        event = TaskEventV2(
            event_id=f"RECAP-{index}", operation=TaskOperation.CONFIRM,
            anchor_clause_id=row.source_clause_ids[0] if row.source_clause_ids else "RECAP",
            source_clause_ids=row.source_clause_ids or ("RECAP",), global_order=0,
            explicit_task_label=row.explicit_task_label, target_action_hint=row.action,
        )
        task_id = resolver.resolve(event, entities)
        if task_id is None:
            unmatched.append(row.action)
            continue
        entity = entities[task_id]
        linked_ids.add(task_id)
        if row.assignee:
            entity.assignee = row.assignee
        if row.deadline_mention_id:
            entity.deadline_mention_id = row.deadline_mention_id
    if snapshot.scope is RecapScope.COMPLETE:
        # This changes state rather than deleting audit history.  Callers should
        # only classify COMPLETE on explicit "all active tasks" language.
        for task_id, entity in entities.items():
            if task_id not in linked_ids and entity.status is TaskStatus.ACTIVE:
                entity.status = TaskStatus.CANCELLED
    return unmatched
