"""AI clients for ambiguous candidate windows."""

import json
import logging
import random
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .schemas import AiEventResponse


LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class AiProviderFatalError(RuntimeError):
    """Non-recoverable account state that must fail the enclosing job."""


def _post_with_retry(
    *args,
    attempts: int = 3,
    on_attempt: Callable[[], None] | None = None,
    **kwargs,
) -> httpx.Response:
    """Retry transient provider failures, never schema or ordinary 4xx errors."""

    last_error: Exception | None = None
    for attempt in range(attempts):
        response = None
        try:
            if on_attempt:
                on_attempt()
            response = httpx.post(*args, **kwargs)
            status_code = getattr(response, "status_code", 200)
            response_text = str(getattr(response, "text", ""))[:2000].casefold()
            if "credit_balance_exhausted" in response_text or "insufficient_quota" in response_text:
                raise AiProviderFatalError("Provider credit balance is exhausted")
            if status_code not in RETRYABLE_STATUS_CODES or attempt == attempts - 1:
                response.raise_for_status()
                return response
            last_error = RuntimeError(f"retryable provider status {status_code}")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                raise
        # 1s, 2s then jitter.  Schema validation happens after this helper and
        # is intentionally never retried.
        retry_after = ""
        if response is not None:
            retry_after = str(getattr(response, "headers", {}).get("Retry-After", ""))
        try:
            delay = max(0.0, min(float(retry_after), 30.0))
        except ValueError:
            delay = (2**attempt) + random.uniform(0, 0.25)
        time.sleep(delay)
    assert last_error is not None
    raise last_error


def _strict_json_schema(schema: dict) -> dict:
    """Make a Pydantic schema compatible with strict structured outputs."""

    result = deepcopy(schema)

    def visit(node) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


class AiClient(Protocol):
    @property
    def enabled(self) -> bool: ...
    def extract_events(self, payload: dict) -> AiEventResponse: ...


@dataclass(frozen=True)
class OpenAiUsageSnapshot:
    """Aggregate provider-reported usage for one client instance.

    Only the successful response usage is used for estimated cost. Retried
    attempts are shown separately because a failed transport attempt has no
    reliable billable-token record.
    """

    model: str
    api_attempt_count: int
    response_count: int
    usage_response_count: int
    usage_missing_count: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    total_tokens: int
    pricing_configured: bool
    input_usd_per_1m: float | None
    cached_input_usd_per_1m: float | None
    output_usd_per_1m: float | None
    estimated_cost_usd: float | None

    @property
    def uncached_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens)


class DisabledAiClient:
    enabled = False

    def extract_events(self, payload: dict) -> AiEventResponse:
        return AiEventResponse(events=[], unresolved=[])


class HttpAiClient:
    """Call a JSON endpoint that implements the constrained AI event contract."""

    enabled = True

    def __init__(self, endpoint: str, api_key: str = "", timeout_seconds: float = 30.0) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def extract_events(self, payload: dict) -> AiEventResponse:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        response = _post_with_retry(
            self.endpoint,
            json=payload,
            headers=headers,
            timeout=self.timeout_seconds,
        )
        return AiEventResponse.model_validate(response.json())


