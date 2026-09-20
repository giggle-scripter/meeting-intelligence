"""Opt-in HTTP shell for the selectable text processing cores.

The existing :mod:`backend.app.main` application is the V1 compatibility API
and remains unchanged.  This module exposes only the common asynchronous file
job contract, with core selection performed once when the application is
created.  A selected core is therefore immutable for the lifetime of one
process and a bad selection fails before the server can start serving traffic.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from secrets import compare_digest
import tempfile
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from .config import Settings, get_settings
from .core import MeetingCore, get_core
from .ingestion import parse_meeting_package
from .jobs import MeetingJobStore
from .main import (
    MeetingNoteRequest,
    MeetingRequest,
    _decode_transcript,
    _decode_utf8_base64_header,
    _display_title,
    _embedded_title,
    _file_defaults,
    _inferred_title,
    _meeting,
)
from .models import MeetingInput
from .v2.context import meeting_note_topic


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HealthResponse(StrictModel):
    status: str
    service: str
    core_id: str
    adaptive: bool
    pipeline_version: str
    runtime_model_id: str
    # ``model`` is retained as the job-envelope-compatible spelling for
    # clients that use one audit field across health and job responses.
    model: str
    supports_meeting_note: bool


class FeedbackRequest(StrictModel):
    job_id: str
    content_hash: str
    corrected_final_tasks: list[dict[str, Any]]
    approval_metadata: dict[str, Any]


_FEEDBACK_TASK_FIELDS = frozenset(
    {"task_name", "assignee", "start_date", "due_date", "due_date_text", "evidence", "status"}
)
_TENANT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _feedback_context(settings: Settings) -> tuple[str, Path] | None:
    tenant = (
        settings.meeting_feedback_tenant_id
        or os.getenv("MEETING_FEEDBACK_TENANT_ID")
        or os.getenv("V227_FEEDBACK_TENANT_ID")
        or ""
    ).strip()
    if not tenant:
        return None
    if len(tenant) > 64 or any(char not in _TENANT_CHARS for char in tenant):
        raise RuntimeError("STOP_INVALID_FEEDBACK_TENANT_ID")
    configured_directory = settings.meeting_feedback_directory
    if configured_directory == "evaluation/runtime/v227-feedback":
        configured_directory = (
            os.getenv("MEETING_FEEDBACK_DIRECTORY")
            or os.getenv("V227_FEEDBACK_DIRECTORY")
            or configured_directory
        )
    return tenant, Path(configured_directory).expanduser().resolve()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_create_json(path: Path, payload: dict[str, Any]) -> bool:
    """Create an append-only private record without replacing a winner."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        temporary.unlink()
        temporary = None
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return True
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _persist_source(
    *, settings: Settings, core: MeetingCore, meeting: MeetingInput,
    content_hash: str, raw_upload_sha256: str, content_hash_base: str,
    content_hash_metadata: str,
) -> None:
    context = _feedback_context(settings)
    if context is None:
        return
    tenant, directory = context
    pipeline_version, runtime_model_id, _ = _core_metadata(core)
    adaptive_training_eligible = (
        core.core_id == "v2-adaptive" and meeting.meeting_note is None
    )
    source = {
        # This is deliberately the trainer's established source schema.  The
        # generic ledger does not import or call any V2 implementation.
        "schema_version": "v227-feedback-source-v1",
        "tenant_id": tenant,
        "content_hash": content_hash,
        "content_hash_algorithm": "core-api-job-v1",
        "content_hash_base": content_hash_base,
        "content_hash_metadata": content_hash_metadata,
        "raw_upload_sha256": raw_upload_sha256,
        "transcript_sha256": sha256(meeting.transcript_raw.encode("utf-8")).hexdigest(),
        "transcript": meeting.transcript_raw,
        "source_modality": "transcript",
        "file_name": meeting.file_name,
        "meeting_id": meeting.meeting_id,
        "meeting_title": meeting.meeting_title,
        "meeting_date": meeting.meeting_date,
        "meeting_note": asdict(meeting.meeting_note) if meeting.meeting_note else None,
        "core_id": core.core_id,
        "pipeline_version": pipeline_version,
        "model": runtime_model_id,
        "adaptive_training_eligible": adaptive_training_eligible,
    }
    path = directory / tenant / "sources" / f"{content_hash}.json"
    if _atomic_create_json(path, source):
        return
    existing = _read_json(path)
    if existing is None or _canonical_json(existing) != _canonical_json(source):
        raise RuntimeError("STOP_SOURCE_RECORD_CONFLICT")


