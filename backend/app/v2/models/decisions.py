"""Extraction decisions are separate from provider execution failures."""

from dataclasses import dataclass, field
from enum import Enum


class ExtractionDecision(str, Enum):
    EVENTS = "EVENTS"
    NO_EVENT = "NO_EVENT"
    UNRESOLVED_REFERENCE = "UNRESOLVED_REFERENCE"


class ReasonCode(str, Enum):
    EXPLICIT_COMMITMENT = "EXPLICIT_COMMITMENT"
    EXPLICIT_ASSIGNMENT = "EXPLICIT_ASSIGNMENT"
    EXPLICIT_HANDOFF = "EXPLICIT_HANDOFF"
    EXPLICIT_CANCELLATION = "EXPLICIT_CANCELLATION"
    EXPLICIT_DEADLINE_CHANGE = "EXPLICIT_DEADLINE_CHANGE"
    PROGRESS_UPDATE = "PROGRESS_UPDATE"
    PAST_WORK = "PAST_WORK"
    QUESTION_ONLY = "QUESTION_ONLY"
    SUGGESTION_ONLY = "SUGGESTION_ONLY"
    RECAP_REFERENCE = "RECAP_REFERENCE"
    VAGUE_REFERENCE = "VAGUE_REFERENCE"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"


@dataclass(frozen=True)
class PrimaryResolution:
    primary_clause_id: str
    decision: ExtractionDecision
    events: tuple["TaskEventV2", ...] = field(default_factory=tuple)
    reason_code: ReasonCode = ReasonCode.INSUFFICIENT_CONTEXT

    def __post_init__(self) -> None:
        from .events import TaskOperation

        if self.decision is ExtractionDecision.NO_EVENT and self.events:
            raise ValueError("NO_EVENT cannot contain events")
        if self.decision is ExtractionDecision.EVENTS and not self.events:
            raise ValueError("EVENTS must contain at least one event")
        if self.decision is ExtractionDecision.UNRESOLVED_REFERENCE and any(
            event.operation is TaskOperation.CREATE for event in self.events
        ):
            raise ValueError("UNRESOLVED_REFERENCE cannot create a task")
