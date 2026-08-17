"""Aggregate provider-reported OpenAI usage from V1 pipeline traces.

Shadow mode creates both a V1 trace and a shadow comparison trace for the
same request. This tool intentionally reads V1 traces only, so each completed
meeting is counted once.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize(trace_directory: Path) -> dict:
    totals = {
        "meetings_with_v1_trace": 0,
        "meetings_with_openai_calls": 0,
        "api_attempt_count": 0,
        "successful_response_count": 0,
        "usage_response_count": 0,
        "usage_missing_count": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "cost_estimate_complete": True,
    }
    models: dict[str, int] = {}
    for path in trace_directory.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("pipeline_version") != "v1":
            continue
        totals["meetings_with_v1_trace"] += 1
        usage = payload.get("openai_usage")
        if not isinstance(usage, dict):
            continue
        response_count = _integer(usage.get("response_count"))
        totals["api_attempt_count"] += _integer(usage.get("api_attempt_count"))
        totals["successful_response_count"] += response_count
        totals["usage_response_count"] += _integer(usage.get("usage_response_count"))
        totals["usage_missing_count"] += _integer(usage.get("usage_missing_count"))
        totals["input_tokens"] += _integer(usage.get("input_tokens"))
        totals["cached_input_tokens"] += _integer(usage.get("cached_input_tokens"))
        totals["output_tokens"] += _integer(usage.get("output_tokens"))
        totals["reasoning_output_tokens"] += _integer(usage.get("reasoning_output_tokens"))
        totals["total_tokens"] += _integer(usage.get("total_tokens"))
        if response_count:
            totals["meetings_with_openai_calls"] += 1
        model = usage.get("model")
        if isinstance(model, str) and model:
            models[model] = models.get(model, 0) + response_count
        cost = usage.get("estimated_cost_usd")
        if cost is None and response_count:
            totals["cost_estimate_complete"] = False
        elif isinstance(cost, (int, float)):
            totals["estimated_cost_usd"] += cost
    totals["uncached_input_tokens"] = max(
        0, totals["input_tokens"] - totals["cached_input_tokens"]
    )
    totals["estimated_cost_usd"] = round(totals["estimated_cost_usd"], 10)
    totals["models"] = models
    return totals


def _integer(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", default="evaluation/traces")
    args = parser.parse_args()
    print(json.dumps(summarize(Path(args.trace_dir)), ensure_ascii=False, indent=2))
