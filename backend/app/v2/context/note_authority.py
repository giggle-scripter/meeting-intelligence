"""Map a dual-view grounding result to bounded downstream authority."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.v2.models import NoteClaim, NoteClaimGrounding, NoteGroundingLevel


@dataclass(frozen=True)
class NoteAuthorityDecision:
    note_claim_id: str
    candidate_signal: str
    direct_event_allowed: bool
    ai_proposal_allowed: bool


def decide_note_authority(claim: NoteClaim, grounding: NoteClaimGrounding) -> NoteAuthorityDecision:
    if grounding.level is NoteGroundingLevel.CONTRADICTED:
        return NoteAuthorityDecision(claim.note_claim_id, "NEGATIVE", False, False)
    if grounding.level is NoteGroundingLevel.FULL_GROUNDED:
        return NoteAuthorityDecision(claim.note_claim_id, "STRONG", False, True)
    if grounding.level is NoteGroundingLevel.PARTIAL_GROUNDED:
        return NoteAuthorityDecision(claim.note_claim_id, "MEDIUM", False, True)
    if claim.source == "AUTO_OVERVIEW":
        return NoteAuthorityDecision(claim.note_claim_id, "CONTEXT_ONLY", False, False)
    return NoteAuthorityDecision(claim.note_claim_id, "WEAK", False, True)
