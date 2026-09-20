"""Configurable, explainable routing over merged candidate evidence."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import CandidateEvidence


class CandidateRoute(str, Enum):
    DROP = "DROP"
    LOCAL_CREATE = "LOCAL_CREATE"
    LOCAL_MUTATION = "LOCAL_MUTATION"
    AI_CREATE_CHECK = "AI_CREATE_CHECK"
    AI_MUTATION_CHECK = "AI_MUTATION_CHECK"
    CONTEXT_ONLY = "CONTEXT_ONLY"


class CandidateDecision(BaseModel):
    """Router output only; downstream code must explicitly execute a route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    route: CandidateRoute
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def reasons_are_unique(self) -> "CandidateDecision":
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("decision reasons must be unique")
        return self


class CandidateRouterConfig(BaseModel):
    """Versioned thresholds; no learned cutoff is hidden in source logic."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_clear_threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    action_ai_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    threshold_version: str = Field(default="candidate-router-thresholds-v1", min_length=1)

    @model_validator(mode="after")
    def thresholds_are_ordered(self) -> "CandidateRouterConfig":
        if self.action_ai_threshold > self.action_clear_threshold:
            raise ValueError(
                "action_ai_threshold must be less than or equal to "
                "action_clear_threshold"
            )
        return self


class CandidateRouter:
    """Combine evidence using deterministic guards and configured thresholds."""

    VERSION = "candidate-evidence-router-v1"

    def __init__(self, config: CandidateRouterConfig | None = None) -> None:
        self.config = config or CandidateRouterConfig()

    def route(self, candidates: list[CandidateEvidence]) -> list[CandidateDecision]:
        return [self.route_one(candidate) for candidate in candidates]

    def route_one(self, candidate: CandidateEvidence) -> CandidateDecision:
        action_score = candidate.classifier_score or 0.0
        update_score = candidate.classifier_update_score or 0.0
        non_action_score = candidate.classifier_non_action_score or 0.0
        note_score = candidate.note_score or 0.0

        if candidate.has_rule_mutation_cue or candidate.has_note_state_signal:
            if candidate.has_unique_mutation_target:
                return self._decision(
                    candidate,
                    CandidateRoute.LOCAL_MUTATION,
                    max(update_score, note_score, 1.0 if candidate.has_rule_mutation_cue else 0.0),
                    ["MUTATION_SIGNAL", "UNIQUE_RULE_TARGET"],
                )
            if candidate.has_rule_mutation_cue or update_score >= self.config.action_ai_threshold:
                return self._decision(
                    candidate,
                    CandidateRoute.AI_MUTATION_CHECK,
                    max(update_score, note_score, 0.5),
                    ["MUTATION_SIGNAL", "TARGET_REQUIRES_RESOLUTION"],
                )
            return self._decision(
                candidate,
                CandidateRoute.CONTEXT_ONLY,
                max(note_score, non_action_score),
                ["NOTE_STATE_WITHOUT_MUTATION_EVIDENCE"],
            )

        if candidate.has_strong_negative_guard:
            reasons = ["STRONG_NEGATIVE_GUARD"]
            if candidate.has_question_cue:
                reasons.append("QUESTION_GUARD")
            if candidate.has_hypothetical_cue:
                reasons.append("HYPOTHETICAL_GUARD")
            if candidate.has_past_completed_cue:
                reasons.append("PAST_COMPLETED_GUARD")
            return self._decision(
                candidate,
                CandidateRoute.CONTEXT_ONLY,
                max(non_action_score, 0.5),
                reasons,
            )

        if action_score >= self.config.action_clear_threshold:
            reasons = ["CLASSIFIER_CLEAR_ACTION"]
            if candidate.has_rule_create_cue:
                reasons.insert(0, "RULE_CREATE_CORROBORATED")
            if candidate.has_note_action_signal:
                reasons.append("NOTE_ACTION_CORROBORATED")
            return self._decision(
                candidate,
                CandidateRoute.LOCAL_CREATE,
                action_score,
                reasons,
            )

        if action_score >= self.config.action_ai_threshold:
            reasons = ["CLASSIFIER_POSSIBLE_ACTION"]
            if candidate.has_rule_create_cue:
                reasons.append("RULE_CREATE_SIGNAL")
            if candidate.has_note_action_signal:
                reasons.append("NOTE_ACTION_SIGNAL")
            return self._decision(
                candidate,
                CandidateRoute.AI_CREATE_CHECK,
                action_score,
                reasons,
            )

        if update_score >= self.config.action_ai_threshold:
            return self._decision(
                candidate,
                CandidateRoute.AI_MUTATION_CHECK,
                update_score,
                ["CLASSIFIER_UPDATE_ONLY", "TARGET_REQUIRES_RESOLUTION"],
            )

        if candidate.has_note_action_signal or candidate.has_note_state_signal:
            return self._decision(
                candidate,
                CandidateRoute.CONTEXT_ONLY,
                max(note_score, non_action_score),
                ["NOTE_SIGNAL_BELOW_ROUTING_THRESHOLD"],
            )

        return self._decision(
            candidate,
            CandidateRoute.DROP,
            non_action_score,
            ["INSUFFICIENT_ACTION_OR_MUTATION_EVIDENCE"],
        )

    @staticmethod
    def _decision(
        candidate: CandidateEvidence,
        route: CandidateRoute,
        confidence: float,
        reasons: list[str],
    ) -> CandidateDecision:
        return CandidateDecision(
            candidate_id=candidate.candidate_id,
            route=route,
            confidence=min(max(confidence, 0.0), 1.0),
            reasons=reasons,
        )


@dataclass(slots=True)
class CandidateRouterShadowSummary:
    router_version: str = "disabled"
    threshold_version: str = "disabled"
    evidence_count: int = 0
    decision_count: int = 0
    route_counts: dict[str, int] = field(default_factory=dict)
    ai_create_check_suppressed_count: int = 0


def summarize_candidate_decisions(
    router: CandidateRouter,
    evidence: list[CandidateEvidence],
    decisions: list[CandidateDecision],
) -> CandidateRouterShadowSummary:
    """Aggregate shadow routes; no route is executed by this function."""

    counts = Counter(decision.route.value for decision in decisions)
    return CandidateRouterShadowSummary(
        router_version=router.VERSION,
        threshold_version=router.config.threshold_version,
        evidence_count=len(evidence),
        decision_count=len(decisions),
        route_counts=dict(sorted(counts.items())),
        ai_create_check_suppressed_count=counts[CandidateRoute.AI_CREATE_CHECK.value],
    )
