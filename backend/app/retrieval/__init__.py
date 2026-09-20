"""Semantic retrieval primitives for bounded task identity lookup."""

from .context_retriever import (
    ContextBundle,
    ContextRetrievalConfig,
    ContextRetriever,
    ContextSelection,
    TaskContextEvidence,
)
from .context_shadow import (
    ContextRetrievalShadowRecord,
    ContextRetrievalShadowSummary,
    evaluate_context_retrieval_shadow,
)
from .scoring import TaskLinkScoringConfig, TaskScore
from .task_index import MutationQuery, TaskIndex, TaskRepresentation
from .task_retriever import (
    TaskRetrievalCandidate,
    TaskRetrievalResult,
    TaskRetriever,
)
from .shadow import (
    TaskLinkerShadowRecord,
    TaskLinkerShadowSummary,
    evaluate_task_linker_shadow,
)
from .topic_index import TopicClauseMatch, TopicIndex, TopicRecord

__all__ = [
    "ContextBundle",
    "ContextRetrievalConfig",
    "ContextRetrievalShadowRecord",
    "ContextRetrievalShadowSummary",
    "ContextRetriever",
    "ContextSelection",
    "MutationQuery",
    "TaskIndex",
    "TaskLinkScoringConfig",
    "TaskRepresentation",
    "TaskRetrievalCandidate",
    "TaskRetrievalResult",
    "TaskRetriever",
    "TaskScore",
    "TaskLinkerShadowSummary",
    "TaskLinkerShadowRecord",
    "TaskContextEvidence",
    "TopicClauseMatch",
    "TopicIndex",
    "TopicRecord",
    "evaluate_context_retrieval_shadow",
    "evaluate_task_linker_shadow",
]
