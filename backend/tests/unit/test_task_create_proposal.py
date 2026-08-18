"""Grounding contract tests for AI-assisted task creation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.candidate import TaskCreateProposal, TaskCreateProposalResponse
from backend.app.models import Clause, ClauseAnnotation, DateMention
from backend.app.verification import validate_task_create_proposal


def _clause(text: str, *, speaker: str = "Lan", order: int = 7) -> Clause:
    return Clause(
        "CLAUSE-000007",
        "SENTENCE-7",
        "SPK-LAN",
        speaker,
        0,
        1000,
        text,
        text.casefold(),
        order_index=order,
    )


def _proposal(**overrides) -> TaskCreateProposal:
    values = {
        "source_clause_ids": ["CLAUSE-000007"],
        "action_span": "em gửi bản báo cáo",
        "owner_span": "em",
        "deadline_mention_id": "DATE-000001",
        "commitment_type": "SELF_COMMITMENT",
        "confidence": 0.81,
    }
    values.update(overrides)
    return TaskCreateProposal(**values)


def test_flat_response_cannot_smuggle_event_or_canonical_fields() -> None:
    with pytest.raises(ValidationError):
        TaskCreateProposalResponse(
            decision="PROPOSE",
            source_clause_ids=["C-1"],
            action_span="gửi báo cáo",
            owner_span=None,
            deadline_mention_id=None,
            commitment_type="OTHER",
            confidence=0.8,
            task_id="TASK-1",
        )


def test_validator_promotes_grounded_proposal_at_source_chronology() -> None:
    clause = _clause("Em gửi bản báo cáo vào thứ Sáu.")
    mention = DateMention(
        "DATE-000001",
        clause.clause_id,
        "thứ Sáu",
        "NEXT_OR_SAME",
        "WEEKDAY",
    )
    result = validate_task_create_proposal(
        _proposal(),
        clauses_by_id={clause.clause_id: clause},
        annotations={clause.clause_id: ClauseAnnotation(clause.clause_id)},
        mentions={mention.date_mention_id: mention},
        start_sequence=4,
        allowed_source_clause_ids={clause.clause_id},
    )

    assert result.accepted is True
    assert result.event is not None
    assert result.event.event_type == "TASK_CREATE"
    assert result.event.action_text == "em gửi bản báo cáo"
    assert result.event.assignee == "Lan"
    assert result.event.deadline_mention_id == "DATE-000001"
    assert result.event.order_index == 7
    assert result.event.extraction_source == "AI_CREATE_PROPOSAL"


def test_validator_rejects_context_only_source() -> None:
    clause = _clause("Em gửi bản báo cáo vào thứ Sáu.")
    result = validate_task_create_proposal(
        _proposal(deadline_mention_id=None),
        clauses_by_id={clause.clause_id: clause},
        annotations={clause.clause_id: ClauseAnnotation(clause.clause_id)},
        mentions={},
        start_sequence=0,
        allowed_source_clause_ids={clause.clause_id},
        required_primary_clause_ids={"CLAUSE-PRIMARY"},
    )

    assert result.accepted is False
    assert result.reasons == ("PRIMARY_SOURCE_NOT_CITED",)


@pytest.mark.parametrize(
    ("proposal", "flags", "reason"),
    [
        (_proposal(action_span="viết API"), set(), "UNGROUNDED_ACTION_SPAN"),
        (_proposal(owner_span="Minh"), set(), "UNGROUNDED_OWNER_SPAN"),
        (_proposal(deadline_mention_id="DATE-INVENTED"), set(), "INVALID_DEADLINE_MENTION"),
        (_proposal(), {"ROOT_QUESTION"}, "QUESTION"),
        (_proposal(), {"SUGGESTION_ONLY"}, "SUGGESTION"),
        (_proposal(), {"HYPOTHETICAL"}, "HYPOTHETICAL"),
        (_proposal(), {"PAST_COMPLETED"}, "PAST_COMPLETED"),
        (_proposal(), {"PROGRESS_UPDATE"}, "PROGRESS_ONLY"),
        (_proposal(), {"REJECTION"}, "REJECTED_PROPOSAL"),
        (_proposal(), {"FUTURE_DISCUSSION"}, "FUTURE_DISCUSSION"),
    ],
)
def test_validator_fails_closed_on_ungrounded_or_negative_evidence(
    proposal: TaskCreateProposal,
    flags: set[str],
    reason: str,
) -> None:
    clause = _clause("Em gửi bản báo cáo vào thứ Sáu.")
    mention = DateMention(
        "DATE-000001", clause.clause_id, "thứ Sáu", "NEXT_OR_SAME", "WEEKDAY"
    )
    result = validate_task_create_proposal(
        proposal,
        clauses_by_id={clause.clause_id: clause},
        annotations={clause.clause_id: ClauseAnnotation(clause.clause_id, flags)},
        mentions={mention.date_mention_id: mention},
        start_sequence=0,
        allowed_source_clause_ids={clause.clause_id},
    )

    assert result.accepted is False
    assert reason in result.reasons
