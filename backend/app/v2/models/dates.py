"""Date mentions retain their exact source span and resolution outcome."""

from dataclasses import dataclass
from enum import Enum


class DateResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    EVENT_DEPENDENT = "EVENT_DEPENDENT"
    INVALID_DATE = "INVALID_DATE"
    AMBIGUOUS = "AMBIGUOUS"
    MISSING_ANCHOR = "MISSING_ANCHOR"
    UNSUPPORTED_WORKING_DAY = "UNSUPPORTED_WORKING_DAY"


@dataclass(frozen=True)
class DateMentionV2:
    mention_id: str
    clause_id: str
    span_start: int
    span_end: int
    raw_text: str
    relation: str
    date_type: str
    purpose: str = "DEADLINE"
    normalized_value: str = ""
    resolution_status: DateResolutionStatus = DateResolutionStatus.MISSING_ANCHOR
