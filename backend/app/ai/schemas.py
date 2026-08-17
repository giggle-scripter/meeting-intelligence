"""Strict mutation-resolution contract for the selective AI fallback."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AiEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event_type: Literal[
        "OWNER_ASSIGN",
        "OWNER_REASSIGN",
        "DEADLINE_SET",
        "DEADLINE_REPLACE",
        "TASK_CANCEL",
        "TASK_REJECT",
    ]
    action_text: str = ""
    assignee: str = ""
    anchor_clause_id: str = Field(min_length=1)
    source_clause_ids: list[str] = Field(min_length=1)
    deadline_mention_id: str = ""
    related_task_id: str = Field(min_length=1)
    related_task_hint: str = ""
    confidence: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"

    @field_validator("source_clause_ids", mode="before")
    @classmethod
    def normalize_source_clause_ids(cls, value):
        if not isinstance(value, list):
            return value
        return [
            item.get("clause_id", "") if isinstance(item, dict) else item
            for item in value
        ]


class AiUnresolved(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    anchor_clause_id: str = Field(min_length=1)
    event_type: Literal[
        "OWNER_ASSIGN",
        "OWNER_REASSIGN",
        "DEADLINE_SET",
        "DEADLINE_REPLACE",
        "TASK_CANCEL",
        "TASK_REJECT",
    ]
    candidate_task_ids: list[str] = Field(default_factory=list)
    reason: Literal[
        "NO_PLAUSIBLE_TARGET",
        "MULTIPLE_PLAUSIBLE_TARGETS",
        "INSUFFICIENT_EXPLICIT_EVIDENCE",
    ]


class AiEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[AiEvent] = Field(default_factory=list)
    unresolved: list[AiUnresolved] = Field(default_factory=list)
