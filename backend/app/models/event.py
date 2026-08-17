"""Candidate window and task event models."""

from dataclasses import dataclass


@dataclass
class CandidateWindow:
    window_id: str
    primary_clause_ids: list[str]
    context_clause_ids: list[str]
    score: int
    extraction_strategy: str = "CONTEXT"


@dataclass
class TaskEvent:
    event_id: str
    event_type: str
    source_clause_ids: list[str]
    action_text: str = ""
    assignee: str = ""
    deadline_mention_id: str = ""
    related_task_id: str = ""
    related_task_hint: str = ""
    confidence: float = 0.0
    extraction_source: str = "RULE"
    order_index: int = 0
    anchor_clause_id: str = ""
    # A deterministic rule recognized the same mutation at the same anchor;
    # AI supplied only the stable ledger target.  This is internal provenance,
    # not provider-authored creation authority.
    corroborated_by_rule: bool = False
