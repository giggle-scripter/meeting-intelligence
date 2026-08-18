"""Stable, untrusted claims parsed from a meeting note."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NoteClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    note_claim_id: str = Field(min_length=1)
    note_line_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    action_hint: str | None = None
    owner_hint: str | None = None
    date_hint: str | None = None
    operation_hint: str | None = None
    source: Literal["SECRETARY", "PARTICIPANT", "MANUAL", "AUTO_OVERVIEW"]


class NoteGroundingLevel(str, Enum):
    FULL_GROUNDED = "FULL_GROUNDED"
    PARTIAL_GROUNDED = "PARTIAL_GROUNDED"
    NOTE_ONLY = "NOTE_ONLY"
    CONTRADICTED = "CONTRADICTED"


class NoteClaimGrounding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    note_claim_id: str
    level: NoteGroundingLevel
    transcript_clause_ids: tuple[str, ...] = ()
    semantic_score: float = Field(ge=0.0, le=1.0)
    lexical_score: float = Field(ge=0.0, le=1.0)
    margin: float = Field(ge=0.0, le=1.0)
    action_supported: bool
    owner_supported: bool
    date_supported: bool
    operation_supported: bool
    contradiction_clause_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
