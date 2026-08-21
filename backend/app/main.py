"""FastAPI entry point for the Python-first meeting pipeline."""

from dataclasses import asdict
from datetime import date
from hashlib import sha256
from base64 import b64decode
from binascii import Error as BinasciiError
import json
import re
from secrets import compare_digest
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from .ai import AzureFoundryAiClient, DisabledAiClient, HttpAiClient, OpenAiResponsesClient
from .config import Settings, get_settings
from .dates import infer_meeting_context_date
from .ingestion import parse_meeting_package
from .jobs import MeetingJobStore
from .models import MeetingInput, MeetingNoteInput
from .pipeline import preprocess_meeting, process_meeting_by_version
from .v2.context import meeting_note_topic
from .v2.orchestration import versioned_idempotency_key


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MeetingNoteRequest(StrictModel):
    content: str = Field(min_length=1, max_length=50_000)
    author: str = Field(default="", max_length=128)
    source: Literal["SECRETARY", "PARTICIPANT", "MANUAL", "AUTO_OVERVIEW"] = "SECRETARY"


class MeetingRequest(StrictModel):
    meeting_id: str = Field(min_length=1, max_length=128)
    meeting_title: str = Field(min_length=1, max_length=255)
    meeting_date: str | None = Field(
        default=None,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    )
    file_name: str = Field(default="meeting.txt", max_length=255)
    transcript: str = Field(min_length=1)
    speaker_aliases: dict[str, str] = Field(default_factory=dict)
    meeting_note: MeetingNoteRequest | None = None


class HealthResponse(StrictModel):
    status: str
    service: str


app = FastAPI(
    title="Meeting Task Pipeline API",
    version="1.0.0",
    description="Python-first transcript-to-task pipeline with selective AI fallback.",
)
job_store = MeetingJobStore()


def verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.power_automate_api_key
    if expected and (not x_api_key or not compare_digest(x_api_key, expected)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


def _meeting(
    request: MeetingRequest,
    settings: Settings,
    meeting_date_source_override: str = "",
) -> MeetingInput:
    if len(request.transcript) > settings.max_transcript_characters:
        raise HTTPException(
            status_code=413,
            detail="Transcript exceeds configured character limit",
        )
    if request.meeting_note and len(request.meeting_note.content) > settings.meeting_note_max_characters:
        raise HTTPException(
            status_code=413,
            detail="Meeting note exceeds configured character limit",
        )
    suffix = request.file_name.lower().rsplit(".", 1)[-1] if "." in request.file_name else "txt"
    if suffix not in {"txt", "vtt", "srt"}:
        raise HTTPException(
            status_code=422,
            detail="Supported transcript types: .txt, .vtt, .srt",
        )
    note = MeetingNoteInput(
            content=request.meeting_note.content,
            author=request.meeting_note.author,
            source=request.meeting_note.source,
        ) if request.meeting_note else None
    contextual_date, contextual_source = infer_meeting_context_date(
        request.transcript,
        note.content if note else "",
    )
    meeting_date = request.meeting_date or contextual_date or date.today().isoformat()
    meeting_date_source = (
        meeting_date_source_override or "REQUEST"
        if request.meeting_date
        else contextual_source or "PROCESSING_DATE"
    )
    return MeetingInput(
        meeting_id=request.meeting_id,
        meeting_title=request.meeting_title,
        meeting_date=meeting_date,
        transcript_raw=request.transcript,
        file_name=request.file_name,
        meeting_note=note,
        meeting_date_source=meeting_date_source,
    )


def _decode_transcript(content: bytes) -> str:
    if not content:
        raise HTTPException(status_code=422, detail="Transcript file is empty")
    encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        transcript = content.decode(encoding)
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail="Transcript must be encoded as UTF-8 or UTF-16",
        ) from exc
    if not transcript.strip():
        raise HTTPException(status_code=422, detail="Transcript file is empty")
    return transcript


def _decode_utf8_base64_header(
    value: str | None,
    *,
    header_name: str,
    max_characters: int,
) -> str | None:
    """Decode optional Unicode metadata carried in an ASCII-safe header."""

    if not value:
        return None
    try:
        decoded = b64decode(value, validate=True).decode("utf-8")
    except (BinasciiError, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{header_name} must be valid Base64-encoded UTF-8",
        ) from exc
    decoded = decoded.strip()
    if not decoded:
        raise HTTPException(status_code=422, detail=f"{header_name} cannot be empty")
    if len(decoded) > max_characters:
        raise HTTPException(
            status_code=422,
            detail=f"{header_name} exceeds {max_characters} characters",
        )
    return decoded


