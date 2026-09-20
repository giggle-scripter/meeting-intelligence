"""Opt-in V2.27 experiment API with the Power Automate job routes.

Run this ASGI app explicitly; the production V1 app is unchanged.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from base64 import b64decode
from binascii import Error as Base64Error
from datetime import date, datetime, timezone
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
from secrets import compare_digest
import tempfile
from typing import Annotated, Any, Protocol

import httpx

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.app.ingestion import parse_meeting_package
from backend.app.jobs import MeetingJobStore
from scripts.experimental_distillation.run_v227_experimental import (
    ARTIFACT_DIR,
    CHALLENGER_ARTIFACTS,
    _artifact_bundle,
    _meeting_date,
    _sha256,
    run,
)


MAX_TRANSCRIPT_BYTES = 2_000_000
MAX_AUDIO_BYTES = 25 * 1024 * 1024
SUFFIXES = {".txt", ".vtt", ".srt"}
AUDIO_SUFFIXES = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}
V227_AUDIO_ENABLED_ENV = "V227_AUDIO_TO_TEXT_ENABLED"
V227_AUDIO_ENABLED_ALIASES = (V227_AUDIO_ENABLED_ENV, "V227_AUDIO_TRANSCRIPTION_ENABLED", "V227_AUDIO_ENABLED")
AUDIO_MODEL = "gpt-4o-transcribe-diarize"
OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
FROZEN_MANIFEST_SHA256 = "7f1dc956fb30def28eb97ade562b7a8aa9b69345b530f6a79f122d180c416ca3"
FEEDBACK_TENANT_ENV = "V227_FEEDBACK_TENANT_ID"
FEEDBACK_DIRECTORY_ENV = "V227_FEEDBACK_DIRECTORY"
DEFAULT_FEEDBACK_DIRECTORY = Path("evaluation/runtime/v227-feedback")
MAX_FEEDBACK_BYTES = 1_000_000
ACTIVE_POINTER_NAME = "active-v227-pointer.json"
ACTIVE_POINTER_SCHEMA = "v227-active-pointer-v1"
_CORRECTED_TASK_FIELDS = frozenset(
    {"task_name", "assignee", "start_date", "due_date", "due_date_text", "evidence", "status"}
)


class AudioTranscriber(Protocol):
    def __call__(self, audio: bytes, file_name: str, content_type: str) -> Any: ...


def _audio_enabled_from_environment() -> bool:
    return any(
        os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "on"}
        for name in V227_AUDIO_ENABLED_ALIASES
    )


def _format_diarized_transcript(value: Any) -> str:
    """Turn diarized_json into the speaker-prefixed text accepted by V1."""
    if not isinstance(value, (str, dict)) and hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("Audio transcription is empty")
        return value.strip() + "\n"
    if not isinstance(value, dict) or not isinstance(value.get("segments"), list):
        raise ValueError("Audio transcription did not contain diarized segments")
    lines: list[str] = []
    for index, segment in enumerate(value["segments"]):
        if not isinstance(segment, dict):
            raise ValueError(f"Invalid diarized segment at index {index}")
        speaker = str(segment.get("speaker", "")).strip()
        text = str(segment.get("text", "")).strip()
        if not speaker or not text:
            continue
        # V1's plain parser uses the first colon as the speaker separator.
        speaker = speaker.replace(chr(13), " ").replace(chr(10), " ")
        lines.append(f"{speaker}: {text.replace(chr(13), ' ').replace(chr(10), ' ')}")
    if not lines:
        raise ValueError("Audio transcription contained no usable speaker segments")
    return "\n".join(lines) + "\n"


class OpenAIAudioTranscriber:
    """Small server-side adapter for the OpenAI Audio Transcriptions API.

    The upload remains in memory and is sent directly to the provider.  The
    API key is read from the server environment and is never part of the HTTP
    upload contract.
    """

    def __init__(self, api_key: str, *, timeout_seconds: float = 300.0) -> None:
        if not api_key.strip():
            raise RuntimeError("STOP_OPENAI_API_KEY_REQUIRED_FOR_AUDIO")
        self._api_key = api_key
        self._timeout = timeout_seconds

    def __call__(self, audio: bytes, file_name: str, content_type: str) -> str:
        response = httpx.post(
            OPENAI_TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            files={"file": (file_name, audio, content_type)},
            data={
                "model": AUDIO_MODEL,
                "response_format": "diarized_json",
                # The API requires auto for recordings over 30 seconds; using
                # it for every upload keeps behavior uniform and documented.
                "chunking_strategy": "auto",
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("OpenAI audio transcription returned invalid JSON") from exc
        return _format_diarized_transcript(payload)


def _feedback_tenant() -> str | None:
    """Read the tenant only from server configuration; never from a request."""
    tenant = os.getenv(FEEDBACK_TENANT_ENV, "").strip()
    if not tenant:
        return None
    if len(tenant) > 64 or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in tenant):
        raise RuntimeError("STOP_INVALID_V227_FEEDBACK_TENANT_ID")
    return tenant


def _feedback_root(directory: Path, tenant: str) -> Path:
    if not tenant or len(tenant) > 64 or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in tenant):
        raise RuntimeError("STOP_INVALID_V227_FEEDBACK_TENANT_ID")
    # The tenant is validated above, so it cannot escape this private root.
    return directory.resolve() / tenant


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _atomic_create_json(path: Path, payload: dict[str, Any]) -> bool:
    """Create one append-only record without ever replacing an existing record.

    ``os.replace`` cannot provide no-overwrite semantics when two processes
    race: both can observe an absent path and the later replace wins.  A hard
    link from a fully fsynced temporary file is an atomic directory operation
    and fails with ``FileExistsError`` when another process won the race.  The
    temporary file and destination are kept in the same directory so the link
    is on one filesystem, including on Windows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical_json(payload))
            handle.write("\n")
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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Replace a private pointer atomically after flushing its contents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical_json(payload))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _pointer_path(directory: Path, tenant_id: str) -> Path:
    """Return the private activation pointer for one configured tenant."""
    return _feedback_root(directory, tenant_id) / ACTIVE_POINTER_NAME


