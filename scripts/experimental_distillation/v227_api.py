"""Opt-in V2.27 experiment API with the Power Automate job routes.

Run this ASGI app explicitly; the production V1 app is unchanged.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from base64 import b64decode
from binascii import Error as Base64Error
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
from secrets import compare_digest
import tempfile
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from backend.app.ingestion import parse_meeting_package
from backend.app.jobs import MeetingJobStore
from scripts.experimental_distillation.run_v227_experimental import (
    ARTIFACT_DIR,
    _artifact_bundle,
    _meeting_date,
    _sha256,
    run,
)


MAX_TRANSCRIPT_BYTES = 2_000_000
SUFFIXES = {".txt", ".vtt", ".srt"}
FROZEN_MANIFEST_SHA256 = "7f1dc956fb30def28eb97ade562b7a8aa9b69345b530f6a79f122d180c416ca3"


def _decoded_header(value: str | None, name: str, *, max_characters: int) -> str | None:
    if value is None:
        return None
    try:
        decoded = b64decode(value, validate=True).decode("utf-8").strip()
    except (Base64Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(422, f"{name} must be Base64 UTF-8") from exc
    if not decoded:
        raise HTTPException(422, f"{name} cannot be empty")
    if len(decoded) > max_characters or any(ord(character) < 32 for character in decoded):
        raise HTTPException(422, f"{name} contains invalid characters or is too long")
    return decoded


def _decode_transcript(content: bytes) -> str:
    if not content or len(content) > MAX_TRANSCRIPT_BYTES:
        raise HTTPException(413 if content else 422, "Invalid transcript size")
    encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        transcript = content.decode(encoding)
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "Transcript must be UTF-8 or UTF-16") from exc
    if not transcript.strip():
        raise HTTPException(422, "Transcript is empty")
    return transcript


def create_app(
    *,
    artifact_directory: Path | None = None,
    api_key: str | None = None,
    expected_manifest_sha256: str = FROZEN_MANIFEST_SHA256,
) -> FastAPI:
    artifacts = Path(artifact_directory or os.getenv("V227_ARTIFACT_DIRECTORY") or ARTIFACT_DIR)
    expected_key = api_key if api_key is not None else os.getenv("POWER_AUTOMATE_API_KEY", "")
    jobs = MeetingJobStore()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not expected_key.strip():
            raise RuntimeError("STOP_POWER_AUTOMATE_API_KEY_REQUIRED")
        manifest_path = artifacts.resolve() / "manifest.json"
        if not manifest_path.is_file() or _sha256(manifest_path) != expected_manifest_sha256:
            raise RuntimeError("STOP_FROZEN_V228_MANIFEST_HASH_MISMATCH")
        _artifact_bundle(artifacts.resolve())
        yield

    app = FastAPI(title="V2.27 Experimental Meeting API", lifespan=lifespan)

    def authorized(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None) -> None:
        if not expected_key or not x_api_key or not compare_digest(x_api_key, expected_key):
            raise HTTPException(401, "Invalid or missing API key")

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "service": "V2.27 Experimental Meeting API",
            "pipeline_version": "v227_experimental",
            "experimental_not_validated": True,
        }

    @app.post(
        "/api/v1/meetings/jobs/process-file",
        status_code=202,
        dependencies=[Depends(authorized)],
    )
    async def submit(
        request: Request,
        x_file_name_base64: Annotated[str | None, Header(alias="X-File-Name-Base64")] = None,
        x_file_name: Annotated[str | None, Header(alias="X-File-Name")] = None,
        x_meeting_id: Annotated[str | None, Header(alias="X-Meeting-Id")] = None,
        x_meeting_title: Annotated[str | None, Header(alias="X-Meeting-Title")] = None,
        x_meeting_title_base64: Annotated[str | None, Header(alias="X-Meeting-Title-Base64")] = None,
        x_meeting_date: Annotated[str | None, Header(alias="X-Meeting-Date")] = None,
    ) -> dict:
        content = await request.body()
        transcript, note, metadata = parse_meeting_package(_decode_transcript(content))
        if note is not None:
            raise HTTPException(422, "V2.27 experiment does not support Meeting Note")
        raw_name = _decoded_header(x_file_name_base64, "X-File-Name-Base64", max_characters=512) or x_file_name or "meeting.txt"
        file_name = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(file_name).suffix.lower()
        if suffix not in SUFFIXES or len(file_name) > 255:
            raise HTTPException(422, "Supported file types: .txt, .vtt, .srt")
        title = _decoded_header(x_meeting_title_base64, "X-Meeting-Title-Base64", max_characters=255) or x_meeting_title or metadata.meeting_title or Path(file_name).stem
        meeting_id = x_meeting_id or metadata.meeting_id or "experimental-" + sha256(content).hexdigest()[:16]
        if (
            len(title) > 255 or not title or len(meeting_id) > 128 or not meeting_id
            or any(ord(character) < 32 for character in title + meeting_id)
        ):
            raise HTTPException(422, "Invalid meeting title or ID")
        raw_date = x_meeting_date or metadata.meeting_date or None
        try:
            meeting_date, _ = _meeting_date(raw_date, transcript)
            date.fromisoformat(meeting_date)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        identity = json.dumps(
            [file_name, meeting_id, title, meeting_date, sha256(content).hexdigest()],
            ensure_ascii=False, separators=(",", ":"),
        )
        content_hash = sha256(identity.encode("utf-8")).hexdigest()

        def work() -> dict:
            if _sha256(artifacts.resolve() / "manifest.json") != expected_manifest_sha256:
                raise RuntimeError("STOP_FROZEN_V228_MANIFEST_HASH_MISMATCH")
            with tempfile.TemporaryDirectory(prefix="v227-api-") as temporary:
                source = Path(temporary) / ("meeting" + suffix)
                source.write_text(transcript, encoding="utf-8")
                payload = run(
                    source,
                    meeting_date=meeting_date,
                    meeting_id=meeting_id,
                    meeting_title=title,
                    artifact_directory=artifacts,
                )
                # Keep the existing Power Automate pipeline-output schema.
                payload.pop("experimental", None)
                return payload

        job, created = jobs.submit(
            "v227-experimental:" + content_hash,
            work,
            pipeline_version="v227_experimental",
            prompt_version="disabled",
            model="v228-frozen-full-fit",
            content_hash=content_hash,
        )
        return {
            "job_id": job.job_id,
            "status": job.status,
            "created": created,
            "status_url": f"/api/v1/meetings/jobs/{job.job_id}",
        }

    @app.get("/api/v1/meetings/jobs/{job_id}", dependencies=[Depends(authorized)])
    def get_job(job_id: str) -> dict:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Meeting job not found")
        return job.as_response()

    return app


app = create_app()