def _file_defaults(file_name: str, content: bytes) -> tuple[str, str]:
    normalized_name = file_name.replace("\\", "/").rsplit("/", 1)[-1] or "meeting.txt"
    title = normalized_name.rsplit(".", 1)[0] if "." in normalized_name else normalized_name
    digest = sha256(normalized_name.encode("utf-8") + b"\0" + content).hexdigest()[:24]
    return title or "Meeting", f"upload-{digest}"


_EMBEDDED_TITLE_RE = re.compile(r"^TITLE:\s*(?P<title>.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_TECHNICAL_TITLE_RE = re.compile(
    r"^(?:W\d+-|upload-|(?:smoke|test|demo|sample|transcript|meeting)"
    r"(?:[-_ ]?[A-Z0-9]+)*|[A-Z0-9]+(?:-[A-Z0-9]+){3,})",
    re.IGNORECASE,
)
_TOPIC_PATTERNS = (
    (
        re.compile(
            r"\b(?:điểm\s+qua|review)\s+(?:các\s+)?"
            r"(?:đầu\s+việc|nội\s+dung)\s+chính\s*:\s*(?P<topic>.+)",
            re.I,
        ),
        "",
    ),
    (
        re.compile(
            r"\bhôm nay\b.*?\b(?:có\s+)?cuộc\s+họp\s+(?P<topic>.+)",
            re.I,
        ),
        "",
    ),
    (re.compile(r"\bhôm nay\b.*?\bhọp(?:\s+nhanh)?\s+về\s+(?P<topic>.+)", re.I), ""),
    (re.compile(r"\bhôm nay\b.*?\bhọp(?:\s+nhanh)?\s+để\s+(?:bàn(?:\s+về)?\s+)?(?P<topic>.+)", re.I), ""),
    (re.compile(r"\bhôm nay\b.*?\bsẽ\s+bàn(?:\s+về)?\s+(?P<topic>.+)", re.I), ""),
    (re.compile(r"\bhôm nay\b.*?\bsẽ\s+rà\s+soát\s+(?P<topic>.+)", re.I), "Rà soát "),
    (re.compile(r"\bhôm nay\b.*?\bcần\s+(?:thống nhất|trao đổi)\s+(?P<topic>.+)", re.I), ""),
)


def _embedded_title(transcript: str) -> str:
    """Read an optional display title without treating test metadata as a meeting date."""
    match = _EMBEDDED_TITLE_RE.search(transcript)
    return match.group("title").strip() if match else ""


def _display_title(value: str | None) -> str:
    """Keep human-readable titles and reject IDs accidentally mapped as titles."""
    title = (value or "").strip()
    return "" if not title or _TECHNICAL_TITLE_RE.match(title) else title


def _inferred_title(transcript: str) -> str:
    """Extract a short meeting topic from an opening agenda sentence when available."""
    for raw_line in transcript.splitlines()[:20]:
        line = re.sub(r"^\s*(?:T\d+\s+)?\[[^\]]+\]\s*", "", raw_line)
        line = re.sub(r"^[^:]{1,80}:\s*", "", line).strip()
        if not line or "-->" in line:
            continue
        for pattern, prefix in _TOPIC_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            topic = match.group("topic")
            topic = re.split(r"[.!?]", topic, maxsplit=1)[0]
            topic = re.sub(r"\b(?:nhé|nha|ạ|thôi)\b.*$", "", topic, flags=re.I)
            topic = re.sub(
                r"^(?:cùng\s+nhau\s+)?(?:nghĩ\s+ra|thảo\s+luận)\s+"
                r"(?:các\s+)?(?:hướng|phương án)\s+",
                "",
                topic,
                flags=re.I,
            ).strip(" ,:;.-")
            topic = re.sub(
                r"^(?:check|kiểm tra)\s+tiến độ\s+",
                "tiến độ ",
                topic,
                flags=re.I,
            )
            if topic:
                if prefix:
                    return (prefix + topic[:1].lower() + topic[1:]).strip()
                return (topic[:1].upper() + topic[1:]).strip()
    return ""


