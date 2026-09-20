"""Safe registry for selecting a built-in processing core."""

from __future__ import annotations

import os
import importlib
from collections.abc import Mapping

from .contracts import MeetingCore, V2AdaptiveUnavailableError
from .v1_frozen import V1_FROZEN_CORE_ID, V1FrozenCore


CORE_ENVIRONMENT_VARIABLE = "MEETING_CORE"
DEFAULT_CORE_ID = V1_FROZEN_CORE_ID
_OPTIONAL_V2_PACKAGE = "meeting_v2_adaptive"
_OPTIONAL_V2_CORE_ID = "v2-adaptive"


class UnknownCoreError(ValueError):
    """Raised when a configured core id is not part of the built-in registry."""


_V1_CORE_FACTORIES: Mapping[str, type[MeetingCore]] = {
    V1_FROZEN_CORE_ID: V1FrozenCore,
}


def _core_factories() -> dict[str, type[MeetingCore]]:
    """Return built-ins plus the fixed optional package when it is present."""

    factories = dict(_V1_CORE_FACTORIES)
    try:
        optional_package = importlib.import_module(_OPTIONAL_V2_PACKAGE)
        optional_core = getattr(optional_package, "V2AdaptiveCore")
    except (ImportError, AttributeError):
        return factories
    factories[_OPTIONAL_V2_CORE_ID] = optional_core
    return factories


def available_core_ids() -> tuple[str, ...]:
    """Return the stable ids that can be selected by configuration."""

    return tuple(_core_factories())


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
    factory = _core_factories().get(selected_id)
    if factory is None:
        if selected_id == _OPTIONAL_V2_CORE_ID:
            raise V2AdaptiveUnavailableError(
                "V2 adaptive core unavailable: optional package "
                f"{_OPTIONAL_V2_PACKAGE!r} is not installed"
            )
        known = ", ".join(available_core_ids())
        raise UnknownCoreError(
            f"Unknown meeting core {selected_id!r}; expected one of: {known}"
        )
    return factory()
