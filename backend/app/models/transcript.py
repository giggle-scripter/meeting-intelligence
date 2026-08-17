"""Transcript-domain dataclasses."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class MeetingNoteInput:
    """Optional notes; human positive assertions may be trusted by policy."""

    content: str
    author: str = ""
    source: Literal["SECRETARY", "PARTICIPANT", "MANUAL", "AUTO_OVERVIEW"] = "SECRETARY"


@dataclass(frozen=True)
class MeetingInput:
    meeting_id: str
    meeting_title: str
    meeting_date: str
    transcript_raw: str
    file_name: str = "meeting.txt"
    meeting_note: MeetingNoteInput | None = None
    meeting_date_source: Literal[
        "REQUEST",
        "PACKAGE_METADATA",
        "TRANSCRIPT_CONTEXT",
        "MEETING_NOTE_CONTEXT",
        "PROCESSING_DATE",
    ] = "REQUEST"


@dataclass
class Caption:
    caption_id: str
    speaker_raw: str
    start_ms: int | None
    end_ms: int | None
    text_raw: str
    source_caption_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.source_caption_ids:
            self.source_caption_ids = [self.caption_id]


@dataclass
class SpeakerTurn:
    turn_id: str
    speaker_id: str
    speaker_name: str
    start_ms: int | None
    end_ms: int | None
    text_raw: str
    source_caption_ids: list[str]


@dataclass
class Sentence:
    sentence_id: str
    turn_id: str
    speaker_id: str
    speaker_name: str
    start_ms: int | None
    end_ms: int | None
    text_raw: str
    text_normalized: str
    source_caption_ids: list[str]
    order_index: int = 0


@dataclass
class Clause:
    clause_id: str
    sentence_id: str
    speaker_id: str
    speaker_name: str
    start_ms: int | None
    end_ms: int | None
    text_raw: str
    text_normalized: str
    source_caption_ids: list[str] = field(default_factory=list)
    order_index: int = 0
