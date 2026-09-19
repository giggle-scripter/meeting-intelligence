"""Small, safe operator client for an already-running V2.27 API.

This module only submits/polls HTTP requests.  It never starts Uvicorn, a
tunnel, a provider, or the feedback trainer.  Transcript bytes are sent as
the request body and are deliberately never included in output files/logs.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urljoin, urlparse
import base64

import httpx


TASK_FIELDS = (
    "task_name",
    "assignee",
    "start_date",
    "due_date",
    "due_date_text",
    "evidence",
    "status",
)
TASK_FIELD_SET = frozenset(TASK_FIELDS)
DEFAULT_API_KEY_ENV = "POWER_AUTOMATE_API_KEY"
DEFAULT_REVIEW_DIR = Path("evaluation/runtime/local-v227-operator/reviews")
DEFAULT_RECEIPT_DIR = Path("evaluation/runtime/local-v227-operator/receipts")
MAX_TRANSCRIPT_BYTES = 2_000_000


class OperatorError(RuntimeError):
    """A user-actionable error safe to show without leaking request data."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove known secrets and common key-shaped values from safe diagnostics."""
    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = re.sub(r"(?i)(x-api-key|api[_-]?key|authorization)(\s*[:=]\s*)([^,\s;]+)", r"\1\2[REDACTED]", result)
    return result[:500]


def _validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise OperatorError("--base-url must be an explicit localhost HTTP URL or HTTPS URL")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise OperatorError("plain HTTP is allowed only for localhost; use HTTPS for remote APIs")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _read_api_key(api_key_file: Path | None, api_key_env: str) -> str:
    if api_key_file is not None:
        try:
            value = api_key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise OperatorError(f"unable to read API key file: {api_key_file}") from exc
    else:
        value = os.environ.get(api_key_env, "").strip()
    if not value:
        source = str(api_key_file) if api_key_file is not None else api_key_env
        raise OperatorError(f"API key is empty or unavailable ({source})")
    return value


def _headers(api_key: str, *, file_name: str | None = None, meeting_id: str | None = None,
             meeting_title: str | None = None, meeting_date: str | None = None) -> dict[str, str]:
    headers = {"X-API-Key": api_key, "Content-Type": "text/plain; charset=utf-8"}
    if file_name:
        headers["X-File-Name-Base64"] = base64.b64encode(file_name.encode("utf-8")).decode("ascii")
    if meeting_id:
        headers["X-Meeting-Id"] = meeting_id
    if meeting_title:
        headers["X-Meeting-Title-Base64"] = base64.b64encode(meeting_title.encode("utf-8")).decode("ascii")
    if meeting_date:
        headers["X-Meeting-Date"] = meeting_date
    return headers


class ApiClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 15.0,
                 *, http_client: httpx.Client | None = None) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise OperatorError("--timeout must be greater than 0 and at most 300 seconds")
        self.base_url = _validate_base_url(base_url)
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._owned = http_client is None
        self.client = http_client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owned:
            self.client.close()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, url, timeout=self.timeout_seconds, **kwargs)
        except httpx.TimeoutException as exc:
            raise OperatorError(f"HTTP {method} timed out") from exc
        except httpx.HTTPError as exc:
            raise OperatorError(f"HTTP {method} failed: {_redact(str(exc), (self.api_key,))}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            # Do not include the server body: it is outside the CLI's control.
            raise OperatorError(f"HTTP {method} returned status {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise OperatorError(f"HTTP {method} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise OperatorError(f"HTTP {method} returned a JSON object was expected")
        return payload

    def submit(self, content: bytes, *, file_name: str, meeting_id: str | None,
               meeting_title: str | None, meeting_date: str | None) -> dict[str, Any]:
        return self._request(
            "POST", self._url("/api/v1/meetings/jobs/process-file"),
            headers=_headers(self.api_key, file_name=file_name, meeting_id=meeting_id,
                             meeting_title=meeting_title, meeting_date=meeting_date),
            content=content,
        )

    def status(self, status_url: str) -> dict[str, Any]:
        if not isinstance(status_url, str) or not status_url:
            raise OperatorError("submit response did not contain status_url")
        target = urljoin(self.base_url.rstrip("/") + "/", status_url)
        origin = urlparse(self.base_url)
        parsed = urlparse(target)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
            raise OperatorError("status_url points outside the configured API base URL")
        return self._request("GET", target, headers={"X-API-Key": self.api_key})

    def feedback(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", self._url(f"/api/v1/meetings/jobs/{job_id}/feedback"),
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            json=payload,
        )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorError(f"unable to read JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise OperatorError(f"JSON file must contain an object: {path}")
    return value


def _atomic_write(path: Path, value: dict[str, Any], *, allow_existing_same: bool = False) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (_canonical(value) + "\n").encode("utf-8")
    if path.exists():
        if allow_existing_same:
            try:
                if path.read_bytes() == encoded:
                    return False
            except OSError:
                pass
        raise OperatorError(f"output already exists: {path}")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # replace is atomic; refusing an already-existing path above makes a
        # second invocation deterministic and avoids overwriting evidence.
        os.replace(temporary, path)
        temporary = None
        return True
    except OSError as exc:
        raise OperatorError(f"unable to write private output: {path}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _validate_tasks(tasks: Any, *, field_name: str) -> list[dict[str, str]]:
    if not isinstance(tasks, list) or len(tasks) > 1000:
        raise OperatorError(f"{field_name} must be an array of at most 1000 tasks")
    clean: list[dict[str, str]] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or frozenset(task) != TASK_FIELD_SET:
            raise OperatorError(f"{field_name}[{index}] must contain exactly the seven task fields")
        if task.get("status") != "Proposed":
            raise OperatorError(f"{field_name}[{index}].status must be Proposed")
        if any(not isinstance(task[field], str) for field in TASK_FIELDS):
            raise OperatorError(f"{field_name}[{index}] task fields must be strings")
        clean.append({field: task[field] for field in TASK_FIELDS})
    return clean


def _parse_review_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperatorError("approval_metadata.reviewed_at is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperatorError("approval_metadata.reviewed_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OperatorError("approval_metadata.reviewed_at must include a timezone")
    return value


def _review_payload(review: dict[str, Any]) -> dict[str, Any]:
    job_id = review.get("job_id")
    content_hash = review.get("content_hash")
    if not isinstance(job_id, str) or not job_id or not isinstance(content_hash, str) or not content_hash:
        raise OperatorError("review must contain job_id and content_hash")
    metadata = review.get("approval_metadata")
    if not isinstance(metadata, dict) or metadata.get("approval") is not True:
        raise OperatorError("approval_metadata.approval must be explicitly true before feedback")
    reviewer = metadata.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 256:
        raise OperatorError("approval_metadata.reviewer is required")
    reviewed_at = _parse_review_timestamp(metadata.get("reviewed_at"))
    tasks = _validate_tasks(review.get("corrected_final_tasks"), field_name="corrected_final_tasks")
    return {
        "job_id": job_id,
        "content_hash": content_hash,
        "corrected_final_tasks": tasks,
        "approval_metadata": {"reviewer": reviewer.strip(), "reviewed_at": reviewed_at, "approval": True},
    }


def submit_review(args: argparse.Namespace, *, http_client: httpx.Client | None = None) -> Path:
    if args.poll_timeout <= 0 or args.poll_interval <= 0:
        raise OperatorError("poll timeout and interval must be greater than zero")
    transcript_path = Path(args.transcript)
    try:
        content = transcript_path.read_bytes()
    except OSError as exc:
        raise OperatorError(f"unable to read transcript file: {transcript_path}") from exc
    if not content or len(content) > MAX_TRANSCRIPT_BYTES:
        raise OperatorError("transcript must be non-empty and at most 2,000,000 bytes")
    key = _read_api_key(Path(args.api_key_file) if args.api_key_file else None, args.api_key_env)
    client = ApiClient(args.base_url, key, args.timeout, http_client=http_client)
    try:
        submitted = client.submit(content, file_name=transcript_path.name, meeting_id=args.meeting_id,
                                  meeting_title=args.meeting_title, meeting_date=args.meeting_date)
        job_id = submitted.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise OperatorError("submit response did not contain job_id")
        status_url = submitted.get("status_url")
        deadline = time.monotonic() + args.poll_timeout
        latest: dict[str, Any] = {}
        while True:
            latest = client.status(status_url)
            state = latest.get("status")
            if state == "succeeded":
                break
            if state == "failed":
                raise OperatorError(f"job failed (job_id={job_id})")
            if state not in {"queued", "running"}:
                raise OperatorError(f"job returned invalid status (job_id={job_id})")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OperatorError(f"poll timeout (job_id={job_id})")
            time.sleep(min(args.poll_interval, remaining))
        result = latest.get("result")
        if not isinstance(result, dict):
            raise OperatorError("succeeded job did not contain result")
        content_hash = latest.get("content_hash") or result.get("content_hash")
        if not isinstance(content_hash, str) or not content_hash:
            raise OperatorError("succeeded job did not contain content_hash")
        tasks = _validate_tasks(result.get("tasks", []), field_name="proposed_tasks")
        review = {
            "schema_version": "local-v227-review-v1",
            "job_id": job_id,
            "content_hash": content_hash,
            "meeting": {
                "meeting_id": args.meeting_id or str(result.get("meeting_id", "")),
                "meeting_title": args.meeting_title or str(result.get("meeting_title", "")),
                "meeting_date": args.meeting_date or str(result.get("meeting_date", "")),
                "file_name": transcript_path.name,
            },
            "proposed_tasks": tasks,
            "corrected_final_tasks": copy.deepcopy(tasks),
            "approval_metadata": {"reviewer": "", "reviewed_at": "", "approval": False},
        }
        output = Path(args.review_output) if args.review_output else DEFAULT_REVIEW_DIR / f"{job_id}.json"
        _atomic_write(output, review)
        return output
    finally:
        client.close()


def send_feedback(args: argparse.Namespace, *, http_client: httpx.Client | None = None) -> Path:
    review = _load_json(Path(args.review))
    payload = _review_payload(review)
    request_sha256 = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    receipt = Path(args.receipt) if args.receipt else DEFAULT_RECEIPT_DIR / f"{payload['job_id']}.json"
    if receipt.exists():
        existing = _load_json(receipt)
        if existing.get("request_sha256") == request_sha256 and existing.get("job_id") == payload["job_id"]:
            return receipt
        raise OperatorError(f"conflicting receipt already exists: {receipt}")
    key = _read_api_key(Path(args.api_key_file) if args.api_key_file else None, args.api_key_env)
    client = ApiClient(args.base_url, key, args.timeout, http_client=http_client)
    try:
        response = client.feedback(payload["job_id"], payload)
        if response.get("job_id") != payload["job_id"]:
            raise OperatorError("feedback response did not contain matching job_id")
        if not isinstance(response.get("created"), bool):
            raise OperatorError("feedback response did not contain a created boolean")
        if not isinstance(response.get("feedback_hash"), str) or not response["feedback_hash"].strip():
            raise OperatorError("feedback response did not contain a nonempty feedback_hash")
        receipt_body = {
            "schema_version": "local-v227-feedback-receipt-v1",
            "job_id": payload["job_id"],
            "content_hash": payload["content_hash"],
            "request_sha256": request_sha256,
            "response": response,
        }
        _atomic_write(receipt, receipt_body)
        return receipt
    finally:
        client.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local operator client for a running V2.27 API")
    sub = parser.add_subparsers(dest="command", required=True)
    submit = sub.add_parser("submit-review", help="submit a transcript, poll, and write a private review")
    submit.add_argument("--base-url", required=True)
    submit.add_argument("--transcript", required=True)
    submit.add_argument("--review-output")
    submit.add_argument("--meeting-id")
    submit.add_argument("--meeting-title")
    submit.add_argument("--meeting-date")
    submit.add_argument("--api-key-file")
    submit.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    submit.add_argument("--timeout", type=float, default=15.0)
    submit.add_argument("--poll-timeout", type=float, default=120.0)
    submit.add_argument("--poll-interval", type=float, default=1.0)
    feedback = sub.add_parser("send-feedback", help="send approved corrected tasks and save a receipt")
    feedback.add_argument("--base-url", required=True)
    feedback.add_argument("--review", required=True)
    feedback.add_argument("--receipt")
    feedback.add_argument("--api-key-file")
    feedback.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    feedback.add_argument("--timeout", type=float, default=15.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if getattr(args, "poll_timeout", 0) <= 0 or getattr(args, "poll_interval", 0) <= 0:
        print("error: poll timeout and interval must be greater than zero", file=sys.stderr)
        return 2
    try:
        path = submit_review(args) if args.command == "submit-review" else send_feedback(args)
        print(path)
        return 0
    except OperatorError as exc:
        print(f"error: {_redact(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