def _normalise_feedback(
    payload: FeedbackRequest, *, route_job_id: str, job_content_hash: str,
) -> dict[str, Any]:
    if payload.job_id != route_job_id:
        raise HTTPException(409, "Feedback job_id does not match the URL")
    if payload.content_hash != job_content_hash:
        raise HTTPException(409, "Feedback content_hash does not match the job")
    if len(payload.corrected_final_tasks) > 1_000:
        raise HTTPException(422, "Too many corrected tasks")
    for index, task in enumerate(payload.corrected_final_tasks):
        if set(task) != _FEEDBACK_TASK_FIELDS or any(
            not isinstance(task[field], str) for field in _FEEDBACK_TASK_FIELDS
        ) or task["status"] != "Proposed":
            raise HTTPException(422, f"Invalid corrected task at index {index}")
    approval = payload.approval_metadata
    if set(approval) != {"reviewer", "reviewed_at", "approval"}:
        raise HTTPException(422, "Invalid approval metadata")
    reviewer, reviewed_at = approval["reviewer"], approval["reviewed_at"]
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 256:
        raise HTTPException(422, "Approval reviewer is required")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip() or len(reviewed_at) > 128:
        raise HTTPException(422, "Approval reviewed_at is required")
    try:
        parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, "Approval reviewed_at must be ISO-8601") from exc
    if parsed.tzinfo is None or approval["approval"] is not True:
        raise HTTPException(422, "Approved feedback requires a timezone-aware timestamp")
    return {
        "job_id": payload.job_id,
        "content_hash": payload.content_hash,
        "corrected_final_tasks": [dict(task) for task in payload.corrected_final_tasks],
        "approval_metadata": {
            "reviewer": reviewer.strip(), "reviewed_at": reviewed_at, "approval": True,
        },
    }


def _core_metadata(core: MeetingCore) -> tuple[str, str, bool]:
    """Read typed core metadata with compatibility defaults for custom cores."""

    capabilities = core.capabilities
    return (
        getattr(capabilities, "pipeline_version", None) or core.core_id,
        getattr(capabilities, "runtime_model_id", None) or core.core_id,
        getattr(capabilities, "supports_meeting_note", True),
    )


