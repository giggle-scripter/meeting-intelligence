"""Candidate scoring and context windows."""

from .ai_batcher import AiWindowBatch, batch_ai_windows
from .evidence import CandidateEvidence, build_candidate_evidence
from .router import (
    CandidateDecision,
    CandidateRoute,
    CandidateRouter,
    CandidateRouterConfig,
    CandidateRouterShadowSummary,
    summarize_candidate_decisions,
)
from .proposal import TaskCreateProposal, TaskCreateProposalResponse
from .scorer import choose_extraction_strategy, is_candidate
from .window_builder import build_candidate_windows
from .window_merger import merge_windows

__all__ = [
    "AiWindowBatch", "CandidateDecision", "CandidateEvidence", "CandidateRoute",
    "CandidateRouter", "CandidateRouterConfig", "CandidateRouterShadowSummary",
    "TaskCreateProposal", "TaskCreateProposalResponse",
    "batch_ai_windows",
    "build_candidate_evidence", "build_candidate_windows",
    "choose_extraction_strategy", "is_candidate", "merge_windows",
    "summarize_candidate_decisions",
]
