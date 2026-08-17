"""Candidate scoring and context windows."""

from .ai_batcher import AiWindowBatch, batch_ai_windows
from .scorer import choose_extraction_strategy, is_candidate
from .window_builder import build_candidate_windows
from .window_merger import merge_windows

__all__ = [
    "AiWindowBatch", "batch_ai_windows", "build_candidate_windows",
    "choose_extraction_strategy", "is_candidate", "merge_windows",
]
