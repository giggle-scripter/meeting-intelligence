"""Shadow-safe V2 orchestration built on the existing transcript parser."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import MeetingInput
from ..pipeline import preprocess_meeting
from ..annotation import annotate_clauses
from .context import build_meeting_context
from .dates import date_mentions_v2
from .extraction import extract_segment_locally
from .models import DateMentionV2, MeetingContext, PrimaryResolution, TaskEntity
from .reduction import ReductionResult, reduce_task_events_v2
from .segmentation import SegmentV2, create_segments


PIPELINE_VERSION = "v2"
PROMPT_VERSION = "v2-decision-contract-1"


@dataclass(frozen=True)
class V2PipelineResult:
    pipeline_version: str
    segments: tuple[SegmentV2, ...]
    decisions: tuple[PrimaryResolution, ...]
    entities: tuple[TaskEntity, ...]
    unresolved_event_ids: tuple[str, ...]
    date_mentions: tuple[DateMentionV2, ...]
    meeting_context: MeetingContext | None = None

    @property
    def active_entities(self) -> tuple[TaskEntity, ...]:
        from .models import TaskStatus
        return tuple(entity for entity in self.entities if entity.status is TaskStatus.ACTIVE)


def process_meeting_v2(
    meeting: MeetingInput,
    speaker_aliases: dict[str, str] | None = None,
    target_segment_size: int = 50,
    overlap: int = 7,
    meeting_context_mode: str = "assist",
    note_grounding_threshold: float = 0.72,
    note_grounding_margin: float = 0.12,
    max_meeting_topics: int = 12,
    max_topic_keywords: int = 8,
    topic_likely_threshold: float = 0.45,
) -> V2PipelineResult:
    """Run deterministic V2 locally for shadow evaluation; V1 remains default."""

    stages = preprocess_meeting(meeting, speaker_aliases)
    clauses = stages["clauses"]
    clauses_by_id = {clause.clause_id: clause for clause in clauses}
    mentions = date_mentions_v2(clauses, meeting.meeting_date)
    if meeting_context_mode not in {"off", "assist", "shadow"}:
        raise ValueError("meeting_context_mode must be off, assist, or shadow")
    annotations = annotate_clauses(
        clauses,
        {
            item.clause_id
            for item in mentions.values()
            if item.purpose != "MEETING_DATE"
        },
    )
    context = (
        build_meeting_context(
            meeting, clauses, annotations, mentions,
            grounding_threshold=note_grounding_threshold,
            grounding_margin=note_grounding_margin,
            max_topics=max_meeting_topics,
            max_topic_keywords=max_topic_keywords,
            topic_likely_threshold=topic_likely_threshold,
        )
        if meeting_context_mode != "off" else None
    )
    segments = create_segments(clauses, target_size=target_segment_size, overlap=overlap)
    decisions = tuple(
        resolution
        for segment in segments
        for resolution in extract_segment_locally(
            segment,
            clauses_by_id,
            mentions,
            annotations,
            context.clause_relevance if context and meeting_context_mode == "assist" else None,
        )
    )
    # The contract guarantees one decision per primary clause; overlap cannot
    # duplicate an event because only primary clauses are anchors.
    events = [event for resolution in decisions for event in resolution.events]
    reduced: ReductionResult = reduce_task_events_v2(meeting.meeting_id, events)
    return V2PipelineResult(
        pipeline_version=PIPELINE_VERSION,
        segments=tuple(segments),
        decisions=decisions,
        entities=tuple(reduced.entities.values()),
        unresolved_event_ids=tuple(reduced.unresolved_event_ids),
        date_mentions=tuple(mentions.values()),
        meeting_context=context,
    )
