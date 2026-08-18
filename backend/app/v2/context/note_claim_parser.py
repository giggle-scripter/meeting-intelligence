"""Deterministically parse note rows into auditable claims, never events."""

from __future__ import annotations

from hashlib import sha256

from backend.app.models import MeetingInput
from backend.app.v2.models import NoteClaim, NoteLineKind, ParsedNoteLine


def _claim_id(meeting_id: str, index: int, text: str) -> str:
    digest = sha256(f"{meeting_id}\x1f{index}\x1f{text}".encode("utf-8")).hexdigest()[:16]
    return f"NCLAIM-{digest}"


def parse_note_claims(
    meeting: MeetingInput,
    note_lines: list[ParsedNoteLine],
) -> list[NoteClaim]:
    """Keep only semantically meaningful rows and use replay-stable IDs."""

    if meeting.meeting_note is None:
        return []
    claims: list[NoteClaim] = []
    for index, line in enumerate(note_lines):
        if line.kind not in {NoteLineKind.ACTION_HINT, NoteLineKind.STATE_HINT}:
            continue
        claims.append(NoteClaim(
            note_claim_id=_claim_id(meeting.meeting_id, index, line.normalized_text),
            note_line_id=line.line_id,
            text=line.raw_text,
            action_hint=(line.task_labels[0] if line.task_labels else line.raw_text),
            owner_hint=line.owner_hints[0] if line.owner_hints else None,
            date_hint=line.deadline_hints[0] if line.deadline_hints else None,
            operation_hint=line.operation_hints[0] if line.operation_hints else None,
            source=meeting.meeting_note.source,
        ))
    return claims
