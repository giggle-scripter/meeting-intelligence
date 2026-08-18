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
    embedding_model_name: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    embedding_device: str = "cpu"
    embedding_fallback_enabled: bool = True
    embedding_fallback_dimension: int = 384
    action_classifier_mode: str = "off"
    action_classifier_model_path: str | None = None
    candidate_router_mode: str = "off"
    task_create_proposal_enabled: bool = False
    ai_create_proposal_enabled: bool = False
    ai_create_max_proposals_per_meeting: int = 3
    task_semantic_linker_mode: str = "off"
    task_link_semantic_weight: float = 0.55
    task_link_lexical_weight: float = 0.20
    task_link_topic_weight: float = 0.10
    task_link_owner_weight: float = 0.10
    task_link_recency_weight: float = 0.05
    task_link_strong_threshold: float = 0.78
    task_link_min_margin: float = 0.12
    task_link_ai_threshold: float = 0.60
    task_link_recency_horizon_clauses: int = 200
    task_link_top_k: int = 5
    task_link_scoring_version: str = "task-link-scoring-v1"
    context_retrieval_mode: str = "off"
    context_max_clauses: int = 30
    context_max_characters: int = 12_000
    context_max_tasks: int = 5
    context_local_before: int = 3
    context_local_after: int = 5
    context_max_topic_clauses: int = 12
    context_max_topics: int = 3
    context_max_history_events_per_task: int = 3
    context_topic_boundary_threshold: float = 0.42
    context_topic_smoothing_window: int = 3
    context_retrieval_version: str = "context-retriever-v1"
    ai_mutation_router_mode: str = "off"
    ai_mutation_prompt_version: str = "mutation-resolution-v2"
    ai_mutation_min_confidence: float = 0.70
    note_dual_view_mode: str = "off"
    note_claim_max_transcript_clauses: int = 8
    note_claim_max_topics: int = 3
    note_claim_grounding_threshold: float = 0.72
    note_claim_grounding_margin: float = 0.12
    note_dual_view_version: str = "note-dual-view-v1"
    action_clear_threshold: float = 0.82
    action_ai_threshold: float = 0.45
    candidate_threshold_version: str = "candidate-router-thresholds-v1"
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
        def env_bool(name: str, default: bool = False) -> bool:
            raw = os.getenv(name)
            if raw is None or not raw.strip():
                return default
            normalized = raw.strip().lower()
            if normalized in {"1", "true", "yes"}:
                return True
            if normalized in {"0", "false", "no"}:
                return False
            raise RuntimeError(f"{name} must be true or false")

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

        embedding_model_name = os.getenv(
            "EMBEDDING_MODEL_NAME",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        ).strip()
        if not embedding_model_name:
            raise RuntimeError("EMBEDDING_MODEL_NAME must not be empty")
        embedding_device = os.getenv("EMBEDDING_DEVICE", "cpu").strip()
        if not embedding_device:
            raise RuntimeError("EMBEDDING_DEVICE must not be empty")
        raw_embedding_dimension = os.getenv("EMBEDDING_FALLBACK_DIMENSION", "384")
        try:
            embedding_fallback_dimension = int(raw_embedding_dimension)
        except ValueError as exc:
            raise RuntimeError(
                "EMBEDDING_FALLBACK_DIMENSION must be an integer"
            ) from exc
        if embedding_fallback_dimension <= 0:
            raise RuntimeError(
                "EMBEDDING_FALLBACK_DIMENSION must be greater than zero"
            )
        action_classifier_mode = os.getenv(
            "ACTION_CLASSIFIER_MODE", "off"
        ).lower()
        if action_classifier_mode not in {"off", "shadow", "assist"}:
            raise RuntimeError("ACTION_CLASSIFIER_MODE must be off, shadow, or assist")
        candidate_router_mode = os.getenv("CANDIDATE_ROUTER_MODE", "off").lower()
        if candidate_router_mode not in {"off", "shadow", "assist"}:
            raise RuntimeError("CANDIDATE_ROUTER_MODE must be off, shadow, or assist")
        if candidate_router_mode != "off" and action_classifier_mode != candidate_router_mode:
            raise RuntimeError(
                f"CANDIDATE_ROUTER_MODE={candidate_router_mode} requires "
                f"ACTION_CLASSIFIER_MODE={candidate_router_mode}"
            )
        task_create_proposal_enabled = env_bool(
            "TASK_CREATE_PROPOSAL_ENABLED", False
        )
        ai_create_proposal_enabled = env_bool("AI_CREATE_PROPOSAL_ENABLED", False)
        try:
            ai_create_max_proposals = int(
                os.getenv("AI_CREATE_MAX_PROPOSALS_PER_MEETING", "3")
            )
        except ValueError as exc:
            raise RuntimeError(
                "AI_CREATE_MAX_PROPOSALS_PER_MEETING must be an integer"
            ) from exc
        if ai_create_max_proposals <= 0:
            raise RuntimeError(
                "AI_CREATE_MAX_PROPOSALS_PER_MEETING must be greater than zero"
            )
        if ai_create_proposal_enabled and not task_create_proposal_enabled:
            raise RuntimeError(
                "AI_CREATE_PROPOSAL_ENABLED requires TASK_CREATE_PROPOSAL_ENABLED=true"
            )
        if task_create_proposal_enabled and candidate_router_mode != "assist":
            raise RuntimeError(
                "TASK_CREATE_PROPOSAL_ENABLED requires CANDIDATE_ROUTER_MODE=assist"
            )
        task_semantic_linker_mode = os.getenv(
            "TASK_SEMANTIC_LINKER_MODE", "off"
        ).lower()
        if task_semantic_linker_mode not in {"off", "shadow"}:
            raise RuntimeError("TASK_SEMANTIC_LINKER_MODE must be off or shadow")
        context_retrieval_mode = os.getenv(
            "CONTEXT_RETRIEVAL_MODE", "off"
        ).lower()
        if context_retrieval_mode not in {"off", "shadow"}:
            raise RuntimeError("CONTEXT_RETRIEVAL_MODE must be off or shadow")
        if context_retrieval_mode == "shadow" and task_semantic_linker_mode != "shadow":
            raise RuntimeError(
                "CONTEXT_RETRIEVAL_MODE=shadow requires TASK_SEMANTIC_LINKER_MODE=shadow"
            )
        ai_mutation_router_mode = os.getenv("AI_MUTATION_ROUTER_MODE", "off").lower()
        if ai_mutation_router_mode not in {"off", "shadow", "assist"}:
            raise RuntimeError("AI_MUTATION_ROUTER_MODE must be off, shadow, or assist")
        if ai_mutation_router_mode == "shadow" and (task_semantic_linker_mode != "shadow" or context_retrieval_mode != "shadow"):
            raise RuntimeError("AI_MUTATION_ROUTER_MODE=shadow requires semantic and context shadow modes")
        if ai_mutation_router_mode == "assist" and (candidate_router_mode != "assist" or task_semantic_linker_mode != "shadow" or context_retrieval_mode != "shadow"):
            raise RuntimeError("AI_MUTATION_ROUTER_MODE=assist requires candidate, semantic, and context routing")
        ai_mutation_prompt_version = os.getenv("AI_MUTATION_PROMPT_VERSION", "mutation-resolution-v2").strip()
        if not ai_mutation_prompt_version:
            raise RuntimeError("AI_MUTATION_PROMPT_VERSION must not be empty")
        try:
            ai_mutation_min_confidence = float(os.getenv("AI_MUTATION_MIN_CONFIDENCE", "0.70"))
        except ValueError as exc:
            raise RuntimeError("AI_MUTATION_MIN_CONFIDENCE must be a number") from exc
        if not 0.0 <= ai_mutation_min_confidence <= 1.0:
            raise RuntimeError("AI_MUTATION_MIN_CONFIDENCE must be between zero and one")
        note_dual_view_mode = os.getenv("NOTE_DUAL_VIEW_MODE", "off").lower()
        if note_dual_view_mode not in {"off", "shadow", "assist"}:
            raise RuntimeError("NOTE_DUAL_VIEW_MODE must be off, shadow, or assist")
        try:
            note_claim_max_transcript_clauses = int(os.getenv("NOTE_CLAIM_MAX_TRANSCRIPT_CLAUSES", "8"))
            note_claim_max_topics = int(os.getenv("NOTE_CLAIM_MAX_TOPICS", "3"))
            note_claim_grounding_threshold = float(os.getenv("NOTE_CLAIM_GROUNDING_THRESHOLD", "0.72"))
            note_claim_grounding_margin = float(os.getenv("NOTE_CLAIM_GROUNDING_MARGIN", "0.12"))
        except ValueError as exc:
            raise RuntimeError("note dual-view limits and thresholds must be numeric") from exc
        if note_claim_max_transcript_clauses <= 0 or note_claim_max_transcript_clauses > 8 or note_claim_max_topics <= 0 or note_claim_max_topics > 3:
            raise RuntimeError("note dual-view limits exceed their hard caps")
        if not 0.0 <= note_claim_grounding_threshold <= 1.0 or not 0.0 <= note_claim_grounding_margin <= 1.0:
            raise RuntimeError("note dual-view thresholds must be between zero and one")
        note_dual_view_version = os.getenv("NOTE_DUAL_VIEW_VERSION", "note-dual-view-v1").strip()
        if not note_dual_view_version:
            raise RuntimeError("NOTE_DUAL_VIEW_VERSION must not be empty")
        task_link_weight_names = (
            "TASK_LINK_SEMANTIC_WEIGHT",
            "TASK_LINK_LEXICAL_WEIGHT",
            "TASK_LINK_TOPIC_WEIGHT",
            "TASK_LINK_OWNER_WEIGHT",
            "TASK_LINK_RECENCY_WEIGHT",
        )
        task_link_weight_defaults = ("0.55", "0.20", "0.10", "0.10", "0.05")
        try:
            task_link_weights = tuple(
                float(os.getenv(name, default))
                for name, default in zip(
                    task_link_weight_names,
                    task_link_weight_defaults,
                    strict=True,
                )
            )
            task_link_strong_threshold = float(
                os.getenv("TASK_LINK_STRONG_THRESHOLD", "0.78")
            )
            task_link_min_margin = float(
                os.getenv("TASK_LINK_MIN_MARGIN", "0.12")
            )
            task_link_ai_threshold = float(
                os.getenv("TASK_LINK_AI_THRESHOLD", "0.60")
            )
        except ValueError as exc:
            raise RuntimeError("task-link weights and thresholds must be numbers") from exc
        if any(value < 0.0 or value > 1.0 for value in task_link_weights):
            raise RuntimeError("task-link weights must be between zero and one")
        if abs(sum(task_link_weights) - 1.0) > 1e-6:
            raise RuntimeError("task-link weights must sum to one")
        if not 0.0 <= task_link_ai_threshold <= task_link_strong_threshold <= 1.0:
            raise RuntimeError(
                "task-link thresholds must satisfy 0 <= AI <= STRONG <= 1"
            )
        if not 0.0 <= task_link_min_margin <= 1.0:
            raise RuntimeError("TASK_LINK_MIN_MARGIN must be between zero and one")
        try:
            task_link_recency_horizon = int(
                os.getenv("TASK_LINK_RECENCY_HORIZON_CLAUSES", "200")
            )
            task_link_top_k = int(os.getenv("TASK_LINK_TOP_K", "5"))
        except ValueError as exc:
            raise RuntimeError("task-link horizon and top-k must be integers") from exc
        if task_link_recency_horizon <= 0:
            raise RuntimeError("TASK_LINK_RECENCY_HORIZON_CLAUSES must be positive")
        if not 1 <= task_link_top_k <= 20:
            raise RuntimeError("TASK_LINK_TOP_K must be between 1 and 20")
        task_link_scoring_version = os.getenv(
            "TASK_LINK_SCORING_VERSION", "task-link-scoring-v1"
        ).strip()
        if not task_link_scoring_version:
            raise RuntimeError("TASK_LINK_SCORING_VERSION must not be empty")
        context_integer_defaults = {
            "CONTEXT_MAX_CLAUSES": 30,
            "CONTEXT_MAX_CHARACTERS": 12_000,
            "CONTEXT_MAX_TASKS": 5,
            "CONTEXT_LOCAL_BEFORE": 3,
            "CONTEXT_LOCAL_AFTER": 5,
            "CONTEXT_MAX_TOPIC_CLAUSES": 12,
            "CONTEXT_MAX_TOPICS": 3,
            "CONTEXT_MAX_HISTORY_EVENTS_PER_TASK": 3,
            "CONTEXT_TOPIC_SMOOTHING_WINDOW": 3,
        }
        try:
            context_integers = {
                name: int(os.getenv(name, str(default)))
                for name, default in context_integer_defaults.items()
            }
            context_topic_boundary_threshold = float(
                os.getenv("CONTEXT_TOPIC_BOUNDARY_THRESHOLD", "0.42")
            )
        except ValueError as exc:
            raise RuntimeError("context retrieval limits must be numeric") from exc
        if not 1 <= context_integers["CONTEXT_MAX_CLAUSES"] <= 30:
            raise RuntimeError("CONTEXT_MAX_CLAUSES must be between 1 and 30")
        if not 1 <= context_integers["CONTEXT_MAX_CHARACTERS"] <= 12_000:
            raise RuntimeError(
                "CONTEXT_MAX_CHARACTERS must be between 1 and 12000"
            )
        if not 1 <= context_integers["CONTEXT_MAX_TASKS"] <= 5:
            raise RuntimeError("CONTEXT_MAX_TASKS must be between 1 and 5")
        if any(
            context_integers[name] < 0
            for name in (
                "CONTEXT_LOCAL_BEFORE",
                "CONTEXT_LOCAL_AFTER",
                "CONTEXT_MAX_TOPIC_CLAUSES",
                "CONTEXT_MAX_HISTORY_EVENTS_PER_TASK",
            )
        ):
            raise RuntimeError("context window limits must not be negative")
        if context_integers["CONTEXT_MAX_TOPIC_CLAUSES"] > 30:
            raise RuntimeError("CONTEXT_MAX_TOPIC_CLAUSES must not exceed 30")
        if context_integers["CONTEXT_MAX_HISTORY_EVENTS_PER_TASK"] > 10:
            raise RuntimeError(
                "CONTEXT_MAX_HISTORY_EVENTS_PER_TASK must not exceed 10"
            )
        if not 1 <= context_integers["CONTEXT_MAX_TOPICS"] <= 12:
            raise RuntimeError("CONTEXT_MAX_TOPICS must be between 1 and 12")
        if context_integers["CONTEXT_TOPIC_SMOOTHING_WINDOW"] not in {2, 3}:
            raise RuntimeError("CONTEXT_TOPIC_SMOOTHING_WINDOW must be 2 or 3")
        if not 0.0 <= context_topic_boundary_threshold <= 1.0:
            raise RuntimeError(
                "CONTEXT_TOPIC_BOUNDARY_THRESHOLD must be between zero and one"
            )
        context_retrieval_version = os.getenv(
            "CONTEXT_RETRIEVAL_VERSION", "context-retriever-v1"
        ).strip()
        if not context_retrieval_version:
            raise RuntimeError("CONTEXT_RETRIEVAL_VERSION must not be empty")
        try:
            action_clear_threshold = float(
                os.getenv("ACTION_CLEAR_THRESHOLD", "0.82")
            )
            action_ai_threshold = float(os.getenv("ACTION_AI_THRESHOLD", "0.45"))
        except ValueError as exc:
            raise RuntimeError(
                "ACTION_CLEAR_THRESHOLD and ACTION_AI_THRESHOLD must be numbers"
            ) from exc
        if not 0.0 <= action_ai_threshold <= action_clear_threshold <= 1.0:
            raise RuntimeError(
                "action thresholds must satisfy 0 <= ACTION_AI_THRESHOLD <= "
                "ACTION_CLEAR_THRESHOLD <= 1"
            )
        candidate_threshold_version = os.getenv(
            "CANDIDATE_THRESHOLD_VERSION", "candidate-router-thresholds-v1"
        ).strip()
        if not candidate_threshold_version:
            raise RuntimeError("CANDIDATE_THRESHOLD_VERSION must not be empty")

        return cls(
            power_automate_api_key=os.getenv("POWER_AUTOMATE_API_KEY") or None,
            max_transcript_characters=limit,
            ai_fallback_endpoint=os.getenv("AI_FALLBACK_ENDPOINT") or None,
            ai_fallback_api_key=os.getenv("AI_FALLBACK_API_KEY") or None,
            ai_timeout_seconds=float(os.getenv("AI_TIMEOUT_SECONDS", "3600")),
            job_timeout_seconds=float(os.getenv("JOB_TIMEOUT_SECONDS", "3600")),
            ai_max_batch_context_clauses=batch_limit,
            pipeline_version=pipeline_version,
            pipeline_trace_enabled=os.getenv("PIPELINE_TRACE_ENABLED", "").lower()
            in {"1", "true", "yes"},
            pipeline_trace_directory=os.getenv("PIPELINE_TRACE_DIRECTORY", "evaluation/traces"),
            meeting_context_mode=context_mode,
            meeting_note_max_characters=int(os.getenv("MEETING_NOTE_MAX_CHARACTERS", "50000")),
            note_grounding_threshold=float(os.getenv("NOTE_GROUNDING_THRESHOLD", "0.72")),
            note_grounding_margin=float(os.getenv("NOTE_GROUNDING_MARGIN", "0.12")),
            topic_likely_threshold=float(os.getenv("TOPIC_LIKELY_THRESHOLD", "0.45")),
            max_meeting_topics=int(os.getenv("MAX_MEETING_TOPICS", "12")),
            max_topic_keywords=int(os.getenv("MAX_TOPIC_KEYWORDS", "8")),
            embedding_model_name=embedding_model_name,
            embedding_device=embedding_device,
            embedding_fallback_enabled=env_bool(
                "EMBEDDING_FALLBACK_ENABLED", True
            ),
            embedding_fallback_dimension=embedding_fallback_dimension,
            action_classifier_mode=action_classifier_mode,
            action_classifier_model_path=(
                os.getenv("ACTION_CLASSIFIER_MODEL_PATH") or ""
            ).strip()
            or None,
            candidate_router_mode=candidate_router_mode,
            task_create_proposal_enabled=task_create_proposal_enabled,
            ai_create_proposal_enabled=ai_create_proposal_enabled,
            ai_create_max_proposals_per_meeting=ai_create_max_proposals,
            task_semantic_linker_mode=task_semantic_linker_mode,
            task_link_semantic_weight=task_link_weights[0],
            task_link_lexical_weight=task_link_weights[1],
            task_link_topic_weight=task_link_weights[2],
            task_link_owner_weight=task_link_weights[3],
            task_link_recency_weight=task_link_weights[4],
            task_link_strong_threshold=task_link_strong_threshold,
            task_link_min_margin=task_link_min_margin,
            task_link_ai_threshold=task_link_ai_threshold,
            task_link_recency_horizon_clauses=task_link_recency_horizon,
            task_link_top_k=task_link_top_k,
            task_link_scoring_version=task_link_scoring_version,
            context_retrieval_mode=context_retrieval_mode,
            context_max_clauses=context_integers["CONTEXT_MAX_CLAUSES"],
            context_max_characters=context_integers["CONTEXT_MAX_CHARACTERS"],
            context_max_tasks=context_integers["CONTEXT_MAX_TASKS"],
            context_local_before=context_integers["CONTEXT_LOCAL_BEFORE"],
            context_local_after=context_integers["CONTEXT_LOCAL_AFTER"],
            context_max_topic_clauses=context_integers["CONTEXT_MAX_TOPIC_CLAUSES"],
            context_max_topics=context_integers["CONTEXT_MAX_TOPICS"],
            context_max_history_events_per_task=(
                context_integers["CONTEXT_MAX_HISTORY_EVENTS_PER_TASK"]
            ),
            context_topic_boundary_threshold=context_topic_boundary_threshold,
            context_topic_smoothing_window=(
                context_integers["CONTEXT_TOPIC_SMOOTHING_WINDOW"]
            ),
            context_retrieval_version=context_retrieval_version,
            ai_mutation_router_mode=ai_mutation_router_mode,
            ai_mutation_prompt_version=ai_mutation_prompt_version,
            ai_mutation_min_confidence=ai_mutation_min_confidence,
            note_dual_view_mode=note_dual_view_mode,
            note_claim_max_transcript_clauses=note_claim_max_transcript_clauses,
            note_claim_max_topics=note_claim_max_topics,
            note_claim_grounding_threshold=note_claim_grounding_threshold,
            note_claim_grounding_margin=note_claim_grounding_margin,
            note_dual_view_version=note_dual_view_version,
            action_clear_threshold=action_clear_threshold,
            action_ai_threshold=action_ai_threshold,
            candidate_threshold_version=candidate_threshold_version,
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
