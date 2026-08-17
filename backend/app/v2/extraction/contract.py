"""Validated decision-per-primary AI response contract."""

from dataclasses import dataclass

from ..models import ExtractionDecision, PrimaryResolution, TaskEventV2


@dataclass(frozen=True)
class ExtractionResponseV2:
    resolutions: tuple[PrimaryResolution, ...]


def validate_response(response: ExtractionResponseV2, primary_clause_ids: set[str], supplied_clause_ids: set[str], supplied_date_ids: set[str]) -> ExtractionResponseV2:
    received = [resolution.primary_clause_id for resolution in response.resolutions]
    if len(received) != len(set(received)):
        raise ValueError("each primary clause must have exactly one decision")
    if set(received) != primary_clause_ids:
        raise ValueError("response must cover every and only primary clauses")
    for resolution in response.resolutions:
        for event in resolution.events:
            if event.anchor_clause_id != resolution.primary_clause_id:
                raise ValueError("event must be anchored to its primary resolution")
            event.validate(primary_clause_ids, supplied_clause_ids, supplied_date_ids)
        if resolution.decision is ExtractionDecision.UNRESOLVED_REFERENCE and any(
            event.operation.value == "CREATE" for event in resolution.events
        ):
            raise ValueError("unresolved references cannot create tasks")
    return response
