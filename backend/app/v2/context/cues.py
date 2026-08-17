"""Convert grounded note hints into routing cues for transcript clauses."""

from __future__ import annotations

from dataclasses import replace

from ...models import ClauseAnnotation
from ..models import MeetingContext, NoteCue, NoteLineKind


def build_note_cue_index(context: MeetingContext) -> dict[str, tuple[NoteCue, ...]]:
    """Index usable note hints by clause without copying note text into evidence."""

    lines = {line.line_id: line for line in context.note_lines}
    indexed: dict[str, list[NoteCue]] = {}
    for grounded in context.grounded_hints:
        if grounded.status not in {"GROUNDED", "AMBIGUOUS"}:
            continue
        line = lines.get(grounded.note_line_id)
        if line is None or line.kind not in {
            NoteLineKind.ACTION_HINT,
            NoteLineKind.STATE_HINT,
        }:
            continue
        for clause_id in grounded.clause_ids:
            indexed.setdefault(clause_id, []).append(
                NoteCue(
                    clause_id=clause_id,
                    note_line_id=line.line_id,
                    kind=line.kind,
                    status=grounded.status,
                    grounding_score=grounded.grounding_score,
                    score_margin=grounded.score_margin,
                    text_hint=line.raw_text,
                    keywords=line.keywords,
                    owner_hints=line.owner_hints,
                    task_labels=line.task_labels,
                    deadline_hints=line.deadline_hints,
                    operation_hints=line.operation_hints,
                )
            )
    return {
        clause_id: tuple(sorted(cues, key=lambda cue: cue.grounding_score, reverse=True))
        for clause_id, cues in indexed.items()
    }


def apply_note_cues_to_annotations(
    annotations: dict[str, ClauseAnnotation],
    cues_by_clause: dict[str, tuple[NoteCue, ...]],
) -> dict[str, ClauseAnnotation]:
    """Enrich routing flags only; note fields never become task fields."""

    result = dict(annotations)
    for clause_id, cues in cues_by_clause.items():
        annotation = result.get(clause_id)
        if annotation is None:
            continue
        flags = set(annotation.flags)
        for cue in cues:
            suffix = "ACTION" if cue.kind == NoteLineKind.ACTION_HINT else "STATE"
            flags.add(f"NOTE_{cue.status}_{suffix}")
            if cue.task_labels:
                flags.add("NOTE_TASK_LABEL_HINT")
            if cue.owner_hints:
                flags.add("NOTE_OWNER_HINT")
            if cue.deadline_hints:
                flags.add("NOTE_DEADLINE_HINT")
        result[clause_id] = replace(
            annotation,
            flags=flags,
        )
    return result
