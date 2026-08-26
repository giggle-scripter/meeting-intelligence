"""Python-first orchestration; AI is invoked only for ambiguous windows."""

from __future__ import annotations

import re
import logging
from collections import Counter
from dataclasses import asdict, replace

import httpx

from .ai import (
    AiClient,
    DisabledAiClient,
    extract_contextual_commitment_events,
    extract_events_by_ai,
    extract_events_by_rule,
    extract_events_from_human_note,
    extract_recap_events,
)
from .annotation import annotate_clauses, extract_date_mentions
from .dates import resolve_date_mention
from .candidate import (
    CandidateRoute,
    batch_ai_windows,
    build_candidate_windows,
    choose_extraction_strategy,
    merge_windows,
)
from .ingestion import parse_transcript
from .models import MeetingInput, PipelineDiagnostics, PipelineResult
from .output import build_pipeline_result
from .preprocessing import build_turns, deduplicate_caption_updates, normalize_speakers, split_clauses, split_sentences
from .reduction import (
    build_deterministic_reconciliation_operations,
    deduplicate_events,
    extract_provisional_task_references,
    is_promotable_task_reference,
    reconcile_ledger,
    reduce_task_events,
    reduce_task_events_to_ledger,
)
from .preprocessing.unicode_normalizer import normalize_for_match
from .utils.text_similarity import similarity, token_overlap
from .trace import write_pipeline_trace
from .verification import validate_task_create_proposal


POSITIVE_TASK_EVENTS = {"TASK_CREATE", "TASK_COMMITMENT", "OWNER_ASSIGN"}
LOGGER = logging.getLogger(__name__)


NO_ACTIVE_TASK_CLOSURE_PATTERNS = (
    r"\b(?:không có|không còn)\s+(?:task|công việc)\s+(?:active|mới)"
    r"[^.]{0,60}\b(?:hôm nay|trong cuộc họp|in this meeting)\b",
    r"\b(?:meeting|cuộc họp)\b[^.]{0,80}\bkhông có output\b",
    r"\bonly cancellations?\b",
    r"\bno (?:new|active) tasks?[^.]{0,60}\b(?:today|in this meeting)\b",
)
FINAL_STATUS_RECAP_RE = re.compile(r"\b(?:recap\s+cuối|final\s+recap)\s*:", re.I)
ANY_RECAP_SCOPE_RE = re.compile(
    r"\b(?:tổng\s+kết|chốt\s+lại|recap|action\s+items?|"
    r"các\s+đầu\s+việc|danh\s+sách\s+công\s+việc|final\s+list|"
    r"trạng\s+thái\s+cuối)\b",
    re.I,
)
COMPLETE_RECAP_SCOPE_RE = re.compile(
    r"\b(?:recap\s+(?:lần\s+)?cuối|recap\s+lại\s+tổng\s+thể|"
    r"recap\s+lại\s+các\s+task\s+đã\s+giao|"
    r"tổng\s+kết\s+(?:lần\s+)?cuối|tổng\s+kết\s+toàn\s+bộ|"
    r"tổng\s+cộng\s+các\s+task\s+hiện\s+tại|"
    r"(?:điểm|tổng\s+kết|recap)[^.]{0,80}\btoàn\s+bộ\b|"
    r"toàn\s+bộ[^.]{0,80}\b(?:task|đầu\s+việc|trạng\s+thái)\b|"
    r"final\s+(?:list|recap)|trạng\s+thái\s+cuối|"
    r"điểm\s+(?:nhanh\s+)?trạng\s+thái\s+từng\s+task)\b",
    re.I,
)
STRICT_AUTHORITATIVE_SNAPSHOT_RE = re.compile(
    r"\b(?:final\s+list\s+(?:chỉ\s+)?còn|"
    r"chốt\s+danh\s+sách\s+cuối\s+cùng|"
    r"các\s+task\s+active\s+cuối\s+cùng|"
    r"tất\s+cả\s+task\s+khác\s+đã\s+hủy|"
    r"all\s+other\s+tasks?\s+(?:are\s+)?cancelled|"
    r"các\s+task\s+khác\s+(?:đã\s+)?(?:hủy|huỷ|hoàn\s+thành)"
    r"(?:\s+hoặc\s+(?:hủy|huỷ|hoàn\s+thành))?)\b",
    re.I,
)
AUTHORITATIVE_SNAPSHOT_RE = re.compile(
    STRICT_AUTHORITATIVE_SNAPSHOT_RE.pattern[:-3] + r"|"
    r"(?:recap|tổng\s+kết)\s+(?:lần\s+)?cuối(?:\s+cùng)?|"
    r"(?:recap|tổng\s+kết|điểm(?:\s+lại|\s+qua|\s+nhanh)?)"
    r"[^.]{0,90}\btoàn\s+bộ\s+(?:các\s+)?"
    r"(?:trạng\s+thái|task|công\s+việc|đầu\s+việc)|"
    r"thống\s+nhất\s+lại\s+toàn\s+bộ\s+(?:các\s+)?task|"
    r"(?:điểm(?:\s+lại|\s+qua|\s+nhanh)?|recap)"
    r"[^.]{0,80}\btrạng\s+thái\s+(?:cuối\s+cùng\s+)?(?:của\s+)?"
    r"(?:từng|các)\s+task|"
    r"trạng\s+thái\s+cuối(?:\s+cùng)?\s+của\s+từng\s+task|"
    r"tổng\s+cộng\s+các\s+task\s+hiện\s+tại|"
    r"official\s+final\s+(?:task\s+)?(?:list|recap))\b",
    re.I,
)
ACTIVE_STATUS_ROW_RE = re.compile(
    r"^(?:các\s+)?tasks?\s+(?:đang\s+)?(?:progress|active|in\s+progress)\s*:\s*(.+)$",
    re.I,
)
LONG_CONTEXT_COMMUNICATION_RE = re.compile(
    r"^(?:gửi|send|liên\s+hệ|contact)\b",
    re.I,
)


def _task_create_proposal_payload(
    meeting: MeetingInput,
    focus_clause_id: str,
    clauses: list,
    annotations: dict,
    mentions: dict,
    *,
    context_radius: int = 2,
) -> dict:
    """Build bounded, ledger-free evidence for one uncertain create candidate."""

    focus_position = next(
        index for index, clause in enumerate(clauses)
        if clause.clause_id == focus_clause_id
    )
    bounded = clauses[
        max(0, focus_position - context_radius):
        min(len(clauses), focus_position + context_radius + 1)
    ]

    def clause_payload(clause) -> dict:
        return {
            "clause_id": clause.clause_id,
            "speaker_id": clause.speaker_id,
            "speaker_name": clause.speaker_name,
            "order_index": clause.order_index,
            "text": clause.text_raw,
            "semantic_flags": sorted(annotations[clause.clause_id].flags),
        }

    bounded_ids = {clause.clause_id for clause in bounded}
    return {
        "mode": "CREATE_PROPOSAL",
        "meeting": {
            "meeting_id": meeting.meeting_id,
            "meeting_title": meeting.meeting_title,
            "meeting_date": meeting.meeting_date,
        },
        "primary_clauses": [
            clause_payload(clause)
            for clause in bounded if clause.clause_id == focus_clause_id
        ],
        "context_clauses": [
            clause_payload(clause)
            for clause in bounded if clause.clause_id != focus_clause_id
        ],
        "known_date_mentions": [
            {
                "deadline_mention_id": mention.date_mention_id,
                "clause_id": mention.clause_id,
                "raw_text": mention.raw_text,
            }
            for mention in mentions.values()
            if mention.clause_id in bounded_ids and mention.purpose != "MEETING_DATE"
        ],
    }


def _ai_usage_trace(ai_client: AiClient) -> dict | None:
    """Return provider telemetry when the selected client exposes it.

    The trace deliberately contains aggregate counters only: no credentials,
    prompt text, response text, or provider response IDs.
    """

    snapshot = getattr(ai_client, "usage_snapshot", None)
    if not callable(snapshot):
        return None
    result = snapshot()
    return asdict(result) if result is not None else None


def _event_reason(event) -> str:
    """Classify origin for local diagnostics without changing public output."""

    if event.extraction_source == "AI":
        return "AI_AMBIGUOUS"
    if event.extraction_source == "AI_CREATE_PROPOSAL":
        return "AI_GROUNDED_CREATE_PROPOSAL"
    if event.extraction_source == "AI_MUTATION_ROUTER":
        return "AI_BOUNDED_MUTATION_ROUTER"
    if event.extraction_source == "HUMAN_NOTE":
        return "TRUSTED_HUMAN_NOTE_POSITIVE"
    if event.extraction_source == "RULE_CONTEXT":
        return "RULE_CONTEXT_REFERENCE"
    if event.extraction_source in {"RULE_REFERENCE", "RULE_REFERENCE_ACTIVE"}:
        return "RULE_EXISTING_TASK_REFERENCE"
    if event.extraction_source == "RULE_FINAL_RECAP":
        return "RULE_RECAP_FINAL"
    if event.extraction_source == "RULE_RECAP":
        return "RULE_RECAP_PARTIAL"
    if event.event_type in {
        "TASK_CANCEL", "TASK_REJECT", "OWNER_REASSIGN",
        "DEADLINE_SET", "DEADLINE_REPLACE",
    }:
        return "RULE_MUTATION"
    if event.event_type == "OWNER_ASSIGN":
        return "RULE_EXPLICIT_ASSIGNMENT"
    return "RULE_EXPLICIT_COMMITMENT"


def _build_ai_task_memory(
    events: list,
    window,
    clauses_by_id: dict,
    *,
    limit: int = 10,
) -> list[dict]:
    """Build bounded active-task candidates from events before this AI window.

    The memory is a navigation aid for long-distance mutation resolution.  It
    never becomes event evidence and never includes tasks created after the
    earliest primary clause in the provider request.
    """

    if limit <= 0 or not window.primary_clause_ids:
        return []
    primary_orders = [
        clauses_by_id[clause_id].order_index
        for clause_id in window.primary_clause_ids
        if clause_id in clauses_by_id
    ]
    if not primary_orders:
        return []
    cutoff = min(primary_orders)
    prior_events = [event for event in events if event.order_index < cutoff]
    if not prior_events:
        return []
    ledger = reduce_task_events_to_ledger(deduplicate_events(prior_events))
    # Candidate memory uses the same guarded duplicate reconciliation as the
    # final ledger. This removes exact/strong duplicate identities without
    # collapsing similarly named sibling work items before AI sees them.
    candidate_operations = build_deterministic_reconciliation_operations(ledger)
    reconcile_ledger(ledger, candidate_operations)
    active_states = [
        state
        for state in ledger.candidate_tasks()
        if not ledger.has_conflicting_identity_aliases(state)
        and (
            state.status != "PROVISIONAL"
            or state.confidence >= 0.92
            or any(
                is_promotable_task_reference(alias)
                for alias in {state.canonical_action, *state.aliases}
            )
        )
    ]
    if not active_states:
        return []
    context_text = " ".join(
        clauses_by_id[clause_id].text_raw
        for clause_id in window.context_clause_ids
        if clause_id in clauses_by_id
    )
    normalized_context = normalize_for_match(context_text)

    def rank(state) -> tuple[float, int, str]:
        candidate_aliases = state.candidate_aliases()
        action_score = max(
            [token_overlap(state.canonical_action, context_text)]
            + [token_overlap(alias, context_text) for alias in candidate_aliases]
        )
        owner_score = 1.0 if (
            state.assignees
            and any(
                normalize_for_match(owner) in normalized_context
                for owner in state.assignees
            )
        ) else 0.0
        return action_score * 3.0 + owner_score, state.last_order_index, state.task_id

    selected = sorted(active_states, key=rank, reverse=True)[:limit]
    return [
        {
            "task_id": state.task_id,
            "canonical_action": state.canonical_action,
            "aliases": sorted(state.candidate_aliases()),
            "assignees": sorted(state.assignees),
            "assignee": "; ".join(sorted(state.assignees)),
            "due_date": state.due_date,
            "status": state.status,
            "last_order_index": state.last_order_index,
            "last_source_clause_id": (
                max(state.source_clause_ids) if state.source_clause_ids else ""
            ),
            "source_clause_ids": sorted(state.source_clause_ids),
        }
        for state in selected
    ]


def _meeting_closes_without_active_tasks(clauses: list) -> bool:
    """Recognize an explicit final decision that this meeting creates no tasks.

    A participant may still promise to update a log or send minutes after this
    decision. Those administrative follow-ups are not task proposals when the
    meeting explicitly concludes that it has no active/new task output.
    """

    return any(
        re.search(pattern, clause.text_raw, re.I)
        for clause in clauses
        for pattern in NO_ACTIVE_TASK_CLOSURE_PATTERNS
    )


