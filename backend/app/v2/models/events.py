"""Immutable, validated task mutation events."""

from dataclasses import dataclass
from enum import Enum


class TaskOperation(str, Enum):
    CREATE = "CREATE"
    CONFIRM = "CONFIRM"
    ASSIGN = "ASSIGN"
    REASSIGN = "REASSIGN"
    RENAME = "RENAME"
    SET_DEADLINE = "SET_DEADLINE"
    REPLACE_DEADLINE = "REPLACE_DEADLINE"
    CLEAR_DEADLINE = "CLEAR_DEADLINE"
    CANCEL = "CANCEL"
    REJECT = "REJECT"
    REOPEN = "REOPEN"


MUTATIONS = frozenset(set(TaskOperation) - {TaskOperation.CREATE})


@dataclass(frozen=True)
class TaskEventV2:
    event_id: str
    operation: TaskOperation
    anchor_clause_id: str
    source_clause_ids: tuple[str, ...]
    global_order: int
    target_task_id: str = ""
    explicit_task_label: str = ""
    target_action_hint: str = ""
    action_patch: str = ""
    assignee_patch: str = ""
    deadline_mention_id: str = ""
    confidence: float = 0.0
    extraction_source: str = ""

    def validate(self, primary_clause_ids: set[str], supplied_clause_ids: set[str], supplied_date_ids: set[str]) -> None:
        if self.anchor_clause_id not in primary_clause_ids:
            raise ValueError("event anchor must be a primary clause")
        if not self.source_clause_ids or not set(self.source_clause_ids).issubset(supplied_clause_ids):
            raise ValueError("event source clauses must exist in its segment")
        if self.deadline_mention_id and self.deadline_mention_id not in supplied_date_ids:
            raise ValueError("event deadline mention must be supplied to extraction")
        has_target = bool(self.target_task_id or self.explicit_task_label or self.target_action_hint)
        if self.operation is TaskOperation.CREATE:
            if not self.action_patch.strip():
                raise ValueError("CREATE requires a concrete action")
        elif not has_target:
            raise ValueError("task mutation requires a target reference")
