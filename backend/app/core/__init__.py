"""Selectable meeting-processing cores.

The core boundary is deliberately small.  A core accepts the existing
``MeetingInput`` contract and returns the existing ``PipelineResult`` shape;
the registry is the only place that maps a configured core id to code.
"""

from .contracts import CoreCapabilities, MeetingCore, PipelineOutput
from .registry import (
    DEFAULT_CORE_ID,
    CORE_ENVIRONMENT_VARIABLE,
    UnknownCoreError,
    available_core_ids,
    get_core,
)
from .v1_frozen import V1_FROZEN_CORE_ID, V1FrozenCore
from .v2_adaptive import (
    V2_ADAPTIVE_CORE_ID,
    V2AdaptiveCore,
    V2AdaptiveUnavailableError,
)

__all__ = [
    "CORE_ENVIRONMENT_VARIABLE",
    "DEFAULT_CORE_ID",
    "CoreCapabilities",
    "MeetingCore",
    "PipelineOutput",
    "UnknownCoreError",
    "V1FrozenCore",
    "V1_FROZEN_CORE_ID",
    "V2AdaptiveCore",
    "V2AdaptiveUnavailableError",
    "V2_ADAPTIVE_CORE_ID",
    "available_core_ids",
    "get_core",
]
