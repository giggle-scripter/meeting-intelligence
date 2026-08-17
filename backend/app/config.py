"""Application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    app_name: str = "Meeting Task Pipeline API"
    power_automate_api_key: str | None = None
    max_transcript_characters: int = 500_000
    ai_fallback_endpoint: str | None = None
    ai_fallback_api_key: str | None = None
    ai_timeout_seconds: float = 3600.0
    job_timeout_seconds: float = 3600.0
    ai_max_batch_context_clauses: int = 56
    pipeline_version: str = "v1"
    pipeline_trace_enabled: bool = False
    pipeline_trace_directory: str = "evaluation/traces"
    meeting_context_mode: str = "assist"
    meeting_note_max_characters: int = 50_000
    note_grounding_threshold: float = 0.72
    note_grounding_margin: float = 0.12
    topic_likely_threshold: float = 0.45
    max_meeting_topics: int = 12
    max_topic_keywords: int = 8
    openai_api_key: str | None = None
    openai_model: str = "gpt-5-mini"
    openai_reasoning_effort: str = "medium"
    openai_input_usd_per_1m: float | None = None
    openai_cached_input_usd_per_1m: float | None = None
    openai_output_usd_per_1m: float | None = None
    ai_fallback_debug: bool = False
    azure_ai_foundry_chat_endpoint: str | None = None
    azure_ai_foundry_api_key: str | None = None
    azure_ai_foundry_model: str | None = None
    azure_ai_foundry_api_version: str = "2024-05-01-preview"

    @classmethod
    def from_env(cls) -> "Settings":
        def optional_nonnegative_float(name: str) -> float | None:
            raw = os.getenv(name, "").strip()
            if not raw:
                return None
            try:
                value = float(raw)
            except ValueError as exc:
                raise RuntimeError(f"{name} must be a number") from exc
            if value < 0:
                raise RuntimeError(f"{name} must be greater than or equal to zero")
            return value

        raw_limit = os.getenv("MAX_TRANSCRIPT_CHARACTERS", "500000")
        try:
            limit = int(raw_limit)
        except ValueError as exc:
            raise RuntimeError("MAX_TRANSCRIPT_CHARACTERS must be an integer") from exc
        if limit <= 0:
            raise RuntimeError("MAX_TRANSCRIPT_CHARACTERS must be greater than zero")

        raw_batch_limit = os.getenv("AI_MAX_BATCH_CONTEXT_CLAUSES", "56")
        try:
            batch_limit = int(raw_batch_limit)
        except ValueError as exc:
            raise RuntimeError(
                "AI_MAX_BATCH_CONTEXT_CLAUSES must be an integer"
            ) from exc
        if batch_limit <= 0:
            raise RuntimeError(
                "AI_MAX_BATCH_CONTEXT_CLAUSES must be greater than zero"
            )

        pipeline_version = os.getenv("PIPELINE_VERSION", "v1").lower()
        if pipeline_version not in {"v1", "v2", "shadow"}:
            raise RuntimeError("PIPELINE_VERSION must be v1, v2, or shadow")
        context_mode = os.getenv("MEETING_CONTEXT_MODE", "assist").lower()
        if context_mode not in {"off", "assist", "shadow"}:
            raise RuntimeError("MEETING_CONTEXT_MODE must be off, assist, or shadow")
        reasoning_effort = os.getenv("OPENAI_REASONING_EFFORT", "medium").lower()
        if reasoning_effort not in {"minimal", "low", "medium", "high"}:
            raise RuntimeError(
                "OPENAI_REASONING_EFFORT must be minimal, low, medium, or high"
            )

        return cls(
            power_automate_api_key=os.getenv("POWER_AUTOMATE_API_KEY") or None,
            max_transcript_characters=limit,
            ai_fallback_endpoint=os.getenv("AI_FALLBACK_ENDPOINT") or None,
            ai_fallback_api_key=os.getenv("AI_FALLBACK_API_KEY") or None,
            ai_timeout_seconds=float(os.getenv("AI_TIMEOUT_SECONDS", "3600")),
            job_timeout_seconds=float(os.getenv("JOB_TIMEOUT_SECONDS", "3600")),
            ai_max_batch_context_clauses=batch_limit,
            pipeline_version=pipeline_version,
            pipeline_trace_enabled=os.getenv("PIPELINE_TRACE_ENABLED", "").lower() in {"1", "true", "yes"},
            pipeline_trace_directory=os.getenv("PIPELINE_TRACE_DIRECTORY", "evaluation/traces"),
            meeting_context_mode=context_mode,
            meeting_note_max_characters=int(os.getenv("MEETING_NOTE_MAX_CHARACTERS", "50000")),
            note_grounding_threshold=float(os.getenv("NOTE_GROUNDING_THRESHOLD", "0.72")),
            note_grounding_margin=float(os.getenv("NOTE_GROUNDING_MARGIN", "0.12")),
            topic_likely_threshold=float(os.getenv("TOPIC_LIKELY_THRESHOLD", "0.45")),
            max_meeting_topics=int(os.getenv("MAX_MEETING_TOPICS", "12")),
            max_topic_keywords=int(os.getenv("MAX_TOPIC_KEYWORDS", "8")),
            openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip() or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            openai_reasoning_effort=reasoning_effort,
            openai_input_usd_per_1m=optional_nonnegative_float("OPENAI_INPUT_USD_PER_1M"),
            openai_cached_input_usd_per_1m=optional_nonnegative_float("OPENAI_CACHED_INPUT_USD_PER_1M"),
            openai_output_usd_per_1m=optional_nonnegative_float("OPENAI_OUTPUT_USD_PER_1M"),
            ai_fallback_debug=os.getenv("AI_FALLBACK_DEBUG", "").lower()
            in {"1", "true", "yes"},
            azure_ai_foundry_chat_endpoint=(
                os.getenv("AZURE_AI_FOUNDRY_CHAT_ENDPOINT") or None
            ),
            azure_ai_foundry_api_key=os.getenv("AZURE_AI_FOUNDRY_API_KEY") or None,
            azure_ai_foundry_model=os.getenv("AZURE_AI_FOUNDRY_MODEL") or None,
            azure_ai_foundry_api_version=os.getenv(
                "AZURE_AI_FOUNDRY_API_VERSION",
                "2024-05-01-preview",
            ),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
