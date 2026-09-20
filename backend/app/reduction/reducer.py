"""Reduce ordered task events through the stable meeting ledger."""

from ..models import TaskEvent, TaskState
from .task_ledger import TaskLedger


def reduce_task_events_to_ledger(
    events: list[TaskEvent],
    *,
    ledger: TaskLedger | None = None,
    recap_reconciliation_mode: str = "off",
) -> TaskLedger:
    if recap_reconciliation_mode not in {"off", "shadow"}:
        raise ValueError("recap_reconciliation_mode must be off or shadow")
    result = ledger or TaskLedger(recap_reconciliation_mode=recap_reconciliation_mode)
    for event in sorted(events, key=lambda item: (item.order_index, item.event_id)):
        result.apply(event)
    return result


def reduce_task_events(
    events: list[TaskEvent],
    *,
    diagnostics: dict[str, int] | None = None,
) -> list[TaskState]:
    ledger = reduce_task_events_to_ledger(events)
    if diagnostics is not None:
        diagnostics.update(ledger.diagnostics)
    return ledger.to_task_states()
