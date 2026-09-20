"""Trusted anchors for temporal expressions.

This module deliberately accepts only an exact, caller-supplied event ID.  It
does not attempt semantic or keyword matching because that would turn a date
calculation into a guessed fact.
"""

from __future__ import annotations

from collections.abc import Mapping


MEETING_DATE_ANCHOR = "MEETING_DATE"


def resolve_trusted_anchor(
    anchor_event: str | None,
    event_anchors: Mapping[str, str] | None,
) -> str | None:
    if not anchor_event or not event_anchors:
        return None
    if anchor_event in event_anchors:
        return event_anchors[anchor_event]
    # Event IDs are identifiers, not natural-language labels. Case-folding is
    # a transport normalization only; it never selects a similar event.
    normalized = anchor_event.casefold()
    for event_id, value in event_anchors.items():
        if event_id.casefold() == normalized:
            return value
    return None
