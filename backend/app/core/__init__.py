"""Selectable meeting-processing cores.

The core boundary is deliberately small.  A core accepts the existing
``MeetingInput`` contract and returns the existing ``PipelineResult`` shape;
the registry is the only place that maps a configured core id to code.
"""

import importlib

from .contracts import (
    CoreCapabilities,
    MeetingCore,
    PipelineOutput,
    V2AdaptiveUnavailableError,
)
from .registry import (
    DEFAULT_CORE_ID,
    CORE_ENVIRONMENT_VARIABLE,
    UnknownCoreError,
    available_core_ids,
    get_core,
)
from .v1_frozen import V1_FROZEN_CORE_ID, V1FrozenCore


_OPTIONAL_V2_EXPORTS = frozenset(
    {
        "V2_ADAPTIVE_CORE_ID",
        "V2_BASE_RUNTIME_MODEL_ID",
        "V2_CHALLENGER_RUNTIME_MODEL_ID",
        "V2_PIPELINE_VERSION",
        "V2AdaptiveCore",
    }
)


def __getattr__(name: str):
    """Load compatibility V2 exports only when a caller asks for one."""

    if name not in _OPTIONAL_V2_EXPORTS:
        raise AttributeError(name)
    optional_package = importlib.import_module("meeting_v2_adaptive")
    try:
        return getattr(optional_package, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc

__all__ = [
    "CORE_ENVIRONMENT_VARIABLE",
    "DEFAULT_CORE_ID",
    "CoreCapabilities",
    "MeetingCore",
    "PipelineOutput",
    "V2AdaptiveUnavailableError",
    "UnknownCoreError",
    "V1FrozenCore",
    "V1_FROZEN_CORE_ID",
    "available_core_ids",
    "get_core",
]
