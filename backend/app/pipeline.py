"""Python-first orchestration; AI is invoked only for ambiguous windows."""

from __future__ import annotations

import re
import logging
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
    candidate_router_mode: str = "off",
    action_clear_threshold: float = 0.82,
    action_ai_threshold: float = 0.45,
    candidate_threshold_version: str = "candidate-router-thresholds-v1",
) -> PipelineResult:
    ai_client = ai_client or DisabledAiClient()
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
    if action_classifier_mode not in {"off", "shadow"}:
        raise ValueError("action_classifier_mode must be off or shadow")
    if candidate_router_mode not in {"off", "shadow"}:
        raise ValueError("candidate_router_mode must be off or shadow")
    if candidate_router_mode == "shadow" and action_classifier_mode != "shadow":
        raise ValueError(
            "candidate_router_mode=shadow requires action_classifier_mode=shadow"
        )
    meeting_context = None
    note_cues_by_clause = {}
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
        )
        note_cues_by_clause = build_note_cue_index(meeting_context)
        if meeting_context_mode == "assist":
            annotations = apply_note_cues_to_annotations(
                annotations,
                note_cues_by_clause,
            )
    action_classifier_shadow = None
    action_predictions_by_clause = {}
    action_classifier_error_count = 0
    if action_classifier_mode == "shadow":
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
    candidate_evidence_shadow = []
    candidate_decisions_shadow = []
    candidate_router_shadow = None
    candidate_router_error_count = 0
    if candidate_router_mode == "shadow":
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
    clauses_by_id = {clause.clause_id: clause for clause in clauses}
    events = []
    if meeting_context_mode == "assist" and meeting.meeting_note:
        note_events, note_clauses = extract_events_from_human_note(
            meeting.meeting_note,
            meeting_context,
            clauses_by_id,
            mentions,
            start_sequence=0,
        )
        clauses_by_id.update(note_clauses)
        events.extend(note_events)
    events.extend(
        extract_provisional_task_references(clauses, start_sequence=len(events))
    )
    unresolved: list[str] = []
    ai_window_count = 0
    ai_provider_call_count = 0
    ai_context_clause_ids: set[str] = set()
    ai_context_clause_count_before_pruning = 0
    ai_fallback_error_count = 0
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

    ai_batches = batch_ai_windows(
        pending_ai_windows,
        ai_max_batch_context_clauses,
        group_keys=ai_group_keys,
    )
    for batch in ai_batches:
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
        except (httpx.HTTPError, ValueError) as exc:
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
    events_before_deduplication = list(events)
    events = deduplicate_events(events)
    ledger = reduce_task_events_to_ledger(events)
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
    diagnostics = PipelineDiagnostics(
        caption_count=stages["original_caption_count"],
        deduplicated_caption_count=len(stages["captions"]),
        turn_count=len(stages["turns"]), sentence_count=len(stages["sentences"]),
        clause_count=len(clauses), candidate_window_count=len(windows),
        rule_event_count=sum(
            event.extraction_source.startswith("RULE") for event in events
        ),
        ai_event_count=sum(event.extraction_source == "AI" for event in events),
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
        recap_scope=recap_scope,
        meeting_date_source=meeting.meeting_date_source,
        effective_meeting_date=meeting.meeting_date,
        action_classifier_mode=action_classifier_mode,
        action_classifier_version=(
            action_classifier_shadow.classifier_version
            if action_classifier_shadow
            else ("unavailable" if action_classifier_mode == "shadow" else "disabled")
        ),
        embedding_model_version=(
            action_classifier_shadow.embedding_model_version
            if action_classifier_shadow
            else ("unavailable" if action_classifier_mode == "shadow" else "disabled")
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
        candidate_router_mode=candidate_router_mode,
        candidate_router_version=(
            candidate_router_shadow.router_version
            if candidate_router_shadow
            else ("unavailable" if candidate_router_mode == "shadow" else "disabled")
        ),
        candidate_threshold_version=(
            candidate_router_shadow.threshold_version
            if candidate_router_shadow
            else (
                candidate_threshold_version
                if candidate_router_mode == "shadow"
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
            candidate_router_shadow.ai_create_check_suppressed_count
            if candidate_router_shadow
            else 0
        ),
        candidate_router_error_count=candidate_router_error_count,
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
            "note_cues_by_clause": {
                clause_id: [asdict(cue) for cue in cues]
                for clause_id, cues in note_cues_by_clause.items()
            },
            "action_classifier_shadow": (
                asdict(action_classifier_shadow) if action_classifier_shadow else None
            ),
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
    candidate_router_mode: str = "off",
    action_clear_threshold: float = 0.82,
    action_ai_threshold: float = 0.45,
    candidate_threshold_version: str = "candidate-router-thresholds-v1",
) -> PipelineResult:
    """Select V1/V2 or run V2 in shadow while returning V1's public result."""

    if pipeline_version not in {"v1", "v2", "shadow"}:
        raise ValueError("pipeline_version must be v1, v2, or shadow")
    v1_result = None
    if pipeline_version in {"v1", "shadow"}:
        v1_result = process_meeting(
            meeting, ai_client, speaker_aliases, summary_topic, ai_max_batch_context_clauses,
            trace_enabled=trace_enabled, trace_directory=trace_directory,
            meeting_context_mode=meeting_context_mode,
            note_grounding_threshold=note_grounding_threshold,
            note_grounding_margin=note_grounding_margin,
            max_meeting_topics=max_meeting_topics,
            max_topic_keywords=max_topic_keywords,
            topic_likely_threshold=topic_likely_threshold,
            action_classifier_mode=action_classifier_mode,
            action_classifier_model_path=action_classifier_model_path,
            candidate_router_mode=candidate_router_mode,
            action_clear_threshold=action_clear_threshold,
            action_ai_threshold=action_ai_threshold,
            candidate_threshold_version=candidate_threshold_version,
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
