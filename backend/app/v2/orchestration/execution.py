"""Bounded segment execution and version-aware idempotency helpers."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Awaitable, Callable, TypeVar

from ..extraction import ExtractionResponseV2, validate_response
from ..segmentation import SegmentV2


PROVIDER_ERROR = "PROVIDER_ERROR"
T = TypeVar("T")


def versioned_idempotency_key(content_hash: str, pipeline_version: str, prompt_version: str, model: str, extraction_config_hash: str) -> str:
    value = "|".join((content_hash, pipeline_version, prompt_version, model, extraction_config_hash))
    return sha256(value.encode("utf-8")).hexdigest()


async def extract_segments(segments: list[SegmentV2], extractor: Callable[[SegmentV2], Awaitable[ExtractionResponseV2]], supplied_date_ids: set[str], concurrency: int = 4) -> tuple[dict[str, ExtractionResponseV2], dict[str, str]]:
    """Execute segment extraction concurrently but return deterministic mapping.

    A provider failure is an execution error, never a ``NO_EVENT`` decision.
    Successful segment responses are retained so a caller can retry only failed
    segments or return a partial job result.
    """

    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    semaphore = asyncio.Semaphore(concurrency)
    results: dict[str, ExtractionResponseV2] = {}
    failures: dict[str, str] = {}

    async def run(segment: SegmentV2) -> None:
        async with semaphore:
            try:
                response = await extractor(segment)
                validate_response(
                    response,
                    set(segment.primary_clause_ids),
                    set(segment.context_clause_ids),
                    supplied_date_ids,
                )
                results[segment.segment_id] = response
            except Exception:
                failures[segment.segment_id] = PROVIDER_ERROR

    await asyncio.gather(*(run(segment) for segment in segments))
    return ({key: results[key] for key in sorted(results)}, {key: failures[key] for key in sorted(failures)})