def _verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    *,
    settings: Settings,
) -> None:
    expected = settings.power_automate_api_key
    if expected and (not x_api_key or not compare_digest(x_api_key, expected)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


def _submit_job(
    *,
    store: MeetingJobStore,
    core: MeetingCore,
    meeting: MeetingInput,
    content_hash: str,
    summary_topic: str | None,
    settings: Settings,
    raw_upload_sha256: str,
) -> dict:
    """Submit one core invocation using the existing durable job store."""

    pipeline_version, runtime_model_id, supports_meeting_note = _core_metadata(core)
    if meeting.meeting_note is not None and not supports_meeting_note:
        raise HTTPException(422, "Selected meeting core does not support Meeting Note")

    metadata_identity = json.dumps(
        {
            "meeting_id": meeting.meeting_id,
            "meeting_title": meeting.meeting_title,
            "meeting_date": meeting.meeting_date,
            "file_name": meeting.file_name,
            "meeting_note": asdict(meeting.meeting_note)
            if meeting.meeting_note
            else None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    effective_content_hash = sha256(
        (content_hash + "\0" + metadata_identity).encode("utf-8")
    ).hexdigest()
    _persist_source(
        settings=settings,
        core=core,
        meeting=meeting,
        content_hash=effective_content_hash,
        raw_upload_sha256=raw_upload_sha256,
        content_hash_base=content_hash,
        content_hash_metadata=metadata_identity,
    )
    # Include the selected core and runtime model in the identity.  A V1 job
    # must never be returned for an otherwise identical request made to a V2
    # process (or vice versa), and a challenger job must not reuse a frozen
    # base job when a shared SQLite store is used during a rollout.
    idempotency_key = sha256(
        (
            core.core_id
            + "\0"
            + pipeline_version
            + "\0"
            + runtime_model_id
            + "\0"
            + effective_content_hash
        ).encode("utf-8")
    ).hexdigest()

    job, created = store.submit(
        idempotency_key,
        lambda: asdict(core.process(meeting, summary_topic=summary_topic)),
        pipeline_version=pipeline_version,
        prompt_version="core-api-v1",
        model=runtime_model_id,
        content_hash=effective_content_hash,
        timeout_seconds=settings.job_timeout_seconds,
    )
    return {
        "job_id": job.job_id,
        "status": job.status,
        "created": created,
        "status_url": f"/api/v1/meetings/jobs/{job.job_id}",
    }


def create_app(
    *,
    core_id: str | None = None,
    core: MeetingCore | None = None,
    settings: Settings | None = None,
    job_store: MeetingJobStore | None = None,
) -> FastAPI:
    """Create the unified text API.

    ``core`` and ``job_store`` are explicit seams for end-to-end tests.  In
    normal operation the registry selects the core from ``MEETING_CORE`` and
    the store reads ``MEETING_JOB_SQLITE_PATH`` exactly as the existing API
    does.  Core construction is intentionally eager so unavailable adaptive
    artifacts fail startup.
    """

    selected_settings = settings or get_settings()
    selected_core = core or get_core(core_id)
    selected_store = job_store or MeetingJobStore(
        sqlite_path=os.getenv("MEETING_JOB_SQLITE_PATH") or None
    )

    app = FastAPI(
        title="Meeting Core API",
        version="1.0.0",
        description="Unified text job API for a selectable meeting core.",
    )
    app.state.core = selected_core
    app.state.job_store = selected_store
    app.state.settings = selected_settings

    def verify_api_key(
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> None:
        _verify_api_key(x_api_key, settings=selected_settings)

    @app.get("/health", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        pipeline_version, runtime_model_id, supports_meeting_note = _core_metadata(
            selected_core
        )
        return HealthResponse(
            status="ok",
            service=selected_settings.app_name,
            core_id=selected_core.core_id,
            adaptive=selected_core.capabilities.adaptive,
            pipeline_version=pipeline_version,
            runtime_model_id=runtime_model_id,
            model=runtime_model_id,
            supports_meeting_note=supports_meeting_note,
        )

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
    ) -> dict:
        content = await raw_request.body()
        transcript, packaged_note, packaged_metadata = parse_meeting_package(
            _decode_transcript(content)
        )
        _, _, supports_meeting_note = _core_metadata(selected_core)
        if packaged_note is not None and not supports_meeting_note:
            raise HTTPException(422, "Selected meeting core does not support Meeting Note")
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
        meeting_request = MeetingRequest(
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
                if packaged_note
                else None
            ),
        )
        meeting = _meeting(
            meeting_request,
            selected_settings,
            meeting_date_source_override=(
                "PACKAGE_METADATA"
                if not x_meeting_date and packaged_metadata.meeting_date
                else ""
            ),
        )
        return _submit_job(
            store=selected_store,
            core=selected_core,
            meeting=meeting,
            content_hash=default_id,
            summary_topic=summary_topic,
            settings=selected_settings,
            raw_upload_sha256=sha256(content).hexdigest(),
        )

    @app.get(
        "/api/v1/meetings/jobs/{job_id}",
        dependencies=[Depends(verify_api_key)],
        tags=["meetings"],
    )
    def get_process_file_job_endpoint(job_id: str) -> dict:
        job = selected_store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Meeting job not found")
        return job.as_response()

    @app.post(
        "/api/v1/meetings/jobs/{job_id}/feedback",
        tags=["meetings"],
        dependencies=[Depends(verify_api_key)],
    )
    def submit_feedback_endpoint(
        job_id: str, payload: FeedbackRequest, response: Response
    ) -> dict:
        job = selected_store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Meeting job not found")
        if job.status != "succeeded":
            raise HTTPException(status_code=409, detail="Feedback requires a succeeded job")
        feedback = _normalise_feedback(
            payload, route_job_id=job_id, job_content_hash=job.content_hash
        )
        context = _feedback_context(selected_settings)
        if context is None:
            raise HTTPException(status_code=503, detail="Feedback ledger is not configured")
        tenant, directory = context
        source_path = directory / tenant / "sources" / f"{job.content_hash}.json"
        source = _read_json(source_path)
        if (
            source is None
            or source.get("tenant_id") != tenant
            or source.get("content_hash") != job.content_hash
        ):
            raise HTTPException(status_code=409, detail="Feedback source record is missing")
        feedback["schema_version"] = "v227-feedback-correction-v1"
        feedback["tenant_id"] = tenant
        feedback["source_transcript_sha256"] = source.get("transcript_sha256", "")
        feedback["core_id"] = source.get("core_id", selected_core.core_id)
        feedback["pipeline_version"] = source.get("pipeline_version", job.pipeline_version)
        feedback["model"] = source.get("model", job.model)
        feedback["adaptive_training_eligible"] = (
            source.get("adaptive_training_eligible") is True
            and source.get("core_id") == "v2-adaptive"
            and not source.get("meeting_note")
        )
        feedback_hash = sha256(_canonical_json(feedback).encode("utf-8")).hexdigest()
        feedback["feedback_hash"] = feedback_hash
        feedback_path = directory / tenant / "feedback" / f"{job_id}.json"
        created = _atomic_create_json(feedback_path, feedback)
        if not created:
            existing = _read_json(feedback_path)
            if not isinstance(existing, dict):
                raise HTTPException(status_code=409, detail="Feedback record is unreadable")
            existing_hash = existing.get("feedback_hash")
            existing_unsigned = {
                key: value for key, value in existing.items() if key != "feedback_hash"
            }
            if (
                not isinstance(existing_hash, str)
                or sha256(_canonical_json(existing_unsigned).encode("utf-8")).hexdigest()
                != existing_hash
            ):
                raise HTTPException(status_code=409, detail="Feedback record hash mismatch")
            candidate_unsigned = {
                key: value for key, value in feedback.items() if key != "feedback_hash"
            }
            if _canonical_json(existing_unsigned) != _canonical_json(candidate_unsigned):
                raise HTTPException(status_code=409, detail="Conflicting feedback already exists")
            feedback_hash = existing_hash
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return {
            "job_id": job_id,
            "created": created,
            "feedback_hash": feedback_hash,
        }

    return app


app = create_app()


__all__ = ["app", "create_app"]
