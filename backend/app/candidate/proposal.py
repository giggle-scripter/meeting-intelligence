"""Grounded pre-event contract for AI-assisted task creation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CommitmentType = Literal[
    "SELF_COMMITMENT",
    "ASSIGNMENT",
    "CONFIRMED_ACTION",
    "OTHER",
]
ProposalDecision = Literal["PROPOSE", "NO_ACTION", "UNRESOLVED"]


class TaskCreateProposal(BaseModel):
    """Provider-authored evidence claim; never a ledger event by itself."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    source_clause_ids: list[str] = Field(min_length=1)
    action_span: str = Field(min_length=1)
    owner_span: str | None = None
    deadline_mention_id: str | None = None
    commitment_type: CommitmentType
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def source_ids_are_unique(self) -> "TaskCreateProposal":
        if len(set(self.source_clause_ids)) != len(self.source_clause_ids):
            raise ValueError("source_clause_ids must not contain duplicates")
        return self


class TaskCreateProposalResponse(BaseModel):
    """Flat provider response compatible with strict structured output."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: ProposalDecision
    source_clause_ids: list[str]
    action_span: str
    owner_span: str | None
    deadline_mention_id: str | None
    commitment_type: CommitmentType | Literal[""]
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def fields_match_decision(self) -> "TaskCreateProposalResponse":
        if self.decision == "PROPOSE":
            if not self.source_clause_ids or not self.action_span:
                raise ValueError("PROPOSE requires source_clause_ids and action_span")
            if not self.commitment_type:
                raise ValueError("PROPOSE requires commitment_type")
        elif (
            self.source_clause_ids
            or self.action_span
            or self.owner_span is not None
            or self.deadline_mention_id is not None
            or self.commitment_type
        ):
            raise ValueError(
                "NO_ACTION and UNRESOLVED must not contain proposal evidence"
            )
        return self

    def to_proposal(self) -> TaskCreateProposal | None:
        if self.decision != "PROPOSE":
            return None
        return TaskCreateProposal(
            source_clause_ids=self.source_clause_ids,
            action_span=self.action_span,
            owner_span=self.owner_span,
            deadline_mention_id=self.deadline_mention_id,
            commitment_type=self.commitment_type,
            confidence=self.confidence,
        )
