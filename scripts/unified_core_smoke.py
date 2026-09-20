"""Bounded smoke client for an already-running Meeting Core API.

The client talks to ``backend.app.core_api:app`` only.  It does not start an
ASGI server, a tunnel, a provider, or a trainer.  A transcript is uploaded as
the raw file body because that is the current core API upload contract; the
file metadata is carried in the existing ``X-*`` headers.
"""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import httpx


API_PATH = "/api/v1/meetings/jobs/process-file"
DEFAULT_API_KEY_ENV = "POWER_AUTOMATE_API_KEY"
MAX_FILE_BYTES = 2_000_000
ALLOWED_SUFFIXES = {".txt", ".vtt", ".srt"}


class SmokeError(RuntimeError):
    """Actionable error which is safe to display to an operator."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Return bounded diagnostics without known secrets or key-shaped values."""

    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = re.sub(
        r"(?i)(x-api-key|api[_-]?key|authorization)(\s*[:=]\s*)([^,\s;]+)",
        r"\1\2[REDACTED]",
        result,
    )
    return result[:500]


def _safe_value(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Redact strings recursively while omitting transcript-like fields."""

    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(r"(?i)(api[_-]?key|authorization|transcript_raw|transcript)", key_text):
                continue
            clean[key_text] = _safe_value(item, secrets)
        return clean
    if isinstance(value, list):
        return [_safe_value(item, secrets) for item in value]
    if isinstance(value, str):
        return _redact(value, secrets)
    return value


def _validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise SmokeError("--base-url must be an HTTP(S) URL without query or fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SmokeError("plain HTTP is allowed only for localhost; use HTTPS for remote APIs")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def read_api_key(api_key_file: Path | None, api_key_env: str = DEFAULT_API_KEY_ENV) -> str:
    """Read an API key without ever writing its value to output."""

    if api_key_file is not None:
        try:
            key = api_key_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise SmokeError(f"unable to read API key file: {api_key_file}") from exc
        source = str(api_key_file)
    else:
        key = os.environ.get(api_key_env, "").strip()
        source = api_key_env
    if not key:
        raise SmokeError(f"API key is empty or unavailable ({source})")
    return key


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _content_type(path: Path) -> str:
    return {".vtt": "text/vtt", ".srt": "application/x-subrip"}.get(path.suffix.lower(), "text/plain")


class ApiClient:
    """Small HTTP seam used by the CLI and mocked tests."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 15.0, *, http_client: httpx.Client | None = None) -> None:
        if timeout <= 0 or timeout > 300:
            raise SmokeError("--timeout must be greater than 0 and at most 300 seconds")
        self.base_url = _validate_base_url(base_url)
        self.api_key = api_key
        self.timeout = timeout
        self._owned = http_client is None
        self.client = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owned:
            self.client.close()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, url, timeout=self.timeout, **kwargs)
        except httpx.TimeoutException as exc:
            raise SmokeError(f"HTTP {method} timed out") from exc
        except httpx.HTTPError as exc:
            raise SmokeError(f"HTTP {method} failed: {_redact(str(exc), (self.api_key,))}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise SmokeError(f"HTTP {method} returned status {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SmokeError(f"HTTP {method} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SmokeError(f"HTTP {method} returned a JSON object was expected")
        return payload

    def health(self) -> dict[str, Any]:
        return self._request("GET", self._url("/health"), headers={"X-API-Key": self.api_key})

    def submit(self, content: bytes, *, file_name: str, meeting_id: str, meeting_title: str, meeting_date: str) -> dict[str, Any]:
        # The core endpoint consumes the file bytes directly.  This is the
        # multipart/file upload equivalent for its existing raw-body schema.
        headers = {
            "X-API-Key": self.api_key,
            "Content-Type": _content_type(Path(file_name)) + "; charset=utf-8",
            "X-File-Name-Base64": _encoded(file_name),
            "X-Meeting-Id": meeting_id,
            "X-Meeting-Title-Base64": _encoded(meeting_title),
            "X-Meeting-Date": meeting_date,
        }
        return self._request("POST", self._url(API_PATH), headers=headers, content=content)

    def status(self, status_url: str) -> dict[str, Any]:
        if not isinstance(status_url, str) or not status_url:
            raise SmokeError("submit response did not contain status_url")
        parsed = urlparse(status_url)
        if parsed.scheme or parsed.netloc or status_url.startswith("//") or "\\" in status_url:
            raise SmokeError("status_url must be a same-origin relative URL")
        target = urljoin(self.base_url.rstrip("/") + "/", status_url.lstrip("/"))
        origin = urlparse(self.base_url)
        target_parts = urlparse(target)
        if (target_parts.scheme, target_parts.netloc) != (origin.scheme, origin.netloc):
            raise SmokeError("status_url points outside the configured API origin")
        return self._request("GET", target, headers={"X-API-Key": self.api_key})

    def feedback(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            self._url(f"/api/v1/meetings/jobs/{job_id}/feedback"),
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            json=payload,
        )


def _verify_health(health: Mapping[str, Any], expected_core: str) -> dict[str, Any]:
    if expected_core not in {"v1-frozen", "v2-adaptive"}:
        raise SmokeError("--expected-core must be v1-frozen or v2-adaptive")
    if health.get("status") != "ok" or health.get("core_id") != expected_core:
        raise SmokeError("health did not report the expected core")
    if not isinstance(health.get("adaptive"), bool):
        raise SmokeError("health did not report a boolean adaptive capability")
    if health["adaptive"] != (expected_core == "v2-adaptive"):
        raise SmokeError("health adaptive capability does not match the expected core")
    for field in ("pipeline_version", "runtime_model_id", "model"):
        if not isinstance(health.get(field), str) or not health[field].strip():
            raise SmokeError(f"health did not report {field}")
    if health["model"] != health["runtime_model_id"]:
        raise SmokeError("health model and runtime_model_id disagree")
    if not isinstance(health.get("supports_meeting_note"), bool):
        raise SmokeError("health did not report supports_meeting_note")
    if health["supports_meeting_note"] != (expected_core == "v1-frozen"):
        raise SmokeError("health Meeting Note capability does not match the expected core")
    return dict(health)


def _validate_file(path: Path) -> bytes:
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise SmokeError("meeting file must have a .txt, .vtt, or .srt extension")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SmokeError(f"unable to read meeting file: {path}") from exc
    if not content or len(content) > MAX_FILE_BYTES:
        raise SmokeError("meeting file must be non-empty and at most 2,000,000 bytes")
    return content


def _validate_success(job: Mapping[str, Any], *, job_id: str, health: Mapping[str, Any]) -> None:
    if job.get("job_id") != job_id:
        raise SmokeError("status response job_id does not match submitted job")
    if job.get("status") != "succeeded":
        raise SmokeError(f"job failed (job_id={job_id})")
    if not isinstance(job.get("result"), dict):
        raise SmokeError("succeeded job did not contain a result object")
    if not isinstance(job.get("content_hash"), str) or not job["content_hash"].strip():
        raise SmokeError("succeeded job did not contain content_hash audit metadata")
    for field in ("pipeline_version", "model"):
        if not isinstance(job.get(field), str) or not job[field].strip():
            raise SmokeError(f"succeeded job did not contain {field} audit metadata")
    if job.get("pipeline_version") != health.get("pipeline_version"):
        raise SmokeError("job pipeline_version does not match health audit metadata")
    if job.get("model") != health.get("runtime_model_id"):
        raise SmokeError("job model does not match health runtime_model_id")


def _feedback_payload(raw: Mapping[str, Any], *, report: Mapping[str, Any]) -> tuple[str, dict[str, Any], bool]:
    job = report.get("job")
    health = report.get("health")
    if not isinstance(job, Mapping) or not isinstance(health, Mapping):
        raise SmokeError("feedback requires a smoke report with job and health identity")
    job_id = raw.get("job_id")
    core_id = raw.get("core_id")
    if job_id != job.get("job_id"):
        raise SmokeError("feedback job_id does not match the smoke job")
    if core_id != health.get("core_id"):
        raise SmokeError("feedback core_id does not match the smoke core")
    required = {"job_id", "content_hash", "corrected_final_tasks", "approval_metadata"}
    if not required.issubset(raw):
        raise SmokeError("feedback JSON must contain job_id, core_id, content_hash, corrected_final_tasks, and approval_metadata")
    if raw.get("content_hash") != job.get("content_hash"):
        raise SmokeError("feedback content_hash does not match the smoke job")
    if not isinstance(raw.get("corrected_final_tasks"), list) or not isinstance(raw.get("approval_metadata"), dict):
        raise SmokeError("feedback corrected_final_tasks and approval_metadata have invalid types")
    approval = raw["approval_metadata"]
    if approval.get("approval") is not True:
        raise SmokeError("feedback approval_metadata.approval must be explicitly true")
    # core_id is an operator-side identity guard; the API schema intentionally
    # remains unchanged, so it is removed from the wire payload.
    payload = {key: deepcopy(raw[key]) for key in required if key != "job_id"}
    payload["job_id"] = job_id
    eligible = health.get("core_id") == "v2-adaptive" and health.get("adaptive") is True
    return job_id, payload, eligible


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (_canonical(value) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise SmokeError(f"unable to write report: {path}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def run_smoke(args: argparse.Namespace, *, http_client: httpx.Client | None = None) -> dict[str, Any]:
    if args.poll_timeout <= 0 or args.poll_interval <= 0:
        raise SmokeError("poll timeout and interval must be greater than zero")
    feedback_file = getattr(args, "feedback_file", None)
    allow_feedback = bool(getattr(args, "allow_feedback", False))
    if bool(feedback_file) != allow_feedback:
        raise SmokeError("feedback write requires both --feedback-file and --allow-feedback")
    transcript_path = Path(args.meeting_file)
    content = _validate_file(transcript_path)
    key = read_api_key(Path(args.api_key_file) if args.api_key_file else None, args.api_key_env)
    client = ApiClient(args.base_url, key, args.timeout, http_client=http_client)
    report: dict[str, Any] = {"schema_version": "unified-core-smoke-report-v1", "ok": False}
    try:
        health = _verify_health(client.health(), args.expected_core)
        report["health"] = _safe_value(health, (key,))
        submitted = client.submit(
            content,
            file_name=transcript_path.name,
            meeting_id=args.meeting_id,
            meeting_title=args.meeting_title,
            meeting_date=args.meeting_date,
        )
        job_id = submitted.get("job_id")
        status_url = submitted.get("status_url")
        if not isinstance(job_id, str) or not job_id or not isinstance(status_url, str):
            raise SmokeError("submit response did not contain job_id and status_url")
        deadline = time.monotonic() + args.poll_timeout
        latest: dict[str, Any]
        while True:
            latest = client.status(status_url)
            state = latest.get("status")
            if state == "succeeded":
                break
            if state == "failed":
                raise SmokeError(f"job failed (job_id={job_id})")
            if state not in {"queued", "running"}:
                raise SmokeError(f"job returned invalid status (job_id={job_id})")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SmokeError(f"poll timeout (job_id={job_id})")
            time.sleep(min(args.poll_interval, remaining))
        _validate_success(latest, job_id=job_id, health=health)
        result = latest["result"]
        eligibility = "adaptive_training_eligible" if health["core_id"] == "v2-adaptive" else "audit_only"
        report.update({
            "meeting": {"meeting_id": args.meeting_id, "meeting_title": args.meeting_title, "meeting_date": args.meeting_date, "file_name": transcript_path.name},
            "submission": {"job_id": job_id, "created": submitted.get("created"), "status_url": status_url},
            "job": {
                "job_id": job_id,
                "status": latest.get("status"),
                "core_id": health["core_id"],
                "content_hash": latest.get("content_hash"),
                "pipeline_version": latest.get("pipeline_version"),
                "prompt_version": latest.get("prompt_version"),
                "model": latest.get("model"),
                "result_summary": {"task_count": len(result.get("tasks", [])) if isinstance(result.get("tasks"), list) else None},
            },
            "feedback_policy": {
                "core_id": health["core_id"],
                "mode": eligibility,
                "audit_only": health["core_id"] == "v1-frozen",
                "adaptive_training_eligible": eligibility == "adaptive_training_eligible",
                "requested": bool(feedback_file),
            },
        })
        if feedback_file:
            try:
                raw_feedback = json.loads(Path(feedback_file).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise SmokeError(f"unable to read feedback JSON: {feedback_file}") from exc
            if not isinstance(raw_feedback, dict):
                raise SmokeError("feedback JSON must contain an object")
            _, payload, eligible = _feedback_payload(raw_feedback, report=report)
            feedback_response = client.feedback(job_id, payload)
            if feedback_response.get("job_id") != job_id or not isinstance(feedback_response.get("created"), bool):
                raise SmokeError("feedback response did not contain matching job_id and created")
            report["feedback_policy"]["adaptive_training_eligible"] = eligible
            report["feedback"] = _safe_value(feedback_response, (key,))
        report["ok"] = True
        return _safe_value(report, (key,))
    finally:
        client.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test an already-running Meeting Core API")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-core", "--core", dest="expected_core", choices=("v1-frozen", "v2-adaptive"), required=True)
    parser.add_argument("--meeting-file", "--transcript", dest="meeting_file", required=True, help="one .txt, .vtt, or .srt file")
    parser.add_argument("--meeting-id", default="demo-meeting-001")
    parser.add_argument("--meeting-title", default="Demo core smoke")
    parser.add_argument("--meeting-date", default="2026-09-21")
    parser.add_argument("--api-key-file")
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--report-output", "--report", dest="report_output", required=True)
    parser.add_argument("--feedback-file")
    parser.add_argument("--allow-feedback", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--poll-timeout", type=float, default=120.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_smoke(args)
        _write_json(Path(args.report_output), report)
        print(f"Smoke succeeded; report written to {args.report_output}")
        return 0
    except SmokeError as exc:
        # Keep failures concise and secret-free.  A failed request never gets
        # retried, and no server response body is printed.
        message = _redact(str(exc))
        try:
            _write_json(Path(args.report_output), {"schema_version": "unified-core-smoke-report-v1", "ok": False, "error": message})
        except SmokeError:
            pass
        print(f"error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
