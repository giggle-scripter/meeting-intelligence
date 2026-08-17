"""Route obvious clauses to rules and ambiguous clauses to AI."""

from ..models import ClauseAnnotation


STATE_CHANGE = {"CORRECTION", "CANCELLATION", "REJECTION"}
TASK_INTENT = {"FIRST_PERSON_COMMITMENT", "DIRECT_ASSIGNMENT", "CONFIRMATION"}
NEGATIVE_CREATION = {
    "BRAINSTORM",
    "HYPOTHETICAL",
    "PAST_COMPLETED",
    "FUTURE_DISCUSSION",
    "ROOT_QUESTION",
    "SUGGESTION_ONLY",
    "PROGRESS_UPDATE",
    "ADMIN_FOLLOWUP",
}
NOTE_ACTION = {"NOTE_GROUNDED_ACTION", "NOTE_AMBIGUOUS_ACTION"}
NOTE_STATE = {"NOTE_GROUNDED_STATE", "NOTE_AMBIGUOUS_STATE"}
NOTE_AMBIGUOUS = {"NOTE_AMBIGUOUS_ACTION", "NOTE_AMBIGUOUS_STATE"}
NOTE_LOCAL_INTENT = {
    "FIRST_PERSON_COMMITMENT", "DIRECT_ASSIGNMENT", "CONFIRMATION", "PROGRESS_UPDATE",
}


def is_candidate(annotation: ClauseAnnotation) -> bool:
    flags = annotation.flags
    if flags & STATE_CHANGE:
        return True
    if flags & NOTE_STATE:
        return True
    if flags & NOTE_ACTION and "ACTION_VERB" in flags:
        # The note only raises a transcript clause for evaluation. It does not
        # supply the action or assignee itself.
        return True
    if flags & NEGATIVE_CREATION and not flags & {
        "FIRST_PERSON_COMMITMENT",
        "CONFIRMATION",
    }:
        return False
    return bool(flags & TASK_INTENT) and bool(
        flags & {"ACTION_VERB", "DATE_MENTION", "FIRST_PERSON_COMMITMENT"}
    )


def choose_extraction_strategy(annotation: ClauseAnnotation) -> str:
    flags = annotation.flags
    if not is_candidate(annotation):
        return "CONTEXT"
    if flags & NOTE_AMBIGUOUS:
        return "AI"
    if "NOTE_GROUNDED_STATE" in flags:
        return "RULE" if "EXPLICIT_TASK_LABEL" in flags else "AI"
    if "NOTE_GROUNDED_ACTION" in flags:
        if flags & {"ROOT_QUESTION", "SUGGESTION_ONLY", "BRAINSTORM", "HYPOTHETICAL"}:
            return "AI"
        if flags & {"PAST_COMPLETED", "FUTURE_DISCUSSION", "ADMIN_FOLLOWUP"}:
            return "CONTEXT"
        if "ACTION_VERB" in flags and flags & NOTE_LOCAL_INTENT:
            return "RULE"
        return "AI"
    if flags & STATE_CHANGE:
        # Mutation only runs locally when a stable task label gives an explicit
        # target. The reducer still verifies that target before applying it.
        return "RULE" if "EXPLICIT_TASK_LABEL" in flags else "AI"
    clear_source = bool(flags & {"FIRST_PERSON_COMMITMENT", "DIRECT_ASSIGNMENT"})
    has_action = "ACTION_VERB" in flags
    ambiguous = bool(flags & NEGATIVE_CREATION)
    if annotation.score >= 6 and clear_source and has_action and not ambiguous:
        return "RULE"
    return "AI"
