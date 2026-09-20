"""Stable task identity and chronological event application for V1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from ..models import TaskEvent, TaskState
from ..preprocessing.unicode_normalizer import normalize_for_match
from ..utils.ids import make_id
from ..utils.text_similarity import similarity, token_overlap
from .provisional_identity import is_promotable_task_reference
from .task_linker import LinkResult, MUTATION_EVENTS, resolve_event_target


POSITIVE_EVENTS = {"TASK_CREATE", "TASK_COMMITMENT", "OWNER_ASSIGN"}
REFERENCE_EVENTS = {"TASK_REFERENCE"}
CREATION_ALLOWED_SOURCES = {
    "HUMAN_NOTE",
    "RULE",
    # This is still a transcript-grounded deterministic rule event; the note
    # only promoted a concrete progress clause for local reconsideration.
    "RULE_NOTE_CUE",
    # A concrete subject discovered deterministically from the discussion is
    # promoted only after the named participant explicitly accepts it.
    "RULE_PENDING_CONFIRMATION",
    "RULE_RECAP",
    "RULE_FINAL_RECAP",
    # Provider creation is authorized only after the Python grounding gate
    # emits this distinct source. Raw AI mutation output remains update-only.
    "AI_CREATE_PROPOSAL",
}
UPDATE_ONLY_SOURCES = {"RULE_CONTEXT", "AI"}
# ``RULE_RECAP`` is a broad, in-line recap heuristic. It may recover owner
# phrases from a progress summary, unlike ``RULE_FINAL_RECAP`` which is the
# structured final-list extractor and is allowed to establish an identity.
RECAP_UPDATE_SOURCES = {"RULE_RECAP"}


def _is_unresolved_recap_fragment(action: str) -> bool:
    """Return whether a generic recap parse contains metadata, not an action."""

    normalized = normalize_for_match(action)
    return bool(re.search(
        r"(?:\bowner\b|^dieu\s+chinh\b|^deadline\b|^task\s+[a-z0-9]+\b)",
        normalized,
    ))


def _assignee_values(value: str) -> set[str]:
    return {
        item.strip()
        for item in re.split(r"\s*;\s*", value)
        if item.strip()
    }


def identity_labels_conflict(labels: set[str]) -> bool:
    """Return whether authoritative labels describe incompatible work items."""

    normalized_labels = [
        normalize_for_match(label)
        for label in labels
        if is_promotable_task_reference(label)
        and len(normalize_for_match(label).split()) >= 3
    ]
    for index, left in enumerate(normalized_labels):
        for right in normalized_labels[index + 1:]:
            if (
                left == right
                or left in right
                or right in left
                or token_overlap(left, right) >= 0.50
                or similarity(left, right) >= 0.80
            ):
                continue
            return True
    return False


def candidate_aliases_conflict(
    canonical_action: str,
    aliases: set[str],
) -> bool:
    """Return whether a provider alias is incompatible with its canonical ID."""

    canonical = normalize_for_match(canonical_action)
    if not canonical:
        return bool(aliases)
    for alias in aliases:
        normalized = normalize_for_match(alias)
        if not normalized or normalized == canonical:
            continue
        if (
            canonical in normalized
            or normalized in canonical
            or token_overlap(canonical, normalized) >= 0.50
            or similarity(canonical, normalized) >= 0.80
        ):
            continue
        return True
    return False


@dataclass
class LedgerTask:
    task_id: str
    canonical_action: str
    aliases: set[str] = field(default_factory=set)
    identity_aliases: set[str] = field(default_factory=set)
    assignees: set[str] = field(default_factory=set)
    assignee_order: list[str] = field(default_factory=list)
    start_date: str = ""
    due_date: str = ""
    due_date_text: str = ""
    deadline_mention_id: str = ""
    deadline_event_ids: list[str] = field(default_factory=list)
    deadline_mention_history: list[str] = field(default_factory=list)
    status: str = "UNKNOWN"
    terminal_order_index: int | None = None
    created_order_index: int = 0
    last_order_index: int = 0
    source_clause_ids: set[str] = field(default_factory=set)
    reference_clause_ids: set[str] = field(default_factory=set)
    event_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    extraction_sources: set[str] = field(default_factory=set)

    def candidate_aliases(self) -> set[str]:
        """Return identity-safe aliases for provider target resolution.

        The full alias set is retained for evidence and deterministic linking,
        but loose contextual aliases must not redefine the work item exposed
        to AI. Authoritative identity labels and close paraphrases are safe.
        """

        canonical = normalize_for_match(self.canonical_action)
        safe = {self.canonical_action}
        for alias in self.aliases | self.identity_aliases:
            normalized = normalize_for_match(alias)
            if not canonical or not normalized:
                continue
            if (
                canonical in normalized
                or normalized in canonical
                or token_overlap(canonical, normalized) >= 0.50
                or similarity(canonical, normalized) >= 0.80
            ):
                safe.add(alias)
        return {item for item in safe if item.strip()}

    def to_task_state(self) -> TaskState:
        ordered_assignees = [
            item for item in self.assignee_order if item in self.assignees
        ]
        ordered_assignees.extend(
            sorted(self.assignees.difference(ordered_assignees))
        )
        return TaskState(
            task_id=self.task_id,
            task_name=self.canonical_action,
            assignee="; ".join(ordered_assignees),
            status=self.status,
            deadline_mention_id=self.deadline_mention_id,
            deadline_event_ids=list(self.deadline_event_ids),
            deadline_mention_history=list(self.deadline_mention_history),
            source_clause_ids=sorted(self.source_clause_ids),
            last_order_index=self.last_order_index,
            confidence=self.confidence,
            extraction_sources=set(self.extraction_sources),
        )


@dataclass
class TaskLedger:
    recap_reconciliation_mode: str = "off"
    tasks: dict[str, LedgerTask] = field(default_factory=dict)
    alias_index: dict[str, set[str]] = field(default_factory=dict)
    unresolved_events: list[TaskEvent] = field(default_factory=list)
    blocked_events: list[TaskEvent] = field(default_factory=list)
    diagnostics: dict[str, int] = field(
        default_factory=lambda: {
            "ledger_task_created_count": 0,
            "ledger_task_updated_count": 0,
            "exact_id_link_count": 0,
            "exact_alias_link_count": 0,
            "semantic_link_count": 0,
            "unresolved_mutation_count": 0,
            "terminal_replay_blocked_count": 0,
            "duplicate_task_merge_count": 0,
            "provisional_task_created_count": 0,
            "provisional_task_promoted_count": 0,
            "provisional_promotion_blocked_count": 0,
            "ambiguous_identity_mutation_blocked_count": 0,
            "sibling_identity_split_count": 0,
            "unauthorized_creation_blocked_count": 0,
            "ledger_unknown_task_id_rejection_count": 0,
            "recap_fragment_shadow_count": 0,
        }
    )

    def _next_task_id(self) -> str:
        sequence = len(self.tasks) + 1
        task_id = make_id("TASK", sequence)
        while task_id in self.tasks:
            sequence += 1
            task_id = make_id("TASK", sequence)
        return task_id

    def add_alias(self, task_id: str, alias: str) -> None:
        normalized = normalize_for_match(alias)
        if not normalized or task_id not in self.tasks:
            return
        task = self.tasks[task_id]
        task.aliases.add(alias.strip())
        self.alias_index.setdefault(normalized, set()).add(task_id)

    def create_task(self, event: TaskEvent) -> LedgerTask:
        task_id = self._next_task_id()
        assignees = _assignee_values(event.assignee)
        task = LedgerTask(
            task_id=task_id,
            canonical_action=event.action_text.strip(),
            identity_aliases={event.action_text.strip()},
            assignees=assignees,
            assignee_order=[
                item.strip()
                for item in re.split(r"\s*;\s*", event.assignee)
                if item.strip()
            ],
            deadline_mention_id=event.deadline_mention_id,
            status=(
                "CONFIRMED"
                if event.extraction_source == "RULE_REFERENCE_ACTIVE"
                else "PROVISIONAL" if event.event_type in REFERENCE_EVENTS
                else "UNKNOWN"
            ),
            created_order_index=event.order_index,
            last_order_index=event.order_index,
        )
        self.tasks[task_id] = task
        self.add_alias(task_id, event.action_text)
        self.diagnostics["ledger_task_created_count"] += 1
        if event.event_type in REFERENCE_EVENTS:
            self.diagnostics["provisional_task_created_count"] += 1
        return task

    def _promote_if_provisional(
        self, task: LedgerTask, event: TaskEvent
    ) -> bool:
        if task.status != "PROVISIONAL":
            return True
        # AI is mutation-only. A deadline correction may enrich an already
        # confirmed task, but it must not turn a discussion/reference identity
        # into a public task. Doing so is creation authority in disguise and
        # caused deadline-only references to leak into the final task list.
        if (
            event.extraction_source == "AI"
            and event.event_type in {"DEADLINE_SET", "DEADLINE_REPLACE"}
            and (
                not event.corroborated_by_rule
                or len(task.reference_clause_ids) < 2
            )
        ):
            self.diagnostics["provisional_promotion_blocked_count"] += 1
            return False
        reference_is_strong = task.confidence >= 0.92 or any(
            is_promotable_task_reference(alias)
            for alias in {task.canonical_action, *task.aliases}
        )
        if not reference_is_strong:
            self.diagnostics["provisional_promotion_blocked_count"] += 1
            return False
        task.status = "CONFIRMED"
        self.diagnostics["provisional_task_promoted_count"] += 1
        return True

    @staticmethod
    def has_conflicting_identity_aliases(task: LedgerTask) -> bool:
        """Detect a ledger ID that contains two distinct concrete work items.

        This is deliberately conservative: an explicit AI mutation must not
        modify an ID whose canonical label and strong aliases disagree about
        the work object. Exact/contained aliases and close paraphrases remain
        valid.
        """

        return identity_labels_conflict(
            task.identity_aliases or {task.canonical_action}
        )

    @staticmethod
    def _record_deadline(task: LedgerTask, event: TaskEvent) -> None:
        if not event.deadline_mention_id:
            return
        task.deadline_mention_id = event.deadline_mention_id
        if event.event_id not in task.deadline_event_ids:
            task.deadline_event_ids.append(event.event_id)
        if event.deadline_mention_id not in task.deadline_mention_history:
            task.deadline_mention_history.append(event.deadline_mention_id)

    @staticmethod
    def _add_assignees(task: LedgerTask, value: str) -> None:
        for assignee in (
            item.strip()
            for item in re.split(r"\s*;\s*", value)
            if item.strip()
        ):
            task.assignees.add(assignee)
            if assignee not in task.assignee_order:
                task.assignee_order.append(assignee)

    def resolve(self, event: TaskEvent) -> LinkResult:
        result = resolve_event_target(event, self)
        diagnostic_key = {
            "EXACT_ID": "exact_id_link_count",
            "EXACT_ALIAS": "exact_alias_link_count",
            "UNIQUE_SEMANTIC": "semantic_link_count",
        }.get(result.status)
        if diagnostic_key:
            self.diagnostics[diagnostic_key] += 1
        return result

    def apply(self, event: TaskEvent) -> LinkResult:
        if (
            self.recap_reconciliation_mode == "shadow"
            and event.extraction_source in RECAP_UPDATE_SOURCES
            and event.event_type == "OWNER_ASSIGN"
            and _is_unresolved_recap_fragment(event.action_text)
        ):
            self.diagnostics["recap_fragment_shadow_count"] += 1
        result = self.resolve(event)
        if result.status == "TERMINAL_REPLAY":
            self.blocked_events.append(event)
            self.diagnostics["terminal_replay_blocked_count"] += 1
            return result

        task_id = result.task_id
        if task_id is None:
            if (
                event.related_task_id.strip()
                and (
                    event.extraction_source == "AI"
                    or event.related_task_id.strip().upper().startswith("TASK-")
                )
            ):
                self.unresolved_events.append(event)
                self.diagnostics["ledger_unknown_task_id_rejection_count"] += 1
                if event.event_type in MUTATION_EVENTS:
                    self.diagnostics["unresolved_mutation_count"] += 1
                return result
            if (
                event.event_type in REFERENCE_EVENTS
                and event.action_text.strip()
                and event.extraction_source
                in {"RULE_REFERENCE", "RULE_REFERENCE_ACTIVE"}
            ):
                task_id = self.create_task(event).task_id
                result = LinkResult(
                    task_id,
                    "CREATED_PROVISIONAL",
                    "strong existing-task reference",
                    (task_id,),
                )
            elif event.extraction_source in UPDATE_ONLY_SOURCES:
                # Context restoration, note-assisted reinterpretation and AI
                # may update an existing identity, but never mint one.
                self.unresolved_events.append(event)
                if event.event_type in POSITIVE_EVENTS:
                    self.diagnostics["unauthorized_creation_blocked_count"] += 1
                if event.event_type in MUTATION_EVENTS:
                    self.diagnostics["unresolved_mutation_count"] += 1
                return result
            elif event.event_type in MUTATION_EVENTS:
                self.unresolved_events.append(event)
                self.diagnostics["unresolved_mutation_count"] += 1
                return result
            elif (
                event.event_type not in POSITIVE_EVENTS
                or not event.action_text.strip()
                or event.extraction_source not in CREATION_ALLOWED_SOURCES
            ):
                return result
            elif task_id is None:
                if (
                    event.event_type == "TASK_CREATE"
                    and result.candidate_task_ids
                ):
                    self.diagnostics["sibling_identity_split_count"] += 1
                task_id = self.create_task(event).task_id
                result = LinkResult(task_id, "CREATED", "new positive task", (task_id,))
        else:
            self.diagnostics["ledger_task_updated_count"] += 1

        task = self.tasks[task_id]
        if (
            event.extraction_source == "AI"
            and event.event_type in {"DEADLINE_SET", "DEADLINE_REPLACE"}
            and self.has_conflicting_identity_aliases(task)
        ):
            self.unresolved_events.append(event)
            self.diagnostics["unresolved_mutation_count"] += 1
            self.diagnostics["ambiguous_identity_mutation_blocked_count"] += 1
            return LinkResult(
                None,
                "AMBIGUOUS_IDENTITY",
                "target task contains conflicting concrete aliases",
                (task_id,),
            )
        if event.event_type == "TASK_CREATE" and event.action_text.strip():
            task.identity_aliases.add(event.action_text.strip())
        if event.event_type in REFERENCE_EVENTS:
            task.reference_clause_ids.update(event.source_clause_ids)
        if (
            event.event_type in POSITIVE_EVENTS
            and event.action_text
            and (
                event.extraction_source in {"RULE_RECAP", "RULE_FINAL_RECAP"}
                or (
                    event.extraction_source != "HUMAN_NOTE"
                    and "HUMAN_NOTE" in task.extraction_sources
                )
            )
        ):
            current = normalize_for_match(task.canonical_action)
            incoming = normalize_for_match(event.action_text)
            if (
                current
                and current in incoming
                and len(incoming.split()) > len(current.split())
                and not re.search(
                    r"\?|[\"“”].*[\"“”]|\b(?:có\s+vẻ|giống\s+nhau|hay\s+là|maybe|similar|duplicate)\b",
                    event.action_text,
                    re.I,
                )
            ):
                self.add_alias(task_id, task.canonical_action)
                task.canonical_action = event.action_text.strip()
        self.add_alias(task_id, event.action_text)

        if event.event_type == "OWNER_ASSIGN" and event.assignee:
            promoted = self._promote_if_provisional(task, event)
            self._add_assignees(task, event.assignee)
            if promoted:
                task.status = "CONFIRMED"
            self._record_deadline(task, event)
        elif event.event_type == "OWNER_REASSIGN" and event.assignee:
            promoted = self._promote_if_provisional(task, event)
            task.assignees = _assignee_values(event.assignee)
            task.assignee_order = [
                item.strip()
                for item in re.split(r"\s*;\s*", event.assignee)
                if item.strip()
            ]
            if promoted:
                task.status = "REASSIGNED"
        elif event.event_type in {"DEADLINE_SET", "DEADLINE_REPLACE"}:
            self._promote_if_provisional(task, event)
            self._record_deadline(task, event)
        elif event.event_type == "TASK_CANCEL":
            task.status = "CANCELLED"
            task.terminal_order_index = event.order_index
        elif event.event_type == "TASK_REJECT":
            task.status = "REJECTED"
            task.terminal_order_index = event.order_index
        elif event.event_type in {"TASK_CREATE", "TASK_COMMITMENT"}:
            promoted = self._promote_if_provisional(task, event)
            if promoted:
                task.status = "CONFIRMED"
            if event.assignee:
                self._add_assignees(task, event.assignee)
            self._record_deadline(task, event)

        task.source_clause_ids.update(event.source_clause_ids)
        task.event_ids.append(event.event_id)
        task.last_order_index = max(task.last_order_index, event.order_index)
        task.confidence = max(task.confidence, event.confidence)
        task.extraction_sources.add(event.extraction_source)
        return result

    def active_tasks(self) -> list[LedgerTask]:
        return sorted(
            (
                task
                for task in self.tasks.values()
                if task.status not in {"PROVISIONAL", "CANCELLED", "REJECTED"}
            ),
            key=lambda task: task.task_id,
        )

    def candidate_tasks(self) -> list[LedgerTask]:
        """Return all non-terminal identities, including provisional ones."""

        return sorted(
            (
                task
                for task in self.tasks.values()
                if task.status not in {"CANCELLED", "REJECTED"}
            ),
            key=lambda task: task.task_id,
        )

    def to_task_states(self) -> list[TaskState]:
        return [self.tasks[task_id].to_task_state() for task_id in sorted(self.tasks)]

    def to_checkpoint(self, meeting_id: str, last_processed_clause_order: int, provider_usage: dict | None = None) -> dict[str, Any]:
        return {
            "meeting_id": meeting_id,
            "last_processed_clause_order": last_processed_clause_order,
            "ledger": {
                task_id: {
                    **asdict(task),
                    "aliases": sorted(task.aliases),
                    "identity_aliases": sorted(task.identity_aliases),
                    "assignees": sorted(task.assignees),
                    "source_clause_ids": sorted(task.source_clause_ids),
                    "reference_clause_ids": sorted(task.reference_clause_ids),
                    "extraction_sources": sorted(task.extraction_sources),
                }
                for task_id, task in self.tasks.items()
            },
            "unresolved_events": [asdict(event) for event in self.unresolved_events],
            "blocked_events": [asdict(event) for event in self.blocked_events],
            "diagnostics": dict(self.diagnostics),
            "provider_usage": provider_usage or {},
        }

    @classmethod
    def from_checkpoint(cls, payload: dict[str, Any]) -> "TaskLedger":
        ledger = cls()
        for task_id, item in payload.get("ledger", {}).items():
            data = dict(item)
            for key in (
                "aliases", "identity_aliases", "assignees",
                "source_clause_ids", "reference_clause_ids",
                "extraction_sources",
            ):
                data[key] = set(data.get(key, []))
            task = LedgerTask(**data)
            if not task.identity_aliases:
                task.identity_aliases.add(task.canonical_action)
            ledger.tasks[task_id] = task
            for alias in task.aliases | {task.canonical_action}:
                ledger.add_alias(task_id, alias)
        ledger.unresolved_events = [TaskEvent(**item) for item in payload.get("unresolved_events", [])]
        ledger.blocked_events = [TaskEvent(**item) for item in payload.get("blocked_events", [])]
        ledger.diagnostics.update(payload.get("diagnostics", {}))
        return ledger
