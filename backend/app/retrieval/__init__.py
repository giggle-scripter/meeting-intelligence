"""Semantic retrieval primitives for bounded task identity lookup."""

from .scoring import TaskLinkScoringConfig, TaskScore
from .task_index import MutationQuery, TaskIndex, TaskRepresentation
from .task_retriever import (
    TaskRetrievalCandidate,
    TaskRetrievalResult,
    TaskRetriever,
)
from .shadow import TaskLinkerShadowSummary, evaluate_task_linker_shadow

__all__ = [
    "MutationQuery",
    "TaskIndex",
    "TaskLinkScoringConfig",
    "TaskRepresentation",
    "TaskRetrievalCandidate",
    "TaskRetrievalResult",
    "TaskRetriever",
    "TaskScore",
    "TaskLinkerShadowSummary",
    "evaluate_task_linker_shadow",
]
