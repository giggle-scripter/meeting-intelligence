"""No-network-by-default teacher cache and explicitly budgeted provider client."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Literal

import httpx

from .contracts import TeacherPayload, TeacherResponse
from .hashing import atomic_write_json, canonical_json_hash, sha256_bytes
from .teacher_prompt import prompt_hash, render_prompt
from .teacher_validator import validate_teacher_response


Provider = Callable[[dict[str, Any], dict[str, str]], dict[str, Any]]


@dataclass(frozen=True)
class ProviderBudget:
    model: str
    max_calls: int
    max_input_chars: int
    max_estimated_usd: float
    input_usd_per_1m: float
    cached_input_usd_per_1m: float
    output_usd_per_1m: float
    max_output_tokens: int = 2048

    @classmethod
    def from_environment(cls) -> "ProviderBudget":
        required = {
            "DISTILL_TEACHER_MODEL": str,
            "DISTILL_MAX_CALLS": int,
            "DISTILL_MAX_INPUT_CHARS": int,
            "DISTILL_MAX_ESTIMATED_USD": float,
            "DISTILL_INPUT_USD_PER_1M": float,
            "DISTILL_CACHED_INPUT_USD_PER_1M": float,
            "DISTILL_OUTPUT_USD_PER_1M": float,
        }
        values: dict[str, Any] = {}
        for name, caster in required.items():
            raw = os.environ.get(name)
            if raw is None or not raw.strip():
                raise ValueError(f"missing provider budget variable: {name}")
            try:
                values[name] = caster(raw)
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid provider budget variable: {name}") from error
        if any(values[name] <= 0 for name in required if name != "DISTILL_TEACHER_MODEL"):
            raise ValueError("provider budget values must be positive")
        if os.environ.get("DISTILL_TEACHER_MODE") != "provider" or os.environ.get("DISTILL_ALLOW_PROVIDER_CALLS") != "1":
            raise ValueError("provider calls are not explicitly authorized")
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("missing provider API key")
        return cls(
            model=values["DISTILL_TEACHER_MODEL"],
            max_calls=values["DISTILL_MAX_CALLS"],
            max_input_chars=values["DISTILL_MAX_INPUT_CHARS"],
            max_estimated_usd=values["DISTILL_MAX_ESTIMATED_USD"],
            input_usd_per_1m=values["DISTILL_INPUT_USD_PER_1M"],
            cached_input_usd_per_1m=values["DISTILL_CACHED_INPUT_USD_PER_1M"],
            output_usd_per_1m=values["DISTILL_OUTPUT_USD_PER_1M"],
        )

    def estimated_max_cost(self) -> float:
        input_tokens = math.ceil(self.max_input_chars / 4)
        input_rate = max(self.input_usd_per_1m, self.cached_input_usd_per_1m)
        return self.max_calls * (input_tokens * input_rate + self.max_output_tokens * self.output_usd_per_1m) / 1_000_000

    def validate(self) -> None:
        if self.estimated_max_cost() > self.max_estimated_usd:
            raise ValueError("estimated provider maximum exceeds authorized USD budget")


def _http_provider(body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    response = httpx.post("https://api.openai.com/v1/responses", json=body, headers=headers, timeout=120.0)
    response.raise_for_status()
    value = response.json()
    if "output_text" in value:
        return json.loads(value["output_text"])
    for item in value.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return json.loads(content["text"])
    raise ValueError("provider response lacks output JSON")


class TeacherClient:
    def __init__(
        self,
        mode: Literal["disabled", "cache-only", "provider"],
        cache_root: Path,
        *,
        provider: Provider | None = None,
    ) -> None:
        self.mode = mode
        self.cache_root = cache_root
        self.provider = provider or _http_provider
        self.calls = 0
        self.estimated_cost = 0.0
        self.budget = ProviderBudget.from_environment() if mode == "provider" else None
        if self.budget:
            self.budget.validate()

    @staticmethod
    def model_hash(model: str) -> str:
        return sha256_bytes(model.encode("utf-8"))

    def request_hash(self, payload: TeacherPayload, version: str, model: str) -> str:
        return canonical_json_hash({"payload_hash": payload.input_hash, "prompt_hash": prompt_hash(version), "model_hash": self.model_hash(model)})

    def get(self, payload: TeacherPayload, version: str, *, model: str) -> TeacherResponse | None:
        if self.mode == "disabled":
            return None
        request_hash = self.request_hash(payload, version, model)
        cache_path = self.cache_root / f"{request_hash}.json"
        if cache_path.exists():
            response = TeacherResponse.model_validate_json(cache_path.read_text(encoding="utf-8-sig"))
            return validate_teacher_response(
                response,
                payload,
                expected_prompt_hash=prompt_hash(version),
                expected_model_hash=self.model_hash(model),
            )
        if self.mode == "cache-only":
            return None
        assert self.budget is not None
        prompt = render_prompt(payload, version)
        if len(prompt) > self.budget.max_input_chars or self.calls >= self.budget.max_calls:
            raise ValueError("provider budget exceeded before network call")
        body = {
            "model": model,
            "input": prompt,
            "store": False,
            "temperature": 0,
            "max_output_tokens": self.budget.max_output_tokens,
        }
        headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"}
        last_error: Exception | None = None
        for _attempt in range(3):
            self.calls += 1
            try:
                raw = self.provider(body, headers)
                response = TeacherResponse.model_validate(raw)
                validate_teacher_response(
                    response,
                    payload,
                    expected_prompt_hash=prompt_hash(version),
                    expected_model_hash=self.model_hash(model),
                )
                atomic_write_json(cache_path, response.model_dump(mode="json"))
                return response
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as error:
                last_error = error
                if self.calls >= self.budget.max_calls:
                    break
        raise ValueError("teacher provider failed after retries") from last_error
