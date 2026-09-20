"""Adapter exposing the existing V1 pipeline through the core contract."""

from __future__ import annotations

from typing import Any

from ..models import MeetingInput
from .contracts import CoreCapabilities, PipelineOutput


V1_FROZEN_CORE_ID = "v1-frozen"


class V1FrozenCore:
    """Compatibility adapter for the release's existing deterministic V1 path."""

    core_id = V1_FROZEN_CORE_ID
    capabilities = CoreCapabilities(
        adaptive=False,
        pipeline_version="v1",
        runtime_model_id=V1_FROZEN_CORE_ID,
        supports_meeting_note=True,
    )

    def process(self, meeting: MeetingInput, **options: Any) -> PipelineOutput:
        """Delegate directly to V1, preserving its behavior and options."""

        # Keep the registry's import surface light.  The adapter still calls
        # the concrete V1 entry point directly when processing is requested.
        from ..pipeline import process_meeting

        return process_meeting(meeting, **options)