def _configured_activation_tenant(tenant_id: str | None = None) -> str:
    """Resolve an offline CLI tenant, while the API remains env-only."""
    configured = _feedback_tenant()
    if configured is not None:
        if tenant_id is not None and tenant_id != configured:
            raise RuntimeError("STOP_ACTIVE_POINTER_TENANT_MISMATCH")
        return configured
    if tenant_id is not None:
        if len(tenant_id) > 64 or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in tenant_id):
            raise RuntimeError("STOP_INVALID_V227_FEEDBACK_TENANT_ID")
        return tenant_id
    raise RuntimeError("STOP_V227_FEEDBACK_TENANT_REQUIRED")


def _verify_base_bundle(
    artifact_directory: Path,
    *,
    expected_manifest_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    manifest_path = artifact_directory.resolve() / "manifest.json"
    if not manifest_path.is_file() or _sha256(manifest_path) != expected_manifest_sha256:
        raise RuntimeError("STOP_FROZEN_V228_MANIFEST_HASH_MISMATCH")
    model, policy, hashes = _artifact_bundle(artifact_directory.resolve())
    if hashes["manifest"] != expected_manifest_sha256:
        raise RuntimeError("STOP_FROZEN_V228_MANIFEST_HASH_MISMATCH")
    return model, policy, hashes


def _verify_challenger(
    challenger_directory: Path,
    *,
    tenant_id: str,
    feedback_directory: Path,
    base_hashes: dict[str, str],
    expected_manifest_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], dict[str, Any]]:
    challenger = challenger_directory.resolve()
    expected_parent = (feedback_directory.resolve() / tenant_id / "challengers").resolve()
    if challenger.parent != expected_parent:
        raise RuntimeError("STOP_CHALLENGER_PATH_TENANT_MISMATCH")
    required_files = set(CHALLENGER_ARTIFACTS) | {"manifest.json", "status.json"}
    if not challenger.is_dir() or {path.name for path in challenger.iterdir()} != required_files:
        raise RuntimeError("STOP_INVALID_CHALLENGER_PACKAGE")
    model, policy, hashes = _artifact_bundle(
        challenger,
        expected_tenant=tenant_id,
        expected_base_hashes=base_hashes,
    )
    manifest = _read_json(challenger / "manifest.json")
    coverage = _read_json(challenger / "coverage.json")
    if manifest is None or coverage is None:
        raise RuntimeError("STOP_INVALID_CHALLENGER_MANIFEST")
    if hashes["manifest"] != manifest.get("manifest_sha256", hashes["manifest"]):
        # A manifest may not self-sign; this branch only rejects an explicitly
        # contradictory value if a future package adds one.
        raise RuntimeError("STOP_CHALLENGER_MANIFEST_HASH_MISMATCH")
    if manifest.get("base_artifact_hashes") != base_hashes:
        raise RuntimeError("STOP_CHALLENGER_BASE_MISMATCH")
    if coverage.get("schema_version") != "v227-feedback-coverage-v1":
        raise RuntimeError("STOP_INVALID_CHALLENGER_COVERAGE")
    if not isinstance(coverage.get("meeting_count"), int) or coverage["meeting_count"] <= 0:
        raise RuntimeError("STOP_CHALLENGER_FEEDBACK_MEETING_COUNT")
    if not isinstance(coverage.get("covered_task_count"), int) or coverage["covered_task_count"] <= 0:
        raise RuntimeError("STOP_CHALLENGER_MATCHED_POSITIVE_REQUIRED")
    status = _read_json(challenger / "status.json")
    if status is None or status.get("schema_version") != "v227-feedback-status-v1" or status.get("status") != "complete" or status.get("manifest_sha256") != hashes["manifest"]:
        raise RuntimeError("STOP_INVALID_CHALLENGER_STATUS")
    if _sha256(challenger / "manifest.json") != hashes["manifest"]:
        raise RuntimeError("STOP_CHALLENGER_MANIFEST_HASH_MISMATCH")
    return model, policy, hashes, manifest


