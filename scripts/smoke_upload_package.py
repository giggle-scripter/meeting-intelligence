"""Submit one self-contained upload package to a local backend and poll it."""

from __future__ import annotations

import argparse
from base64 import b64encode
import json
from pathlib import Path
import sys
import time
from urllib.parse import urljoin

import httpx


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--api-key", default="mi-demo-secret")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--expect-task-count", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    content = args.file.read_bytes()
    headers = {
        "Content-Type": "application/octet-stream",
        "X-File-Name-Base64": b64encode(args.file.name.encode("utf-8")).decode(),
    }
    if args.api_key:
        headers["X-API-Key"] = args.api_key

    base_url = args.base_url.rstrip("/")
    submit_url = f"{base_url}/api/v1/meetings/jobs/process-file"
    deadline = time.monotonic() + args.timeout
    with httpx.Client(timeout=30.0) as client:
        submit_response = client.post(submit_url, content=content, headers=headers)
        submit_response.raise_for_status()
        submit = submit_response.json()
        status_url = urljoin(f"{base_url}/", submit["status_url"].lstrip("/"))
        while True:
            response = client.get(status_url, headers=headers)
            response.raise_for_status()
            job = response.json()
            if job["status"] in {"succeeded", "failed"}:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Job {job['job_id']} did not finish in time")
            time.sleep(args.poll_interval)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(job, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    result = job.get("result") or {}
    diagnostics = result.get("diagnostics") or {}
    summary = {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "meeting_title": result.get("meeting_title"),
        "task_count": len(result.get("tasks") or []),
        "meeting_date_source": diagnostics.get("meeting_date_source"),
        "effective_meeting_date": diagnostics.get("effective_meeting_date"),
        "explicit_task_start_date_count": diagnostics.get(
            "explicit_task_start_date_count"
        ),
        "ai_provider_enabled": diagnostics.get("ai_provider_enabled"),
        "ai_provider_call_count": diagnostics.get("ai_provider_call_count"),
        "unresolved_window_count": diagnostics.get("unresolved_window_count"),
        "error": job.get("error"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if job["status"] != "succeeded":
        raise SystemExit(1)
    if (
        args.expect_task_count is not None
        and summary["task_count"] != args.expect_task_count
    ):
        raise SystemExit(
            f"Expected {args.expect_task_count} tasks, got {summary['task_count']}"
        )


if __name__ == "__main__":
    main()
