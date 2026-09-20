"""Create contract-compatible output from reduced task states."""

from datetime import date, timedelta
import re

from ..dates import resolve_date_mention
from ..models import Clause, DateMention, FinalTask, PipelineDiagnostics, PipelineResult, TaskState
from ..preprocessing.unicode_normalizer import normalize_for_match
from .evidence_builder import build_evidence
from .summary_builder import build_summary


_HANDOFF_FROM_RE = re.compile(
    r"\s+(?:từ|from)\s+(?:thứ\s+[\wÀ-ỹ]+|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*$",
    re.I,
)
_HANDOFF_UNTIL_RE = re.compile(
    r"\s+(?:đến(?:\s+hết)?|until|through)\s+"
    r"(?:thứ\s+[\wÀ-ỹ]+|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*$",
    re.I,
)


def _handoff_base(task_name: str) -> str:
    without_boundary = _HANDOFF_FROM_RE.sub("", task_name)
    without_boundary = _HANDOFF_UNTIL_RE.sub("", without_boundary)
    return normalize_for_match(without_boundary)


def _apply_handoff_start_dates(tasks: list[FinalTask]) -> None:
    """Start a receiving shift on the day after the preceding shift ends."""

    ending_dates: dict[str, list[date]] = {}
    for task in tasks:
        if not task.due_date or not _HANDOFF_UNTIL_RE.search(task.task_name):
            continue
        ending_dates.setdefault(_handoff_base(task.task_name), []).append(
            date.fromisoformat(task.due_date)
        )

    for task in tasks:
        if not _HANDOFF_FROM_RE.search(task.task_name):
            continue
        previous_ends = ending_dates.get(_handoff_base(task.task_name), [])
        if previous_ends:
            task.start_date = (max(previous_ends) + timedelta(days=1)).isoformat()


def _deadline_state_key(mention: DateMention) -> tuple:
    return (
        mention.relation,
        mention.date_type,
        mention.target_weekday,
        mention.week_scope,
        mention.explicit_day,
        mention.explicit_month,
        mention.explicit_year,
        mention.duration_days,
        mention.relative_day_offset,
    )


def _best_deadline_text_mention(
    state: TaskState,
    mentions: dict[str, DateMention],
    meeting_date: str,
    start_date: str,
) -> DateMention | None:
    current = mentions.get(state.deadline_mention_id)
    if current is None:
        return None
    current_key = _deadline_state_key(current)
    current_resolved = resolve_date_mention(meeting_date, start_date, current)
    candidates = [
        mention
        for mention_id in state.deadline_mention_history
        if (mention := mentions.get(mention_id)) is not None
        and (
            _deadline_state_key(mention) == current_key
            or (
                current_resolved
                and resolve_date_mention(meeting_date, start_date, mention)
                == current_resolved
                and (
                    mention.relation == current.relation
                    or mention.relation == "BEFORE_TIME"
                )
            )
        )
    ]
    if current not in candidates:
        candidates.append(current)

    def quality(mention: DateMention) -> tuple[int, int, int]:
        timed_boundary = int(mention.relation == "BEFORE_TIME")
        explicit_label = int(bool(re.search(
            r"\b(?:deadline|hạn(?:\s+chót)?|chậm\s+nhất|trước|before|by)\b",
            mention.raw_text,
            re.I,
        )))
        return timed_boundary, explicit_label, len(mention.raw_text.strip())

    return max(candidates, key=quality)


def _task_start_date(
    state: TaskState,
    clauses_by_id: dict[str, Clause],
    mentions: dict[str, DateMention],
    meeting_date: str,
) -> tuple[str, bool]:
    candidates = [
        mention
        for mention in mentions.values()
        if mention.purpose == "START_DATE"
        and mention.clause_id in state.source_clause_ids
    ]
    candidates.sort(
        key=lambda mention: (
            clauses_by_id.get(mention.clause_id).order_index
            if mention.clause_id in clauses_by_id
            else -10_000,
            mention.span_start,
        ),
        reverse=True,
    )
    for mention in candidates:
        resolved = resolve_date_mention(meeting_date, meeting_date, mention)
        if resolved:
            return resolved, True
    return meeting_date, False


def build_pipeline_result(
    meeting_title: str,
    meeting_date: str,
    states: list[TaskState],
    clauses_by_id: dict[str, Clause],
    mentions: dict[str, DateMention],
    diagnostics: PipelineDiagnostics,
    unresolved_window_ids: list[str],
    summary_topic: str | None = None,
    no_active_reason: str | None = None,
    meeting_note_present: bool | None = None,
    temporal_due_dates: dict[str, str] | None = None,
) -> PipelineResult:
    final_tasks: list[FinalTask] = []
    explicit_start_count = 0
    for state in states:
        if state.status in {"PROVISIONAL", "CANCELLED", "REJECTED"}:
            continue
        mention = mentions.get(state.deadline_mention_id)
        start_date, is_explicit_start = _task_start_date(
            state,
            clauses_by_id,
            mentions,
            meeting_date,
        )
        explicit_start_count += int(is_explicit_start)
        text_mention = _best_deadline_text_mention(
            state,
            mentions,
            meeting_date,
            start_date,
        )
        final_tasks.append(
            FinalTask(
                state.task_name,
                state.assignee,
                start_date,
                (temporal_due_dates or {}).get(
                    state.deadline_mention_id,
                    resolve_date_mention(meeting_date, start_date, mention),
                ),
                text_mention.raw_text if text_mention else "",
                build_evidence(state.source_clause_ids, clauses_by_id),
            )
        )
    _apply_handoff_start_dates(final_tasks)
    diagnostics.explicit_task_start_date_count = explicit_start_count
    return PipelineResult(
        meeting_title,
        build_summary(
            summary_topic if summary_topic is not None else meeting_title,
            final_tasks,
            no_active_reason,
            meeting_note_present,
        ),
        final_tasks,
        diagnostics,
        unresolved_window_ids,
    )
