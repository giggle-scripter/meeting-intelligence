from backend.app.models import TaskEvent, TaskState
from backend.app.pipeline import (
    _apply_final_active_snapshot,
    _apply_long_context_creation_policy,
)


def _send_event() -> TaskEvent:
    return TaskEvent(
        "E-1",
        "TASK_COMMITMENT",
        ["C-1"],
        "Gửi file spec qua chat",
        "Lan",
        extraction_source="RULE",
    )


def test_long_scoped_communication_is_update_only() -> None:
    events = _apply_long_context_creation_policy(
        [_send_event()],
        clause_count=120,
        recap_scope="PARTIAL",
    )
    assert events[0].extraction_source == "RULE_CONTEXT"


def test_short_explicit_communication_keeps_creation_authority() -> None:
    events = _apply_long_context_creation_policy(
        [_send_event()],
        clause_count=12,
        recap_scope="PARTIAL",
    )
    assert events[0].extraction_source == "RULE"


def test_explicit_recap_communication_keeps_creation_authority() -> None:
    event = _send_event()
    event.extraction_source = "RULE_FINAL_RECAP"
    events = _apply_long_context_creation_policy(
        [event],
        clause_count=120,
        recap_scope="AUTHORITATIVE",
    )
    assert events[0].extraction_source == "RULE_FINAL_RECAP"


def test_final_active_snapshot_requires_authoritative_scope() -> None:
    states = [
        TaskState("TASK-1", "Viết API spec", "Lan"),
        TaskState("TASK-2", "Chuẩn bị UAT", "Minh"),
    ]

    assert _apply_final_active_snapshot(
        states,
        ("Viết API spec",),
        "PARTIAL",
    ) == states
    assert _apply_final_active_snapshot(
        states,
        ("Viết API spec",),
        "AUTHORITATIVE",
    ) == [states[0]]
