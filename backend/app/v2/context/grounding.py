"""Ground note hints to transcript evidence without owner-only matching."""

from __future__ import annotations

from ...models import Clause, ClauseAnnotation, DateMention
from ...preprocessing.unicode_normalizer import normalize_for_match
from .keywords import extract_keywords, keyword_overlap
from ..models import GroundedNoteHint, NoteLineKind, ParsedNoteLine


def _label_score(note: ParsedNoteLine, clause: Clause) -> float:
    text = normalize_for_match(clause.text_raw)
    return 1.0 if any(normalize_for_match(label) in text for label in note.task_labels) else 0.0


def _owner_score(note: ParsedNoteLine, clause: Clause) -> float:
    if not note.owner_hints:
        return 0.0
    text = normalize_for_match(clause.text_raw)
    return 1.0 if any(normalize_for_match(owner) in text for owner in note.owner_hints) else 0.0


def _deadline_or_operation_score(note: ParsedNoteLine, clause: Clause, mentions: dict[str, DateMention]) -> float:
    date_match = bool(note.deadline_hints) and any(
        item.clause_id == clause.clause_id and item.purpose == "DEADLINE"
        for item in mentions.values()
    )
    operation_match = bool(note.operation_hints) and any(word in normalize_for_match(clause.text_raw) for word in ("huy", "bo", "chuyen", "doi", "deadline"))
    return 1.0 if date_match or operation_match else 0.0


def ground_note_lines(note_lines: list[ParsedNoteLine], clauses: list[Clause], annotations: dict[str, ClauseAnnotation], mentions: dict[str, DateMention], threshold: float = 0.72, margin: float = 0.12) -> list[GroundedNoteHint]:
    grounded: list[GroundedNoteHint] = []
    for note in note_lines:
        if note.kind not in {NoteLineKind.ACTION_HINT, NoteLineKind.STATE_HINT}:
            continue
        ranked: list[tuple[float, float, str]] = []
        for clause in clauses:
            clause_keywords = extract_keywords(clause.text_raw)
            action_score = keyword_overlap(note.keywords, clause_keywords)
            label_score = _label_score(note, clause)
            owner_score = _owner_score(note, clause)
            state_score = _deadline_or_operation_score(note, clause, mentions)
            # A note cannot ground solely by owner: it needs action/topic/label
            # evidence from the transcript.
            if not (action_score or label_score or state_score):
                continue
            score = 0.72 * action_score + 0.15 * label_score + 0.05 * owner_score + 0.08 * state_score
            ranked.append((score, action_score, clause.clause_id))
        ranked.sort(reverse=True)
        best_score, best_action_score, best_id = ranked[0] if ranked else (0.0, 0.0, "")
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        score_margin = best_score - second_score
        if best_score >= threshold and score_margin >= margin:
            status = "GROUNDED"
            clause_ids = (best_id,)
        elif best_score >= threshold and best_action_score >= 0.72 and len(note.keywords) >= 3:
            # Repeated discussion/recap clauses may tie. Keeping a few strongly
            # action-matched anchors is safer than selecting one arbitrarily;
            # grounding still cannot create or mutate a task by itself.
            status = "GROUNDED"
            clause_ids = tuple(
                clause_id for score, action_score, clause_id in ranked
                if score >= threshold and action_score >= 0.72
            )[:3]
        elif best_score >= 0.60:
            status = "AMBIGUOUS"
            clause_ids = (best_id,)
        else:
            status = "UNGROUNDED"
            clause_ids = ()
        grounded.append(GroundedNoteHint(note.line_id, clause_ids, best_score, score_margin, status))
    return grounded
