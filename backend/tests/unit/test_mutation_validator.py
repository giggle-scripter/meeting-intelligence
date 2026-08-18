from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.ai.schemas import MutationResolutionResponse
from backend.app.candidate import CandidateRoute
from backend.app.models import Clause, ClauseAnnotation, DateMention, TaskEvent
from backend.app.reduction.task_ledger import TaskLedger
from backend.app.verification import validate_mutation_resolution


def _clause(clause_id: str, text: str, order: int, speaker: str = "Lan") -> Clause:
    return Clause(clause_id, "S-1", "P-1", speaker, None, None, text, text, [], order)


def _ledger() -> TaskLedger:
    ledger = TaskLedger()
    ledger.apply(TaskEvent("EVENT-1", "TASK_CREATE", ["C-1"], "Chuẩn bị UAT", "Lan", order_index=1))
    return ledger


def _response(**overrides) -> MutationResolutionResponse:
    value = {
        "decision": "EVENT", "event_type": "DEADLINE_REPLACE",
        "related_task_id": "TASK-000001", "source_clause_ids": ["C-2"],
        "anchor_clause_id": "C-2", "owner_span": None,
        "deadline_mention_id": "DATE-1", "confidence": 0.9,
        "unresolved_reason": "",
    }
    value.update(overrides)
    return MutationResolutionResponse.model_validate(value)


def _validate(response: MutationResolutionResponse):
    clauses = {"C-1": _clause("C-1", "Lan chuẩn bị UAT.", 1), "C-2": _clause("C-2", "Lan nói UAT chuyển sang thứ Sáu.", 2)}
    return validate_mutation_resolution(
        response, route=CandidateRoute.AI_MUTATION_CHECK,
        supplied_task_ids={"TASK-000001"}, ledger=_ledger(), clauses_by_id=clauses,
        annotations={"C-1": ClauseAnnotation("C-1"), "C-2": ClauseAnnotation("C-2", {"CORRECTION"})},
        mentions={"DATE-1": DateMention("DATE-1", "C-2", "thứ Sáu", "NEXT_WEEKDAY", "WEEKDAY")},
        allowed_context_clause_ids={"C-1", "C-2"}, primary_clause_ids={"C-2"},
        minimum_confidence=0.7, start_sequence=1,
    )


def test_valid_deadline_resolution_promotes_grounded_event() -> None:
    result = _validate(_response())
    assert result.accepted is True
    assert result.event is not None
    assert result.event.related_task_id == "TASK-000001"
    assert result.event.order_index == 2


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"related_task_id": "TASK-999999"}, "UNKNOWN_TASK_ID"),
        ({"source_clause_ids": ["C-1"]}, "PRIMARY_SOURCE_NOT_CITED"),
        ({"anchor_clause_id": "C-1"}, "ANCHOR_OUTSIDE_PRIMARY"),
        ({"deadline_mention_id": "DATE-404"}, "INVALID_DEADLINE_MENTION"),
        ({"confidence": 0.2}, "LOW_CONFIDENCE"),
    ],
)
def test_invalid_resolution_fails_closed(override: dict, reason: str) -> None:
    result = _validate(_response(**override))
    assert result.accepted is False
    assert result.reasons == (reason,)


def test_owner_must_be_grounded_and_deadline_is_not_allowed_on_owner_event() -> None:
    result = _validate(_response(event_type="OWNER_REASSIGN", owner_span="Minh", deadline_mention_id=None))
    assert result.reasons == ("UNGROUNDED_OWNER_SPAN",)
    result = _validate(_response(event_type="OWNER_REASSIGN", owner_span="Lan", deadline_mention_id="DATE-1"))
    assert result.reasons == ("DEADLINE_NOT_ALLOWED_FOR_EVENT",)


def test_response_contract_forbids_ungrounded_unresolved_shape_and_duplicate_sources() -> None:
    with pytest.raises(ValidationError):
        MutationResolutionResponse.model_validate({
            "decision": "UNRESOLVED", "event_type": "TASK_CANCEL", "related_task_id": "",
            "source_clause_ids": [], "anchor_clause_id": "", "owner_span": None,
            "deadline_mention_id": None, "confidence": 0.0,
            "unresolved_reason": "NO_PLAUSIBLE_TARGET",
        })
    with pytest.raises(ValidationError):
        _response(source_clause_ids=["C-2", "C-2"])
