"""Fail-closed, per-meeting budgets around every optional provider call."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .client import AiClient


@dataclass(frozen=True)
class AiCostBudget:
    max_provider_calls: int = 3
    max_payload_characters: int = 20_000
    max_estimated_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if self.max_provider_calls <= 0:
            raise ValueError("max_provider_calls must be positive")
        if self.max_payload_characters <= 0:
            raise ValueError("max_payload_characters must be positive")
        if self.max_estimated_cost_usd is not None and self.max_estimated_cost_usd < 0:
            raise ValueError("max_estimated_cost_usd must be non-negative")


@dataclass(frozen=True)
class AiCostGateSnapshot:
    provider_call_count: int
    blocked_call_count: int
    payload_characters_sent: int
    budget_exhausted: bool
    block_reasons: dict[str, int]


class BudgetedAiClient:
    """Wrap a provider so exhausted or unpriced budgets fail closed.

    The provider owns token accounting. An estimated-cost ceiling is checked
    before each subsequent request; it can never make an extra provider call
    merely to learn the price.
    """

    def __init__(self, client: AiClient, budget: AiCostBudget) -> None:
        self._client = client
        self._budget = budget
        self._provider_call_count = 0
        self._blocked_call_count = 0
        self._payload_characters_sent = 0
        self._block_reasons: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self._client.enabled

    def _record_block(self, reason: str) -> None:
        self._blocked_call_count += 1
        self._block_reasons[reason] = self._block_reasons.get(reason, 0) + 1

    def _reserve(self, payload: dict[str, Any]) -> None:
        if self._provider_call_count >= self._budget.max_provider_calls:
            self._record_block("MAX_PROVIDER_CALLS")
            raise RuntimeError("AI cost gate blocked provider call limit")
        payload_characters = len(json.dumps(payload, ensure_ascii=False))
        if payload_characters > self._budget.max_payload_characters:
            self._record_block("MAX_PAYLOAD_CHARACTERS")
            raise RuntimeError("AI cost gate blocked payload size")
        if self._budget.max_estimated_cost_usd is not None:
            snapshot = self._usage_snapshot()
            if snapshot is None or snapshot.get("estimated_cost_usd") is None:
                self._record_block("COST_ESTIMATE_UNAVAILABLE")
                raise RuntimeError("AI cost gate requires estimated provider cost")
            if float(snapshot["estimated_cost_usd"]) >= self._budget.max_estimated_cost_usd:
                self._record_block("MAX_ESTIMATED_COST_USD")
                raise RuntimeError("AI cost gate blocked estimated cost limit")
        self._provider_call_count += 1
        self._payload_characters_sent += payload_characters

    def extract_events(self, payload: dict):
        self._reserve(payload)
        return self._client.extract_events(payload)

    def propose_task(self, payload: dict):
        self._reserve(payload)
        return self._client.propose_task(payload)

    def resolve_mutation(self, payload: dict):
        self._reserve(payload)
        return self._client.resolve_mutation(payload)

    def _usage_snapshot(self) -> dict[str, Any] | None:
        usage_snapshot = getattr(self._client, "usage_snapshot", None)
        if not callable(usage_snapshot):
            return None
        result = usage_snapshot()
        return asdict(result) if result is not None else None

    def usage_snapshot(self):
        return getattr(self._client, "usage_snapshot", lambda: None)()

    def cost_gate_snapshot(self) -> AiCostGateSnapshot:
        return AiCostGateSnapshot(
            provider_call_count=self._provider_call_count,
            blocked_call_count=self._blocked_call_count,
            payload_characters_sent=self._payload_characters_sent,
            budget_exhausted=bool(self._block_reasons),
            block_reasons=dict(sorted(self._block_reasons.items())),
        )
