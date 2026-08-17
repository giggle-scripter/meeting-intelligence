"""Temporary public-output adapter for feature-flagged V2 runs."""

from ..models import FinalTask, PipelineDiagnostics, PipelineResult
from ..output.evidence_builder import build_evidence
from ..output.summary_builder import build_summary
from .models import TaskStatus
from .pipeline import V2PipelineResult


def to_pipeline_result_v2(
    meeting_title: str,
    meeting_date: str,
    result: V2PipelineResult,
    clauses_by_id: dict,
    meeting_note_present: bool | None = None,
) -> PipelineResult:
    mentions = {mention.mention_id: mention for mention in result.date_mentions}
    start_dates_by_clause = {
        mention.clause_id: mention.normalized_value
        for mention in result.date_mentions
        if mention.purpose == "START_DATE" and mention.normalized_value
    }
    tasks = [
        FinalTask(
            task_name=entity.canonical_action,
            assignee=entity.assignee,
            start_date=next(
                (
                    start_dates_by_clause[clause_id]
                    for clause_id in reversed(entity.source_clause_ids)
                    if clause_id in start_dates_by_clause
                ),
                meeting_date,
            ),
            due_date=mentions[entity.deadline_mention_id].normalized_value if entity.deadline_mention_id in mentions else "",
            due_date_text=mentions[entity.deadline_mention_id].raw_text if entity.deadline_mention_id in mentions else "",
            evidence=build_evidence(entity.source_clause_ids, clauses_by_id),
        )
        for entity in result.entities
        if entity.status is TaskStatus.ACTIVE
    ]
    diagnostics = PipelineDiagnostics(
        clause_count=len(clauses_by_id),
        rule_event_count=sum(len(resolution.events) for resolution in result.decisions),
        unresolved_window_count=len(result.unresolved_event_ids),
        effective_meeting_date=meeting_date,
        explicit_task_start_date_count=sum(
            any(clause_id in start_dates_by_clause for clause_id in entity.source_clause_ids)
            for entity in result.entities
            if entity.status is TaskStatus.ACTIVE
        ),
    )
    return PipelineResult(
        meeting_title,
        build_summary(meeting_title, tasks, meeting_note_present=meeting_note_present),
        tasks,
        diagnostics,
        list(result.unresolved_event_ids),
    )
