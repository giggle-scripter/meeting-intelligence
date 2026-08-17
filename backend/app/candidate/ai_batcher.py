"""Bounded batches for AI-only candidate windows."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import CandidateWindow
from ..utils.ids import make_id


@dataclass(frozen=True)
class AiWindowBatch:
    """One provider request plus the original windows it represents."""

    window: CandidateWindow
    source_window_ids: tuple[str, ...]
    primary_clause_ids_by_window: dict[str, frozenset[str]]


def batch_ai_windows(
    windows: list[CandidateWindow],
    max_context_clauses: int = 48,
    max_gap_clauses: int = 3,
    group_keys: dict[str, tuple[str, ...]] | None = None,
) -> list[AiWindowBatch]:
    """Combine chronological AI windows without exceeding a context budget.

    A batch retains the primary clause IDs of every source window so the caller
    can mark only the windows actually addressed by the returned AI events as
    resolved. A single oversized window is kept intact rather than truncated.
    """

    if max_context_clauses <= 0:
        raise ValueError("max_context_clauses must be greater than zero")
    if max_gap_clauses < 0:
        raise ValueError("max_gap_clauses must not be negative")

    batches: list[AiWindowBatch] = []
    group_keys = group_keys or {}
    current: list[CandidateWindow] = []
    context_ids: list[str] = []

    def flush() -> None:
        nonlocal current, context_ids
        if not current:
            return
        primary_ids = list(
            dict.fromkeys(
                clause_id
                for source in current
                for clause_id in source.primary_clause_ids
            )
        )
        batch_window = CandidateWindow(
            window_id=make_id("AI-BATCH", len(batches) + 1),
            primary_clause_ids=primary_ids,
            context_clause_ids=context_ids,
            score=max(source.score for source in current),
            extraction_strategy="AI",
        )
        batches.append(
            AiWindowBatch(
                window=batch_window,
                source_window_ids=tuple(source.window_id for source in current),
                primary_clause_ids_by_window={
                    source.window_id: frozenset(source.primary_clause_ids)
                    for source in current
                },
            )
        )
        current = []
        context_ids = []

    def clause_number(clause_id: str) -> int | None:
        try:
            return int(clause_id.rsplit("-", 1)[-1])
        except ValueError:
            return None

    def is_nearby(source: CandidateWindow) -> bool:
        if not context_ids or not source.context_clause_ids:
            return False
        current_end = clause_number(context_ids[-1])
        source_start = clause_number(source.context_clause_ids[0])
        if current_end is None or source_start is None:
            return False
        return source_start - current_end <= max_gap_clauses

    for source in windows:
        proposed_context_ids = list(
            dict.fromkeys(context_ids + source.context_clause_ids)
        )
        if current and (
            len(proposed_context_ids) > max_context_clauses
            or not is_nearby(source)
            or (
                group_keys.get(current[0].window_id, ())
                != group_keys.get(source.window_id, ())
            )
        ):
            flush()
            proposed_context_ids = list(source.context_clause_ids)
        current.append(source)
        context_ids = proposed_context_ids
    flush()
    return batches
