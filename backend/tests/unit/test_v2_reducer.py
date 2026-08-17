from backend.app.v2.models import TaskEventV2, TaskOperation, TaskStatus
from backend.app.v2.reduction import reduce_task_events_v2


def event(event_id: str, operation: TaskOperation, order: int, **kwargs) -> TaskEventV2:
    return TaskEventV2(
        event_id=event_id,
        operation=operation,
        anchor_clause_id=f"C-{order}",
        source_clause_ids=(f"C-{order}",),
        global_order=order,
        **kwargs,
    )


def test_create_assign_and_deadline_replace_are_sequential() -> None:
    result = reduce_task_events_v2("meeting-1", [
        event("E-1", TaskOperation.CREATE, 1, explicit_task_label="A", action_patch="Viết tài liệu"),
        event("E-2", TaskOperation.ASSIGN, 2, explicit_task_label="A", assignee_patch="Lan"),
        event("E-3", TaskOperation.SET_DEADLINE, 3, explicit_task_label="A", deadline_mention_id="D-1"),
        event("E-4", TaskOperation.REPLACE_DEADLINE, 4, explicit_task_label="A", deadline_mention_id="D-2"),
    ])

    task = next(iter(result.entities.values()))
    assert task.assignee == "Lan"
    assert task.deadline_mention_id == "D-2"


def test_mutation_without_target_is_unresolved_and_cannot_create_task() -> None:
    result = reduce_task_events_v2("meeting-1", [
        event("E-1", TaskOperation.CREATE, 1, action_patch="Viết tài liệu", assignee_patch="Lan"),
        event("E-2", TaskOperation.CANCEL, 2, target_action_hint=""),
    ])

    assert len(result.entities) == 1
    assert result.unresolved_event_ids == ["E-2"]


def test_reassign_does_not_select_same_owner_or_most_recent_task() -> None:
    result = reduce_task_events_v2("meeting-1", [
        event("E-1", TaskOperation.CREATE, 1, explicit_task_label="A", action_patch="Kiểm tra API", assignee_patch="Lan"),
        event("E-2", TaskOperation.CREATE, 2, explicit_task_label="B", action_patch="Kiểm tra log", assignee_patch="Lan"),
        event("E-3", TaskOperation.REASSIGN, 3, target_action_hint="", assignee_patch="Minh"),
    ])

    assert result.unresolved_event_ids == ["E-3"]
    assert [task.assignee for task in result.entities.values()] == ["Lan", "Lan"]


def test_cancel_is_retained_and_create_ids_are_stable_and_idempotent() -> None:
    events = [
        event("E-1", TaskOperation.CREATE, 1, explicit_task_label="A", action_patch="Kiểm tra audit"),
        event("E-2", TaskOperation.CANCEL, 2, explicit_task_label="A"),
        event("E-1", TaskOperation.CREATE, 1, explicit_task_label="A", action_patch="Kiểm tra audit"),
    ]
    first = reduce_task_events_v2("meeting-1", events)
    second = reduce_task_events_v2("meeting-1", list(reversed(events)))

    assert len(first.entities) == 1
    assert next(iter(first.entities.values())).status is TaskStatus.CANCELLED
    assert list(first.entities) == list(second.entities)
