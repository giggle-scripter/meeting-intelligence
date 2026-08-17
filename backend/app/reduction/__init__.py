"""Event linking and state reduction."""

from .event_deduplicator import deduplicate_events
from .reconciliation import (
    ReconciliationOperation,
    ReconciliationResult,
    build_deterministic_reconciliation_operations,
    reconcile_ledger,
)
from .reducer import reduce_task_events, reduce_task_events_to_ledger
from .provisional_identity import (
    extract_provisional_task_references,
    is_promotable_task_reference,
)
from .task_ledger import LedgerTask, TaskLedger
from .task_linker import LinkResult, MUTATION_EVENTS, resolve_event_target, resolve_task

__all__ = [
    "LedgerTask", "LinkResult", "MUTATION_EVENTS", "TaskLedger",
    "ReconciliationOperation", "ReconciliationResult",
    "deduplicate_events", "reduce_task_events", "reduce_task_events_to_ledger",
    "extract_provisional_task_references",
    "is_promotable_task_reference",
    "build_deterministic_reconciliation_operations", "reconcile_ledger",
    "resolve_event_target", "resolve_task",
]
