"""Feature-flag-safe comparison between legacy and temporal date resolution."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ...models import DateMention
from ..resolver import resolve_date_mention
from .contracts import TemporalResolutionStatus, TemporalType
from .parser import parse_temporal_expression
from .resolver import WORKING_DAY_POLICY, resolve_temporal_expression


@dataclass(frozen=True)
class TemporalSemanticsSummary:
    expression_count: int = 0
    type_counts: dict[str, int] = field(default_factory=dict)
    existing_resolved_count: int = 0
    ast_resolved_count: int = 0
    ast_unresolved_count: int = 0
    unresolved_anchor_count: int = 0
    agreement_count: int = 0
    disagreement_count: int = 0
    improve_count: int = 0
    regress_count: int = 0
    parser_error_count: int = 0
    resolution_status_counts: dict[str, int] = field(default_factory=dict)


def evaluate_temporal_semantics(
    mentions: dict[str, DateMention],
    *,
    meeting_date: str,
    parser_version: str,
    working_day_policy: str = WORKING_DAY_POLICY,
) -> tuple[TemporalSemanticsSummary, dict[str, str]]:
    """Compare without changing legacy results; return safe assist-only fallbacks."""

    type_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    existing = ast_resolved = ast_unresolved = unresolved_anchor = 0
    agreement = disagreement = improve = regress = parser_errors = 0
    safe_fallbacks: dict[str, str] = {}
    for mention in mentions.values():
        legacy = resolve_date_mention(meeting_date, meeting_date, mention)
        existing += int(bool(legacy))
        try:
            expression = parse_temporal_expression(
                mention.raw_text,
                source_id=mention.date_mention_id,
                parser_version=parser_version,
            )
            resolution = resolve_temporal_expression(
                expression,
                meeting_date=meeting_date,
                working_day_policy=working_day_policy,
            )
        except (KeyError, TypeError, ValueError):
            parser_errors += 1
            continue
        type_counts[expression.type.value] += 1
        status_counts[resolution.status.value] += 1
        resolved = resolution.resolved_date
        ast_resolved += int(resolution.status is TemporalResolutionStatus.RESOLVED)
        ast_unresolved += int(resolution.status is not TemporalResolutionStatus.RESOLVED)
        unresolved_anchor += int(resolution.status is TemporalResolutionStatus.UNRESOLVED_ANCHOR)
        if legacy and resolved:
            if legacy == resolved:
                agreement += 1
            else:
                disagreement += 1
                regress += 1
        elif not legacy and resolved:
            improve += 1
            # Promotion is intentionally limited to grammar that the old resolver
            # left unresolved. Exact legacy dates can never be overridden here.
            if expression.type is TemporalType.DURATION_AFTER_MEETING:
                safe_fallbacks[mention.date_mention_id] = resolved
    return TemporalSemanticsSummary(
        expression_count=len(mentions), type_counts=dict(sorted(type_counts.items())),
        existing_resolved_count=existing, ast_resolved_count=ast_resolved,
        ast_unresolved_count=ast_unresolved, unresolved_anchor_count=unresolved_anchor,
        agreement_count=agreement, disagreement_count=disagreement,
        improve_count=improve, regress_count=regress, parser_error_count=parser_errors,
        resolution_status_counts=dict(sorted(status_counts.items())),
    ), safe_fallbacks
