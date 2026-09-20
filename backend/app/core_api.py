"""Opt-in HTTP shell for the selectable text processing cores.

The existing :mod:`backend.app.main` application is the V1 compatibility API
and remains unchanged.  This module exposes only the common asynchronous file
job contract, with core selection performed once when the application is
created.  A selected core is therefore immutable for the lifetime of one
process and a bad selection fails before the server can start serving traffic.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import os
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
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

    return app


app = create_app()


__all__ = ["app", "create_app"]
