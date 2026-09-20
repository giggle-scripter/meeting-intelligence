from dataclasses import dataclass

import pytest

from backend.app.ai.cost_gate import AiCostBudget, BudgetedAiClient


class FakeAiClient:
    enabled = True

    def __init__(self) -> None:
        self.calls = 0

    def extract_events(self, _payload: dict):
        self.calls += 1
        return None

    propose_task = extract_events
    resolve_mutation = extract_events


def test_cost_gate_blocks_after_per_meeting_call_cap() -> None:
    client = FakeAiClient()
    gated = BudgetedAiClient(client, AiCostBudget(max_provider_calls=1, max_payload_characters=20))

    gated.extract_events({"x": 1})
    with pytest.raises(RuntimeError, match="call limit"):
        gated.extract_events({"x": 2})

    assert client.calls == 1
    assert gated.cost_gate_snapshot().block_reasons == {"MAX_PROVIDER_CALLS": 1}


def test_cost_gate_blocks_oversized_payload_before_provider() -> None:
    client = FakeAiClient()
    gated = BudgetedAiClient(client, AiCostBudget(max_provider_calls=2, max_payload_characters=5))

    with pytest.raises(RuntimeError, match="payload size"):
        gated.extract_events({"text": "too large"})

    assert client.calls == 0
    assert gated.cost_gate_snapshot().block_reasons == {"MAX_PAYLOAD_CHARACTERS": 1}


def test_cost_gate_requires_pricing_when_cost_ceiling_enabled() -> None:
    client = FakeAiClient()
    gated = BudgetedAiClient(client, AiCostBudget(max_estimated_cost_usd=0.01))

    with pytest.raises(RuntimeError, match="requires estimated"):
        gated.extract_events({"x": 1})

    assert client.calls == 0
