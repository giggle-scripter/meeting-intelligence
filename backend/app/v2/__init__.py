"""Version 2 task pipeline.

V2 is intentionally isolated from the V1 API path so it can run in shadow
mode until its quality gates are met.
"""

from .pipeline import V2PipelineResult, process_meeting_v2
from .serializer import to_pipeline_result_v2

__all__ = ["V2PipelineResult", "process_meeting_v2", "to_pipeline_result_v2"]
