"""Build and parse a self-contained meeting upload package."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..models import MeetingNoteInput


METADATA_START = "=== MEETING METADATA ==="
METADATA_END = "=== END MEETING METADATA ==="
NOTE_START = "=== MEETING NOTE ==="
NOTE_END = "=== END MEETING NOTE ==="
TRANSCRIPT_START = "=== TRANSCRIPT ==="


@dataclass(frozen=True)
class MeetingPackageMetadata:
    meeting_id: str = ""
    meeting_title: str = ""
    meeting_date: str = ""


def _one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def build_meeting_package(
    transcript: str,
    note: str | None,
    *,
    meeting_id: str = "",
    meeting_title: str = "",
    meeting_date: str = "",
) -> str:
    metadata = MeetingPackageMetadata(
        _one_line(meeting_id),
        _one_line(meeting_title),
        _one_line(meeting_date),
    )
    has_metadata = any(
        (metadata.meeting_id, metadata.meeting_title, metadata.meeting_date)
    )
    has_note = bool(note and note.strip())
    if not has_metadata and not has_note:
        return transcript

    sections: list[str] = []
    if has_metadata:
        sections.extend((
            METADATA_START,
            f"meeting_id: {metadata.meeting_id}",
            f"meeting_title: {metadata.meeting_title}",
            f"meeting_date: {metadata.meeting_date}",
            METADATA_END,
        ))
    if has_note:
        sections.extend((NOTE_START, note.strip(), NOTE_END))
    sections.extend((TRANSCRIPT_START, transcript.lstrip()))
    return "\n".join(sections)


def parse_meeting_package(
    value: str,
) -> tuple[str, MeetingNoteInput | None, MeetingPackageMetadata]:
    remaining = value.lstrip()
    metadata = MeetingPackageMetadata()
    metadata_pattern = re.compile(
        rf"^{re.escape(METADATA_START)}\s*\n(?P<metadata>.*?)\n"
        rf"{re.escape(METADATA_END)}\s*\n(?P<remaining>.*)$",
        re.S,
    )
    metadata_match = metadata_pattern.match(remaining)
    if metadata_match:
        fields: dict[str, str] = {}
        for raw_line in metadata_match.group("metadata").splitlines():
            key, separator, raw_value = raw_line.partition(":")
            normalized_key = key.strip().casefold()
            if separator and normalized_key in {
                "meeting_id", "meeting_title", "meeting_date"
            }:
                fields[normalized_key] = raw_value.strip()
        metadata = MeetingPackageMetadata(
            meeting_id=fields.get("meeting_id", ""),
            meeting_title=fields.get("meeting_title", ""),
            meeting_date=fields.get("meeting_date", ""),
        )
        remaining = metadata_match.group("remaining")

    note_pattern = re.compile(
        rf"^{re.escape(NOTE_START)}\s*\n(?P<note>.*?)\n"
        rf"{re.escape(NOTE_END)}\s*\n(?P<remaining>.*)$",
        re.S,
    )
    note_match = note_pattern.match(remaining)
    note = None
    if note_match:
        note_text = note_match.group("note").strip()
        note = (
            MeetingNoteInput(note_text, "Thư ký", "SECRETARY")
            if note_text else None
        )
        remaining = note_match.group("remaining")

    transcript_pattern = re.compile(
        rf"^{re.escape(TRANSCRIPT_START)}\s*\n(?P<transcript>.*)$",
        re.S,
    )
    transcript_match = transcript_pattern.match(remaining)
    if transcript_match:
        remaining = transcript_match.group("transcript")
    elif metadata_match or note_match:
        # A partial package is invalid; leave it untouched so callers never
        # silently discard user content.
        return value, None, MeetingPackageMetadata()
    return remaining, note, metadata


def split_meeting_package(value: str) -> tuple[str, MeetingNoteInput | None]:
    """Backward-compatible note/transcript split used by older callers."""

    transcript, note, _ = parse_meeting_package(value)
    return transcript, note