def _load_active_bundle(
    *,
    artifact_directory: Path,
    feedback_directory: Path,
    tenant_id: str | None,
    expected_manifest_sha256: str,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, str], str]:
    """Select the base unless one valid pointer activates this tenant."""
    base_model, base_policy, base_hashes = _verify_base_bundle(
        artifact_directory, expected_manifest_sha256=expected_manifest_sha256,
    )
    if tenant_id is None:
        return artifact_directory.resolve(), base_model, base_policy, base_hashes, "base"
    pointer_file = _pointer_path(feedback_directory, tenant_id)
    if not pointer_file.is_file():
        return artifact_directory.resolve(), base_model, base_policy, base_hashes, "base"
    pointer = _read_json(pointer_file)
    if pointer is None or pointer.get("schema_version") != ACTIVE_POINTER_SCHEMA:
        raise RuntimeError("STOP_INVALID_ACTIVE_POINTER")
    if pointer.get("tenant_id") != tenant_id or pointer.get("base_manifest_sha256") != expected_manifest_sha256:
        raise RuntimeError("STOP_ACTIVE_POINTER_TENANT_MISMATCH")
    audit = pointer.get("audit")
    if not isinstance(audit, list) or any(not isinstance(item, dict) for item in audit):
        raise RuntimeError("STOP_INVALID_ACTIVE_POINTER_AUDIT")
    if pointer.get("active") is not True:
        if pointer.get("challenger_path") is not None or pointer.get("challenger_manifest_sha256") is not None:
            raise RuntimeError("STOP_INVALID_ACTIVE_POINTER")
        return artifact_directory.resolve(), base_model, base_policy, base_hashes, "base"
    raw_path = pointer.get("challenger_path")
    manifest_hash = pointer.get("challenger_manifest_sha256")
    if not isinstance(raw_path, str) or not isinstance(manifest_hash, str):
        raise RuntimeError("STOP_INVALID_ACTIVE_POINTER")
    challenger = Path(raw_path)
    if not challenger.is_absolute() or _sha256(challenger / "manifest.json") != manifest_hash:
        raise RuntimeError("STOP_ACTIVE_POINTER_TAMPERED")
    model, policy, _, _ = _verify_challenger(
        challenger,
        tenant_id=tenant_id,
        feedback_directory=feedback_directory,
        base_hashes=base_hashes,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    # Keep the verified frozen-base hashes for per-job challenger checks.  The
    # challenger hashes identify the selected package itself and must not be
    # used as the challenger's declared base bundle.
    return challenger, model, policy, base_hashes, "challenger"


def _verify_active_bundle(
    artifact_directory: Path,
    *,
    active_model_kind: str,
    active_manifest_hash: str,
    feedback_tenant: str | None,
    active_base_hashes: dict[str, str],
) -> None:
    """Re-verify the startup-selected bundle before every job's side effects."""
    manifest_path = artifact_directory.resolve() / "manifest.json"
    if not manifest_path.is_file() or _sha256(manifest_path) != active_manifest_hash:
        raise RuntimeError(
            "STOP_ACTIVE_POINTER_TAMPERED"
            if active_model_kind == "challenger"
            else "STOP_FROZEN_V228_MANIFEST_HASH_MISMATCH"
        )
    _artifact_bundle(
        artifact_directory,
        expected_tenant=feedback_tenant if active_model_kind == "challenger" else None,
        expected_base_hashes=active_base_hashes if active_model_kind == "challenger" else None,
    )


def activate_challenger(
    challenger_directory: Path,
    *,
    feedback_directory: Path = DEFAULT_FEEDBACK_DIRECTORY,
    artifact_directory: Path = ARTIFACT_DIR,
    tenant_id: str | None = None,
    expected_manifest_sha256: str = FROZEN_MANIFEST_SHA256,
) -> dict[str, Any]:
    """Atomically activate one verified challenger for the configured tenant."""
    tenant = _configured_activation_tenant(tenant_id)
    _, _, base_hashes = _verify_base_bundle(artifact_directory, expected_manifest_sha256=expected_manifest_sha256)
    _, _, challenger_hashes, manifest = _verify_challenger(
        Path(challenger_directory), tenant_id=tenant, feedback_directory=feedback_directory,
        base_hashes=base_hashes, expected_manifest_sha256=expected_manifest_sha256,
    )
    pointer_file = _pointer_path(feedback_directory, tenant)
    previous = _read_json(pointer_file) if pointer_file.is_file() else None
    if pointer_file.is_file() and previous is None:
        raise RuntimeError("STOP_INVALID_ACTIVE_POINTER")
    if previous is not None and previous.get("tenant_id") != tenant:
        raise RuntimeError("STOP_ACTIVE_POINTER_TENANT_MISMATCH")
    audit = list(previous.get("audit", [])) if previous else []
    event = {
        "action": "activate", "tenant_id": tenant, "challenger_path": str(Path(challenger_directory).resolve()),
        "challenger_manifest_sha256": challenger_hashes["manifest"],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    audit.append(event)
    pointer = {
        "schema_version": ACTIVE_POINTER_SCHEMA, "active": True, "tenant_id": tenant,
        "base_manifest_sha256": expected_manifest_sha256,
        "challenger_path": event["challenger_path"],
        "challenger_manifest_sha256": event["challenger_manifest_sha256"],
        "previous": previous, "audit": audit,
    }
    _atomic_write_json(pointer_file, pointer)
    return pointer


def rollback_activation(
    *,
    feedback_directory: Path = DEFAULT_FEEDBACK_DIRECTORY,
    artifact_directory: Path = ARTIFACT_DIR,
    tenant_id: str | None = None,
    expected_manifest_sha256: str = FROZEN_MANIFEST_SHA256,
) -> dict[str, Any]:
    """Atomically switch this tenant back to the pinned frozen V2.28 base."""
    tenant = _configured_activation_tenant(tenant_id)
    _verify_base_bundle(artifact_directory, expected_manifest_sha256=expected_manifest_sha256)
    pointer_file = _pointer_path(feedback_directory, tenant)
    previous = _read_json(pointer_file) if pointer_file.is_file() else None
    if pointer_file.is_file() and previous is None:
        raise RuntimeError("STOP_INVALID_ACTIVE_POINTER")
    if previous is not None and previous.get("tenant_id") != tenant:
        raise RuntimeError("STOP_ACTIVE_POINTER_TENANT_MISMATCH")
    audit = list(previous.get("audit", [])) if previous else []
    audit.append({"action": "rollback", "tenant_id": tenant, "at": datetime.now(timezone.utc).isoformat()})
    pointer = {
        "schema_version": ACTIVE_POINTER_SCHEMA, "active": False, "tenant_id": tenant,
        "base_manifest_sha256": expected_manifest_sha256,
        "challenger_path": None, "challenger_manifest_sha256": None,
        "previous": previous, "audit": audit,
    }
    _atomic_write_json(pointer_file, pointer)
    return pointer


def _approval_metadata(body: dict[str, Any]) -> dict[str, Any]:
    metadata = body.get("approval_metadata")
    # Accepting the three fields at the top level keeps the HTTP contract easy
    # for Power Automate while still requiring all explicit approval fields.
    if metadata is None:
        metadata = {key: body.get(key) for key in ("reviewer", "reviewed_at", "approval")}
    if not isinstance(metadata, dict):
        raise HTTPException(422, "approval_metadata is required")
    reviewer = metadata.get("reviewer")
    reviewed_at = metadata.get("reviewed_at")
    approval = metadata.get("approval")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 256:
        raise HTTPException(422, "approval_metadata.reviewer is required")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip() or len(reviewed_at) > 128:
        raise HTTPException(422, "approval_metadata.reviewed_at is required")
    try:
        parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, "approval_metadata.reviewed_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise HTTPException(422, "approval_metadata.reviewed_at must include a timezone")
    if approval is not True:
        raise HTTPException(422, "approval_metadata.approval must be true")
    return {"reviewer": reviewer.strip(), "reviewed_at": reviewed_at, "approval": True}


def _validate_corrected_tasks(value: Any) -> list[dict[str, Any]]:
    """Validate the correction list against the public pipeline task contract."""
    if not isinstance(value, list):
        raise HTTPException(422, "corrected_final_tasks must be a list of objects")
    if len(value) > 1_000:
        raise HTTPException(422, "Too many corrected tasks")
    for index, task in enumerate(value):
        if not isinstance(task, dict):
            raise HTTPException(422, f"corrected_final_tasks[{index}] must be an object")
        if set(task) != _CORRECTED_TASK_FIELDS:
            raise HTTPException(
                422,
                f"corrected_final_tasks[{index}] must contain exactly the pipeline task fields",
            )
        for field in _CORRECTED_TASK_FIELDS:
            if not isinstance(task[field], str):
                raise HTTPException(422, f"corrected_final_tasks[{index}].{field} must be a string")
        if task["status"] != "Proposed":
            raise HTTPException(422, f"corrected_final_tasks[{index}].status must be Proposed")
    return value


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
    feedback_directory: Path | None = None,
    api_key: str | None = None,
    expected_manifest_sha256: str = FROZEN_MANIFEST_SHA256,
    audio_enabled: bool | None = None,
    audio_transcriber: AudioTranscriber | None = None,
    openai_api_key: str | None = None,
    job_sqlite_path: str | Path | None = None,
) -> FastAPI:
    artifacts = Path(artifact_directory or os.getenv("V227_ARTIFACT_DIRECTORY") or ARTIFACT_DIR)
    configured_feedback_directory = Path(feedback_directory or os.getenv(FEEDBACK_DIRECTORY_ENV) or DEFAULT_FEEDBACK_DIRECTORY)
    feedback_tenant = _feedback_tenant()
    expected_key = api_key if api_key is not None else os.getenv("POWER_AUTOMATE_API_KEY", "")
    audio_opt_in = _audio_enabled_from_environment() if audio_enabled is None else audio_enabled
    configured_audio_transcriber = audio_transcriber
    # SQLite is opt-in and scoped to this single local worker.  Passing a path
    # is useful for tests and operators who do not want to mutate the process
    # environment; otherwise MeetingJobStore reads MEETING_JOB_SQLITE_PATH.
    jobs = MeetingJobStore(sqlite_path=job_sqlite_path)
    active_artifacts = artifacts.resolve()
    active_model_kind = "base"
    active_base_hashes: dict[str, str] = {}
    active_manifest_hash = expected_manifest_sha256

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal active_artifacts, active_model_kind, active_base_hashes, active_manifest_hash, configured_audio_transcriber
        if not expected_key.strip():
            raise RuntimeError("STOP_POWER_AUTOMATE_API_KEY_REQUIRED")
        if audio_opt_in and audio_transcriber is None and not (openai_api_key or os.getenv("OPENAI_API_KEY", "")).strip():
            raise RuntimeError("STOP_OPENAI_API_KEY_REQUIRED_FOR_AUDIO")
        if audio_opt_in and configured_audio_transcriber is None:
            configured_audio_transcriber = OpenAIAudioTranscriber(
                openai_api_key if openai_api_key is not None else os.getenv("OPENAI_API_KEY", "")
            )
        active_artifacts, _, _, active_base_hashes, active_model_kind = _load_active_bundle(
            artifact_directory=artifacts,
            feedback_directory=configured_feedback_directory,
            tenant_id=feedback_tenant,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        active_manifest_hash = _sha256(active_artifacts / "manifest.json")
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
        raw_upload_sha256 = sha256(content).hexdigest()
        identity = json.dumps(
            [file_name, meeting_id, title, meeting_date, raw_upload_sha256],
            ensure_ascii=False, separators=(",", ":"),
        )
        content_hash = sha256(identity.encode("utf-8")).hexdigest()

        def work() -> dict:
            # Re-verify the selected package for every job.  There is no hot
            # reload: a restart is required to select a new pointer, while a
            # tampered package fails closed before producing output.
            _verify_active_bundle(
                active_artifacts,
                active_model_kind=active_model_kind,
                active_manifest_hash=active_manifest_hash,
                feedback_tenant=feedback_tenant,
                active_base_hashes=active_base_hashes,
            )
            with tempfile.TemporaryDirectory(prefix="v227-api-") as temporary:
                source = Path(temporary) / ("meeting" + suffix)
                source.write_text(transcript, encoding="utf-8")
                payload = run(
                    source,
                    meeting_date=meeting_date,
                    meeting_id=meeting_id,
                    meeting_title=title,
                    artifact_directory=active_artifacts,
                )
                # Keep the existing Power Automate pipeline-output schema.
                payload.pop("experimental", None)
                return payload

        if feedback_tenant:
            source_root = _feedback_root(configured_feedback_directory, feedback_tenant)
            source_record = {
                "schema_version": "v227-feedback-source-v1",
                "source_modality": "transcript",
                "tenant_id": feedback_tenant,
                "content_hash": content_hash,
                "raw_upload_sha256": raw_upload_sha256,
                "transcript_sha256": sha256(transcript.encode("utf-8")).hexdigest(),
                "transcript": transcript,
                "file_name": file_name,
                "meeting_id": meeting_id,
                "meeting_title": title,
                "meeting_date": meeting_date,
            }
            try:
                # This record must exist before a job is submitted.  The
                # content hash is stable across retries and is also the job's
                # idempotency key, so no job-id link record is needed.
                source_path = source_root / "sources" / f"{content_hash}.json"
                source_created = _atomic_create_json(source_path, source_record)
                if not source_created:
                    existing_source = _read_json(source_path)
                    if (
                        not existing_source
                        or existing_source.get("content_hash") != content_hash
                        or existing_source.get("source_modality", "transcript") != "transcript"
                        or existing_source.get("raw_upload_sha256") != raw_upload_sha256
                        or existing_source.get("transcript_sha256") != source_record["transcript_sha256"]
                    ):
                        raise OSError("private source record conflicts with the content hash")
            except OSError as exc:
                # Do not expose the transcript or private path in the response/logs.
                raise HTTPException(500, "Unable to persist private feedback source") from exc

        job, created = jobs.submit(
            "v227-experimental:" + content_hash,
            work,
            pipeline_version="v227_experimental",
            prompt_version="disabled",
            model="v227-tenant-challenger" if active_model_kind == "challenger" else "v228-frozen-full-fit",
            content_hash=content_hash,
        )
        return {
            "job_id": job.job_id,
            "status": job.status,
            "created": created,
            "status_url": f"/api/v1/meetings/jobs/{job.job_id}",
        }

    @app.post(
        "/api/v1/meetings/jobs/process-audio",
        status_code=202,
        dependencies=[Depends(authorized)],
    )
    async def submit_audio(
        request: Request,
        x_file_name_base64: Annotated[str | None, Header(alias="X-File-Name-Base64")] = None,
        x_file_name: Annotated[str | None, Header(alias="X-File-Name")] = None,
        x_meeting_id: Annotated[str | None, Header(alias="X-Meeting-Id")] = None,
        x_meeting_title: Annotated[str | None, Header(alias="X-Meeting-Title")] = None,
        x_meeting_title_base64: Annotated[str | None, Header(alias="X-Meeting-Title-Base64")] = None,
        x_meeting_date: Annotated[str | None, Header(alias="X-Meeting-Date")] = None,
    ) -> dict:
        if not audio_opt_in:
            raise HTTPException(404, "V2.27 audio transcription is disabled")
        if configured_audio_transcriber is None:
            # This can only happen if the app is called without its lifespan;
            # fail closed instead of accepting an upload that cannot be run.
            raise HTTPException(503, "Audio transcription provider is unavailable")
        content = await request.body()
        if not content:
            raise HTTPException(422, "Audio upload is empty")
        if len(content) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Audio upload exceeds the 25 MB limit")
        raw_name = _decoded_header(
            x_file_name_base64, "X-File-Name-Base64", max_characters=512,
        ) or x_file_name
        if not raw_name:
            raise HTTPException(422, "X-File-Name or X-File-Name-Base64 is required")
        file_name = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(file_name).suffix.lower()
        if suffix not in AUDIO_SUFFIXES or len(file_name) > 255 or not file_name or any(ord(character) < 32 for character in file_name):
            raise HTTPException(422, "Supported audio types: .mp3, .mp4, .mpeg, .mpga, .m4a, .wav, .webm")
        title = _decoded_header(
            x_meeting_title_base64, "X-Meeting-Title-Base64", max_characters=255,
        ) or x_meeting_title or Path(file_name).stem
        meeting_id = x_meeting_id or "audio-" + sha256(content).hexdigest()[:16]
        if (
            len(title) > 255 or not title or len(meeting_id) > 128 or not meeting_id
            or any(ord(character) < 32 for character in title + meeting_id)
        ):
            raise HTTPException(422, "Invalid meeting title or ID")
        if not x_meeting_date or len(x_meeting_date) > 32:
            raise HTTPException(422, "X-Meeting-Date is required for audio uploads")
        try:
            meeting_date = date.fromisoformat(x_meeting_date.strip()).isoformat()
        except ValueError as exc:
            raise HTTPException(422, "X-Meeting-Date must be ISO-8601 YYYY-MM-DD") from exc

        raw_upload_sha256 = sha256(content).hexdigest()
        identity = json.dumps(
            ["audio", file_name, meeting_id, title, meeting_date, raw_upload_sha256],
            ensure_ascii=False, separators=(",", ":"),
        )
        content_hash = sha256(identity.encode("utf-8")).hexdigest()
        content_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip()
        if not content_type or "/" not in content_type:
            content_type = "application/octet-stream"

        def work() -> dict:
            _verify_active_bundle(
                active_artifacts,
                active_model_kind=active_model_kind,
                active_manifest_hash=active_manifest_hash,
                feedback_tenant=feedback_tenant,
                active_base_hashes=active_base_hashes,
            )
            # Keep the audio bytes in memory only. The temporary file below
            # contains the derived transcript and is deleted after inference.
            transcript = _format_diarized_transcript(
                configured_audio_transcriber(content, file_name, content_type)
            )
            if feedback_tenant:
                source_root = _feedback_root(configured_feedback_directory, feedback_tenant)
                transcript_hash = sha256(transcript.encode("utf-8")).hexdigest()
                source_record = {
                    "schema_version": "v227-feedback-source-v1",
                    "source_modality": "audio",
                    "tenant_id": feedback_tenant,
                    "content_hash": content_hash,
                    "raw_upload_sha256": raw_upload_sha256,
                    "transcript_sha256": transcript_hash,
                    "transcript": transcript,
                    "file_name": file_name,
                    "meeting_id": meeting_id,
                    "meeting_title": title,
                    "meeting_date": meeting_date,
                }
                source_path = source_root / "sources" / f"{content_hash}.json"
                try:
                    created = _atomic_create_json(source_path, source_record)
                    if not created:
                        existing = _read_json(source_path)
                        if (
                            not existing
                            or existing.get("content_hash") != content_hash
                            or existing.get("source_modality") != "audio"
                            or existing.get("raw_upload_sha256") != raw_upload_sha256
                            or existing.get("transcript_sha256") != transcript_hash
                        ):
                            raise OSError("private source record conflicts with the content hash")
                except OSError as exc:
                    raise RuntimeError("Unable to persist private feedback source") from exc
            with tempfile.TemporaryDirectory(prefix="v227-audio-api-") as temporary:
                source = Path(temporary) / "meeting.txt"
                source.write_text(transcript, encoding="utf-8")
                payload = run(
                    source,
                    meeting_date=meeting_date,
                    meeting_id=meeting_id,
                    meeting_title=title,
                    artifact_directory=active_artifacts,
                )
                payload.pop("experimental", None)
                return payload

        job, created = jobs.submit(
            "v227-experimental-audio:" + content_hash,
            work,
            pipeline_version="v227_experimental",
            prompt_version="disabled",
            model=AUDIO_MODEL,
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

    @app.post("/api/v1/meetings/jobs/{job_id}/feedback", dependencies=[Depends(authorized)])
    async def submit_feedback(job_id: str, request: Request) -> JSONResponse:
        """Store one human-approved correction for a completed V2.27 job."""
        if feedback_tenant is None:
            raise HTTPException(404, "V2.27 feedback intake is disabled")
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Meeting job not found")
        if job.status != "succeeded":
            raise HTTPException(409, "Feedback requires a succeeded meeting job")
        raw_body = await request.body()
        if not raw_body or len(raw_body) > MAX_FEEDBACK_BYTES:
            raise HTTPException(413 if raw_body else 422, "Invalid feedback payload size")
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, "Feedback payload must be UTF-8 JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(422, "Feedback payload must be an object")
        if body.get("job_id") != job_id:
            raise HTTPException(409, "Feedback job_id does not match the route")
        if body.get("content_hash") != job.content_hash:
            raise HTTPException(409, "Feedback content_hash does not match the job")
        tasks = _validate_corrected_tasks(body.get("corrected_final_tasks"))
        approval = _approval_metadata(body)

        source_root = _feedback_root(configured_feedback_directory, feedback_tenant)
        source_path = source_root / "sources" / f"{job.content_hash}.json"
        source = _read_json(source_path)
        if source is None or source.get("content_hash") != job.content_hash:
            raise HTTPException(409, "Feedback source does not match the meeting job")
        record = {
            "schema_version": "v227-feedback-correction-v1",
            "tenant_id": feedback_tenant,
            "job_id": job_id,
            "content_hash": job.content_hash,
            "source_modality": source.get("source_modality", "transcript"),
            "source_transcript_sha256": source.get("transcript_sha256", ""),
            "corrected_final_tasks": tasks,
            "approval_metadata": approval,
        }
        feedback_path = source_root / "feedback" / f"{job_id}.json"
        feedback_hash = sha256(_canonical_json(record).encode("utf-8")).hexdigest()
        record["feedback_hash"] = feedback_hash
        existing = _read_json(feedback_path)
        if existing is not None:
            if existing.get("feedback_hash") == feedback_hash:
                return JSONResponse({"job_id": job_id, "created": False, "feedback_hash": feedback_hash}, status_code=200)
            raise HTTPException(409, "Conflicting feedback already exists for this job")
        try:
            created = _atomic_create_json(feedback_path, record)
        except OSError as exc:
            raise HTTPException(500, "Unable to persist private feedback") from exc
        if not created:
            existing = _read_json(feedback_path)
            if existing and existing.get("feedback_hash") == feedback_hash:
                return JSONResponse({"job_id": job_id, "created": False, "feedback_hash": feedback_hash}, status_code=200)
            raise HTTPException(409, "Conflicting feedback already exists for this job")
        return JSONResponse({"job_id": job_id, "created": True, "feedback_hash": feedback_hash}, status_code=201)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the private V2.27 tenant challenger pointer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    activate_parser = subparsers.add_parser("activate", help="activate one verified challenger; restart the API")
    activate_parser.add_argument("--challenger", type=Path, required=True)
    rollback_parser = subparsers.add_parser("rollback", help="switch the tenant back to the frozen base; restart the API")
    for command_parser in (activate_parser, rollback_parser):
        command_parser.add_argument("--tenant-id", help="offline tenant only when V227_FEEDBACK_TENANT_ID is unset")
        command_parser.add_argument("--feedback-directory", type=Path, default=DEFAULT_FEEDBACK_DIRECTORY)
        command_parser.add_argument("--artifact-directory", type=Path, default=ARTIFACT_DIR)
        command_parser.add_argument("--expected-manifest-sha256", default=FROZEN_MANIFEST_SHA256)
    args = parser.parse_args(argv)
    try:
        if args.command == "activate":
            result = activate_challenger(
                args.challenger, feedback_directory=args.feedback_directory,
                artifact_directory=args.artifact_directory, tenant_id=args.tenant_id,
                expected_manifest_sha256=args.expected_manifest_sha256,
            )
        else:
            result = rollback_activation(
                feedback_directory=args.feedback_directory, artifact_directory=args.artifact_directory,
                tenant_id=args.tenant_id, expected_manifest_sha256=args.expected_manifest_sha256,
            )
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed with one diagnostic
        print(json.dumps({"status": "STOP", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "complete", "active": result["active"], "tenant_id": result["tenant_id"]}, sort_keys=True))
    return 0


app = create_app()


if __name__ == "__main__":
    raise SystemExit(main())
