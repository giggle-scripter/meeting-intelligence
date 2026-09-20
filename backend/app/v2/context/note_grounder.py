"""Bounded transcript evidence and chronology checks for parsed note claims."""

from __future__ import annotations

from backend.app.models import Clause, ClauseAnnotation, DateMention
from backend.app.preprocessing.unicode_normalizer import normalize_for_match
from backend.app.utils.text_similarity import token_overlap
from backend.app.v2.models import NoteClaim, NoteClaimGrounding, NoteGroundingLevel

from .keywords import extract_keywords, keyword_overlap


def _supports_hint(hint: str | None, text: str) -> bool:
    return bool(hint and normalize_for_match(hint) in normalize_for_match(text))


def _supports_operation(hint: str | None, text: str) -> bool:
    if hint is None:
        return True
    normalized = normalize_for_match(text)
    if hint == "CANCEL":
        return any(word in normalized for word in ("huy", "bo", "cancel"))
    if hint == "STATE":
        return any(word in normalized for word in ("chuyen", "doi", "deadline", "han", "due"))
    return _supports_hint(hint, text)


def _contradictions(
    claim: NoteClaim, selected: list[Clause], annotations: dict[str, ClauseAnnotation],
) -> tuple[str, ...]:
    if not selected:
        return ()
    latest = max(item.order_index for item in selected)
    reference = claim.action_hint or claim.text
    contradictory: list[str] = []
    for clause in selected:
        if clause.order_index < latest:
            continue
        flags = annotations.get(clause.clause_id, ClauseAnnotation(clause.clause_id)).flags
        if not ({"CANCELLATION", "REJECTION", "CORRECTION"} & flags):
            continue
        if token_overlap(reference, clause.text_raw) >= 0.25:
            contradictory.append(clause.clause_id)
    return tuple(dict.fromkeys(contradictory))


def ground_note_claims(
    claims: list[NoteClaim], clauses: list[Clause], annotations: dict[str, ClauseAnnotation],
    mentions: dict[str, DateMention], *, max_clauses: int = 8,
    threshold: float = 0.72, margin: float = 0.12,
) -> list[NoteClaimGrounding]:
    """Use bounded lexical/semantic proxy retrieval; no note can become evidence."""

    result: list[NoteClaimGrounding] = []
    for claim in claims:
        keywords = extract_keywords(claim.text)
        ranked: list[tuple[float, float, Clause]] = []
        for clause in clauses:
            lexical = keyword_overlap(keywords, extract_keywords(clause.text_raw))
            semantic = max(lexical, token_overlap(claim.action_hint or claim.text, clause.text_raw))
            if semantic:
                ranked.append((semantic, lexical, clause))
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2].order_index, item[2].clause_id))
        selected = [item[2] for item in ranked[:max_clauses]]
        top_score = ranked[0][0] if ranked else 0.0
        lexical_score = ranked[0][1] if ranked else 0.0
        score_margin = max(0.0, top_score - (ranked[1][0] if len(ranked) > 1 else 0.0))
        joined = " ".join(item.text_raw for item in selected)
        action_supported = bool(selected and top_score >= threshold)
        owner_supported = claim.owner_hint is None or _supports_hint(claim.owner_hint, joined)
        date_supported = claim.date_hint is None or _supports_hint(claim.date_hint, joined)
        operation_supported = _supports_operation(claim.operation_hint, joined)
        contradictions = _contradictions(claim, selected, annotations)
        if contradictions:
            level, reasons = NoteGroundingLevel.CONTRADICTED, ("LATER_TRANSCRIPT_CONTRADICTION",)
        elif action_supported and score_margin >= margin and owner_supported and date_supported and operation_supported:
            level, reasons = NoteGroundingLevel.FULL_GROUNDED, ("ALL_CLAIM_FIELDS_GROUNDED",)
        elif action_supported:
            level, reasons = NoteGroundingLevel.PARTIAL_GROUNDED, ("CLAIM_FIELD_UNGROUNDED",)
        else:
            level, reasons = NoteGroundingLevel.NOTE_ONLY, ("INSUFFICIENT_TRANSCRIPT_SUPPORT",)
        result.append(NoteClaimGrounding(
            note_claim_id=claim.note_claim_id, level=level,
            transcript_clause_ids=tuple(item.clause_id for item in selected),
            semantic_score=top_score, lexical_score=lexical_score, margin=score_margin,
            action_supported=action_supported, owner_supported=owner_supported,
            date_supported=date_supported, operation_supported=operation_supported,
            contradiction_clause_ids=contradictions, reasons=reasons,
        ))
    return result
