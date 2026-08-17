"""Selective AI fallback interfaces."""

from .client import (
    AiProviderFatalError,
    AiClient,
    AzureFoundryAiClient,
    DisabledAiClient,
    HttpAiClient,
    OpenAiResponsesClient,
)
from .event_extractor import (
    extract_contextual_commitment_events,
    extract_events_by_ai,
    extract_events_by_rule,
    extract_events_from_human_note,
    extract_recap_events,
)

__all__ = [
    "AiClient",
    "AiProviderFatalError",
    "AzureFoundryAiClient",
    "DisabledAiClient",
    "HttpAiClient",
    "OpenAiResponsesClient",
    "extract_events_by_ai",
    "extract_events_by_rule",
    "extract_events_from_human_note",
    "extract_contextual_commitment_events",
    "extract_recap_events",
]