class OpenAiResponsesClient:
    """Call OpenAI Responses API with the same constrained event contract."""

    enabled = True
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5-mini",
        reasoning_effort: str = "medium",
        timeout_seconds: float = 30.0,
        debug: bool = False,
        input_usd_per_1m: float | None = None,
        cached_input_usd_per_1m: float | None = None,
        output_usd_per_1m: float | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.debug = debug
        self.input_usd_per_1m = input_usd_per_1m
        self.cached_input_usd_per_1m = cached_input_usd_per_1m
        self.output_usd_per_1m = output_usd_per_1m
        self._usage_lock = Lock()
        self._api_attempt_count = 0
        self._response_count = 0
        self._usage_response_count = 0
        self._usage_missing_count = 0
        self._input_tokens = 0
        self._cached_input_tokens = 0
        self._output_tokens = 0
        self._reasoning_output_tokens = 0
        self._total_tokens = 0
        self.system_prompt = (
            Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8").strip()
        )

    def extract_events(self, payload: dict) -> AiEventResponse:
        request_body = {
            "model": self.model,
            "store": False,
            "input": [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "meeting_task_events",
                    "strict": True,
                    "schema": _strict_json_schema(
                        AiEventResponse.model_json_schema()
                    ),
                }
            },
        }
        if self.model.startswith("gpt-5"):
            request_body["reasoning"] = {"effort": self.reasoning_effort}
        response = _post_with_retry(
            self.endpoint,
            json=request_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            timeout=self.timeout_seconds,
            on_attempt=self._record_attempt,
        )
        body = response.json()
        self._record_response_usage(body)
        content = self._response_text(body)
        if self.debug:
            LOGGER.warning("OpenAI fallback raw event response: %s", content)
        return AiEventResponse.model_validate_json(content)

    def _record_attempt(self) -> None:
        with self._usage_lock:
            self._api_attempt_count += 1

    def _record_response_usage(self, body: dict) -> None:
        usage = body.get("usage") if isinstance(body, dict) else None
        with self._usage_lock:
            self._response_count += 1
            if not isinstance(usage, dict):
                self._usage_missing_count += 1
                return
            self._usage_response_count += 1
            self._input_tokens += _usage_int(usage.get("input_tokens"))
            self._output_tokens += _usage_int(usage.get("output_tokens"))
            self._total_tokens += _usage_int(usage.get("total_tokens"))
            input_details = usage.get("input_tokens_details")
            if isinstance(input_details, dict):
                self._cached_input_tokens += _usage_int(input_details.get("cached_tokens"))
            output_details = usage.get("output_tokens_details")
            if isinstance(output_details, dict):
                self._reasoning_output_tokens += _usage_int(output_details.get("reasoning_tokens"))

    def usage_snapshot(self) -> OpenAiUsageSnapshot:
        with self._usage_lock:
            input_tokens = self._input_tokens
            cached_tokens = self._cached_input_tokens
            output_tokens = self._output_tokens
            rates = (
                self.input_usd_per_1m,
                self.cached_input_usd_per_1m,
                self.output_usd_per_1m,
            )
            pricing_configured = all(rate is not None for rate in rates)
            estimated_cost = None
            if pricing_configured:
                estimated_cost = round(
                    (
                        max(0, input_tokens - cached_tokens) * self.input_usd_per_1m
                        + cached_tokens * self.cached_input_usd_per_1m
                        + output_tokens * self.output_usd_per_1m
                    ) / 1_000_000,
                    10,
                )
            return OpenAiUsageSnapshot(
                model=self.model,
                api_attempt_count=self._api_attempt_count,
                response_count=self._response_count,
                usage_response_count=self._usage_response_count,
                usage_missing_count=self._usage_missing_count,
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens,
                output_tokens=output_tokens,
                reasoning_output_tokens=self._reasoning_output_tokens,
                total_tokens=self._total_tokens,
                pricing_configured=pricing_configured,
                input_usd_per_1m=self.input_usd_per_1m,
                cached_input_usd_per_1m=self.cached_input_usd_per_1m,
                output_usd_per_1m=self.output_usd_per_1m,
                estimated_cost_usd=estimated_cost,
            )

    @staticmethod
    def _response_text(body: dict) -> str:
        """Read text from both SDK-style and raw Responses API payloads."""

        content = body.get("output_text")
        if isinstance(content, str):
            return content

        for item in body.get("output", []):
            if item.get("type") != "message":
                continue
            for block in item.get("content", []):
                if block.get("type") == "output_text" and isinstance(
                    block.get("text"), str
                ):
                    return block["text"]
                if block.get("type") == "refusal":
                    raise ValueError("OpenAI refused the fallback request")

        status = body.get("status", "unknown")
        raise ValueError(
            f"OpenAI response has no output text (status={status}, "
            f"output_items={len(body.get('output', []))})"
        )


def _usage_int(value: object) -> int:
    """Accept only non-negative integer token counters from a provider body."""

    return value if isinstance(value, int) and value >= 0 else 0


class AzureFoundryAiClient:
    """Call a Microsoft Foundry chat-completions deployment with strict JSON."""

    enabled = True

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str = "",
        api_version: str = "2024-05-01-preview",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.endpoint = self._with_api_version(endpoint, api_version)
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.system_prompt = (
            Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8").strip()
        )

    @staticmethod
    def _with_api_version(endpoint: str, api_version: str) -> str:
        parts = urlsplit(endpoint.strip())
        if not parts.scheme or not parts.netloc:
            raise ValueError("AZURE_AI_FOUNDRY_CHAT_ENDPOINT must be an absolute URL")
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if api_version and "api-version" not in query:
            query["api-version"] = api_version
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    def extract_events(self, payload: dict) -> AiEventResponse:
        request_body = {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "meeting_task_events",
                    "strict": True,
                    "schema": _strict_json_schema(
                        AiEventResponse.model_json_schema()
                    ),
                },
            },
        }
        if self.model:
            request_body["model"] = self.model
        response = _post_with_retry(
            self.endpoint,
            json=request_body,
            headers={
                "Content-Type": "application/json",
                "api-key": self.api_key,
            },
            timeout=self.timeout_seconds,
        )
        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Foundry response does not contain message content") from exc
        if not isinstance(content, str):
            raise ValueError("Foundry message content must be a JSON string")
        return AiEventResponse.model_validate_json(content)
