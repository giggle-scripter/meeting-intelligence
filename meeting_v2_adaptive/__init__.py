"""Optional V2 adaptive meeting core.

This package is a fixed, selectable extension boundary for the unified core
registry.  The release shell can omit the whole package and continue to run
the V1 core.  The implementation intentionally reuses the existing
experimental runner and artifact validation code; it does not move or expose
those experimental assets as part of the stable shell.
"""

from .core import (
    V2_ADAPTIVE_CORE_ID,
    V2_BASE_RUNTIME_MODEL_ID,
    V2_CHALLENGER_RUNTIME_MODEL_ID,
    V2_PIPELINE_VERSION,
    V2AdaptiveCore,
    V2AdaptiveUnavailableError,
)

__all__ = [
    "V2_ADAPTIVE_CORE_ID",
    "V2_BASE_RUNTIME_MODEL_ID",
    "V2_CHALLENGER_RUNTIME_MODEL_ID",
    "V2_PIPELINE_VERSION",
    "V2AdaptiveCore",
    "V2AdaptiveUnavailableError",
]
