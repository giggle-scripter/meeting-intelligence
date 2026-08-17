"""Untrusted meeting-note and transcript-context models for V2."""

from dataclasses import dataclass, field
from enum import Enum


class NoteLineKind(str, Enum):
    HEADER = "HEADER"
    TOPIC = "TOPIC"
    ACTION_HINT = "ACTION_HINT"
    STATE_HINT = "STATE_HINT"
    QUESTION = "QUESTION"
    NOISE = "NOISE"
    UNKNOWN = "UNKNOWN"


class RelevanceClass(str, Enum):
    MANDATORY = "MANDATORY"
    LIKELY = "LIKELY"
    CONTEXT = "CONTEXT"
    NOISE = "NOISE"


@dataclass(frozen=True)
class ParsedNoteLine:
    line_id: str
    raw_text: str
    normalized_text: str
    kind: NoteLineKind
    keywords: tuple[str, ...] = ()
    owner_hints: tuple[str, ...] = ()
    task_labels: tuple[str, ...] = ()
    deadline_hints: tuple[str, ...] = ()
    operation_hints: tuple[str, ...] = ()
    confidence: float = 0.0


@dataclass(frozen=True)
class TopicHint:
    topic_id: str
    label: str
    keywords: tuple[str, ...]
    source: str
    confidence: float


@dataclass(frozen=True)
class GroundedNoteHint:
    note_line_id: str
    clause_ids: tuple[str, ...]
    grounding_score: float
    score_margin: float
    status: str


@dataclass(frozen=True)
class NoteCue:
    """A note hint routed to a transcript clause; never standalone evidence."""

    clause_id: str
    note_line_id: str
    kind: NoteLineKind
    status: str
    grounding_score: float
    score_margin: float
    text_hint: str = ""
    keywords: tuple[str, ...] = ()
    owner_hints: tuple[str, ...] = ()
    task_labels: tuple[str, ...] = ()
    deadline_hints: tuple[str, ...] = ()
    operation_hints: tuple[str, ...] = ()

    @property
    def local_usable(self) -> bool:
        return self.status == "GROUNDED"

    @property
    def ai_usable(self) -> bool:
        return self.status in {"GROUNDED", "AMBIGUOUS"}


@dataclass(frozen=True)
class ClauseRelevance:
    clause_id: str
    relevance_class: RelevanceClass
    score: float
    reasons: tuple[str, ...]
    topic_ids: tuple[str, ...] = ()
    note_line_ids: tuple[str, ...] = ()


@dataclass
class MeetingContext:
    note_present: bool
    note_source: str
    note_lines: list[ParsedNoteLine] = field(default_factory=list)
    topics: list[TopicHint] = field(default_factory=list)
    grounded_hints: list[GroundedNoteHint] = field(default_factory=list)
    clause_relevance: dict[str, ClauseRelevance] = field(default_factory=dict)