def _ai_client(settings: Settings):
    if settings.openai_api_key:
        return OpenAiResponsesClient(
            settings.openai_api_key,
            settings.openai_model,
            settings.openai_reasoning_effort,
            settings.ai_timeout_seconds,
            settings.ai_fallback_debug,
            settings.openai_input_usd_per_1m,
            settings.openai_cached_input_usd_per_1m,
            settings.openai_output_usd_per_1m,
        )
    if settings.azure_ai_foundry_chat_endpoint:
        if not settings.azure_ai_foundry_api_key:
            raise RuntimeError(
                "AZURE_AI_FOUNDRY_API_KEY is required when Foundry is enabled"
            )
        return AzureFoundryAiClient(
            settings.azure_ai_foundry_chat_endpoint,
            settings.azure_ai_foundry_api_key,
            settings.azure_ai_foundry_model or "",
            settings.azure_ai_foundry_api_version,
            settings.ai_timeout_seconds,
        )
    if settings.ai_fallback_endpoint:
        return HttpAiClient(
            settings.ai_fallback_endpoint,
            settings.ai_fallback_api_key or "",
            settings.ai_timeout_seconds,
        )
    return DisabledAiClient()


def _model_name(settings: Settings) -> str:
    if settings.openai_api_key:
        return settings.openai_model
    if settings.azure_ai_foundry_chat_endpoint:
        return settings.azure_ai_foundry_model or "azure-foundry"
    if settings.ai_fallback_endpoint:
        return "http-fallback"
    return "deterministic"


