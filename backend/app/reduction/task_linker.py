"""Resolve task-event targets with explicit, alias-aware, fail-closed rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Protocol

from ..models import TaskEvent, TaskState
from ..preprocessing.unicode_normalizer import normalize_for_match
from ..utils.text_similarity import similarity, token_overlap

if TYPE_CHECKING:
    from .task_ledger import TaskLedger


TERMINAL_STATUSES = {"CANCELLED", "REJECTED"}
IDENTITY_EVENTS = {
    "TASK_CREATE", "TASK_COMMITMENT", "OWNER_ASSIGN", "TASK_REFERENCE",
}


def _explicit_list_item_key(value: str) -> str:
    """Return a stable row marker for labels such as ``Task 3:`` or ``B -``."""

    import re

    normalized = normalize_for_match(value).strip()
    match = re.match(
        r"^(?:task\s*)?(?P<key>\d{1,3}|[a-z])\s*[:\-–—]\s*",
        normalized,
        re.I,
    )
    return match.group("key").casefold() if match else ""
MUTATION_EVENTS = {
    "TASK_CANCEL",
    "TASK_REJECT",
    "OWNER_REASSIGN",
    "DEADLINE_SET",
    "DEADLINE_REPLACE",
}


class TaskLike(Protocol):
    task_id: str
    status: str
    last_order_index: int


@dataclass(frozen=True)
class LinkResult:
    task_id: str | None
    status: str
    reason: str
    candidate_task_ids: tuple[str, ...] = ()


def _task_name(state: TaskLike) -> str:
    return str(
        getattr(state, "canonical_action", "")
        or getattr(state, "task_name", "")
    )


def _assignee(state: TaskLike) -> str:
    assignees = getattr(state, "assignees", None)
    if assignees is not None:
        return "; ".join(sorted(assignees))
    return str(getattr(state, "assignee", ""))


def _aliases(state: TaskLike) -> set[str]:
    aliases = set(getattr(state, "aliases", set()))
    aliases.add(_task_name(state))
    return {normalize_for_match(value) for value in aliases if value}


def _semantic_normalize(value: str) -> str:
    text = normalize_for_match(value).replace('"', " ")
    replacements = (
        (r"\bdata\s*set\b", "du lieu"),
        (r"\bdataset\b", "du lieu"),
        (r"\bbo du lieu\b", "du lieu"),
        (r"\bmodule\b", ""),
    )
    import re
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return " ".join(text.split())


def _task_similarity(
    event: TaskEvent,
    state: TaskLike,
    *,
    enforce_owner: bool = True,
) -> float:
    state_owner = _assignee(state)
    if (
        enforce_owner
        and event.assignee
        and state_owner
        and event.event_type not in {"OWNER_ASSIGN", "OWNER_REASSIGN"}
        and normalize_for_match(event.assignee) not in {
            normalize_for_match(item) for item in state_owner.split(";")
        }
    ):
        return 0.0
    source = _semantic_normalize(event.action_text or event.related_task_hint)
    if not source:
        return 0.0
    if event.event_type in IDENTITY_EVENTS:
        source_row = _explicit_list_item_key(
            event.action_text or event.related_task_hint
        )
        state_rows = {
            _explicit_list_item_key(value)
            for value in _aliases(state)
            if _explicit_list_item_key(value)
        }
        if source_row and state_rows and source_row not in state_rows:
            return 0.0
    scores = []
    for target in {_semantic_normalize(item) for item in _aliases(state)}:
        if not target:
            continue
        if source in target or target in source:
            scores.append(0.85)
        scores.append(max(similarity(source, target), token_overlap(source, target)))
    score = max(scores, default=0.0)
    if (
        event.event_type == "OWNER_ASSIGN"
        and event.assignee
        and state_owner
        and score >= 0.45
    ):
        normalized_owner = normalize_for_match(event.assignee)
        state_owners = {
            normalize_for_match(item) for item in state_owner.split(";")
        }
        if normalized_owner in state_owners:
            # A named owner is supporting evidence for assignment linking, but
            # a different owner is not a veto because OWNER_ASSIGN may add a
            # collaborator to an existing task.
            score = min(1.0, score + 0.30)
    return score


def _resolve(event: TaskEvent, states: Mapping[str, TaskLike]) -> LinkResult:
    explicit_id = event.related_task_id.strip()
    if explicit_id:
        if explicit_id in states:
            state = states[explicit_id]
            if (
                event.event_type in IDENTITY_EVENTS
                and state.status in TERMINAL_STATUSES
            ):
                return LinkResult(
                    explicit_id,
                    "TERMINAL_REPLAY",
                    "Explicit target is terminal and V1 has no TASK_REOPEN",
                    (explicit_id,),
                )
            return LinkResult(explicit_id, "EXACT_ID", "related_task_id matched", (explicit_id,))
        return LinkResult(None, "UNRESOLVED", "related_task_id does not exist", ())

    source = normalize_for_match(event.action_text or event.related_task_hint)
    exact_alias_ids = tuple(
        sorted(
            task_id
            for task_id, state in states.items()
            if source and source in _aliases(state)
        )
    )
    if len(exact_alias_ids) == 1:
        task_id = exact_alias_ids[0]
        if (
            event.event_type in IDENTITY_EVENTS
            and states[task_id].status in TERMINAL_STATUSES
        ):
            return LinkResult(
                task_id,
                "TERMINAL_REPLAY",
                "Exact alias belongs to a terminal task",
                exact_alias_ids,
            )
        if states[task_id].status not in TERMINAL_STATUSES:
            return LinkResult(task_id, "EXACT_ALIAS", "normalized alias matched", exact_alias_ids)
    if len(exact_alias_ids) > 1:
        return LinkResult(None, "UNRESOLVED", "alias matches multiple tasks", exact_alias_ids)

    if (
        event.extraction_source == "RULE_FINAL_RECAP"
        and event.event_type in IDENTITY_EVENTS
    ):
        return LinkResult(None, "UNRESOLVED", "explicit final-list siblings remain distinct", ())

    candidates = [state for state in states.values() if state.status not in TERMINAL_STATUSES]
    if (
        event.event_type in {"TASK_CREATE", "TASK_COMMITMENT", "OWNER_ASSIGN"}
        and event.deadline_mention_id
        and event.assignee
    ):
        owner = normalize_for_match(event.assignee)
        owner_matches = [
            state
            for state in candidates
            if owner in {
                normalize_for_match(item)
                for item in _assignee(state).split(";")
            }
        ]
        if len(owner_matches) == 1 and _task_similarity(event, owner_matches[0]) >= 0.45:
            state = owner_matches[0]
            return LinkResult(
                state.task_id,
                "UNIQUE_SEMANTIC",
                "one same-owner task shares the updated work object",
                (state.task_id,),
            )
    ranked = sorted(
        (
            (_task_similarity(event, state), state.last_order_index, state.task_id)
            for state in candidates
        ),
        reverse=True,
    )
    if ranked:
        best_score, _, best_id = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        # A create assertion is an identity boundary, not merely an update.
        # Keep similarly worded sibling deliverables distinct unless
        # they are exact aliases or an exceptionally close semantic match.
        # References retain the looser linker because later cancellation and
        # corrections often use shortened forms of an established label.
        # Duplicate cleanup is deferred to guarded reconciliation.
        threshold = (
            0.72
            if event.event_type in MUTATION_EVENTS
            else 0.84
            if event.event_type == "TASK_CREATE"
            else 0.58
        )
        margin = (
            0.12
            if event.event_type in MUTATION_EVENTS
            else 0.10
            if event.event_type == "TASK_CREATE"
            else 0.05 if event.event_type == "OWNER_ASSIGN" else 0.08
        )
        plausible = tuple(item[2] for item in ranked if item[0] >= threshold - margin)
        if best_score >= threshold and best_score - second_score >= margin:
            return LinkResult(
                best_id,
                "UNIQUE_SEMANTIC",
                f"unique semantic target score={best_score:.3f}",
                plausible or (best_id,),
            )
        if plausible:
            return LinkResult(None, "UNRESOLVED", "no unique semantic target", plausible)

    if event.event_type == "DEADLINE_SET" and event.assignee:
        owner = normalize_for_match(event.assignee)
        owner_matches = [
            state
            for state in candidates
            if owner in {
                normalize_for_match(item)
                for item in _assignee(state).split(";")
            }
            and 0 <= event.order_index - state.last_order_index <= 20
        ]
        if len(owner_matches) == 1:
            state = owner_matches[0]
            return LinkResult(
                state.task_id,
                "UNIQUE_SEMANTIC",
                "one recently active task has the explicitly named owner",
                (state.task_id,),
            )
    if event.event_type == "DEADLINE_REPLACE":
        immediate = [
            state
            for state in candidates
            if 0 <= event.order_index - state.last_order_index <= 5
        ]
        if len(candidates) == 1 and len(immediate) == 1:
            return LinkResult(
                immediate[0].task_id,
                "UNIQUE_SEMANTIC",
                "one active task is the immediate correction antecedent",
                (immediate[0].task_id,),
            )

    # Positive events are checked against terminal history using a stricter
    # threshold.  This prevents an implicit reopen while allowing genuinely
    # different work to receive a new stable ID.
    if event.event_type in IDENTITY_EVENTS:
        terminal_ranked = sorted(
            (
                (_task_similarity(event, state, enforce_owner=False), state.task_id)
                for state in states.values()
                if state.status in TERMINAL_STATUSES
            ),
            reverse=True,
        )
        if terminal_ranked:
            best_score, best_id = terminal_ranked[0]
            second_score = terminal_ranked[1][0] if len(terminal_ranked) > 1 else 0.0
            if best_score >= 0.80 and best_score - second_score >= 0.15:
                return LinkResult(
                    best_id,
                    "TERMINAL_REPLAY",
                    "positive mention semantically replays a terminal task",
                    (best_id,),
                )

    return LinkResult(None, "UNRESOLVED", "no sufficiently specific target", ())


def resolve_task(event: TaskEvent, states: Mapping[str, TaskState]) -> LinkResult:
    """Backward-compatible resolver for callers that still own TaskState maps."""

    return _resolve(event, states)


def resolve_event_target(event: TaskEvent, ledger: "TaskLedger") -> LinkResult:
    return _resolve(event, ledger.tasks)


def find_task_id(event: TaskEvent, states: dict[str, TaskState]) -> str | None:
    return resolve_task(event, states).task_id


def find_terminal_task_id(
    event: TaskEvent,
    states: dict[str, TaskState],
    *,
    minimum_score: float = 0.80,
    minimum_margin: float = 0.15,
) -> str | None:
    terminal = [state for state in states.values() if state.status in TERMINAL_STATUSES]
    ranked = sorted(
        ((_task_similarity(event, state, enforce_owner=False), state.task_id) for state in terminal),
        reverse=True,
    )
    if not ranked:
        return None
    best_score, best_id = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score >= minimum_score and best_score - second_score >= minimum_margin:
        return best_id
    return None
