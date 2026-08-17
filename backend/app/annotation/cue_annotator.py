"""Annotate clauses with deterministic semantic cues."""

import re

from ..models import Clause, ClauseAnnotation
from .cue_patterns import (
    ACTION_VERBS, ASSIGNMENT_PATTERNS, BRAINSTORM_PATTERNS, CANCELLATION_PATTERNS,
    COMMITMENT_PATTERNS, CONFIRMATION_PATTERNS, CORRECTION_PATTERNS,
    FUTURE_DISCUSSION_PATTERNS, HYPOTHETICAL_PATTERNS, PAST_COMPLETED_PATTERNS,
    REJECTION_PATTERNS, ADMIN_FOLLOWUP_PATTERNS, EXPLICIT_TASK_LABEL_RE,
    PROGRESS_UPDATE_PATTERNS, SUGGESTION_ONLY_PATTERNS, is_root_question,
)


PATTERN_GROUPS = {
    "FIRST_PERSON_COMMITMENT": COMMITMENT_PATTERNS,
    "DIRECT_ASSIGNMENT": ASSIGNMENT_PATTERNS,
    "CONFIRMATION": CONFIRMATION_PATTERNS,
    "CORRECTION": CORRECTION_PATTERNS,
    "CANCELLATION": CANCELLATION_PATTERNS,
    "REJECTION": REJECTION_PATTERNS,
    "BRAINSTORM": BRAINSTORM_PATTERNS,
    "HYPOTHETICAL": HYPOTHETICAL_PATTERNS,
    "PAST_COMPLETED": PAST_COMPLETED_PATTERNS,
    "FUTURE_DISCUSSION": FUTURE_DISCUSSION_PATTERNS,
    "SUGGESTION_ONLY": SUGGESTION_ONLY_PATTERNS,
    "PROGRESS_UPDATE": PROGRESS_UPDATE_PATTERNS,
    "ADMIN_FOLLOWUP": ADMIN_FOLLOWUP_PATTERNS,
}

SCORES = {
    "FIRST_PERSON_COMMITMENT": 3, "DIRECT_ASSIGNMENT": 3, "CONFIRMATION": 2,
    "DATE_MENTION": 2, "CORRECTION": 2, "CANCELLATION": 3,
    "REJECTION": 2, "ACTION_VERB": 1, "BRAINSTORM": -3,
    "HYPOTHETICAL": -3, "PAST_COMPLETED": -3, "FUTURE_DISCUSSION": -3,
    "ROOT_QUESTION": -3, "SUGGESTION_ONLY": -3, "PROGRESS_UPDATE": -3,
    "ADMIN_FOLLOWUP": -3, "EXPLICIT_TASK_LABEL": 1,
}


def annotate_clause(clause: Clause, has_date_mention: bool = False) -> ClauseAnnotation:
    flags: set[str] = set()
    text = clause.text_normalized
    for flag, patterns in PATTERN_GROUPS.items():
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            flags.add(flag)
    if is_root_question(clause.text_raw):
        flags.add("ROOT_QUESTION")
    if EXPLICIT_TASK_LABEL_RE.search(clause.text_raw):
        flags.add("EXPLICIT_TASK_LABEL")
    if any(re.search(rf"\b{re.escape(verb)}\b", text) for verb in ACTION_VERBS):
        flags.add("ACTION_VERB")
    if has_date_mention:
        flags.add("DATE_MENTION")
    score = sum(SCORES.get(flag, 0) for flag in flags)
    positive = sum(1 for flag in flags if SCORES.get(flag, 0) > 0)
    confidence = min(0.99, 0.45 + positive * 0.12)
    return ClauseAnnotation(clause.clause_id, flags, confidence, score)


def annotate_clauses(clauses: list[Clause], date_clause_ids: set[str] | None = None) -> dict[str, ClauseAnnotation]:
    date_clause_ids = date_clause_ids or set()
    return {clause.clause_id: annotate_clause(clause, clause.clause_id in date_clause_ids) for clause in clauses}
