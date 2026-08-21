from backend.app.models import TaskEvent
from backend.app.reduction import (
    deduplicate_events,
    reduce_task_events,
    reduce_task_events_to_ledger,
)


def test_later_reassignment_and_deadline_replace_win() -> None:
    events = [
        TaskEvent("E-1", "TASK_COMMITMENT", ["C-1"], "Chuẩn bị checklist", "Linh", "D-1", confidence=0.9, order_index=1),
        TaskEvent("E-2", "OWNER_REASSIGN", ["C-2"], "Chuẩn bị checklist", "Phương", confidence=0.9, order_index=2),
        TaskEvent("E-3", "DEADLINE_REPLACE", ["C-3"], related_task_hint="checklist", deadline_mention_id="D-2", confidence=0.9, order_index=3),
    ]
    states = reduce_task_events(events)
    assert len(states) == 1
    assert states[0].assignee == "Phương"
    assert states[0].deadline_mention_id == "D-2"


def test_cancelled_task_is_retained_as_state_for_audit() -> None:
    states = reduce_task_events([TaskEvent("E-1", "TASK_COMMITMENT", ["C-1"], "Kiểm tra audit log", "Minh", order_index=1), TaskEvent("E-2", "TASK_CANCEL", ["C-2"], related_task_hint="audit log", order_index=2)])
    assert states[0].status == "CANCELLED"


def test_ambiguous_mutation_never_falls_back_to_latest_active_task() -> None:
    events = [
        TaskEvent("E-1", "TASK_COMMITMENT", ["C-1"], "Viết API spec", "Lan", order_index=1),
        TaskEvent("E-2", "TASK_COMMITMENT", ["C-2"], "Chuẩn bị UAT", "Minh", order_index=2),
        TaskEvent("E-3", "TASK_CANCEL", ["C-3"], related_task_hint="phần đó", order_index=3),
    ]

    states = reduce_task_events(events)

    assert [state.status for state in states] == ["CONFIRMED", "CONFIRMED"]


def test_explicit_mutation_matches_its_task_not_another_active_task() -> None:
    events = [
        TaskEvent("E-1", "TASK_COMMITMENT", ["C-1"], "Viết API spec", "Lan", order_index=1),
        TaskEvent("E-2", "TASK_COMMITMENT", ["C-2"], "Chuẩn bị UAT", "Minh", order_index=2),
        TaskEvent("E-3", "TASK_CANCEL", ["C-3"], related_task_hint="Viết API spec", order_index=3),
    ]

    states = reduce_task_events(events)

    assert states[0].status == "CANCELLED"
    assert states[1].status == "CONFIRMED"


def test_repeated_positive_event_does_not_reopen_cancelled_task() -> None:
    diagnostics = {}
    states = reduce_task_events(
        [
            TaskEvent("E-1", "TASK_COMMITMENT", ["C-1"], "Viết API spec", "Lan", order_index=1),
            TaskEvent("E-2", "TASK_CANCEL", ["C-2"], related_task_hint="Viết API spec", order_index=2),
            TaskEvent("E-3", "TASK_COMMITMENT", ["C-3"], "Viết API spec", "Minh", order_index=3),
        ],
        diagnostics=diagnostics,
    )

    assert len(states) == 1
    assert states[0].status == "CANCELLED"
    assert diagnostics["terminal_replay_blocked_count"] == 1


def test_repeated_positive_event_does_not_reopen_rejected_task() -> None:
    states = reduce_task_events(
        [
            TaskEvent("E-1", "TASK_COMMITMENT", ["C-1"], "Viết API spec", "Lan", order_index=1),
            TaskEvent("E-2", "TASK_REJECT", ["C-2"], related_task_hint="Viết API spec", order_index=2),
            TaskEvent("E-3", "TASK_COMMITMENT", ["C-3"], "Viết API spec", "Lan", order_index=3),
        ]
    )

    assert len(states) == 1
    assert states[0].status == "REJECTED"


def test_explicit_terminal_task_id_does_not_bypass_replay_guard() -> None:
    states = reduce_task_events(
        [
            TaskEvent("E-1", "TASK_COMMITMENT", ["C-1"], "Viết API spec", "Lan", order_index=1),
            TaskEvent("E-2", "TASK_CANCEL", ["C-2"], related_task_hint="Viết API spec", order_index=2),
            TaskEvent(
                "E-3", "TASK_COMMITMENT", ["C-3"], "Viết API spec", "Lan",
                related_task_id="TASK-000001", order_index=3,
            ),
        ]
    )

    assert len(states) == 1
    assert states[0].status == "CANCELLED"


def test_deduplication_does_not_discard_a_more_specific_mutation_target() -> None:
    events = deduplicate_events(
        [
            TaskEvent(
                "E-1", "DEADLINE_REPLACE", ["C-9"],
                deadline_mention_id="D-2", related_task_hint="phần đó",
                extraction_source="RULE", order_index=9,
            ),
            TaskEvent(
                "E-2", "DEADLINE_REPLACE", ["C-9"],
                deadline_mention_id="D-2", related_task_hint="Viết API spec",
                extraction_source="AI", order_index=9,
            ),
        ]
    )

    assert len(events) == 2


def test_shadow_recap_fragment_keeps_output_unchanged_and_records_candidate() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            TaskEvent(
                "E-1", "OWNER_ASSIGN", ["C-1"], "Điều chỉnh thành 23/02", "Minh",
                extraction_source="RULE_RECAP", order_index=1,
            ),
        ],
        recap_reconciliation_mode="shadow",
    )

    assert [task.canonical_action for task in ledger.active_tasks()] == [
        "Điều chỉnh thành 23/02"
    ]
    assert ledger.diagnostics["recap_fragment_shadow_count"] == 1


def test_recap_owner_assignment_updates_resolved_task() -> None:
    ledger = reduce_task_events_to_ledger(
        [
            TaskEvent(
                "E-1", "TASK_COMMITMENT", ["C-1"], "Viết script", "Lan",
                extraction_source="RULE", order_index=1,
            ),
            TaskEvent(
                "E-2", "OWNER_ASSIGN", ["C-2"], "Viết script", "Minh",
                extraction_source="RULE_RECAP", order_index=2,
            ),
        ]
    )

    assert len(ledger.active_tasks()) == 1
    assert ledger.active_tasks()[0].assignees == {"Lan", "Minh"}
    assert ledger.diagnostics["recap_fragment_shadow_count"] == 0
