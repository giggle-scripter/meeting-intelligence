"""Build writable review queues from runtime attribution without changing truth data."""

from __future__ import annotations

from .review_models import AttributionKind, AttributionRecord, ReviewQueueItem


def build_review_queue(records: list[AttributionRecord]) -> list[ReviewQueueItem]:
    items: list[ReviewQueueItem] = []
    for record in records:
        if record.kind in {AttributionKind.EXPECTED_EVIDENCE, AttributionKind.MISSING}:
            queue_kind = "TASK_EVIDENCE"
        elif record.kind is AttributionKind.UNEXPECTED:
            queue_kind = "CANDIDATE_NEGATIVE"
        else:
            continue
        items.append(ReviewQueueItem(
            queue_id=f"QUEUE-{record.record_id[5:]}", case_id=record.case_id,
            queue_kind=queue_kind, task=record.task,
            suggested_taxonomy=record.suggested_taxonomy,
            evidence=record.suggested_source_evidence,
            attribution_record_id=record.record_id,
        ))
    return items
