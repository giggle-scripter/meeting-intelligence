"""Typed contracts shared by selectable processing cores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..models import MeetingInput, PipelineResult


# The public pipeline output remains the domain result used by the existing
# API.  Keeping this alias here gives future cores one stable return contract
# without introducing a second serializer or output model.
PipelineOutput = PipelineResult


@dataclass(frozen=True, slots=True)
class CoreCapabilities:
    """Capabilities advertised by a processing core.

    ``adaptive`` is explicit even for a frozen core so callers can make a
    policy decision from typed metadata rather than infer it from an id.
    """

    adaptive: bool
    # Keep optional defaults for compatibility with small custom cores that
    # only advertised ``adaptive`` before the unified API exposed audit
    # metadata.  Built-in cores always provide all three values explicitly.
    pipeline_version: str = ""
    runtime_model_id: str = ""
    supports_meeting_note: bool = True


class MeetingCore(Protocol):
    """Minimal processing interface implemented by every selectable core."""

    core_id: str
    capabilities: CoreCapabilities

    def process(self, meeting: MeetingInput, **options: Any) -> PipelineOutput:
        """Process one meeting using the core's implementation."""