def _latest_recap_scope(clauses: list) -> str:
    """Expose explicit complete recap language for diagnostics."""

    scope = "NONE"
    for clause in clauses:
        text = clause.text_raw
        if (
            AUTHORITATIVE_SNAPSHOT_RE.search(text)
            or FINAL_STATUS_RECAP_RE.search(text)
        ):
            scope = "AUTHORITATIVE"
        elif scope != "AUTHORITATIVE" and COMPLETE_RECAP_SCOPE_RE.search(text):
            scope = "COMPLETE"
        elif scope not in {"AUTHORITATIVE", "COMPLETE"} and ANY_RECAP_SCOPE_RE.search(text):
            scope = "PARTIAL"
    return scope


def _final_active_recap_labels(clauses: list) -> tuple[str, ...]:
    """Read an explicit final active-status row without inferring task fields."""

    marker_indexes = [
        index
        for index, clause in enumerate(clauses)
        if FINAL_STATUS_RECAP_RE.search(clause.text_raw)
    ]
    if not marker_indexes:
        return ()
    start = marker_indexes[-1]
    marker = clauses[start]
    saw_terminal_category = False
    labels: list[str] = []
    for clause in clauses[start:start + 10]:
        if clause.speaker_id != marker.speaker_id:
            break
        text = clause.text_raw.strip()
        text = FINAL_STATUS_RECAP_RE.sub("", text).strip()
        match = ACTIVE_STATUS_ROW_RE.match(text)
        if match:
            labels.extend(
                item.strip(" .:;-")
                for item in re.split(r"\s*(?:,|;|\bvà\b|\band\b)\s*", match.group(1), flags=re.I)
                if item.strip(" .:;-")
            )
        if re.match(r"^(?:hủy|cancel(?:led)?|unresolved|pending)\s*:", text, re.I):
            saw_terminal_category = True
    # Require both the active row and at least one explicit non-active category;
    # this prevents a casual progress recap from deleting other valid tasks.
    return tuple(labels) if labels and saw_terminal_category else ()


def _apply_final_active_snapshot(
    states: list,
    labels: tuple[str, ...],
    recap_scope: str,
) -> list:
    """Keep only states named active by a complete final status snapshot."""

    if recap_scope != "AUTHORITATIVE" or not labels:
        return states
    selected = []
    used_ids: set[str] = set()
    for label in labels:
        ranked = []
        normalized_label = normalize_for_match(label)
        for state in states:
            normalized_task = normalize_for_match(state.task_name)
            overlap = token_overlap(normalized_task, normalized_label)
            sequence = similarity(normalized_task, normalized_label)
            if overlap < 0.6 and sequence < 0.7:
                continue
            quality = int(bool(state.deadline_mention_id)) + int(
                normalize_for_match(state.assignee)
                not in {"", "anh", "chi", "em", "toi", "minh"}
            )
            ranked.append((max(overlap, sequence), quality, state.last_order_index, state))
        if not ranked:
            continue
        state = max(ranked, key=lambda item: item[:3])[3]
        if state.task_id not in used_ids:
            selected.append(state)
            used_ids.add(state.task_id)
    # A final status recap may use aliases that the current reducer cannot yet
    # resolve. Never erase all prior state on a partial/failed link; defer that
    # case to AI/global identity resolution instead.
    return selected if len(selected) == len(labels) else states


def _apply_context_authority(events: list) -> list:
    """Drop pronoun-only duplicates once a context event restores the object."""

    context_owners = {
        normalize_for_match(event.assignee)
        for event in events
        if event.extraction_source == "RULE_CONTEXT" and event.assignee
    }
    if not context_owners:
        return events
    filtered = []
    for event in events:
        action = normalize_for_match(event.action_text)
        if (
            event.extraction_source == "RULE"
            and event.event_type in POSITIVE_TASK_EVENTS
            and normalize_for_match(event.assignee) in context_owners
            and action
            and re.fullmatch(
                r"(?:hoan thanh|gui|cap nhat|lam)"
                r"(?: dung han| dung nhu vay| nhu vay)?",
                action,
            )
        ):
            continue
        filtered.append(event)
    return filtered


def _apply_long_context_creation_policy(
    events: list,
    *,
    clause_count: int,
    recap_scope: str,
) -> list:
    """Make communication-only follow-ups update-only in scoped long meetings.

    A send/contact action can still be a standalone task in a short meeting or
    an explicit recap/note row. In a long discussion with a recap inventory,
    an incidental local-rule mention must link to an existing identity instead
    of minting another task.
    """

    if clause_count < 80 or recap_scope == "NONE":
        return events
    return [
        replace(event, extraction_source="RULE_CONTEXT")
        if (
            event.extraction_source == "RULE"
            and event.event_type in POSITIVE_TASK_EVENTS
            and LONG_CONTEXT_COMMUNICATION_RE.match(event.action_text.strip())
        )
        else event
        for event in events
    ]


def _apply_recap_authority(
    events: list,
    mentions: dict,
    meeting_date: str,
    recap_scope: str = "NONE",
    strict_authoritative: bool = False,
) -> list:
    """For an owner named in a final recap, replace earlier positive candidates."""

    final_recap_events = [
        event for event in events if event.extraction_source == "RULE_FINAL_RECAP"
    ]
    if final_recap_events:
        claimed_surface_event_ids: set[str] = set()
        # Prefer an earlier, more explicit surface form (for example
        # "Deadline 20/02" over recap shorthand "20/02") when both point to
        # the same calendar date. The final recap still owns task identity and
        # state; this only preserves faithful due_date_text evidence.
        for recap_event in final_recap_events:
            recap_mention = mentions.get(recap_event.deadline_mention_id)
            if recap_mention is None:
                continue
            ranked = []
            for candidate in events:
                if (
                    candidate is recap_event
                    or candidate.event_id in claimed_surface_event_ids
                    or candidate.order_index >= recap_event.order_index
                    or not candidate.deadline_mention_id
                    or normalize_for_match(candidate.assignee)
                    != normalize_for_match(recap_event.assignee)
                ):
                    continue
                candidate_mention = mentions.get(candidate.deadline_mention_id)
                if candidate_mention is None:
                    continue
                same_date = (
                    recap_mention.relation == candidate_mention.relation
                    and recap_mention.date_type == candidate_mention.date_type
                    and
                    recap_mention.explicit_day == candidate_mention.explicit_day
                    and recap_mention.explicit_month == candidate_mention.explicit_month
                    and (
                        recap_mention.explicit_year == candidate_mention.explicit_year
                        or recap_mention.explicit_year is None
                        or candidate_mention.explicit_year is None
                    )
                )
                if (
                    not same_date
                    and candidate_mention.relation == "BEFORE_TIME"
                    and resolve_date_mention(
                        meeting_date, meeting_date, candidate_mention
                    )
                    == resolve_date_mention(
                        meeting_date, meeting_date, recap_mention
                    )
                ):
                    same_date = True
                if not same_date or len(candidate_mention.raw_text) <= len(recap_mention.raw_text):
                    continue
                candidate_label = candidate.action_text or candidate.related_task_hint
                overlap_score = token_overlap(
                    candidate_label, recap_event.action_text
                )
                score = max(
                    similarity(candidate_label, recap_event.action_text),
                    overlap_score,
                )
                minimum_score = (
                    0.18
                    if candidate_mention.relation == "BEFORE_TIME"
                    else 0.45
                )
                if (
                    score >= minimum_score
                    and (
                        candidate_mention.relation != "BEFORE_TIME"
                        or overlap_score > 0
                    )
                ):
                    ranked.append((score, candidate.order_index, candidate))
            if ranked:
                _, _, source = max(ranked, key=lambda item: (item[0], item[1]))
                claimed_surface_event_ids.add(source.event_id)
                recap_event.deadline_mention_id = source.deadline_mention_id
                recap_event.source_clause_ids = list(
                    dict.fromkeys(source.source_clause_ids + recap_event.source_clause_ids)
                )
        if len(final_recap_events) >= 2 and recap_scope == "AUTHORITATIVE":
            if strict_authoritative:
                return final_recap_events
            # A full-state recap can accidentally omit a task confirmed only
            # moments earlier. Preserve such recent declarations unless the
            # transcript uses a strict whitelist marker such as "only these
            # tasks remain" or "all other tasks are cancelled".
            snapshot_order = min(event.order_index for event in final_recap_events)
            recent_positive_events = [
                event
                for event in events
                if event.extraction_source not in {"RULE_RECAP", "RULE_FINAL_RECAP"}
                and event.event_type == "OWNER_ASSIGN"
                and event.assignee
                and event.deadline_mention_id
                and 0 <= snapshot_order - event.order_index <= 12
            ]
            post_snapshot_events = [
                event
                for event in events
                if event.extraction_source not in {"RULE_RECAP", "RULE_FINAL_RECAP"}
                and event.order_index > snapshot_order
            ]

            # If a task omitted by the snapshot is explicitly named again
            # afterwards, restore its earlier concrete owner/action assertion.
            # A unique semantic match is required; a short label shared by
            # sibling tasks cannot reopen either one.
            earlier_positive_events = [
                event
                for event in events
                if event.order_index < snapshot_order
                and event.extraction_source not in {"RULE_RECAP", "RULE_FINAL_RECAP"}
                and event.event_type in POSITIVE_TASK_EVENTS
                and event.action_text
            ]
            restored_events = []
            for post_event in post_snapshot_events:
                reference = post_event.action_text or post_event.related_task_hint
                if not reference:
                    continue
                ranked = sorted(
                    (
                        (
                            max(
                                similarity(reference, candidate.action_text),
                                token_overlap(reference, candidate.action_text),
                                0.85
                                if normalize_for_match(reference)
                                in normalize_for_match(candidate.action_text)
                                else 0.0,
                            ),
                            candidate.order_index,
                            candidate,
                        )
                        for candidate in earlier_positive_events
                    ),
                    reverse=True,
                    key=lambda item: (item[0], item[1]),
                )
                if not ranked or ranked[0][0] < 0.45:
                    continue
                second_score = ranked[1][0] if len(ranked) > 1 else 0.0
                if ranked[0][0] - second_score < 0.12:
                    strong_assertions = [
                        candidate
                        for score, _, candidate in ranked
                        if score >= 0.45
                        and candidate.assignee
                        and candidate.deadline_mention_id
                    ]
                    distinct_assertions = {}
                    for candidate in strong_assertions:
                        key = (
                            normalize_for_match(candidate.action_text),
                            normalize_for_match(candidate.assignee),
                            candidate.deadline_mention_id,
                        )
                        distinct_assertions.setdefault(key, candidate)
                    if len(distinct_assertions) != 1:
                        continue
                    restored_events.append(next(iter(distinct_assertions.values())))
                else:
                    restored_events.append(ranked[0][2])

            # A speaker may add a deadline immediately after a complete recap
            # without repeating the work item (for example, "I will test by
            # tomorrow"). Link that update only when the recap has exactly one
            # task owned by that speaker. Ambiguous owners remain unresolved
            # instead of receiving a guessed deadline.
            recap_by_member: dict[str, list] = {}
            for recap_event in final_recap_events:
                for owner in recap_event.assignee.split(";"):
                    owner_key = normalize_for_match(owner)
                    if owner_key:
                        recap_by_member.setdefault(owner_key, []).append(recap_event)
            normalized_post_events = []
            for event in post_snapshot_events:
                owner_matches = recap_by_member.get(
                    normalize_for_match(event.assignee), []
                )
                if (
                    event.event_type == "TASK_COMMITMENT"
                    and event.deadline_mention_id
                    and len(owner_matches) == 1
                ):
                    target = owner_matches[0]
                    event = replace(
                        event,
                        event_type="DEADLINE_SET",
                        action_text="",
                        related_task_hint=target.action_text,
                        extraction_source="RULE_CONTEXT",
                    )
                elif event.event_type == "TASK_COMMITMENT":
                    event = replace(event, extraction_source="RULE_CONTEXT")
                normalized_post_events.append(event)
            return [
                *recent_positive_events,
                *[
                    event for event in restored_events
                    if event not in recent_positive_events
                ],
                *final_recap_events,
                *normalized_post_events,
            ]
    recap_events = [
        event
        for event in events
        if event.extraction_source in {"RULE_RECAP", "RULE_FINAL_RECAP"}
    ]
    if not recap_events:
        return events
    recap_by_owner: dict[str, list] = {}
    for event in recap_events:
        owner = normalize_for_match(event.assignee)
        if owner:
            recap_by_owner.setdefault(owner, []).append(event)
    for recap_event in recap_events:
        if recap_event.deadline_mention_id:
            continue
        owner = normalize_for_match(recap_event.assignee)
        recap_action = normalize_for_match(recap_event.action_text)
        ranked = []
        for candidate in events:
            if candidate is recap_event or candidate.order_index > recap_event.order_index:
                continue
            if normalize_for_match(candidate.assignee) != owner:
                continue
            candidate_action = normalize_for_match(candidate.action_text)
            score = max(
                similarity(candidate_action, recap_action),
                token_overlap(candidate_action, recap_action),
            )
            ranked.append((score, candidate.order_index, candidate))
        if ranked:
            score, _, source = max(ranked, key=lambda item: (item[0], item[1]))
            if score >= 0.45 and source.deadline_mention_id:
                recap_event.deadline_mention_id = source.deadline_mention_id
                recap_event.source_clause_ids = list(
                    dict.fromkeys(source.source_clause_ids + recap_event.source_clause_ids)
                )
    filtered = []
    authoritative_recap = (
        recap_scope == "AUTHORITATIVE"
        and len(recap_events) >= 2
    )
    for event in events:
        owner = normalize_for_match(event.assignee)
        candidates = recap_by_owner.get(owner, [])
        event_action = normalize_for_match(event.action_text)
        superseded = False
        if (
            event.extraction_source not in {"RULE_RECAP", "RULE_FINAL_RECAP"}
            and event.event_type in POSITIVE_TASK_EVENTS
            and event_action
        ):
            for recap_event in candidates:
                if event.order_index > recap_event.order_index:
                    continue
                if authoritative_recap:
                    superseded = True
                    break
        if not superseded:
            filtered.append(event)
    return filtered


