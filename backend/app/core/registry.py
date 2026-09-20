"""Safe registry for selecting a built-in processing core."""

from __future__ import annotations

import os
from collections.abc import Mapping

from .contracts import MeetingCore
from .v1_frozen import V1_FROZEN_CORE_ID, V1FrozenCore
from .v2_adaptive import V2_ADAPTIVE_CORE_ID, V2AdaptiveCore


CORE_ENVIRONMENT_VARIABLE = "MEETING_CORE"
DEFAULT_CORE_ID = V1_FROZEN_CORE_ID


class UnknownCoreError(ValueError):
    """Raised when a configured core id is not part of the built-in registry."""


# Keep this map explicit.  In particular, values from the environment are
# never treated as module or class paths and are never dynamically imported.
_CORE_FACTORIES: Mapping[str, type[MeetingCore]] = {
    V1_FROZEN_CORE_ID: V1FrozenCore,
    V2_ADAPTIVE_CORE_ID: V2AdaptiveCore,
}


def available_core_ids() -> tuple[str, ...]:
    """Return the stable ids that can be selected by configuration."""

    return tuple(_CORE_FACTORIES)


def get_core(core_id: str | None = None) -> MeetingCore:
    """Resolve a core from an explicit id or ``MEETING_CORE``.

    An unset variable selects the frozen V1 core.  Any set, unknown value
    (including an empty string) fails closed with ``UnknownCoreError``.
    """

    selected_id = (
        os.environ.get(CORE_ENVIRONMENT_VARIABLE)
        if core_id is None
        else core_id
    )
    if selected_id is None:
        selected_id = DEFAULT_CORE_ID
    factory = _CORE_FACTORIES.get(selected_id)
    if factory is None:
        known = ", ".join(available_core_ids())
        raise UnknownCoreError(
            f"Unknown meeting core {selected_id!r}; expected one of: {known}"
        )
    return factory()