def _submit_job(
    meeting: MeetingInput,
    *,
    content_hash: str,
    speaker_aliases: dict[str, str],
    summary_topic: str | None,
    settings: Settings,
) -> dict:
    """Submit V1/V2/shadow work with its real input and configuration identity."""

    model_name = _model_name(settings)
    metadata_identity = json.dumps(
        {
            "meeting_id": meeting.meeting_id,
            "meeting_title": meeting.meeting_title,
            "meeting_date": meeting.meeting_date,
            "file_name": meeting.file_name,
            "speaker_aliases": speaker_aliases,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    effective_content_hash = sha256(
        (content_hash + "\0" + metadata_identity).encode("utf-8")
    ).hexdigest()
    job_key = versioned_idempotency_key(
        effective_content_hash,
        settings.pipeline_version,
        "v1-ledger-proposal-v1",
        model_name,
        "|".join((
            f"batch={settings.ai_max_batch_context_clauses}",
            f"timeout={settings.ai_timeout_seconds}",
            f"job_timeout={settings.job_timeout_seconds}",
            f"context={settings.meeting_context_mode}",
            f"action_classifier={settings.action_classifier_mode}",
            f"action_model={settings.action_classifier_model_path or 'none'}",
            f"action_candidates={settings.action_candidate_builder_mode}",
            f"action_candidates_version={settings.action_candidate_builder_version}",
            f"commitment_router={settings.commitment_router_mode}",
            f"commitment_router_version={settings.commitment_router_version}",
            f"commitment_router_types={','.join(settings.commitment_router_active_types)}",
            f"action_canonicalization={settings.action_canonicalization_mode}",
            f"action_canonicalization_version={settings.action_canonicalization_version}",
            f"recap_reconciliation={settings.recap_reconciliation_mode}",
            f"owner_grounding={settings.owner_grounding_mode}",
            f"candidate_router={settings.candidate_router_mode}",
            f"candidate_thresholds={settings.candidate_threshold_version}",
            f"action_clear={settings.action_clear_threshold}",
            f"action_ai={settings.action_ai_threshold}",
            f"task_create_proposal={settings.task_create_proposal_enabled}",
            f"ai_create_proposal={settings.ai_create_proposal_enabled}",
            f"ai_create_max={settings.ai_create_max_proposals_per_meeting}",
            f"task_linker={settings.task_semantic_linker_mode}",
            f"task_link_scoring={settings.task_link_scoring_version}",
            f"task_link_weights={settings.task_link_semantic_weight},"
            f"{settings.task_link_lexical_weight},{settings.task_link_topic_weight},"
            f"{settings.task_link_owner_weight},{settings.task_link_recency_weight}",
            f"task_link_thresholds={settings.task_link_strong_threshold},"
            f"{settings.task_link_min_margin},{settings.task_link_ai_threshold}",
            f"task_link_top_k={settings.task_link_top_k}",
            f"context_retrieval={settings.context_retrieval_mode}",
            f"context_limits={settings.context_max_clauses},"
            f"{settings.context_max_characters},{settings.context_max_tasks}",
            f"context_local={settings.context_local_before},"
            f"{settings.context_local_after}",
            f"context_topic={settings.context_topic_boundary_threshold},"
            f"{settings.context_topic_smoothing_window},"
            f"{settings.context_max_topics},{settings.context_max_topic_clauses}",
            f"context_history={settings.context_max_history_events_per_task}",
            f"context_version={settings.context_retrieval_version}",
        )),
    )
    job, created = job_store.submit(
        job_key,
        lambda: asdict(
            process_meeting_by_version(
                meeting,
                pipeline_version=settings.pipeline_version,
                ai_client=_ai_client(settings),
                speaker_aliases=speaker_aliases,
                summary_topic=summary_topic,
                ai_max_batch_context_clauses=settings.ai_max_batch_context_clauses,
                trace_enabled=settings.pipeline_trace_enabled,
                trace_directory=settings.pipeline_trace_directory,
                meeting_context_mode=settings.meeting_context_mode,
                note_grounding_threshold=settings.note_grounding_threshold,
                note_grounding_margin=settings.note_grounding_margin,
                max_meeting_topics=settings.max_meeting_topics,
                max_topic_keywords=settings.max_topic_keywords,
                topic_likely_threshold=settings.topic_likely_threshold,
                action_classifier_mode=settings.action_classifier_mode,
                action_classifier_model_path=settings.action_classifier_model_path,
                action_candidate_builder_mode=settings.action_candidate_builder_mode,
                action_candidate_builder_version=settings.action_candidate_builder_version,
                commitment_router_mode=settings.commitment_router_mode,
                commitment_router_version=settings.commitment_router_version,
                commitment_router_active_types=settings.commitment_router_active_types,
                action_canonicalization_mode=settings.action_canonicalization_mode,
                action_canonicalization_version=settings.action_canonicalization_version,
                recap_reconciliation_mode=settings.recap_reconciliation_mode,
                owner_grounding_mode=settings.owner_grounding_mode,
                candidate_router_mode=settings.candidate_router_mode,
                action_clear_threshold=settings.action_clear_threshold,
                action_ai_threshold=settings.action_ai_threshold,
                candidate_threshold_version=settings.candidate_threshold_version,
                task_create_proposal_enabled=settings.task_create_proposal_enabled,
                ai_create_proposal_enabled=settings.ai_create_proposal_enabled,
                ai_create_max_proposals_per_meeting=(
                    settings.ai_create_max_proposals_per_meeting
                ),
                task_semantic_linker_mode=settings.task_semantic_linker_mode,
                task_link_embedding_model_name=settings.embedding_model_name,
                task_link_embedding_device=settings.embedding_device,
                task_link_embedding_fallback_enabled=(
                    settings.embedding_fallback_enabled
                ),
                task_link_embedding_fallback_dimension=(
                    settings.embedding_fallback_dimension
                ),
                task_link_semantic_weight=settings.task_link_semantic_weight,
                task_link_lexical_weight=settings.task_link_lexical_weight,
                task_link_topic_weight=settings.task_link_topic_weight,
                task_link_owner_weight=settings.task_link_owner_weight,
                task_link_recency_weight=settings.task_link_recency_weight,
                task_link_strong_threshold=settings.task_link_strong_threshold,
                task_link_min_margin=settings.task_link_min_margin,
                task_link_ai_threshold=settings.task_link_ai_threshold,
                task_link_recency_horizon_clauses=(
                    settings.task_link_recency_horizon_clauses
                ),
                task_link_top_k=settings.task_link_top_k,
                task_link_scoring_version=settings.task_link_scoring_version,
                context_retrieval_mode=settings.context_retrieval_mode,
                context_max_clauses=settings.context_max_clauses,
                context_max_characters=settings.context_max_characters,
                context_max_tasks=settings.context_max_tasks,
                context_local_before=settings.context_local_before,
                context_local_after=settings.context_local_after,
                context_max_topic_clauses=settings.context_max_topic_clauses,
                context_max_topics=settings.context_max_topics,
                context_max_history_events_per_task=(
                    settings.context_max_history_events_per_task
                ),
                context_topic_boundary_threshold=(
                    settings.context_topic_boundary_threshold
                ),
                context_topic_smoothing_window=(
                    settings.context_topic_smoothing_window
                ),
                context_retrieval_version=settings.context_retrieval_version,
                ai_mutation_router_mode=settings.ai_mutation_router_mode,
                ai_mutation_prompt_version=settings.ai_mutation_prompt_version,
                ai_mutation_min_confidence=settings.ai_mutation_min_confidence,
                note_dual_view_mode=settings.note_dual_view_mode,
                note_claim_max_transcript_clauses=settings.note_claim_max_transcript_clauses,
                note_claim_max_topics=settings.note_claim_max_topics,
                note_claim_grounding_threshold=settings.note_claim_grounding_threshold,
                note_claim_grounding_margin=settings.note_claim_grounding_margin,
                note_dual_view_version=settings.note_dual_view_version,
                temporal_semantics_mode=settings.temporal_semantics_mode,
                temporal_parser_version=settings.temporal_parser_version,
                temporal_working_day_policy=settings.temporal_working_day_policy,
                temporal_min_confidence=settings.temporal_min_confidence,
            )
        ),
        pipeline_version=settings.pipeline_version,
        prompt_version="v1-ledger-proposal-v1",
        model=model_name,
        content_hash=effective_content_hash,
        timeout_seconds=settings.job_timeout_seconds,
    )
    return {
        "job_id": job.job_id,
        "status": job.status,
        "created": created,
        "status_url": f"/api/v1/meetings/jobs/{job.job_id}",
    }


@app.get("/health", response_model=HealthResponse, tags=["operations"])
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name)


@app.post(
    "/api/v1/transcripts/preprocess",
    dependencies=[Depends(verify_api_key)],
    tags=["transcripts"],
)
def preprocess_endpoint(
    request: MeetingRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    meeting = _meeting(request, settings)
    stages = preprocess_meeting(meeting, request.speaker_aliases)
    return {
        "meeting_id": meeting.meeting_id,
        "statistics": {
            "original_captions": stages["original_caption_count"],
            "clean_captions": len(stages["captions"]),
            "duplicates_removed": stages["original_caption_count"] - len(stages["captions"]),
            "turns": len(stages["turns"]),
            "sentences": len(stages["sentences"]),
            "clauses": len(stages["clauses"]),
        },
        "captions": [asdict(item) for item in stages["captions"]],
        "turns": [asdict(item) for item in stages["turns"]],
        "sentences": [asdict(item) for item in stages["sentences"]],
        "clauses": [asdict(item) for item in stages["clauses"]],
    }


@app.post(
    "/api/v1/meetings/process",
    dependencies=[Depends(verify_api_key)],
    tags=["meetings"],
)
def process_endpoint(
    request: MeetingRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    meeting = _meeting(request, settings)
    result = process_meeting_by_version(
        meeting,
        pipeline_version=settings.pipeline_version,
        ai_client=_ai_client(settings),
        speaker_aliases=request.speaker_aliases,
        summary_topic=(
            _display_title(request.meeting_title)
            or meeting_note_topic(meeting.meeting_note)
            or None
        ),
        ai_max_batch_context_clauses=settings.ai_max_batch_context_clauses,
        trace_enabled=settings.pipeline_trace_enabled,
        trace_directory=settings.pipeline_trace_directory,
        meeting_context_mode=settings.meeting_context_mode,
        note_grounding_threshold=settings.note_grounding_threshold,
        note_grounding_margin=settings.note_grounding_margin,
        max_meeting_topics=settings.max_meeting_topics,
        max_topic_keywords=settings.max_topic_keywords,
        topic_likely_threshold=settings.topic_likely_threshold,
        action_classifier_mode=settings.action_classifier_mode,
        action_classifier_model_path=settings.action_classifier_model_path,
        action_candidate_builder_mode=settings.action_candidate_builder_mode,
        action_candidate_builder_version=settings.action_candidate_builder_version,
        commitment_router_mode=settings.commitment_router_mode,
        commitment_router_version=settings.commitment_router_version,
        commitment_router_active_types=settings.commitment_router_active_types,
        action_canonicalization_mode=settings.action_canonicalization_mode,
        action_canonicalization_version=settings.action_canonicalization_version,
        recap_reconciliation_mode=settings.recap_reconciliation_mode,
        owner_grounding_mode=settings.owner_grounding_mode,
        candidate_router_mode=settings.candidate_router_mode,
        action_clear_threshold=settings.action_clear_threshold,
        action_ai_threshold=settings.action_ai_threshold,
        candidate_threshold_version=settings.candidate_threshold_version,
        task_create_proposal_enabled=settings.task_create_proposal_enabled,
        ai_create_proposal_enabled=settings.ai_create_proposal_enabled,
        ai_create_max_proposals_per_meeting=(
            settings.ai_create_max_proposals_per_meeting
        ),
        task_semantic_linker_mode=settings.task_semantic_linker_mode,
        task_link_embedding_model_name=settings.embedding_model_name,
        task_link_embedding_device=settings.embedding_device,
        task_link_embedding_fallback_enabled=settings.embedding_fallback_enabled,
        task_link_embedding_fallback_dimension=settings.embedding_fallback_dimension,
        task_link_semantic_weight=settings.task_link_semantic_weight,
        task_link_lexical_weight=settings.task_link_lexical_weight,
        task_link_topic_weight=settings.task_link_topic_weight,
        task_link_owner_weight=settings.task_link_owner_weight,
        task_link_recency_weight=settings.task_link_recency_weight,
        task_link_strong_threshold=settings.task_link_strong_threshold,
        task_link_min_margin=settings.task_link_min_margin,
        task_link_ai_threshold=settings.task_link_ai_threshold,
        task_link_recency_horizon_clauses=(
            settings.task_link_recency_horizon_clauses
        ),
        task_link_top_k=settings.task_link_top_k,
        task_link_scoring_version=settings.task_link_scoring_version,
        context_retrieval_mode=settings.context_retrieval_mode,
        context_max_clauses=settings.context_max_clauses,
        context_max_characters=settings.context_max_characters,
        context_max_tasks=settings.context_max_tasks,
        context_local_before=settings.context_local_before,
        context_local_after=settings.context_local_after,
        context_max_topic_clauses=settings.context_max_topic_clauses,
        context_max_topics=settings.context_max_topics,
        context_max_history_events_per_task=(
            settings.context_max_history_events_per_task
        ),
        context_topic_boundary_threshold=settings.context_topic_boundary_threshold,
        context_topic_smoothing_window=settings.context_topic_smoothing_window,
        context_retrieval_version=settings.context_retrieval_version,
        ai_mutation_router_mode=settings.ai_mutation_router_mode,
        ai_mutation_prompt_version=settings.ai_mutation_prompt_version,
        ai_mutation_min_confidence=settings.ai_mutation_min_confidence,
        note_dual_view_mode=settings.note_dual_view_mode,
        note_claim_max_transcript_clauses=settings.note_claim_max_transcript_clauses,
        note_claim_max_topics=settings.note_claim_max_topics,
        note_claim_grounding_threshold=settings.note_claim_grounding_threshold,
        note_claim_grounding_margin=settings.note_claim_grounding_margin,
        note_dual_view_version=settings.note_dual_view_version,
        temporal_semantics_mode=settings.temporal_semantics_mode,
        temporal_parser_version=settings.temporal_parser_version,
        temporal_working_day_policy=settings.temporal_working_day_policy,
        temporal_min_confidence=settings.temporal_min_confidence,
    )
    return asdict(result)


@app.post(
    "/api/v1/meetings/jobs/process",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_api_key)],
    tags=["meetings"],
)
async def submit_process_job_endpoint(
    request: MeetingRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Queue a JSON meeting request, including its optional human note."""

    meeting = _meeting(request, settings)
    note_content = request.meeting_note.content if request.meeting_note else ""
    note_source = request.meeting_note.source if request.meeting_note else ""
    content_hash = sha256(
        (request.transcript + "\0" + note_source + "\0" + note_content).encode("utf-8")
    ).hexdigest()
    return _submit_job(
        meeting,
        content_hash=content_hash,
        speaker_aliases=request.speaker_aliases,
        summary_topic=(
            _display_title(request.meeting_title)
            or meeting_note_topic(meeting.meeting_note)
            or None
        ),
        settings=settings,
    )


@app.post(
    "/api/v1/meetings/process-file",
    dependencies=[Depends(verify_api_key)],
    tags=["meetings"],
)
async def process_file_endpoint(
    raw_request: Request,
    x_file_name: Annotated[str | None, Header(alias="X-File-Name")] = None,
    x_file_name_base64: Annotated[
        str | None, Header(alias="X-File-Name-Base64")
    ] = None,
    x_meeting_id: Annotated[str | None, Header(alias="X-Meeting-Id")] = None,
    x_meeting_title: Annotated[str | None, Header(alias="X-Meeting-Title")] = None,
    x_meeting_title_base64: Annotated[
        str | None, Header(alias="X-Meeting-Title-Base64")
    ] = None,
    x_meeting_date: Annotated[str | None, Header(alias="X-Meeting-Date")] = None,
    settings: Settings = Depends(get_settings),
) -> dict:
    content = await raw_request.body()
    transcript, packaged_note, packaged_metadata = parse_meeting_package(
        _decode_transcript(content)
    )
    file_name = _decode_utf8_base64_header(
        x_file_name_base64,
        header_name="X-File-Name-Base64",
        max_characters=512,
    ) or x_file_name or "meeting.txt"
    meeting_title = _decode_utf8_base64_header(
        x_meeting_title_base64,
        header_name="X-Meeting-Title-Base64",
        max_characters=255,
    ) or x_meeting_title or packaged_metadata.meeting_title
    default_title, default_id = _file_defaults(file_name, content)
    summary_topic = (
        _display_title(meeting_title)
        or meeting_note_topic(packaged_note)
        or _embedded_title(transcript)
        or _inferred_title(transcript)
    )
    request = MeetingRequest(
        meeting_id=x_meeting_id or packaged_metadata.meeting_id or default_id,
        meeting_title=meeting_title or default_title,
        meeting_date=x_meeting_date or packaged_metadata.meeting_date or None,
        file_name=file_name,
        transcript=transcript,
        meeting_note=(
            MeetingNoteRequest(
                content=packaged_note.content,
                author=packaged_note.author,
                source=packaged_note.source,
            )
            if packaged_note else None
        ),
    )
    meeting = _meeting(
        request,
        settings,
        meeting_date_source_override=(
            "PACKAGE_METADATA"
            if not x_meeting_date and packaged_metadata.meeting_date
            else ""
        ),
    )
    result = process_meeting_by_version(
        meeting,
        pipeline_version=settings.pipeline_version,
        ai_client=_ai_client(settings),
        speaker_aliases={},
        summary_topic=summary_topic,
        ai_max_batch_context_clauses=settings.ai_max_batch_context_clauses,
        trace_enabled=settings.pipeline_trace_enabled,
        trace_directory=settings.pipeline_trace_directory,
        meeting_context_mode=settings.meeting_context_mode,
        note_grounding_threshold=settings.note_grounding_threshold,
        note_grounding_margin=settings.note_grounding_margin,
        max_meeting_topics=settings.max_meeting_topics,
        max_topic_keywords=settings.max_topic_keywords,
        topic_likely_threshold=settings.topic_likely_threshold,
        action_classifier_mode=settings.action_classifier_mode,
        action_classifier_model_path=settings.action_classifier_model_path,
        action_candidate_builder_mode=settings.action_candidate_builder_mode,
        action_candidate_builder_version=settings.action_candidate_builder_version,
        commitment_router_mode=settings.commitment_router_mode,
        commitment_router_version=settings.commitment_router_version,
        commitment_router_active_types=settings.commitment_router_active_types,
        action_canonicalization_mode=settings.action_canonicalization_mode,
        action_canonicalization_version=settings.action_canonicalization_version,
        recap_reconciliation_mode=settings.recap_reconciliation_mode,
        owner_grounding_mode=settings.owner_grounding_mode,
        candidate_router_mode=settings.candidate_router_mode,
        action_clear_threshold=settings.action_clear_threshold,
        action_ai_threshold=settings.action_ai_threshold,
        candidate_threshold_version=settings.candidate_threshold_version,
        task_create_proposal_enabled=settings.task_create_proposal_enabled,
        ai_create_proposal_enabled=settings.ai_create_proposal_enabled,
        ai_create_max_proposals_per_meeting=(
            settings.ai_create_max_proposals_per_meeting
        ),
        task_semantic_linker_mode=settings.task_semantic_linker_mode,
        task_link_embedding_model_name=settings.embedding_model_name,
        task_link_embedding_device=settings.embedding_device,
        task_link_embedding_fallback_enabled=settings.embedding_fallback_enabled,
        task_link_embedding_fallback_dimension=settings.embedding_fallback_dimension,
        task_link_semantic_weight=settings.task_link_semantic_weight,
        task_link_lexical_weight=settings.task_link_lexical_weight,
        task_link_topic_weight=settings.task_link_topic_weight,
        task_link_owner_weight=settings.task_link_owner_weight,
        task_link_recency_weight=settings.task_link_recency_weight,
        task_link_strong_threshold=settings.task_link_strong_threshold,
        task_link_min_margin=settings.task_link_min_margin,
        task_link_ai_threshold=settings.task_link_ai_threshold,
        task_link_recency_horizon_clauses=(
            settings.task_link_recency_horizon_clauses
        ),
        task_link_top_k=settings.task_link_top_k,
        task_link_scoring_version=settings.task_link_scoring_version,
        context_retrieval_mode=settings.context_retrieval_mode,
        context_max_clauses=settings.context_max_clauses,
        context_max_characters=settings.context_max_characters,
        context_max_tasks=settings.context_max_tasks,
        context_local_before=settings.context_local_before,
        context_local_after=settings.context_local_after,
        context_max_topic_clauses=settings.context_max_topic_clauses,
        context_max_topics=settings.context_max_topics,
        context_max_history_events_per_task=(
            settings.context_max_history_events_per_task
        ),
        context_topic_boundary_threshold=settings.context_topic_boundary_threshold,
        context_topic_smoothing_window=settings.context_topic_smoothing_window,
        context_retrieval_version=settings.context_retrieval_version,
        ai_mutation_router_mode=settings.ai_mutation_router_mode,
        ai_mutation_prompt_version=settings.ai_mutation_prompt_version,
        ai_mutation_min_confidence=settings.ai_mutation_min_confidence,
        note_dual_view_mode=settings.note_dual_view_mode,
        note_claim_max_transcript_clauses=settings.note_claim_max_transcript_clauses,
        note_claim_max_topics=settings.note_claim_max_topics,
        note_claim_grounding_threshold=settings.note_claim_grounding_threshold,
        note_claim_grounding_margin=settings.note_claim_grounding_margin,
        note_dual_view_version=settings.note_dual_view_version,
        temporal_semantics_mode=settings.temporal_semantics_mode,
        temporal_parser_version=settings.temporal_parser_version,
        temporal_working_day_policy=settings.temporal_working_day_policy,
        temporal_min_confidence=settings.temporal_min_confidence,
    )
    return asdict(result)


@app.post(
    "/api/v1/meetings/jobs/process-file",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_api_key)],
    tags=["meetings"],
)
async def submit_process_file_job_endpoint(
    raw_request: Request,
    x_file_name: Annotated[str | None, Header(alias="X-File-Name")] = None,
    x_file_name_base64: Annotated[
        str | None, Header(alias="X-File-Name-Base64")
    ] = None,
    x_meeting_id: Annotated[str | None, Header(alias="X-Meeting-Id")] = None,
    x_meeting_title: Annotated[str | None, Header(alias="X-Meeting-Title")] = None,
    x_meeting_title_base64: Annotated[
        str | None, Header(alias="X-Meeting-Title-Base64")
    ] = None,
    x_meeting_date: Annotated[str | None, Header(alias="X-Meeting-Date")] = None,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Queue a transcript job and return before a long AI fallback completes."""

    content = await raw_request.body()
    transcript, packaged_note, packaged_metadata = parse_meeting_package(
        _decode_transcript(content)
    )
    file_name = _decode_utf8_base64_header(
        x_file_name_base64,
        header_name="X-File-Name-Base64",
        max_characters=512,
    ) or x_file_name or "meeting.txt"
    meeting_title = _decode_utf8_base64_header(
        x_meeting_title_base64,
        header_name="X-Meeting-Title-Base64",
        max_characters=255,
    ) or x_meeting_title or packaged_metadata.meeting_title
    default_title, default_id = _file_defaults(file_name, content)
    summary_topic = (
        _display_title(meeting_title)
        or meeting_note_topic(packaged_note)
        or _embedded_title(transcript)
        or _inferred_title(transcript)
    )
    request = MeetingRequest(
        meeting_id=x_meeting_id or packaged_metadata.meeting_id or default_id,
        meeting_title=meeting_title or default_title,
        meeting_date=x_meeting_date or packaged_metadata.meeting_date or None,
        file_name=file_name,
        transcript=transcript,
        meeting_note=(
            MeetingNoteRequest(
                content=packaged_note.content,
                author=packaged_note.author,
                source=packaged_note.source,
            )
            if packaged_note else None
        ),
    )
    meeting = _meeting(
        request,
        settings,
        meeting_date_source_override=(
            "PACKAGE_METADATA"
            if not x_meeting_date and packaged_metadata.meeting_date
            else ""
        ),
    )

    return _submit_job(
        meeting,
        content_hash=default_id,
        speaker_aliases={},
        summary_topic=summary_topic,
        settings=settings,
    )


@app.get(
    "/api/v1/meetings/jobs/{job_id}",
    dependencies=[Depends(verify_api_key)],
    tags=["meetings"],
)
def get_process_file_job_endpoint(job_id: str) -> dict:
    """Return the current job state and final pipeline result when complete."""

    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Meeting job not found")
    return job.as_response()
