"""Recap snapshots carry explicit scope instead of implicit authority."""

from dataclasses import dataclass, field
from enum import Enum


class RecapScope(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RecapRow:
    action: str
    explicit_task_label: str = ""
    assignee: str = ""
    deadline_mention_id: str = ""
    source_clause_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecapSnapshot:
    scope: RecapScope
    rows: tuple[RecapRow, ...] = field(default_factory=tuple)
    source_clause_ids: tuple[str, ...] = field(default_factory=tuple)
