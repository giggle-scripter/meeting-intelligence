import asyncio

import pytest

from backend.app.v2.extraction import ExtractionResponseV2, validate_response
from backend.app.v2.models import (
    ExtractionDecision,
    PrimaryResolution,
    ReasonCode,
    TaskEventV2,
    TaskOperation,
)
from backend.app.v2.orchestration import extract_segments, versioned_idempotency_key
from backend.app.v2.segmentation import SegmentV2


def test_contract_requires_one_decision_for_every_primary_clause() -> None:
    response = ExtractionResponseV2((
        PrimaryResolution("C-1", ExtractionDecision.NO_EVENT, (), ReasonCode.PROGRESS_UPDATE),
    ))
    with pytest.raises(ValueError, match="cover every"):
        validate_response(response, {"C-1", "C-2"}, {"C-1", "C-2"}, set())


def test_contract_rejects_event_anchored_in_context_clause() -> None:
    event = TaskEventV2(
        "E-1", TaskOperation.CREATE, "C-2", ("C-2",), 2, action_patch="Viết tài liệu"
    )
    response = ExtractionResponseV2((
        PrimaryResolution("C-1", ExtractionDecision.EVENTS, (event,), ReasonCode.EXPLICIT_COMMITMENT),
    ))
    with pytest.raises(ValueError, match="anchored"):
        validate_response(response, {"C-1"}, {"C-1", "C-2"}, set())


def test_parallel_execution_keeps_provider_errors_out_of_no_event_decisions() -> None:
    segments = [
        SegmentV2("S-1", ("C-1",), ("C-1",)),
        SegmentV2("S-2", ("C-2",), ("C-2",)),
    ]

    async def extractor(segment: SegmentV2) -> ExtractionResponseV2:
        if segment.segment_id == "S-2":
            raise TimeoutError("provider timed out")
        return ExtractionResponseV2((
            PrimaryResolution("C-1", ExtractionDecision.NO_EVENT, (), ReasonCode.PROGRESS_UPDATE),
        ))

    results, failures = asyncio.run(extract_segments(segments, extractor, set(), concurrency=2))
    assert list(results) == ["S-1"]
    assert failures == {"S-2": "PROVIDER_ERROR"}


def test_idempotency_key_changes_with_pipeline_or_prompt_version() -> None:
    base = versioned_idempotency_key("content", "v2", "prompt-1", "model", "config")
    assert base != versioned_idempotency_key("content", "v1", "prompt-1", "model", "config")
    assert base != versioned_idempotency_key("content", "v2", "prompt-2", "model", "config")
