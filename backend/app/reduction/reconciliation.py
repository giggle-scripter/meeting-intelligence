"""Validated final reconciliation over a compact task ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..models import TaskEvent
from ..preprocessing.unicode_normalizer import normalize_for_match
from ..utils.text_similarity import similarity, token_overlap
from .task_ledger import LedgerTask, TaskLedger
from .task_linker import MUTATION_EVENTS, _semantic_normalize


@dataclass(frozen=True)
class ReconciliationOperation:
    operation: Literal["MERGE_TASKS", "BIND_UNRESOLVED_EVENT", "NO_CHANGE"]
    primary_task_id: str = ""
    duplicate_task_ids: tuple[str, ...] = ()
    event_id: str = ""
    related_task_id: str = ""
    reason_clause_ids: tuple[str, ...] = ()


@dataclass
class ReconciliationResult:
    ledger: TaskLedger
    applied_operations: list[ReconciliationOperation] = field(default_factory=list)
    rejected_operations: list[dict[str, str]] = field(default_factory=list)


def _merge(ledger: TaskLedger, primary_id: str, duplicate_id: str) -> bool:
    if primary_id == duplicate_id or primary_id not in ledger.tasks or duplicate_id not in ledger.tasks:
        return False
    primary = ledger.tasks[primary_id]
    duplicate = ledger.tasks[duplicate_id]
    if primary.status in {"CANCELLED", "REJECTED"} or duplicate.status in {"CANCELLED", "REJECTED"}:
        return False
    if (
        "RULE_FINAL_RECAP" in primary.extraction_sources
        and "RULE_FINAL_RECAP" in duplicate.extraction_sources
    ):
        return False
    primary.aliases.update(duplicate.aliases | {duplicate.canonical_action})
    primary.identity_aliases.update(
        duplicate.identity_aliases | {duplicate.canonical_action}
    )
    primary.assignees.update(duplicate.assignees)
    primary.assignee_order.extend(
        assignee
        for assignee in duplicate.assignee_order
        if assignee not in primary.assignee_order
    )
    primary.source_clause_ids.update(duplicate.source_clause_ids)
    primary.event_ids.extend(
        event_id for event_id in duplicate.event_ids if event_id not in primary.event_ids
    )
    primary.deadline_event_ids.extend(
        event_id
        for event_id in duplicate.deadline_event_ids
        if event_id not in primary.deadline_event_ids
    )
    primary.deadline_mention_history.extend(
        mention_id
        for mention_id in duplicate.deadline_mention_history
        if mention_id not in primary.deadline_mention_history
    )
    primary.extraction_sources.update(duplicate.extraction_sources)
    primary_normalized = normalize_for_match(primary.canonical_action)
    duplicate_normalized = normalize_for_match(duplicate.canonical_action)
    if (
        primary_normalized
        and primary_normalized in duplicate_normalized
        and len(duplicate_normalized.split()) > len(primary_normalized.split())
    ):
        primary.canonical_action = duplicate.canonical_action
    if duplicate.last_order_index >= primary.last_order_index:
        primary.last_order_index = duplicate.last_order_index
        if duplicate.deadline_mention_id:
            primary.deadline_mention_id = duplicate.deadline_mention_id
    for alias in primary.aliases | {primary.canonical_action}:
        ledger.add_alias(primary_id, alias)
    del ledger.tasks[duplicate_id]
    for task_ids in ledger.alias_index.values():
        task_ids.discard(duplicate_id)
    ledger.diagnostics["duplicate_task_merge_count"] += 1
    return True


def _can_merge(ledger: TaskLedger, primary_id: str, duplicate_id: str) -> bool:
    if primary_id == duplicate_id or primary_id not in ledger.tasks or duplicate_id not in ledger.tasks:
        return False
    primary = ledger.tasks[primary_id]
    duplicate = ledger.tasks[duplicate_id]
    if primary.status in {"CANCELLED", "REJECTED"} or duplicate.status in {"CANCELLED", "REJECTED"}:
        return False
    return not (
        "RULE_FINAL_RECAP" in primary.extraction_sources
        and "RULE_FINAL_RECAP" in duplicate.extraction_sources
    )


def _compatible_fields(left: LedgerTask, right: LedgerTask) -> bool:
    owners_compatible = (
        not left.assignees
        or not right.assignees
        or bool(left.assignees.intersection(right.assignees))
    )
    deadlines_compatible = (
        not left.deadline_mention_id
        or not right.deadline_mention_id
        or left.deadline_mention_id == right.deadline_mention_id
    )
    return owners_compatible and deadlines_compatible


def _is_final_list_sibling(left: LedgerTask, right: LedgerTask) -> bool:
    return (
        "RULE_FINAL_RECAP" in left.extraction_sources
        and "RULE_FINAL_RECAP" in right.extraction_sources
    )


def _task_semantic_score(left: LedgerTask, right: LedgerTask) -> float:
    left_aliases = left.aliases | {left.canonical_action}
    right_aliases = right.aliases | {right.canonical_action}
    best = 0.0
    for left_value in left_aliases:
        normalized_left = _semantic_normalize(left_value)
        if not normalized_left:
            continue
        for right_value in right_aliases:
            normalized_right = _semantic_normalize(right_value)
            if not normalized_right:
                continue
            if normalized_left == normalized_right:
                return 1.0
            containment = (
                0.90
                if normalized_left in normalized_right or normalized_right in normalized_left
                else 0.0
            )
            best = max(
                best,
                containment,
                similarity(normalized_left, normalized_right),
                token_overlap(normalized_left, normalized_right),
            )
    return best


def build_deterministic_reconciliation_operations(
    ledger: TaskLedger,
    *,
    minimum_score: float = 0.84,
    minimum_margin: float = 0.08,
) -> list[ReconciliationOperation]:
    """Build conservative, non-creating duplicate merges over active tasks.

    A pair must have compatible owner/deadline state and be each other's
    unique best semantic candidate. Explicit final-list siblings are never
    eligible because their repeated wording can represent distinct rows.
    """

    active = {
        task.task_id: task
        for task in ledger.active_tasks()
    }
    operations: list[ReconciliationOperation] = []
    claimed: set[str] = set()

    exact_groups: dict[str, set[str]] = {}
    for task_id, task in active.items():
        for alias in task.aliases | {task.canonical_action}:
            normalized = _semantic_normalize(alias)
            if normalized:
                exact_groups.setdefault(normalized, set()).add(task_id)
    for task_ids in sorted(exact_groups.values(), key=lambda items: tuple(sorted(items))):
        available = sorted(task_id for task_id in task_ids if task_id not in claimed)
        if len(available) < 2:
            continue
        primary_id = available[0]
        duplicates = [
            task_id
            for task_id in available[1:]
            if not _is_final_list_sibling(active[primary_id], active[task_id])
            and _compatible_fields(active[primary_id], active[task_id])
        ]
        if not duplicates:
            continue
        operations.append(
            ReconciliationOperation(
                "MERGE_TASKS",
                primary_task_id=primary_id,
                duplicate_task_ids=tuple(duplicates),
                reason_clause_ids=tuple(sorted(
                    set().union(*(
                        active[task_id].source_clause_ids
                        for task_id in (primary_id, *duplicates)
                    ))
                )),
            )
        )
        claimed.update({primary_id, *duplicates})

    candidate_scores: dict[str, list[tuple[float, str]]] = {
        task_id: [] for task_id in active if task_id not in claimed
    }
    for left_id in sorted(candidate_scores):
        for right_id in sorted(candidate_scores):
            if left_id >= right_id:
                continue
            left = active[left_id]
            right = active[right_id]
            if _is_final_list_sibling(left, right) or not _compatible_fields(left, right):
                continue
            score = _task_semantic_score(left, right)
            if score < minimum_score:
                continue
            candidate_scores[left_id].append((score, right_id))
            candidate_scores[right_id].append((score, left_id))

    unique_best: dict[str, str] = {}
    for task_id, ranked in candidate_scores.items():
        ranked.sort(reverse=True)
        if not ranked:
            continue
        best_score, best_id = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        if best_score - second_score >= minimum_margin:
            unique_best[task_id] = best_id

    for task_id in sorted(unique_best):
        other_id = unique_best[task_id]
        if unique_best.get(other_id) != task_id:
            continue
        if task_id in claimed or other_id in claimed:
            continue
        primary_id, duplicate_id = sorted((task_id, other_id))
        reason_clause_ids = tuple(sorted(
            active[primary_id].source_clause_ids
            | active[duplicate_id].source_clause_ids
        ))
        operations.append(
            ReconciliationOperation(
                "MERGE_TASKS",
                primary_task_id=primary_id,
                duplicate_task_ids=(duplicate_id,),
                reason_clause_ids=reason_clause_ids,
            )
        )
        claimed.update({primary_id, duplicate_id})
    return operations


def reconcile_ledger(
    ledger: TaskLedger,
    operations: list[ReconciliationOperation] | None = None,
) -> ReconciliationResult:
    """Apply deterministic cleanup, then a small validated operation language.

    There is deliberately no CREATE operation.  Reconciliation can only merge
    existing IDs or bind an already-unresolved event to an existing ID.
    """

    # A deadline can precede the formal task declaration. It remains an
    # unresolved mutation during streaming reduction and is bound here only
    # when the completed ledger contains one uniquely owned nearby target.
    for event in list(ledger.unresolved_events):
        if event.event_type != "DEADLINE_SET" or not event.assignee:
            continue
        owner = normalize_for_match(event.assignee)
        matches = [
            task
            for task in ledger.active_tasks()
            if owner in {normalize_for_match(item) for item in task.assignees}
            and abs(task.created_order_index - event.order_index) <= 20
        ]
        if len(matches) != 1:
            continue
        ledger.unresolved_events.remove(event)
        rebound = TaskEvent(**{**event.__dict__, "related_task_id": matches[0].task_id})
        ledger.apply(rebound)
    ledger.diagnostics["unresolved_mutation_count"] = sum(
        event.event_type in MUTATION_EVENTS for event in ledger.unresolved_events
    )
    result = ReconciliationResult(ledger)
    for operation in operations or []:
        if operation.operation == "NO_CHANGE":
            result.applied_operations.append(operation)
            continue
        if operation.operation == "MERGE_TASKS":
            if not operation.primary_task_id or not operation.duplicate_task_ids:
                result.rejected_operations.append({"operation": "MERGE_TASKS", "reason": "MISSING_TASK_IDS"})
                continue
            if all(
                _can_merge(ledger, operation.primary_task_id, item)
                for item in operation.duplicate_task_ids
            ):
                for item in operation.duplicate_task_ids:
                    _merge(ledger, operation.primary_task_id, item)
                result.applied_operations.append(operation)
            else:
                result.rejected_operations.append({"operation": "MERGE_TASKS", "reason": "INVALID_OR_UNSAFE_MERGE"})
            continue
        if operation.operation == "BIND_UNRESOLVED_EVENT":
            event = next((item for item in ledger.unresolved_events if item.event_id == operation.event_id), None)
            if (
                event is None
                or operation.related_task_id not in ledger.tasks
                or ledger.tasks[operation.related_task_id].status in {"CANCELLED", "REJECTED"}
            ):
                result.rejected_operations.append({"operation": "BIND_UNRESOLVED_EVENT", "reason": "UNKNOWN_EVENT_OR_TASK_ID"})
                continue
            rebound = TaskEvent(**{**event.__dict__, "related_task_id": operation.related_task_id})
            ledger.unresolved_events.remove(event)
            ledger.apply(rebound)
            result.applied_operations.append(operation)
            continue
        result.rejected_operations.append({"operation": str(operation.operation), "reason": "UNKNOWN_OPERATION"})
    return result