def preprocess_meeting(meeting: MeetingInput, speaker_aliases: dict[str, str] | None = None) -> dict:
    captions = parse_transcript(meeting.transcript_raw, meeting.file_name)
    original_caption_count = len(captions)
    captions = deduplicate_caption_updates(captions)
    captions = normalize_speakers(captions, speaker_aliases)
    turns = build_turns(captions)
    sentences = split_sentences(turns)
    clauses = split_clauses(sentences)
    return {"captions": captions, "original_caption_count": original_caption_count, "turns": turns, "sentences": sentences, "clauses": clauses}


def process_meeting(
    meeting: MeetingInput,
    ai_client: AiClient | None = None,
    speaker_aliases: dict[str, str] | None = None,
    summary_topic: str | None = None,
    ai_max_batch_context_clauses: int = 56,
    ai_cost_gate_mode: str = "off",
    ai_cost_max_provider_calls_per_meeting: int = 3,
    ai_cost_max_payload_characters: int = 20_000,
    ai_cost_max_estimated_usd_per_meeting: float | None = None,
    trace_enabled: bool = False,
    trace_directory: str = "evaluation/traces",
    meeting_context_mode: str = "assist",
    note_grounding_threshold: float = 0.72,
    note_grounding_margin: float = 0.12,
    max_meeting_topics: int = 12,
    max_topic_keywords: int = 8,
    topic_likely_threshold: float = 0.45,
    action_classifier_mode: str = "off",
    action_classifier_model_path: str | None = None,
    action_candidate_builder_mode: str = "off",
    action_candidate_builder_version: str = "action-candidate-v2",
    commitment_router_mode: str = "off",
    commitment_router_version: str = "commitment-router-v2",
    commitment_router_active_types: tuple[str, ...] = (
        "DIRECT_ASSIGNMENT", "SELF_COMMITMENT",
    ),
    action_canonicalization_mode: str = "off",
    action_canonicalization_version: str = "action-canonicalization-v2",
    recap_reconciliation_mode: str = "off",
    owner_grounding_mode: str = "off",
    deadline_grounding_mode: str = "off",
    candidate_router_mode: str = "off",
    action_clear_threshold: float = 0.82,
    action_ai_threshold: float = 0.45,
    candidate_threshold_version: str = "candidate-router-thresholds-v1",
    task_create_proposal_enabled: bool = False,
    ai_create_proposal_enabled: bool = False,
    ai_create_max_proposals_per_meeting: int = 3,
    ai_quality_uplift_mode: str = "off",
    task_semantic_linker_mode: str = "off",
    task_link_embedding_model_name: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    ),
    task_link_embedding_device: str = "cpu",
    task_link_embedding_fallback_enabled: bool = True,
    task_link_embedding_fallback_dimension: int = 384,
    task_link_semantic_weight: float = 0.55,
    task_link_lexical_weight: float = 0.20,
    task_link_topic_weight: float = 0.10,
    task_link_owner_weight: float = 0.10,
    task_link_recency_weight: float = 0.05,
    task_link_strong_threshold: float = 0.78,
    task_link_min_margin: float = 0.12,
    task_link_ai_threshold: float = 0.60,
    task_link_recency_horizon_clauses: int = 200,
    task_link_top_k: int = 5,
    task_link_scoring_version: str = "task-link-scoring-v1",
    context_retrieval_mode: str = "off",
    context_max_clauses: int = 30,
    context_max_characters: int = 12_000,
    context_max_tasks: int = 5,
    context_local_before: int = 3,
    context_local_after: int = 5,
    context_max_topic_clauses: int = 12,
    context_max_topics: int = 3,
    context_max_history_events_per_task: int = 3,
    context_topic_boundary_threshold: float = 0.42,
    context_topic_smoothing_window: int = 3,
    context_retrieval_version: str = "context-retriever-v1",
    ai_mutation_router_mode: str = "off",
    ai_mutation_prompt_version: str = "mutation-resolution-v2",
    ai_mutation_min_confidence: float = 0.70,
    note_dual_view_mode: str = "off",
    note_claim_max_transcript_clauses: int = 8,
    note_claim_max_topics: int = 3,
    note_claim_grounding_threshold: float = 0.72,
    note_claim_grounding_margin: float = 0.12,
    note_dual_view_version: str = "note-dual-view-v1",
    temporal_semantics_mode: str = "off",
    temporal_parser_version: str = "temporal-parser-v1",
    temporal_working_day_policy: str = "weekdays-only-v1",
    temporal_min_confidence: float = 1.0,
) -> PipelineResult:
    if ai_quality_uplift_mode not in {"off", "shadow"}:
        raise ValueError("ai_quality_uplift_mode must be off or shadow")
    if ai_cost_gate_mode not in {"off", "enforce"}:
        raise ValueError("ai_cost_gate_mode must be off or enforce")
    ai_client = ai_client or DisabledAiClient()
    if ai_cost_gate_mode == "enforce":
        from .ai.cost_gate import AiCostBudget, BudgetedAiClient

        ai_client = BudgetedAiClient(
            ai_client,
            AiCostBudget(
                max_provider_calls=ai_cost_max_provider_calls_per_meeting,
                max_payload_characters=ai_cost_max_payload_characters,
                max_estimated_cost_usd=ai_cost_max_estimated_usd_per_meeting,
            ),
        )
    stages = preprocess_meeting(meeting, speaker_aliases)
    clauses = stages["clauses"]
    mentions = extract_date_mentions(clauses)
    annotations = annotate_clauses(
        clauses,
        {
            item.clause_id
            for item in mentions.values()
            if item.purpose != "MEETING_DATE"
        },
    )
    if meeting_context_mode not in {"off", "assist", "shadow"}:
        raise ValueError("meeting_context_mode must be off, assist, or shadow")
    if action_classifier_mode not in {"off", "shadow", "assist"}:
        raise ValueError("action_classifier_mode must be off, shadow, or assist")
    if action_candidate_builder_mode not in {"off", "shadow"}:
        raise ValueError("action_candidate_builder_mode must be off or shadow")
    if not action_candidate_builder_version:
        raise ValueError("action_candidate_builder_version must not be empty")
    if commitment_router_mode not in {"off", "shadow", "assist"}:
        raise ValueError("commitment_router_mode must be off, shadow, or assist")
    if not commitment_router_version:
        raise ValueError("commitment_router_version must not be empty")
    if action_canonicalization_mode not in {"off", "shadow"}:
        raise ValueError("action_canonicalization_mode must be off or shadow")
    if not action_canonicalization_version:
        raise ValueError("action_canonicalization_version must not be empty")
    if recap_reconciliation_mode not in {"off", "shadow"}:
        raise ValueError("recap_reconciliation_mode must be off or shadow")
    if owner_grounding_mode not in {"off", "shadow"}:
        raise ValueError("owner_grounding_mode must be off or shadow")
    if deadline_grounding_mode not in {"off", "shadow"}:
        raise ValueError("deadline_grounding_mode must be off or shadow")
    if candidate_router_mode not in {"off", "shadow", "assist"}:
        raise ValueError("candidate_router_mode must be off, shadow, or assist")
    if candidate_router_mode != "off" and action_classifier_mode != candidate_router_mode:
        raise ValueError(
            f"candidate_router_mode={candidate_router_mode} requires "
            f"action_classifier_mode={candidate_router_mode}"
        )
    if task_create_proposal_enabled and candidate_router_mode != "assist":
        raise ValueError(
            "task_create_proposal_enabled requires candidate_router_mode=assist"
        )
    if ai_create_proposal_enabled and not task_create_proposal_enabled:
        raise ValueError(
            "ai_create_proposal_enabled requires task_create_proposal_enabled"
        )
    if ai_create_max_proposals_per_meeting <= 0:
        raise ValueError("ai_create_max_proposals_per_meeting must be positive")
    if task_semantic_linker_mode not in {"off", "shadow"}:
        raise ValueError("task_semantic_linker_mode must be off or shadow")
    if context_retrieval_mode not in {"off", "shadow"}:
        raise ValueError("context_retrieval_mode must be off or shadow")
    if context_retrieval_mode == "shadow" and task_semantic_linker_mode != "shadow":
        raise ValueError(
            "context_retrieval_mode=shadow requires task_semantic_linker_mode=shadow"
        )
    if ai_mutation_router_mode not in {"off", "shadow", "assist"}:
        raise ValueError("ai_mutation_router_mode must be off, shadow, or assist")
    if ai_mutation_router_mode == "shadow" and (task_semantic_linker_mode != "shadow" or context_retrieval_mode != "shadow"):
        raise ValueError("ai_mutation_router shadow requires semantic and context shadow modes")
    if ai_mutation_router_mode == "assist" and (candidate_router_mode != "assist" or task_semantic_linker_mode != "shadow" or context_retrieval_mode != "shadow"):
        raise ValueError("ai_mutation_router assist requires candidate, semantic, and context routing")
    if not 0.0 <= ai_mutation_min_confidence <= 1.0:
        raise ValueError("ai_mutation_min_confidence must be between zero and one")
    if note_dual_view_mode not in {"off", "shadow", "assist"}:
        raise ValueError("note_dual_view_mode must be off, shadow, or assist")
    if note_dual_view_mode != "off" and meeting_context_mode == "off":
        raise ValueError("note_dual_view_mode requires meeting_context_mode")
    if not 0 < note_claim_max_transcript_clauses <= 8 or not 0 < note_claim_max_topics <= 3:
        raise ValueError("note dual-view limits exceed their hard caps")
    if not 0.0 <= note_claim_grounding_threshold <= 1.0 or not 0.0 <= note_claim_grounding_margin <= 1.0:
        raise ValueError("note dual-view thresholds must be between zero and one")
    if not note_dual_view_version:
        raise ValueError("note_dual_view_version must not be empty")
    if temporal_semantics_mode not in {"off", "shadow", "assist"}:
        raise ValueError("temporal_semantics_mode must be off, shadow, or assist")
    if not temporal_parser_version:
        raise ValueError("temporal_parser_version must not be empty")
    if temporal_working_day_policy != "weekdays-only-v1":
        raise ValueError("temporal_working_day_policy must be weekdays-only-v1")
    if temporal_min_confidence != 1.0:
        raise ValueError("temporal_min_confidence must be exactly 1.0")
    temporal_summary = None
    temporal_due_dates: dict[str, str] = {}
    if temporal_semantics_mode != "off":
        try:
            from .dates.temporal import evaluate_temporal_semantics

            temporal_summary, temporal_due_dates = evaluate_temporal_semantics(
                mentions,
                meeting_date=meeting.meeting_date,
                parser_version=temporal_parser_version,
                working_day_policy=temporal_working_day_policy,
            )
            if temporal_semantics_mode != "assist":
                temporal_due_dates = {}
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("Temporal semantics evaluation failed: %s", exc)
    meeting_context = None
    note_cues_by_clause = {}
    note_dual_view_stats = {
        "claim_count": 0, "full_count": 0, "partial_count": 0, "only_count": 0,
        "contradicted_count": 0, "retrieval_clause_count": 0, "mean_top1_score": 0.0,
        "mean_margin": 0.0, "human_proposal_candidate_count": 0,
        "auto_overview_context_only_count": 0, "direct_event_suppressed_count": 0,
        "error_count": 0, "reason_counts": {},
    }
    if meeting_context_mode != "off":
        from .v2.context import (
            apply_note_cues_to_annotations,
            build_meeting_context,
            build_note_cue_index,
        )
        meeting_context = build_meeting_context(
            meeting,
            clauses,
            annotations,
            mentions,
            grounding_threshold=note_grounding_threshold,
            grounding_margin=note_grounding_margin,
            max_topics=max_meeting_topics,
            max_topic_keywords=max_topic_keywords,
            topic_likely_threshold=topic_likely_threshold,
            note_dual_view_mode=note_dual_view_mode,
            note_claim_max_transcript_clauses=note_claim_max_transcript_clauses,
            note_claim_grounding_threshold=note_claim_grounding_threshold,
            note_claim_grounding_margin=note_claim_grounding_margin,
        )
        note_cues_by_clause = build_note_cue_index(meeting_context)
        if note_dual_view_mode != "off":
            from .v2.context import decide_note_authority
            from .v2.models import NoteGroundingLevel

            claims_by_id = {item.note_claim_id: item for item in meeting_context.note_claims}
            groundings = meeting_context.note_claim_groundings
            levels = Counter(item.level.value for item in groundings)
            reason_counts = Counter(
                reason for item in groundings for reason in item.reasons
            )
            authorities = [
                decide_note_authority(claims_by_id[item.note_claim_id], item)
                for item in groundings if item.note_claim_id in claims_by_id
            ]
            note_dual_view_stats.update({
                "claim_count": len(groundings),
                "full_count": levels[NoteGroundingLevel.FULL_GROUNDED.value],
                "partial_count": levels[NoteGroundingLevel.PARTIAL_GROUNDED.value],
                "only_count": levels[NoteGroundingLevel.NOTE_ONLY.value],
                "contradicted_count": levels[NoteGroundingLevel.CONTRADICTED.value],
                "retrieval_clause_count": sum(len(item.transcript_clause_ids) for item in groundings),
                "mean_top1_score": (sum(item.semantic_score for item in groundings) / len(groundings) if groundings else 0.0),
                "mean_margin": (sum(item.margin for item in groundings) / len(groundings) if groundings else 0.0),
                "human_proposal_candidate_count": sum(item.candidate_signal == "WEAK" for item in authorities),
                "auto_overview_context_only_count": sum(item.candidate_signal == "CONTEXT_ONLY" for item in authorities),
                "reason_counts": dict(sorted(reason_counts.items())),
            })
        if meeting_context_mode == "assist":
            annotations = apply_note_cues_to_annotations(
                annotations,
                note_cues_by_clause,
            )
    action_classifier_shadow = None
    action_predictions_by_clause = {}
    action_classifier_error_count = 0
    if action_classifier_mode != "off":
        try:
            from .ml.action_classifier import (
                predict_clause_actions,
                summarize_shadow_predictions,
            )
            from .ml.model_registry import get_action_classifier

            classifier = get_action_classifier(action_classifier_model_path)
            action_predictions_by_clause = predict_clause_actions(
                classifier,
                clauses,
                annotations,
                speaker_names={
                    clause.speaker_name for clause in clauses if clause.speaker_name
                },
                note_supported_clause_ids=set(note_cues_by_clause),
            )
            action_classifier_shadow = summarize_shadow_predictions(
                action_predictions_by_clause,
                clauses,
                annotations,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Action classifier shadow inference failed: %s", exc)
            action_classifier_error_count = 1
    action_candidates_shadow = []
    evidence_seeds_shadow = []
    action_candidate_builder_error_count = 0
    if action_candidate_builder_mode == "shadow" or commitment_router_mode != "off":
        try:
            from .candidate import build_action_candidates, build_action_proposals_v3

            builder = (
                build_action_proposals_v3
                if action_candidate_builder_version == "action-proposal-v3"
                else build_action_candidates
            )
            action_candidates_shadow = builder(
                clauses, annotations, mentions,
                builder_version=action_candidate_builder_version,
                **({"note_supported_clause_ids": set(note_cues_by_clause)} if builder is build_action_proposals_v3 else {}),
            )
            if action_candidate_builder_version == "action-proposal-v3":
                from .candidate import build_evidence_seeds
                evidence_seeds_shadow = build_evidence_seeds(clauses, annotations, mentions)
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Action candidate builder shadow failed: %s", exc)
            action_candidate_builder_error_count = 1
    clauses_by_id = {clause.clause_id: clause for clause in clauses}
    commitment_decisions_shadow = []
    commitment_router_summary = {"route_counts": {}, "authority_counts": {}}
    commitment_router_error_count = 0
    if commitment_router_mode != "off":
        try:
            from .candidate import AuthorityKind, route_commitments, summarize_commitment_decisions

            active_authorities = frozenset(
                AuthorityKind(value) for value in commitment_router_active_types
            )
            commitment_decisions_shadow = route_commitments(
                action_candidates_shadow,
                clauses_by_id,
                active_authorities=active_authorities,
            )
            commitment_router_summary = summarize_commitment_decisions(
                commitment_decisions_shadow
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Commitment router failed: %s", exc)
            commitment_router_error_count = 1
    candidate_evidence_shadow = []
    candidate_decisions_shadow = []
    candidate_router_shadow = None
    candidate_router_error_count = 0
    if candidate_router_mode != "off":
        if action_classifier_shadow is None:
            candidate_router_error_count = 1
        else:
            try:
                from .candidate import (
                    CandidateRouter,
                    CandidateRouterConfig,
                    build_candidate_evidence,
                    summarize_candidate_decisions,
                )

                candidate_evidence_shadow = build_candidate_evidence(
                    clauses,
                    annotations,
                    predictions_by_clause=action_predictions_by_clause,
                    note_cues_by_clause=note_cues_by_clause,
                    meeting_context=meeting_context,
                )
                candidate_router = CandidateRouter(
                    CandidateRouterConfig(
                        action_clear_threshold=action_clear_threshold,
                        action_ai_threshold=action_ai_threshold,
                        threshold_version=candidate_threshold_version,
                    )
                )
                candidate_decisions_shadow = candidate_router.route(
                    candidate_evidence_shadow
                )
                candidate_router_shadow = summarize_candidate_decisions(
                    candidate_router,
                    candidate_evidence_shadow,
                    candidate_decisions_shadow,
                )
            except (KeyError, RuntimeError, TypeError, ValueError) as exc:
                LOGGER.warning("Candidate evidence router shadow failed: %s", exc)
                candidate_router_error_count = 1
    windows = merge_windows(build_candidate_windows(clauses, annotations))
    events = []
    if meeting_context_mode == "assist" and meeting.meeting_note and note_dual_view_mode != "assist":
        note_events, note_clauses = extract_events_from_human_note(
            meeting.meeting_note,
            meeting_context,
            clauses_by_id,
            mentions,
            start_sequence=0,
        )
        clauses_by_id.update(note_clauses)
        events.extend(note_events)
    elif meeting_context_mode == "assist" and meeting.meeting_note and note_dual_view_mode == "assist":
        # A parsed note is never direct ledger authority in dual-view assist.
        note_dual_view_stats["direct_event_suppressed_count"] = len(
            meeting_context.note_claims if meeting_context else ()
        )
    events.extend(
        extract_provisional_task_references(clauses, start_sequence=len(events))
    )
    proposal_rejection_reasons: Counter[str] = Counter()
    task_create_proposal_call_count = 0
    task_create_proposal_accepted_count = 0
    task_create_proposal_no_action_count = 0
    task_create_proposal_unresolved_count = 0
    task_create_proposal_rejected_count = 0
    ai_create_decisions = [
        decision
        for decision in candidate_decisions_shadow
        if decision.route == CandidateRoute.AI_CREATE_CHECK
    ]
    candidate_order = {
        item.candidate_id: clauses_by_id[item.focus_clause_id].order_index
        for item in candidate_evidence_shadow
    }
    selected_create_decisions = sorted(
        ai_create_decisions,
        key=lambda item: (-item.confidence, candidate_order.get(item.candidate_id, 0)),
    )[:ai_create_max_proposals_per_meeting]
    ai_quality_create_records = []
    ai_quality_selected_create_ids: list[str] = []
    ai_quality_create_error_count = 0
    if ai_quality_uplift_mode == "shadow":
        try:
            from .ai.quality_uplift import preflight_ai_create_checks

            ai_quality_create_records, ai_quality_selected_create_ids = (
                preflight_ai_create_checks(
                    candidate_decisions_shadow,
                    candidate_evidence_shadow,
                    action_candidates_shadow,
                    commitment_decisions_shadow,
                    maximum=ai_create_max_proposals_per_meeting,
                )
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("AI quality uplift preflight failed: %s", exc)
            ai_quality_create_error_count = 1
    proposal_method = getattr(ai_client, "propose_task", None)
    if (
        task_create_proposal_enabled
        and ai_create_proposal_enabled
        and ai_client.enabled
        and callable(proposal_method)
    ):
        evidence_by_candidate = {
            item.candidate_id: item for item in candidate_evidence_shadow
        }
        for decision in selected_create_decisions:
            evidence = evidence_by_candidate[decision.candidate_id]
            payload = _task_create_proposal_payload(
                meeting,
                evidence.focus_clause_id,
                clauses,
                annotations,
                mentions,
            )
            bounded_ids = {
                item["clause_id"]
                for key in ("primary_clauses", "context_clauses")
                for item in payload[key]
            }
            task_create_proposal_call_count += 1
            try:
                response = proposal_method(payload)
                proposal = response.to_proposal()
                if response.decision == "NO_ACTION":
                    task_create_proposal_no_action_count += 1
                    continue
                if response.decision == "UNRESOLVED" or proposal is None:
                    task_create_proposal_unresolved_count += 1
                    continue
                validation = validate_task_create_proposal(
                    proposal,
                    clauses_by_id=clauses_by_id,
                    annotations=annotations,
                    mentions=mentions,
                    start_sequence=len(events),
                    allowed_source_clause_ids=bounded_ids,
                    required_primary_clause_ids={evidence.focus_clause_id},
                )
                if validation.accepted and validation.event is not None:
                    events.append(validation.event)
                    task_create_proposal_accepted_count += 1
                else:
                    task_create_proposal_rejected_count += 1
                    proposal_rejection_reasons.update(validation.reasons)
            except httpx.HTTPStatusError as exc:
                LOGGER.warning(
                    "AI create proposal rejected candidate %s: status=%s",
                    decision.candidate_id,
                    exc.response.status_code,
                )
                task_create_proposal_unresolved_count += 1
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                LOGGER.warning(
                    "AI create proposal failed candidate %s: %s",
                    decision.candidate_id,
                    exc,
                )
                task_create_proposal_unresolved_count += 1
    unresolved: list[str] = []
    ai_window_count = 0
    ai_provider_call_count = task_create_proposal_call_count
    ai_context_clause_ids: set[str] = set()
    ai_context_clause_count_before_pruning = 0
    ai_fallback_error_count = 0
    commitment_router_suppressed_event_count = 0
    commitment_route_by_clause: dict[str, str] = {}
    commitment_noncreate_flags = {
        "ROOT_QUESTION", "SUGGESTION_ONLY", "BRAINSTORM", "HYPOTHETICAL",
        "PAST_COMPLETED", "PROGRESS_UPDATE", "FUTURE_DISCUSSION",
        "ADMIN_FOLLOWUP", "REJECTION", "CANCELLATION",
    }
    if commitment_router_mode == "assist":
        candidates_by_id = {item.candidate_id: item for item in action_candidates_shadow}
        for decision in commitment_decisions_shadow:
            candidate = candidates_by_id[decision.candidate_id]
            for clause_id in candidate.primary_clause_ids:
                previous = commitment_route_by_clause.get(clause_id)
                if previous != "LOCAL_CREATE":
                    commitment_route_by_clause[clause_id] = decision.route.value
    ai_contract_diagnostics = {
        "rejection_count": 0,
        "structural_rejection_count": 0,
        "semantic_rejection_count": 0,
        "unknown_task_id_rejection_count": 0,
        "invalid_source_clause_rejection_count": 0,
        "invalid_anchor_clause_rejection_count": 0,
        "non_concrete_action_rejection_count": 0,
        "invalid_assignee_rejection_count": 0,
    }
    pending_ai_windows = []
    ai_group_keys: dict[str, tuple[str, ...]] = {}
    for window in windows:
        rule_events = extract_events_by_rule(
            window,
            clauses_by_id,
            annotations,
            mentions,
            start_sequence=len(events),
            note_cues_by_clause=(
                note_cues_by_clause if meeting_context_mode == "assist" else {}
            ),
        )
        if commitment_route_by_clause:
            retained_rule_events = []
            for event in rule_events:
                route = commitment_route_by_clause.get(event.source_clause_ids[0])
                has_noncreate_authority = any(
                    annotations[clause_id].flags & commitment_noncreate_flags
                    for clause_id in event.source_clause_ids
                    if clause_id in annotations
                )
                if (
                    (
                        has_noncreate_authority
                        or (route is not None and route != "LOCAL_CREATE")
                    )
                    and event.event_type in {"TASK_COMMITMENT", "OWNER_ASSIGN"}
                ):
                    commitment_router_suppressed_event_count += 1
                    continue
                retained_rule_events.append(event)
            rule_events = retained_rule_events
        events.extend(rule_events)
        covered_clause_ids = {clause_id for event in rule_events for clause_id in event.source_clause_ids}
        ai_primary_clause_ids = [
            clause_id
            for clause_id in window.primary_clause_ids
            if choose_extraction_strategy(annotations[clause_id]) == "AI"
            and annotations[clause_id].flags
            & {
                "CORRECTION",
                "CANCELLATION",
                "REJECTION",
            }
            and (
                clause_id not in covered_clause_ids
                or annotations[clause_id].flags
                & {"CORRECTION", "CANCELLATION", "REJECTION"}
            )
        ]
        if ai_primary_clause_ids:
            ai_window_count += 1
            context_clause_ids = list(window.context_clause_ids)
            ai_context_clause_count_before_pruning += len(context_clause_ids)
            if meeting_context_mode == "assist" and meeting_context:
                from .v2.context import compact_context_clause_ids
                context_clause_ids = list(compact_context_clause_ids(
                    context_clause_ids,
                    ai_primary_clause_ids,
                    clauses_by_id,
                    meeting_context.clause_relevance,
                    max_clauses=min(ai_max_batch_context_clauses, len(context_clause_ids)),
                ))
            ai_context_clause_ids.update(context_clause_ids)
            pending_ai_windows.append(
                replace(
                    window,
                    primary_clause_ids=ai_primary_clause_ids,
                    context_clause_ids=context_clause_ids,
                )
            )
            mutation_kinds = tuple(
                sorted(
                    {
                        kind
                        for clause_id in ai_primary_clause_ids
                        for kind in annotations[clause_id].flags
                        if kind in {"CORRECTION", "CANCELLATION", "REJECTION"}
                    }
                )
            )
            topic_ids = ()
            if meeting_context:
                topic_ids = tuple(
                    sorted(
                        {
                            topic_id
                            for clause_id in ai_primary_clause_ids
                            for topic_id in meeting_context.clause_relevance.get(
                                clause_id
                            ).topic_ids
                        }
                    )
                ) if all(
                    clause_id in meeting_context.clause_relevance
                    for clause_id in ai_primary_clause_ids
                ) else ()
            ai_group_keys[window.window_id] = mutation_kinds + topic_ids

    # Complete the deterministic identity universe before assigning task IDs
    # to AI candidate memory. These extractors may emit events whose chronology
    # precedes an AI window; adding them after provider calls would shift the
    # sequential TASK IDs between candidate retrieval and final reduction.
    events.extend(
        extract_contextual_commitment_events(clauses, mentions, len(events))
    )
    events = _apply_context_authority(events)
    recap_scope = _latest_recap_scope(clauses)
    events.extend(extract_recap_events(clauses, mentions, len(events)))
    events = _apply_recap_authority(
        events,
        mentions,
        meeting.meeting_date,
        recap_scope,
        strict_authoritative=any(
            STRICT_AUTHORITATIVE_SNAPSHOT_RE.search(clause.text_raw)
            for clause in clauses
        ),
    )
    events = _apply_long_context_creation_policy(
        events,
        clause_count=len(clauses),
        recap_scope=recap_scope,
    )

    # The routed path is deliberately built before legacy provider calls.  It
    # replays only deterministic events before each anchor, so provider output
    # can never supply its own retrieval memory.
    ai_mutation_router_summary = None
    ai_mutation_router_traces: list[dict] = []
    router_owned_primary_clause_ids: set[str] = set()
    if ai_mutation_router_mode != "off":
        try:
            from .ai.router import MutationRouter, MutationRouterSummary
            from .ml.model_registry import get_model_registry
            from .retrieval import ContextRetrievalConfig, ContextRetriever, TopicIndex
            from .retrieval import TaskLinkScoringConfig

            embedding_model = get_model_registry().get_embedding_model(
                task_link_embedding_model_name,
                device=task_link_embedding_device,
                fallback_dimension=task_link_embedding_fallback_dimension,
                allow_fallback=task_link_embedding_fallback_enabled,
            )
            context_retriever = ContextRetriever(
                clauses,
                TopicIndex(
                    clauses, embedding_model,
                    boundary_threshold=context_topic_boundary_threshold,
                    smoothing_window=context_topic_smoothing_window,
                ),
                ContextRetrievalConfig(
                    max_clauses=context_max_clauses,
                    max_characters=context_max_characters,
                    max_tasks=context_max_tasks,
                    local_before=context_local_before,
                    local_after=context_local_after,
                    max_topic_clauses=context_max_topic_clauses,
                    max_topics=context_max_topics,
                    max_history_events_per_task=context_max_history_events_per_task,
                    version=context_retrieval_version,
                ),
            )
            scoring = TaskLinkScoringConfig(
                semantic_weight=task_link_semantic_weight,
                lexical_weight=task_link_lexical_weight,
                topic_weight=task_link_topic_weight,
                owner_weight=task_link_owner_weight,
                recency_weight=task_link_recency_weight,
                strong_threshold=task_link_strong_threshold,
                minimum_margin=task_link_min_margin,
                ai_threshold=task_link_ai_threshold,
                recency_horizon_clauses=task_link_recency_horizon_clauses,
                top_k=task_link_top_k,
                version=task_link_scoring_version,
            )
            router = MutationRouter(
                minimum_confidence=ai_mutation_min_confidence,
                prompt_version=ai_mutation_prompt_version,
            )
            aggregate = MutationRouterSummary(
                mode=ai_mutation_router_mode,
                prompt_version=ai_mutation_prompt_version,
            )
            evidence_by_candidate = {
                item.candidate_id: item for item in candidate_evidence_shadow
            }
            for decision in candidate_decisions_shadow:
                if decision.route != CandidateRoute.AI_MUTATION_CHECK:
                    continue
                evidence = evidence_by_candidate[decision.candidate_id]
                event, trace, summary = router.execute(
                    meeting=meeting, decision=decision, evidence=evidence,
                    deterministic_events=events, clauses_by_id=clauses_by_id,
                    annotations=annotations, mentions=mentions,
                    context_retriever=context_retriever, embedding_model=embedding_model,
                    scoring=scoring, ai_client=ai_client, mode=ai_mutation_router_mode,
                    start_sequence=len(events),
                )
                ai_mutation_router_traces.append(trace)
                for name in (
                    "candidate_count", "payload_count", "call_count", "event_count",
                    "unresolved_count", "rejected_count", "error_count",
                    "unknown_task_id_count", "invalid_source_count", "invalid_anchor_count",
                    "invalid_owner_span_count", "invalid_deadline_count", "candidate_task_count",
                    "context_clause_count", "context_character_count",
                ):
                    setattr(aggregate, name, getattr(aggregate, name) + getattr(summary, name))
                aggregate.rejection_reasons.update(summary.rejection_reasons)
                if event is not None:
                    events.append(event)
                if ai_mutation_router_mode == "assist":
                    router_owned_primary_clause_ids.update(evidence.clause_ids)
            ai_mutation_router_summary = aggregate
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("AI mutation router failed closed: %s", exc)
            from .ai.router import MutationRouterSummary
            ai_mutation_router_summary = MutationRouterSummary(
                mode=ai_mutation_router_mode, prompt_version=ai_mutation_prompt_version,
                error_count=1,
            )

    ai_batches = batch_ai_windows(
        pending_ai_windows,
        ai_max_batch_context_clauses,
        group_keys=ai_group_keys,
    )
    if ai_mutation_router_summary is not None:
        ai_provider_call_count += ai_mutation_router_summary.call_count
    for batch in ai_batches:
        if (
            ai_mutation_router_mode == "assist"
            and set(batch.window.primary_clause_ids) & router_owned_primary_clause_ids
        ):
            # The new router owns the candidate; never call the legacy provider
            # path for the same anchor, including when it fail-closes.
            unresolved.extend(batch.source_window_ids)
            continue
        task_memory = (
            _build_ai_task_memory(events, batch.window, clauses_by_id)
            if ai_client.enabled
            else []
        )
        # Mutation resolution cannot safely run without an existing target.
        # Keep the source windows unresolved and avoid a provider call that
        # could only hallucinate a new task identity.
        if ai_client.enabled and not task_memory:
            unresolved.extend(batch.source_window_ids)
            continue
        if ai_client.enabled:
            ai_provider_call_count += 1
        try:
            ai_events = extract_events_by_ai(
                batch.window,
                clauses_by_id,
                annotations,
                mentions,
                ai_client,
                start_sequence=len(events),
                note_cues_by_clause=(
                    note_cues_by_clause if meeting_context_mode == "assist" else {}
                ),
                task_memory=task_memory,
                contract_diagnostics=ai_contract_diagnostics,
            )
        except httpx.HTTPStatusError as exc:
            # AI is additive. A provider outage or a capacity denial must not
            # prevent the deterministic rule pipeline from returning a result.
            LOGGER.warning(
                "AI fallback rejected batch %s: status=%s response=%s",
                batch.window.window_id,
                exc.response.status_code,
                exc.response.text[:500],
            )
            ai_fallback_error_count += 1
            unresolved.extend(batch.source_window_ids)
            continue
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            LOGGER.warning("AI fallback failed for batch %s: %s", batch.window.window_id, exc)
            ai_fallback_error_count += 1
            unresolved.extend(batch.source_window_ids)
            continue

        resolved_window_ids = {
            source_window_id
            for source_window_id, primary_ids in batch.primary_clause_ids_by_window.items()
            if any(set(event.source_clause_ids).intersection(primary_ids) for event in ai_events)
        }
        if ai_events:
            events.extend(ai_events)
        unresolved.extend(
            source_window_id
            for source_window_id in batch.source_window_ids
            if source_window_id not in resolved_window_ids
        )
    action_canonicalization_records: list[dict] = []
    action_canonicalization_error_count = 0
    if action_canonicalization_mode != "off":
        try:
            from .candidate import build_action_frame

            canonicalized_events = []
            for event in events:
                if (
                    event.event_type not in POSITIVE_TASK_EVENTS
                    or event.extraction_source != "RULE"
                    or not event.action_text
                ):
                    canonicalized_events.append(event)
                    continue
                frame = build_action_frame(
                    event.action_text,
                    tuple(event.source_clause_ids),
                )
                changed = frame.valid and frame.canonical_action != event.action_text
                action_canonicalization_records.append({
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "frame": frame.model_dump(mode="json"),
                    "changed": changed,
                })
                canonicalized_events.append(event)
            events = canonicalized_events
        except (RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Action canonicalization failed: %s", exc)
            action_canonicalization_error_count = 1
    owner_grounding_records: list[dict] = []
    owner_grounding_error_count = 0
    if owner_grounding_mode == "shadow":
        try:
            from .candidate import build_owner_evidence

            for event in events:
                if event.event_type not in POSITIVE_TASK_EVENTS | {"OWNER_REASSIGN"} or not event.assignee:
                    continue
                evidence = build_owner_evidence(event, clauses_by_id)
                owner_grounding_records.append({
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "assignee": event.assignee,
                    "evidence": [item.model_dump(mode="json") for item in evidence],
                })
        except (RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Owner grounding shadow failed: %s", exc)
            owner_grounding_error_count = 1
    deadline_grounding_records: list[dict] = []
    deadline_grounding_error_count = 0
    if deadline_grounding_mode == "shadow":
        try:
            from .candidate import build_deadline_attachment_evidence

            for event in events:
                evidence = build_deadline_attachment_evidence(
                    event, mentions, clauses_by_id
                )
                if evidence is not None:
                    deadline_grounding_records.append(evidence.model_dump(mode="json"))
        except (RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Deadline grounding shadow failed: %s", exc)
            deadline_grounding_error_count = 1
    events_before_deduplication = list(events)
    events = deduplicate_events(events)
    task_semantic_linker_shadow = None
    task_semantic_linker_results = []
    task_semantic_linker_error_count = 0
    task_link_embedding_model = None
    if task_semantic_linker_mode == "shadow":
        try:
            from .ml.model_registry import get_model_registry
            from .retrieval import TaskLinkScoringConfig
            from .retrieval.shadow import evaluate_task_linker_shadow

            task_link_embedding_model = get_model_registry().get_embedding_model(
                task_link_embedding_model_name,
                device=task_link_embedding_device,
                fallback_dimension=task_link_embedding_fallback_dimension,
                allow_fallback=task_link_embedding_fallback_enabled,
            )
            scoring_config = TaskLinkScoringConfig(
                semantic_weight=task_link_semantic_weight,
                lexical_weight=task_link_lexical_weight,
                topic_weight=task_link_topic_weight,
                owner_weight=task_link_owner_weight,
                recency_weight=task_link_recency_weight,
                strong_threshold=task_link_strong_threshold,
                minimum_margin=task_link_min_margin,
                ai_threshold=task_link_ai_threshold,
                recency_horizon_clauses=task_link_recency_horizon_clauses,
                top_k=task_link_top_k,
                version=task_link_scoring_version,
            )
            topic_ids_by_clause = {}
            if meeting_context:
                topic_ids_by_clause = {
                    clause_id: tuple(relevance.topic_ids)
                    for clause_id, relevance in meeting_context.clause_relevance.items()
                }
            (
                task_semantic_linker_shadow,
                task_semantic_linker_results,
            ) = evaluate_task_linker_shadow(
                events,
                clauses_by_id,
                embedding_model=task_link_embedding_model,
                config=scoring_config,
                topic_ids_by_clause=topic_ids_by_clause,
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Task semantic linker shadow failed: %s", exc)
            task_semantic_linker_error_count = 1
    context_retrieval_shadow = None
    context_retrieval_records = []
    context_retrieval_error_count = 0
    if context_retrieval_mode == "shadow":
        try:
            from .retrieval import (
                ContextRetrievalConfig,
                ContextRetriever,
                TopicIndex,
                evaluate_context_retrieval_shadow,
            )

            if task_link_embedding_model is None:
                raise RuntimeError("task semantic embedding model is unavailable")
            topic_index = TopicIndex(
                clauses,
                task_link_embedding_model,
                boundary_threshold=context_topic_boundary_threshold,
                smoothing_window=context_topic_smoothing_window,
            )
            context_retriever = ContextRetriever(
                clauses,
                topic_index,
                ContextRetrievalConfig(
                    max_clauses=context_max_clauses,
                    max_characters=context_max_characters,
                    max_tasks=context_max_tasks,
                    local_before=context_local_before,
                    local_after=context_local_after,
                    max_topic_clauses=context_max_topic_clauses,
                    max_topics=context_max_topics,
                    max_history_events_per_task=(
                        context_max_history_events_per_task
                    ),
                    version=context_retrieval_version,
                ),
            )
            (
                context_retrieval_shadow,
                context_retrieval_records,
            ) = evaluate_context_retrieval_shadow(
                events,
                clauses_by_id,
                task_semantic_linker_results,
                retriever=context_retriever,
                note_cues_by_clause=note_cues_by_clause,
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Context retrieval shadow failed: %s", exc)
            context_retrieval_error_count = 1
    ledger = reduce_task_events_to_ledger(
        events,
        recap_reconciliation_mode=recap_reconciliation_mode,
    )
    reconciliation_operations = build_deterministic_reconciliation_operations(ledger)
    reconciliation = reconcile_ledger(ledger, reconciliation_operations)
    reduction_diagnostics: dict[str, int] = dict(ledger.diagnostics)
    states = reconciliation.ledger.to_task_states()
    states = _apply_final_active_snapshot(
        states,
        _final_active_recap_labels(clauses),
        recap_scope,
    )
    closes_without_active_tasks = _meeting_closes_without_active_tasks(clauses)
    no_active_reason = None
    if closes_without_active_tasks:
        if any("CANCELLATION" in annotation.flags for annotation in annotations.values()):
            no_active_reason = "cancelled"
        states = []
    cost_gate_snapshot_method = getattr(ai_client, "cost_gate_snapshot", None)
    cost_gate_snapshot = (
        asdict(cost_gate_snapshot_method())
        if callable(cost_gate_snapshot_method)
        else {}
    )
    diagnostics = PipelineDiagnostics(
        caption_count=stages["original_caption_count"],
        deduplicated_caption_count=len(stages["captions"]),
        turn_count=len(stages["turns"]), sentence_count=len(stages["sentences"]),
        clause_count=len(clauses), candidate_window_count=len(windows),
        rule_event_count=sum(
            event.extraction_source.startswith("RULE") for event in events
        ),
        ai_event_count=sum(
            event.extraction_source in {"AI", "AI_CREATE_PROPOSAL", "AI_MUTATION_ROUTER"}
            for event in events
        ),
        ai_window_count=ai_window_count,
        ai_provider_enabled=ai_client.enabled,
        ai_provider_call_count=ai_provider_call_count,
        ai_context_clause_count=len(ai_context_clause_ids),
        ai_fallback_error_count=ai_fallback_error_count,
        unresolved_window_count=len(unresolved),
        ai_call_rate=(ai_window_count / len(windows)) if windows else 0.0,
        ai_clause_coverage=(
            len(ai_context_clause_ids) / len(clauses) if clauses else 0.0
        ),
        ai_batch_count=len(ai_batches),
        event_count_before_deduplication=len(events_before_deduplication),
        event_count_after_deduplication=len(events),
        terminal_replay_blocked_count=reduction_diagnostics.get(
            "terminal_replay_blocked_count", 0
        ),
        ledger_task_created_count=reduction_diagnostics.get(
            "ledger_task_created_count", 0
        ),
        ledger_task_updated_count=reduction_diagnostics.get(
            "ledger_task_updated_count", 0
        ),
        exact_id_link_count=reduction_diagnostics.get("exact_id_link_count", 0),
        exact_alias_link_count=reduction_diagnostics.get("exact_alias_link_count", 0),
        semantic_link_count=reduction_diagnostics.get("semantic_link_count", 0),
        unresolved_mutation_count=reduction_diagnostics.get(
            "unresolved_mutation_count", 0
        ),
        duplicate_task_merge_count=reduction_diagnostics.get(
            "duplicate_task_merge_count", 0
        ),
        provisional_task_created_count=reduction_diagnostics.get(
            "provisional_task_created_count", 0
        ),
        provisional_task_promoted_count=reduction_diagnostics.get(
            "provisional_task_promoted_count", 0
        ),
        provisional_promotion_blocked_count=reduction_diagnostics.get(
            "provisional_promotion_blocked_count", 0
        ),
        ambiguous_identity_mutation_blocked_count=reduction_diagnostics.get(
            "ambiguous_identity_mutation_blocked_count", 0
        ),
        sibling_identity_split_count=reduction_diagnostics.get(
            "sibling_identity_split_count", 0
        ),
        ai_contract_rejection_count=ai_contract_diagnostics.get(
            "rejection_count", 0
        ),
        ai_structural_contract_rejection_count=ai_contract_diagnostics.get(
            "structural_rejection_count", 0
        ),
        ai_semantic_rejection_count=ai_contract_diagnostics.get(
            "semantic_rejection_count", 0
        ),
        ai_unknown_task_id_rejection_count=ai_contract_diagnostics.get(
            "unknown_task_id_rejection_count", 0
        ),
        ai_invalid_source_clause_rejection_count=ai_contract_diagnostics.get(
            "invalid_source_clause_rejection_count", 0
        ),
        ai_invalid_anchor_clause_rejection_count=ai_contract_diagnostics.get(
            "invalid_anchor_clause_rejection_count", 0
        ),
        ai_non_concrete_action_rejection_count=ai_contract_diagnostics.get(
            "non_concrete_action_rejection_count", 0
        ),
        ai_invalid_assignee_rejection_count=ai_contract_diagnostics.get(
            "invalid_assignee_rejection_count", 0
        ),
        unauthorized_creation_blocked_count=reduction_diagnostics.get(
            "unauthorized_creation_blocked_count", 0
        ),
        ledger_unknown_task_id_rejection_count=reduction_diagnostics.get(
            "ledger_unknown_task_id_rejection_count", 0
        ),
        recap_reconciliation_mode=recap_reconciliation_mode,
        recap_fragment_shadow_count=reduction_diagnostics.get(
            "recap_fragment_shadow_count", 0
        ),
        owner_grounding_mode=owner_grounding_mode,
        owner_evidence_count=sum(
            len(item["evidence"]) for item in owner_grounding_records
        ),
        owner_ungrounded_event_count=sum(
            not item["evidence"] for item in owner_grounding_records
        ),
        owner_evidence_type_counts=dict(sorted(Counter(
            evidence["evidence_type"]
            for item in owner_grounding_records
            for evidence in item["evidence"]
        ).items())),
        deadline_grounding_mode=deadline_grounding_mode,
        deadline_attachment_count=len(deadline_grounding_records),
        deadline_unresolved_attachment_count=sum(
            item["attachment_type"] == "UNRESOLVED"
            for item in deadline_grounding_records
        ),
        deadline_attachment_type_counts=dict(sorted(Counter(
            item["attachment_type"] for item in deadline_grounding_records
        ).items())),
        recap_scope=recap_scope,
        meeting_date_source=meeting.meeting_date_source,
        effective_meeting_date=meeting.meeting_date,
        action_classifier_mode=action_classifier_mode,
        action_classifier_version=(
            action_classifier_shadow.classifier_version
            if action_classifier_shadow
            else ("unavailable" if action_classifier_mode != "off" else "disabled")
        ),
        embedding_model_version=(
            action_classifier_shadow.embedding_model_version
            if action_classifier_shadow
            else ("unavailable" if action_classifier_mode != "off" else "disabled")
        ),
        action_classifier_clause_count=(
            action_classifier_shadow.clause_count if action_classifier_shadow else 0
        ),
        action_classifier_prediction_counts=(
            action_classifier_shadow.prediction_counts
            if action_classifier_shadow
            else {}
        ),
        action_classifier_would_create_count=(
            action_classifier_shadow.would_create_count
            if action_classifier_shadow
            else 0
        ),
        action_classifier_would_review_count=(
            action_classifier_shadow.would_review_count
            if action_classifier_shadow
            else 0
        ),
        action_classifier_would_update_count=(
            action_classifier_shadow.would_update_count
            if action_classifier_shadow
            else 0
        ),
        action_classifier_rule_action_clause_count=(
            action_classifier_shadow.rule_action_clause_count
            if action_classifier_shadow
            else 0
        ),
        action_classifier_rule_agreement_count=(
            action_classifier_shadow.rule_agreement_count
            if action_classifier_shadow
            else 0
        ),
        action_classifier_rule_disagreement_count=(
            action_classifier_shadow.rule_disagreement_count
            if action_classifier_shadow
            else 0
        ),
        action_classifier_error_count=action_classifier_error_count,
        action_candidate_builder_mode=(
            "shadow"
            if action_candidate_builder_mode == "shadow" or commitment_router_mode != "off"
            else "off"
        ),
        action_candidate_builder_version=(
            action_candidate_builder_version
            if action_candidate_builder_mode == "shadow" or commitment_router_mode != "off"
            else "disabled"
        ),
        action_candidate_count=len(action_candidates_shadow),
        action_candidate_action_span_count=sum(
            len(item.action_spans) for item in action_candidates_shadow
        ),
        action_candidate_kind_counts=dict(sorted(Counter(
            item.candidate_kind for item in action_candidates_shadow
        ).items())),
        action_candidate_state_counts=dict(sorted(Counter(
            item.state.value for item in action_candidates_shadow
        ).items())),
        action_candidate_builder_error_count=action_candidate_builder_error_count,
        commitment_router_mode=commitment_router_mode,
        commitment_router_version=(
            commitment_router_version if commitment_router_mode != "off" else "disabled"
        ),
        commitment_router_decision_count=len(commitment_decisions_shadow),
        commitment_router_route_counts=commitment_router_summary["route_counts"],
        commitment_router_authority_counts=commitment_router_summary["authority_counts"],
        commitment_router_suppressed_event_count=commitment_router_suppressed_event_count,
        commitment_router_error_count=commitment_router_error_count,
        action_canonicalization_mode=action_canonicalization_mode,
        action_canonicalization_version=(
            action_canonicalization_version
            if action_canonicalization_mode != "off" else "disabled"
        ),
        action_canonicalization_frame_count=len(action_canonicalization_records),
        action_canonicalization_changed_count=sum(
            item["changed"] for item in action_canonicalization_records
        ),
        action_canonicalization_rejected_count=sum(
            not item["frame"]["valid"] for item in action_canonicalization_records
        ),
        action_canonicalization_error_count=action_canonicalization_error_count,
        candidate_router_mode=candidate_router_mode,
        candidate_router_version=(
            candidate_router_shadow.router_version
            if candidate_router_shadow
            else ("unavailable" if candidate_router_mode != "off" else "disabled")
        ),
        candidate_threshold_version=(
            candidate_router_shadow.threshold_version
            if candidate_router_shadow
            else (
                candidate_threshold_version
                if candidate_router_mode != "off"
                else "disabled"
            )
        ),
        candidate_evidence_count=(
            candidate_router_shadow.evidence_count if candidate_router_shadow else 0
        ),
        candidate_decision_count=(
            candidate_router_shadow.decision_count if candidate_router_shadow else 0
        ),
        candidate_route_counts=(
            candidate_router_shadow.route_counts if candidate_router_shadow else {}
        ),
        candidate_ai_create_check_suppressed_count=(
            max(0, len(ai_create_decisions) - task_create_proposal_call_count)
        ),
        candidate_router_error_count=candidate_router_error_count,
        task_create_proposal_call_count=task_create_proposal_call_count,
        task_create_proposal_accepted_count=task_create_proposal_accepted_count,
        task_create_proposal_no_action_count=task_create_proposal_no_action_count,
        task_create_proposal_unresolved_count=task_create_proposal_unresolved_count,
        task_create_proposal_rejected_count=task_create_proposal_rejected_count,
        task_create_proposal_rejection_reasons=dict(
            sorted(proposal_rejection_reasons.items())
        ),
        ai_quality_uplift_mode=ai_quality_uplift_mode,
        ai_quality_create_candidate_count=len(ai_quality_create_records),
        ai_quality_create_eligible_count=sum(
            item.eligible for item in ai_quality_create_records
        ),
        ai_quality_create_selected_count=len(ai_quality_selected_create_ids),
        ai_quality_create_exclusion_reasons=dict(sorted(Counter(
            item.reason for item in ai_quality_create_records if not item.eligible
        ).items())),
        ai_cost_gate_mode=ai_cost_gate_mode,
        ai_cost_gate_provider_call_count=int(
            cost_gate_snapshot.get("provider_call_count", 0)
        ),
        ai_cost_gate_blocked_call_count=int(
            cost_gate_snapshot.get("blocked_call_count", 0)
        ),
        ai_cost_gate_payload_characters_sent=int(
            cost_gate_snapshot.get("payload_characters_sent", 0)
        ),
        ai_cost_gate_block_reasons=cost_gate_snapshot.get("block_reasons", {}),
        task_semantic_linker_mode=task_semantic_linker_mode,
        task_semantic_linker_version=(
            task_semantic_linker_shadow.linker_version
            if task_semantic_linker_shadow
            else (
                "unavailable" if task_semantic_linker_mode == "shadow" else "disabled"
            )
        ),
        task_semantic_index_version=(
            task_semantic_linker_shadow.index_version
            if task_semantic_linker_shadow
            else (
                "unavailable" if task_semantic_linker_mode == "shadow" else "disabled"
            )
        ),
        task_semantic_scoring_version=(
            task_semantic_linker_shadow.scoring_version
            if task_semantic_linker_shadow
            else (
                task_link_scoring_version
                if task_semantic_linker_mode == "shadow"
                else "disabled"
            )
        ),
        task_semantic_embedding_model_version=(
            task_semantic_linker_shadow.embedding_model_version
            if task_semantic_linker_shadow
            else (
                "unavailable" if task_semantic_linker_mode == "shadow" else "disabled"
            )
        ),
        task_semantic_query_count=(
            task_semantic_linker_shadow.query_count
            if task_semantic_linker_shadow else 0
        ),
        task_semantic_scored_query_count=(
            task_semantic_linker_shadow.scored_query_count
            if task_semantic_linker_shadow else 0
        ),
        task_semantic_route_counts=(
            task_semantic_linker_shadow.route_counts
            if task_semantic_linker_shadow else {}
        ),
        task_semantic_reason_counts=(
            task_semantic_linker_shadow.reason_counts
            if task_semantic_linker_shadow else {}
        ),
        task_semantic_production_agreement_count=(
            task_semantic_linker_shadow.production_agreement_count
            if task_semantic_linker_shadow else 0
        ),
        task_semantic_production_disagreement_count=(
            task_semantic_linker_shadow.production_disagreement_count
            if task_semantic_linker_shadow else 0
        ),
        task_semantic_ambiguous_sibling_count=(
            task_semantic_linker_shadow.ambiguous_sibling_count
            if task_semantic_linker_shadow else 0
        ),
        task_semantic_mean_top1_score=(
            task_semantic_linker_shadow.mean_top1_score
            if task_semantic_linker_shadow else 0.0
        ),
        task_semantic_mean_margin=(
            task_semantic_linker_shadow.mean_margin
            if task_semantic_linker_shadow else 0.0
        ),
        task_semantic_linker_error_count=task_semantic_linker_error_count,
        context_retrieval_mode=context_retrieval_mode,
        context_retrieval_version=(
            context_retrieval_shadow.retriever_version
            if context_retrieval_shadow
            else ("unavailable" if context_retrieval_mode == "shadow" else "disabled")
        ),
        context_topic_index_version=(
            context_retrieval_shadow.topic_index_version
            if context_retrieval_shadow
            else ("unavailable" if context_retrieval_mode == "shadow" else "disabled")
        ),
        context_embedding_model_version=(
            context_retrieval_shadow.embedding_model_version
            if context_retrieval_shadow
            else ("unavailable" if context_retrieval_mode == "shadow" else "disabled")
        ),
        context_bundle_count=(
            context_retrieval_shadow.bundle_count if context_retrieval_shadow else 0
        ),
        context_total_clause_count=(
            context_retrieval_shadow.total_clause_count
            if context_retrieval_shadow else 0
        ),
        context_total_character_count=(
            context_retrieval_shadow.total_character_count
            if context_retrieval_shadow else 0
        ),
        context_total_task_count=(
            context_retrieval_shadow.total_task_count
            if context_retrieval_shadow else 0
        ),
        context_total_history_event_count=(
            context_retrieval_shadow.total_history_event_count
            if context_retrieval_shadow else 0
        ),
        context_total_note_cue_count=(
            context_retrieval_shadow.total_note_cue_count
            if context_retrieval_shadow else 0
        ),
        context_max_clause_count_observed=(
            context_retrieval_shadow.max_clause_count_observed
            if context_retrieval_shadow else 0
        ),
        context_max_character_count_observed=(
            context_retrieval_shadow.max_character_count_observed
            if context_retrieval_shadow else 0
        ),
        context_clause_cap_hit_count=(
            context_retrieval_shadow.clause_cap_hit_count
            if context_retrieval_shadow else 0
        ),
        context_character_cap_hit_count=(
            context_retrieval_shadow.character_cap_hit_count
            if context_retrieval_shadow else 0
        ),
        context_tier_clause_counts=(
            context_retrieval_shadow.tier_clause_counts
            if context_retrieval_shadow else {}
        ),
        context_retrieval_error_count=(
            context_retrieval_error_count
            + (context_retrieval_shadow.error_count if context_retrieval_shadow else 0)
        ),
        ai_mutation_router_mode=ai_mutation_router_mode,
        ai_mutation_router_version=(
            ai_mutation_router_summary.version if ai_mutation_router_summary else "disabled"
        ),
        ai_mutation_prompt_version=(
            ai_mutation_router_summary.prompt_version if ai_mutation_router_summary else "disabled"
        ),
        ai_mutation_candidate_count=(
            ai_mutation_router_summary.candidate_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_payload_count=(
            ai_mutation_router_summary.payload_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_call_count=(
            ai_mutation_router_summary.call_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_event_count=(
            ai_mutation_router_summary.event_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_unresolved_count=(
            ai_mutation_router_summary.unresolved_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_rejected_count=(
            ai_mutation_router_summary.rejected_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_error_count=(
            ai_mutation_router_summary.error_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_rejection_reasons=(
            ai_mutation_router_summary.rejection_reasons if ai_mutation_router_summary else {}
        ),
        ai_mutation_candidate_task_count=(
            ai_mutation_router_summary.candidate_task_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_context_clause_count=(
            ai_mutation_router_summary.context_clause_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_context_character_count=(
            ai_mutation_router_summary.context_character_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_unknown_task_id_count=(
            ai_mutation_router_summary.unknown_task_id_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_invalid_source_count=(
            ai_mutation_router_summary.invalid_source_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_invalid_anchor_count=(
            ai_mutation_router_summary.invalid_anchor_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_invalid_owner_span_count=(
            ai_mutation_router_summary.invalid_owner_span_count if ai_mutation_router_summary else 0
        ),
        ai_mutation_invalid_deadline_count=(
            ai_mutation_router_summary.invalid_deadline_count if ai_mutation_router_summary else 0
        ),
        note_dual_view_mode=note_dual_view_mode,
        note_dual_view_version=(note_dual_view_version if note_dual_view_mode != "off" else "disabled"),
        note_claim_count=note_dual_view_stats["claim_count"],
        note_full_grounded_count=note_dual_view_stats["full_count"],
        note_partial_grounded_count=note_dual_view_stats["partial_count"],
        note_only_count=note_dual_view_stats["only_count"],
        note_contradicted_count=note_dual_view_stats["contradicted_count"],
        note_claim_retrieval_clause_count=note_dual_view_stats["retrieval_clause_count"],
        note_claim_mean_top1_score=note_dual_view_stats["mean_top1_score"],
        note_claim_mean_margin=note_dual_view_stats["mean_margin"],
        note_human_proposal_candidate_count=note_dual_view_stats["human_proposal_candidate_count"],
        note_auto_overview_context_only_count=note_dual_view_stats["auto_overview_context_only_count"],
        note_direct_event_suppressed_count=note_dual_view_stats["direct_event_suppressed_count"],
        note_dual_view_error_count=note_dual_view_stats["error_count"],
        note_grounding_reason_counts=note_dual_view_stats["reason_counts"],
        temporal_semantics_mode=temporal_semantics_mode,
        temporal_parser_version=(temporal_parser_version if temporal_semantics_mode != "off" else "disabled"),
        temporal_working_day_policy=(temporal_working_day_policy if temporal_semantics_mode != "off" else "disabled"),
        temporal_expression_count=(temporal_summary.expression_count if temporal_summary else 0),
        temporal_type_counts=(temporal_summary.type_counts if temporal_summary else {}),
        temporal_existing_resolved_count=(temporal_summary.existing_resolved_count if temporal_summary else 0),
        temporal_ast_resolved_count=(temporal_summary.ast_resolved_count if temporal_summary else 0),
        temporal_ast_unresolved_count=(temporal_summary.ast_unresolved_count if temporal_summary else 0),
        temporal_unresolved_anchor_count=(temporal_summary.unresolved_anchor_count if temporal_summary else 0),
        temporal_agreement_count=(temporal_summary.agreement_count if temporal_summary else 0),
        temporal_disagreement_count=(temporal_summary.disagreement_count if temporal_summary else 0),
        temporal_improve_count=(temporal_summary.improve_count if temporal_summary else 0),
        temporal_regress_count=(temporal_summary.regress_count if temporal_summary else 0),
        temporal_parser_error_count=(temporal_summary.parser_error_count if temporal_summary else 0),
        temporal_resolution_status_counts=(temporal_summary.resolution_status_counts if temporal_summary else {}),
    )
    result = build_pipeline_result(
        meeting.meeting_title,
        meeting.meeting_date,
        states,
        clauses_by_id,
        mentions,
        diagnostics,
        unresolved,
        summary_topic=summary_topic,
        no_active_reason=no_active_reason,
        meeting_note_present=meeting.meeting_note is not None,
        temporal_due_dates=temporal_due_dates,
    )
    if trace_enabled:
        write_pipeline_trace(trace_directory, meeting.meeting_id, "v1", {
            "pipeline_version": "v1",
            "meeting_id": meeting.meeting_id,
            "clauses": [asdict(item) for item in clauses],
            "annotations": {key: asdict(value) for key, value in annotations.items()},
            "candidate_windows": [asdict(item) for item in windows],
            "date_mentions": {key: asdict(value) for key, value in mentions.items()},
            "events_before_deduplication": [
                asdict(item) for item in events_before_deduplication
            ],
            "events_after_deduplication": [asdict(item) for item in events],
            "event_reason_by_id": {item.event_id: _event_reason(item) for item in events},
            "task_states": [asdict(item) for item in states],
            "unresolved_window_ids": unresolved,
            "final_tasks": [asdict(item) for item in result.tasks],
            "meeting_context": asdict(meeting_context) if meeting_context else None,
            "note_dual_view": note_dual_view_stats,
            "note_cues_by_clause": {
                clause_id: [asdict(cue) for cue in cues]
                for clause_id, cues in note_cues_by_clause.items()
            },
            "action_classifier_shadow": (
                asdict(action_classifier_shadow) if action_classifier_shadow else None
            ),
            "action_candidates_v2": {
                "mode": (
                    "shadow"
                    if action_candidate_builder_mode == "shadow" or commitment_router_mode != "off"
                    else "off"
                ),
                "version": action_candidate_builder_version,
                "error_count": action_candidate_builder_error_count,
                "records": [
                    item.model_dump(mode="json") for item in action_candidates_shadow
                ],
                "executed": bool(action_candidates_shadow),
            },
            "proposal_evidence_seeds_v3": {
                "executed": bool(evidence_seeds_shadow),
                "count": len(evidence_seeds_shadow),
                "records": [item.model_dump(mode="json") for item in evidence_seeds_shadow],
            },
            "commitment_router_v2": {
                "mode": commitment_router_mode,
                "version": commitment_router_version,
                "active_types": list(commitment_router_active_types),
                "error_count": commitment_router_error_count,
                "suppressed_event_count": commitment_router_suppressed_event_count,
                "summary": commitment_router_summary,
                "decisions": [
                    {
                        "candidate_id": item.candidate_id,
                        "route": item.route.value,
                        "authority_kind": item.authority_kind.value,
                        "reasons": list(item.reasons),
                    }
                    for item in commitment_decisions_shadow
                ],
                "executed": commitment_router_mode == "assist",
            },
            "action_canonicalization_v2": {
                "mode": action_canonicalization_mode,
                "version": action_canonicalization_version,
                "error_count": action_canonicalization_error_count,
                "records": action_canonicalization_records,
                "executed": False,
            },
            "recap_reconciliation_v2": {
                "mode": recap_reconciliation_mode,
                "fragment_shadow_count": reduction_diagnostics.get(
                    "recap_fragment_shadow_count", 0
                ),
                "executed": False,
            },
            "owner_grounding_v2": {
                "mode": owner_grounding_mode,
                "error_count": owner_grounding_error_count,
                "records": owner_grounding_records,
                "executed": False,
            },
            "deadline_grounding_v2": {
                "mode": deadline_grounding_mode,
                "error_count": deadline_grounding_error_count,
                "records": deadline_grounding_records,
                "executed": False,
            },
            "candidate_router_shadow": {
                "summary": (
                    asdict(candidate_router_shadow) if candidate_router_shadow else None
                ),
                "evidence": [
                    item.model_dump(mode="json")
                    for item in candidate_evidence_shadow
                ],
                "decisions": [
                    item.model_dump(mode="json")
                    for item in candidate_decisions_shadow
                ],
                "executed": candidate_router_mode == "assist",
            },
            "ai_mutation_router": {
                "summary": asdict(ai_mutation_router_summary) if ai_mutation_router_summary else None,
                "candidates": ai_mutation_router_traces,
            },
            "task_create_proposals": {
                "enabled": task_create_proposal_enabled,
                "ai_enabled": ai_create_proposal_enabled,
                "call_count": task_create_proposal_call_count,
                "accepted_count": task_create_proposal_accepted_count,
                "no_action_count": task_create_proposal_no_action_count,
                "unresolved_count": task_create_proposal_unresolved_count,
                "rejected_count": task_create_proposal_rejected_count,
                "rejection_reasons": dict(sorted(proposal_rejection_reasons.items())),
            },
            "ai_quality_uplift_v1": {
                "mode": ai_quality_uplift_mode,
                "error_count": ai_quality_create_error_count,
                "create_checks": [
                    {
                        "candidate_id": item.candidate_id,
                        "focus_clause_id": item.focus_clause_id,
                        "eligible": item.eligible,
                        "reason": item.reason,
                    }
                    for item in ai_quality_create_records
                ],
                "selected_create_candidate_ids": ai_quality_selected_create_ids,
                "executed": False,
            },
            "ai_cost_gate": {
                "mode": ai_cost_gate_mode,
                "budget": {
                    "max_provider_calls": ai_cost_max_provider_calls_per_meeting,
                    "max_payload_characters": ai_cost_max_payload_characters,
                    "max_estimated_cost_usd": ai_cost_max_estimated_usd_per_meeting,
                },
                "snapshot": cost_gate_snapshot,
            },
            "task_semantic_linker_shadow": {
                "summary": (
                    asdict(task_semantic_linker_shadow)
                    if task_semantic_linker_shadow else None
                ),
                "results": [
                    item.model_dump(mode="json")
                    for item in task_semantic_linker_results
                ],
                "executed": False,
            },
            "context_retrieval_shadow": {
                "summary": (
                    asdict(context_retrieval_shadow)
                    if context_retrieval_shadow else None
                ),
                "bundles": [
                    item.model_dump(mode="json")
                    for item in context_retrieval_records
                ],
                "executed": False,
            },
            "context_compaction": {
                "before_clause_count": ai_context_clause_count_before_pruning,
                "after_unique_clause_count": len(ai_context_clause_ids),
                "mode": meeting_context_mode,
            },
            "reduction_diagnostics": reduction_diagnostics,
            "task_ledger": reconciliation.ledger.to_checkpoint(
                meeting.meeting_id,
                max((clause.order_index for clause in clauses), default=-1),
                _ai_usage_trace(ai_client),
            ),
            "reconciliation": {
                "applied_operations": [
                    asdict(item) for item in reconciliation.applied_operations
                ],
                "rejected_operations": reconciliation.rejected_operations,
            },
            "recap_scope": recap_scope,
            "openai_usage": _ai_usage_trace(ai_client),
        })
    return result


def process_meeting_by_version(
    meeting: MeetingInput,
    *,
    pipeline_version: str,
    ai_client: AiClient | None = None,
    speaker_aliases: dict[str, str] | None = None,
    summary_topic: str | None = None,
    ai_max_batch_context_clauses: int = 56,
    ai_cost_gate_mode: str = "off",
    ai_cost_max_provider_calls_per_meeting: int = 3,
    ai_cost_max_payload_characters: int = 20_000,
    ai_cost_max_estimated_usd_per_meeting: float | None = None,
    trace_enabled: bool = False,
    trace_directory: str = "evaluation/traces",
    meeting_context_mode: str = "assist",
    note_grounding_threshold: float = 0.72,
    note_grounding_margin: float = 0.12,
    max_meeting_topics: int = 12,
    max_topic_keywords: int = 8,
    topic_likely_threshold: float = 0.45,
    action_classifier_mode: str = "off",
    action_classifier_model_path: str | None = None,
    action_candidate_builder_mode: str = "off",
    action_candidate_builder_version: str = "action-candidate-v2",
    commitment_router_mode: str = "off",
    commitment_router_version: str = "commitment-router-v2",
    commitment_router_active_types: tuple[str, ...] = (
        "DIRECT_ASSIGNMENT", "SELF_COMMITMENT",
    ),
    action_canonicalization_mode: str = "off",
    action_canonicalization_version: str = "action-canonicalization-v2",
    recap_reconciliation_mode: str = "off",
    owner_grounding_mode: str = "off",
    deadline_grounding_mode: str = "off",
    candidate_router_mode: str = "off",
    action_clear_threshold: float = 0.82,
    action_ai_threshold: float = 0.45,
    candidate_threshold_version: str = "candidate-router-thresholds-v1",
    task_create_proposal_enabled: bool = False,
    ai_create_proposal_enabled: bool = False,
    ai_create_max_proposals_per_meeting: int = 3,
    ai_quality_uplift_mode: str = "off",
    task_semantic_linker_mode: str = "off",
    task_link_embedding_model_name: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    ),
    task_link_embedding_device: str = "cpu",
    task_link_embedding_fallback_enabled: bool = True,
    task_link_embedding_fallback_dimension: int = 384,
    task_link_semantic_weight: float = 0.55,
    task_link_lexical_weight: float = 0.20,
    task_link_topic_weight: float = 0.10,
    task_link_owner_weight: float = 0.10,
    task_link_recency_weight: float = 0.05,
    task_link_strong_threshold: float = 0.78,
    task_link_min_margin: float = 0.12,
    task_link_ai_threshold: float = 0.60,
    task_link_recency_horizon_clauses: int = 200,
    task_link_top_k: int = 5,
    task_link_scoring_version: str = "task-link-scoring-v1",
    context_retrieval_mode: str = "off",
    context_max_clauses: int = 30,
    context_max_characters: int = 12_000,
    context_max_tasks: int = 5,
    context_local_before: int = 3,
    context_local_after: int = 5,
    context_max_topic_clauses: int = 12,
    context_max_topics: int = 3,
    context_max_history_events_per_task: int = 3,
    context_topic_boundary_threshold: float = 0.42,
    context_topic_smoothing_window: int = 3,
    context_retrieval_version: str = "context-retriever-v1",
    ai_mutation_router_mode: str = "off",
    ai_mutation_prompt_version: str = "mutation-resolution-v2",
    ai_mutation_min_confidence: float = 0.70,
    note_dual_view_mode: str = "off",
    note_claim_max_transcript_clauses: int = 8,
    note_claim_max_topics: int = 3,
    note_claim_grounding_threshold: float = 0.72,
    note_claim_grounding_margin: float = 0.12,
    note_dual_view_version: str = "note-dual-view-v1",
    temporal_semantics_mode: str = "off",
    temporal_parser_version: str = "temporal-parser-v1",
    temporal_working_day_policy: str = "weekdays-only-v1",
    temporal_min_confidence: float = 1.0,
) -> PipelineResult:
    """Select V1/V2 or run V2 in shadow while returning V1's public result."""

    if pipeline_version not in {"v1", "v2", "shadow"}:
        raise ValueError("pipeline_version must be v1, v2, or shadow")
    v1_result = None
    if pipeline_version in {"v1", "shadow"}:
        v1_result = process_meeting(
            meeting, ai_client, speaker_aliases, summary_topic,
            ai_max_batch_context_clauses,
            ai_cost_gate_mode, ai_cost_max_provider_calls_per_meeting,
            ai_cost_max_payload_characters, ai_cost_max_estimated_usd_per_meeting,
            trace_enabled=trace_enabled, trace_directory=trace_directory,
            meeting_context_mode=meeting_context_mode,
            note_grounding_threshold=note_grounding_threshold,
            note_grounding_margin=note_grounding_margin,
            max_meeting_topics=max_meeting_topics,
            max_topic_keywords=max_topic_keywords,
            topic_likely_threshold=topic_likely_threshold,
            action_classifier_mode=action_classifier_mode,
            action_classifier_model_path=action_classifier_model_path,
            action_candidate_builder_mode=action_candidate_builder_mode,
            action_candidate_builder_version=action_candidate_builder_version,
            commitment_router_mode=commitment_router_mode,
            commitment_router_version=commitment_router_version,
            commitment_router_active_types=commitment_router_active_types,
            action_canonicalization_mode=action_canonicalization_mode,
            action_canonicalization_version=action_canonicalization_version,
            recap_reconciliation_mode=recap_reconciliation_mode,
            owner_grounding_mode=owner_grounding_mode,
            deadline_grounding_mode=deadline_grounding_mode,
            candidate_router_mode=candidate_router_mode,
            action_clear_threshold=action_clear_threshold,
            action_ai_threshold=action_ai_threshold,
            candidate_threshold_version=candidate_threshold_version,
            task_create_proposal_enabled=task_create_proposal_enabled,
            ai_create_proposal_enabled=ai_create_proposal_enabled,
            ai_create_max_proposals_per_meeting=ai_create_max_proposals_per_meeting,
            ai_quality_uplift_mode=ai_quality_uplift_mode,
            task_semantic_linker_mode=task_semantic_linker_mode,
            task_link_embedding_model_name=task_link_embedding_model_name,
            task_link_embedding_device=task_link_embedding_device,
            task_link_embedding_fallback_enabled=(
                task_link_embedding_fallback_enabled
            ),
            task_link_embedding_fallback_dimension=(
                task_link_embedding_fallback_dimension
            ),
            task_link_semantic_weight=task_link_semantic_weight,
            task_link_lexical_weight=task_link_lexical_weight,
            task_link_topic_weight=task_link_topic_weight,
            task_link_owner_weight=task_link_owner_weight,
            task_link_recency_weight=task_link_recency_weight,
            task_link_strong_threshold=task_link_strong_threshold,
            task_link_min_margin=task_link_min_margin,
            task_link_ai_threshold=task_link_ai_threshold,
            task_link_recency_horizon_clauses=(
                task_link_recency_horizon_clauses
            ),
            task_link_top_k=task_link_top_k,
            task_link_scoring_version=task_link_scoring_version,
            context_retrieval_mode=context_retrieval_mode,
            context_max_clauses=context_max_clauses,
            context_max_characters=context_max_characters,
            context_max_tasks=context_max_tasks,
            context_local_before=context_local_before,
            context_local_after=context_local_after,
            context_max_topic_clauses=context_max_topic_clauses,
            context_max_topics=context_max_topics,
            context_max_history_events_per_task=context_max_history_events_per_task,
            context_topic_boundary_threshold=context_topic_boundary_threshold,
            context_topic_smoothing_window=context_topic_smoothing_window,
            context_retrieval_version=context_retrieval_version,
            ai_mutation_router_mode=ai_mutation_router_mode,
            ai_mutation_prompt_version=ai_mutation_prompt_version,
            ai_mutation_min_confidence=ai_mutation_min_confidence,
            note_dual_view_mode=note_dual_view_mode,
            note_claim_max_transcript_clauses=note_claim_max_transcript_clauses,
            note_claim_max_topics=note_claim_max_topics,
            note_claim_grounding_threshold=note_claim_grounding_threshold,
            note_claim_grounding_margin=note_claim_grounding_margin,
            note_dual_view_version=note_dual_view_version,
            temporal_semantics_mode=temporal_semantics_mode,
            temporal_parser_version=temporal_parser_version,
            temporal_working_day_policy=temporal_working_day_policy,
            temporal_min_confidence=temporal_min_confidence,
        )
    if pipeline_version == "v1":
        assert v1_result is not None
        return v1_result

    # Import lazily to keep V1's default startup surface independent of V2.
    from .v2 import process_meeting_v2, to_pipeline_result_v2

    stages = preprocess_meeting(meeting, speaker_aliases)
    v2_result = process_meeting_v2(
        meeting,
        speaker_aliases,
        meeting_context_mode=meeting_context_mode,
        note_grounding_threshold=note_grounding_threshold,
        note_grounding_margin=note_grounding_margin,
        max_meeting_topics=max_meeting_topics,
        max_topic_keywords=max_topic_keywords,
        topic_likely_threshold=topic_likely_threshold,
    )
    v2_public_result = to_pipeline_result_v2(
        meeting.meeting_title, meeting.meeting_date, v2_result,
        {clause.clause_id: clause for clause in stages["clauses"]},
        meeting_note_present=meeting.meeting_note is not None,
    )
    v2_public_result.diagnostics.meeting_date_source = meeting.meeting_date_source
    if trace_enabled:
        write_pipeline_trace(trace_directory, meeting.meeting_id, "shadow", {
            "pipeline_version": "shadow",
            "v1_final_tasks": [asdict(item) for item in v1_result.tasks] if v1_result else [],
            "v2_final_tasks": [asdict(item) for item in v2_public_result.tasks],
            "v2_unresolved_event_ids": list(v2_result.unresolved_event_ids),
            "v2_decisions": [asdict(item) for item in v2_result.decisions],
            "v2_meeting_context": asdict(v2_result.meeting_context) if v2_result.meeting_context else None,
            "openai_usage": _ai_usage_trace(ai_client or DisabledAiClient()),
        })
    if pipeline_version == "shadow":
        assert v1_result is not None
        return v1_result
    return v2_public_result
