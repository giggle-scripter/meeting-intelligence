"""Build globally ordered primary/context segments from one parsed transcript."""

from dataclasses import dataclass

from ...models import Clause


@dataclass(frozen=True)
class SegmentV2:
    segment_id: str
    primary_clause_ids: tuple[str, ...]
    context_clause_ids: tuple[str, ...]


def create_segments(clauses: list[Clause], target_size: int = 50, overlap: int = 7, hard_maximum: int = 80) -> list[SegmentV2]:
    if not 1 <= target_size <= hard_maximum:
        raise ValueError("target_size must be within 1..hard_maximum")
    if overlap < 0:
        raise ValueError("overlap must not be negative")
    segments: list[SegmentV2] = []
    for start in range(0, len(clauses), target_size):
        primary = clauses[start:min(start + target_size, len(clauses))]
        if len(primary) > hard_maximum:
            raise AssertionError("segment primary range exceeded hard maximum")
        context_start = max(0, start - overlap)
        context_end = min(len(clauses), start + len(primary) + overlap)
        segments.append(
            SegmentV2(
                segment_id=f"SEGMENT-{len(segments) + 1:04d}",
                primary_clause_ids=tuple(clause.clause_id for clause in primary),
                context_clause_ids=tuple(clause.clause_id for clause in clauses[context_start:context_end]),
            )
        )
    primary_ids = [clause_id for segment in segments for clause_id in segment.primary_clause_ids]
    if primary_ids != [clause.clause_id for clause in clauses] or len(primary_ids) != len(set(primary_ids)):
        raise AssertionError("each clause must be primary in exactly one segment")
    return segments
