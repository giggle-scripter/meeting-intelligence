"""Merge strongly overlapping candidate windows."""

from ..models import CandidateWindow
from ..utils.ids import make_id


def _overlap_ratio(left: CandidateWindow, right: CandidateWindow) -> float:
    a, b = set(left.context_clause_ids), set(right.context_clause_ids)
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _clause_number(clause_id: str) -> int | None:
    try:
        return int(clause_id.rsplit("-", 1)[-1])
    except ValueError:
        return None


def _nearby(left: CandidateWindow, right: CandidateWindow, max_gap: int) -> bool:
    left_end = _clause_number(left.context_clause_ids[-1])
    right_start = _clause_number(right.context_clause_ids[0])
    if left_end is None or right_start is None:
        return False
    return right_start - left_end <= max_gap


def merge_windows(
    windows: list[CandidateWindow],
    max_context_clauses: int = 80,
    max_gap_clauses: int = 3,
) -> list[CandidateWindow]:
    merged: list[CandidateWindow] = []
    for window in windows:
        previous = merged[-1] if merged else None
        combined_context = (
            list(dict.fromkeys(previous.context_clause_ids + window.context_clause_ids))
            if previous
            else []
        )
        can_merge_nearby = (
            previous is not None
            and previous.extraction_strategy == "AI"
            and window.extraction_strategy == "AI"
            and len(combined_context) <= max_context_clauses
            and _nearby(previous, window, max_gap_clauses)
        )
        if previous and (
            _overlap_ratio(previous, window) >= 0.60 or can_merge_nearby
        ):
            previous.primary_clause_ids = list(dict.fromkeys(previous.primary_clause_ids + window.primary_clause_ids))
            previous.context_clause_ids = combined_context
            previous.score = max(previous.score, window.score)
            if "AI" in {previous.extraction_strategy, window.extraction_strategy}:
                previous.extraction_strategy = "AI"
        else:
            merged.append(window)
    for index, window in enumerate(merged, start=1):
        window.window_id = make_id("WIN", index)
    return merged
