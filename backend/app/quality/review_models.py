"""Strict, reviewable contracts for quality-attribution runtime artifacts."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ReviewStatus(str, Enum):
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REVIEWED = "REVIEWED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AttributionKind(str, Enum):
    EXPECTED_EVIDENCE = "EXPECTED_EVIDENCE"
    MISSING = "MISSING"
    UNEXPECTED = "UNEXPECTED"
    FIELD_ERROR = "FIELD_ERROR"


class GroundedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_id: str
    text: str
    score: float = 0.0
    role: str = "SUGGESTED_SOURCE"


class AttributionRecord(BaseModel):
    """A suggested first divergence, never a substitute for reviewer evidence."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    case_id: str
    wave: str = ""
    labels: tuple[str, ...] = ()
    kind: AttributionKind
    task: dict[str, str]
    suggested_taxonomy: str
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_source_evidence: tuple[GroundedEvidence, ...] = ()
    candidate_window_ids: tuple[str, ...] = ()
    provenance_event_ids: tuple[str, ...] = ()
    provenance_task_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class ReviewQueueItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_id: str
    case_id: str
    queue_kind: str
    task: dict[str, str]
    suggested_taxonomy: str
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW
    evidence: tuple[GroundedEvidence, ...] = ()
    attribution_record_id: str
