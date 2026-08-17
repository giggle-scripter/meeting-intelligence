"""Adapt the existing parser while preserving V2 resolution status."""

from ...annotation import extract_date_mentions
from ...dates import resolve_date_mention
from ...models import Clause
from ..models import DateMentionV2, DateResolutionStatus


def date_mentions_v2(clauses: list[Clause], meeting_date: str) -> dict[str, DateMentionV2]:
    source_mentions = extract_date_mentions(clauses)
    result: dict[str, DateMentionV2] = {}
    for mention_id, source in source_mentions.items():
        resolved = resolve_date_mention(meeting_date, meeting_date, source)
        if resolved:
            status = DateResolutionStatus.RESOLVED
        elif source.date_type == "EVENT_DEPENDENT":
            status = DateResolutionStatus.EVENT_DEPENDENT
        elif source.date_type == "AMBIGUOUS":
            status = DateResolutionStatus.AMBIGUOUS
        elif source.date_type == "WORKING_DAY_DURATION":
            status = DateResolutionStatus.UNSUPPORTED_WORKING_DAY
        elif source.date_type in {"ABSOLUTE_DATE", "DAY_MONTH"}:
            status = DateResolutionStatus.INVALID_DATE
        else:
            status = DateResolutionStatus.MISSING_ANCHOR
        result[mention_id] = DateMentionV2(
            mention_id=mention_id,
            clause_id=source.clause_id,
            span_start=source.span_start,
            span_end=source.span_end,
            raw_text=source.raw_text,
            relation=source.relation,
            date_type=source.date_type,
            purpose=source.purpose,
            normalized_value=resolved,
            resolution_status=status,
        )
    return result
