"""Deterministic evidence for deadline-to-task attachment audits."""

from __future__ import annotations

from enum import Enum
import re

from pydantic import BaseModel, ConfigDict

from backend.app.models import Clause, DateMention, TaskEvent
from backend.app.preprocessing.unicode_normalizer import normalize_for_match


class DeadlineAttachmentType(str, Enum):
    SAME_ACTION_SOURCE = "SAME_ACTION_SOURCE"
    EXPLICIT_TASK_LABEL = "EXPLICIT_TASK_LABEL"
    ADJACENT_SUPPORT = "ADJACENT_SUPPORT"
    UNRESOLVED = "UNRESOLVED"


class DeadlineAttachmentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    deadline_mention_id: str
    mention_clause_id: str
    attachment_type: DeadlineAttachmentType
    target_clause_ids: tuple[str, ...] = ()


def _has_explicit_task_reference(event: TaskEvent, clause: Clause) -> bool:
    hint = normalize_for_match(event.related_task_hint or event.action_text)
    text = normalize_for_match(clause.text_raw)
    if not hint or len(hint.split()) < 2:
        return False
    return hint in text or bool(re.search(r"\btask\s+[a-z0-9-]+\b", text))


def build_deadline_attachment_evidence(
    event: TaskEvent,
    mentions: dict[str, DateMention],
    clauses_by_id: dict[str, Clause],
) -> DeadlineAttachmentEvidence | None:
    """Classify only grounded attachment relationships; never choose nearest."""

    if not event.deadline_mention_id:
        return None
    mention = mentions.get(event.deadline_mention_id)
    if mention is None:
        return DeadlineAttachmentEvidence(
            event_id=event.event_id,
            deadline_mention_id=event.deadline_mention_id,
            mention_clause_id="",
            attachment_type=DeadlineAttachmentType.UNRESOLVED,
        )
    target_ids = tuple(event.source_clause_ids)
    if mention.clause_id in event.source_clause_ids:
        attachment_type = DeadlineAttachmentType.SAME_ACTION_SOURCE
    else:
        mention_clause = clauses_by_id.get(mention.clause_id)
        source_orders = [
            clauses_by_id[item].order_index
            for item in event.source_clause_ids
            if item in clauses_by_id
        ]
        if mention_clause and _has_explicit_task_reference(event, mention_clause):
            attachment_type = DeadlineAttachmentType.EXPLICIT_TASK_LABEL
        elif mention_clause and source_orders and min(
            abs(mention_clause.order_index - order) for order in source_orders
        ) == 1:
            attachment_type = DeadlineAttachmentType.ADJACENT_SUPPORT
        else:
            attachment_type = DeadlineAttachmentType.UNRESOLVED
    return DeadlineAttachmentEvidence(
        event_id=event.event_id,
        deadline_mention_id=event.deadline_mention_id,
        mention_clause_id=mention.clause_id,
        attachment_type=attachment_type,
        target_clause_ids=target_ids,
    )
