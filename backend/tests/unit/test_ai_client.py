import json

import pytest

from backend.app.ai.client import (
    AiProviderFatalError,
    AzureFoundryAiClient,
    OpenAiResponsesClient,
    _post_with_retry,
)
from backend.app.ai.schemas import AiEventResponse


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.body


def test_provider_retry_uses_retry_after(monkeypatch) -> None:
    calls = []
    sleeps = []

    class RetryResponse(FakeResponse):
        status_code = 429
        headers = {"Retry-After": "0.01"}
        text = "rate limited"

        def raise_for_status(self) -> None:
            raise AssertionError("first retryable response must not be raised")

    responses = [RetryResponse({}), FakeResponse({})]

    def fake_post(*args, **kwargs):
        calls.append(1)
        return responses.pop(0)

    monkeypatch.setattr("backend.app.ai.client.httpx.post", fake_post)
    monkeypatch.setattr("backend.app.ai.client.time.sleep", sleeps.append)
    _post_with_retry("https://example.test", attempts=2)
    assert len(calls) == 2
    assert sleeps == [0.01]


def test_provider_credit_exhaustion_fails_fast(monkeypatch) -> None:
    class CreditResponse(FakeResponse):
        status_code = 400
        headers = {}
        text = '{"code":"credit_balance_exhausted"}'

    monkeypatch.setattr(
        "backend.app.ai.client.httpx.post", lambda *args, **kwargs: CreditResponse({})
    )
    with pytest.raises(AiProviderFatalError, match="credit balance"):
        _post_with_retry("https://example.test")


def test_foundry_client_sends_strict_schema_and_parses_events(monkeypatch) -> None:
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        content = {
            "events": [
                {
                    "event_type": "OWNER_ASSIGN",
                    "action_text": "Xử lý phần API",
                    "assignee": "Minh",
                    "anchor_clause_id": "CLAUSE-000001",
                    "source_clause_ids": ["CLAUSE-000001"],
                    "deadline_mention_id": "",
                    "related_task_id": "TASK-000001",
                    "related_task_hint": "",
                    "confidence": "HIGH",
                }
            ],
            "unresolved": [],
        }
        return FakeResponse(
            {"choices": [{"message": {"content": json_module.dumps(content)}}]}
        )

    json_module = json
    monkeypatch.setattr("backend.app.ai.client.httpx.post", fake_post)
    client = AzureFoundryAiClient(
        "https://example.services.ai.azure.com/models/chat/completions",
        "secret",
        model="gpt-5-chat",
        timeout_seconds=12,
    )

    result = client.extract_events(
        {
            "primary_clause_ids": ["CLAUSE-000001"],
            "clauses": [],
            "date_mentions": [],
        }
    )

    assert result.events[0].assignee == "Minh"
    assert "api-version=2024-05-01-preview" in captured["url"]
    assert captured["headers"]["api-key"] == "secret"
    assert captured["json"]["model"] == "gpt-5-chat"
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert captured["json"]["response_format"]["json_schema"]["strict"] is True
    schema = captured["json"]["response_format"]["json_schema"]["schema"]
    event_schema = schema["$defs"]["AiEvent"]
    assert set(event_schema["required"]) == set(event_schema["properties"])
    assert "anchor_clause_id" in event_schema["required"]
    assert "default" not in event_schema["properties"]["deadline_mention_id"]
    assert captured["timeout"] == 12


def test_foundry_client_rejects_missing_message_content(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.ai.client.httpx.post",
        lambda *args, **kwargs: FakeResponse({"choices": []}),
    )
    client = AzureFoundryAiClient(
        "https://example.services.ai.azure.com/models/chat/completions",
        "secret",
    )

    with pytest.raises(ValueError, match="does not contain message content"):
        client.extract_events({"clauses": []})


def test_foundry_client_requires_absolute_endpoint() -> None:
    with pytest.raises(ValueError, match="absolute URL"):
        AzureFoundryAiClient("/models/chat/completions", "secret")


def test_openai_client_sends_structured_output_request(monkeypatch) -> None:
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return FakeResponse(
            {
                "output_text": json_module.dumps(
                    {
                        "events": [
                            {
                                "event_type": "OWNER_ASSIGN",
                                "action_text": "Kiểm tra API",
                                "assignee": "Linh",
                                "anchor_clause_id": "CLAUSE-000001",
                                "source_clause_ids": ["CLAUSE-000001"],
                                "deadline_mention_id": "",
                                "related_task_id": "TASK-000001",
                                "related_task_hint": "",
                                "confidence": "HIGH",
                            }
                        ],
                        "unresolved": [],
                    }
                )
            }
        )

    json_module = json
    monkeypatch.setattr("backend.app.ai.client.httpx.post", fake_post)
    client = OpenAiResponsesClient(
        "secret", model="gpt-5-mini", reasoning_effort="low", timeout_seconds=12
    )

    result = client.extract_events({"clauses": []})

    assert result.events[0].action_text == "Kiểm tra API"
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"]["model"] == "gpt-5-mini"
    assert captured["json"]["reasoning"]["effort"] == "low"
    assert captured["json"]["text"]["format"]["strict"] is True


def test_openai_client_aggregates_provider_usage_and_estimates_cost(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.ai.client.httpx.post",
        lambda *args, **kwargs: FakeResponse(
            {
                "output_text": json.dumps({"events": []}),
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 20},
                    "output_tokens": 50,
                    "output_tokens_details": {"reasoning_tokens": 12},
                    "total_tokens": 150,
                },
            }
        ),
    )
    client = OpenAiResponsesClient(
        "secret",
        input_usd_per_1m=1.0,
        cached_input_usd_per_1m=0.25,
        output_usd_per_1m=4.0,
    )

    client.extract_events({"clauses": []})
    usage = client.usage_snapshot()

    assert usage.api_attempt_count == 1
    assert usage.response_count == 1
    assert usage.usage_response_count == 1
    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 20
    assert usage.uncached_input_tokens == 80
    assert usage.output_tokens == 50
    assert usage.reasoning_output_tokens == 12
    assert usage.estimated_cost_usd == 0.000285


def test_openai_client_parses_raw_responses_api_output(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.ai.client.httpx.post",
        lambda *args, **kwargs: FakeResponse(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps({"events": []}),
                            }
                        ],
                    }
                ],
            }
        ),
    )

    result = OpenAiResponsesClient("secret").extract_events({"clauses": []})

    assert result.events == []


def test_ai_hub_clause_objects_are_normalized_to_ids() -> None:
    response = AiEventResponse.model_validate(
        {
            "events": [
                {
                    "event_type": "OWNER_ASSIGN",
                    "action_text": "Xử lý phần API",
                    "assignee": "Minh",
                    "anchor_clause_id": "CLAUSE-000001",
                    "source_clause_ids": [
                        {"clause_id": "CLAUSE-000001"},
                        {"clause_id": "CLAUSE-000002"},
                    ],
                    "deadline_mention_id": "",
                    "related_task_id": "TASK-000001",
                    "related_task_hint": "",
                    "confidence": "HIGH",
                }
            ]
        }
    )

    assert response.events[0].source_clause_ids == [
        "CLAUSE-000001",
        "CLAUSE-000002",
    ]
