"""Task registry entity retained through the full audit trail."""

from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class TaskEntity:
    task_id: str
    canonical_action: str
    aliases: list[str] = field(default_factory=list)
    explicit_labels: set[str] = field(default_factory=set)
    assignee: str = ""
    deadline_mention_id: str = ""
    status: TaskStatus = TaskStatus.ACTIVE
    created_order: int = 0
    last_updated_order: int = 0
    source_clause_ids: list[str] = field(default_factory=list)
    event_history: list[str] = field(default_factory=list)
