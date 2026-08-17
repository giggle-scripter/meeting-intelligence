"""In-memory background jobs for long-running meeting processing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from threading import Lock
from typing import Callable
from uuid import uuid4


LOGGER = logging.getLogger(__name__)


@dataclass
class MeetingJob:
    job_id: str
    idempotency_key: str
    status: str
    created_at: str
    started_at: str = ""
    completed_at: str = ""
    result: dict | None = None
    error: str = ""
    pipeline_version: str = "v1"
    prompt_version: str = "v1"
    model: str = ""
    content_hash: str = ""
    segments_total: int = 0
    segments_succeeded: int = 0
    segments_failed: int = 0
    segment_results: dict[str, dict] | None = None
    timeout_seconds: float | None = None

    def as_response(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
            "pipeline_version": self.pipeline_version,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "content_hash": self.content_hash,
            "segments_total": self.segments_total,
            "segments_succeeded": self.segments_succeeded,
            "segments_failed": self.segments_failed,
            "timeout_seconds": self.timeout_seconds,
        }


class MeetingJobStore:
    """Run blocking pipeline work in background threads and retain short-lived state.

    The store is deliberately in-memory for the local/tunnel PoC. A production
    deployment should replace it with durable queue and status storage.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, MeetingJob] = {}
        self._job_ids_by_key: dict[str, str] = {}
        self._lock = Lock()
        self._tasks: set[asyncio.Task] = set()

    def submit(
        self,
        idempotency_key: str,
        work: Callable[[], dict],
        *,
        pipeline_version: str = "v1",
        prompt_version: str = "v1",
        model: str = "",
        content_hash: str = "",
        timeout_seconds: float | None = None,
    ) -> tuple[MeetingJob, bool]:
        with self._lock:
            existing_id = self._job_ids_by_key.get(idempotency_key)
            if existing_id:
                return self._jobs[existing_id], False

            job = MeetingJob(
                job_id=f"job-{uuid4().hex}",
                idempotency_key=idempotency_key,
                status="queued",
                created_at=_now(),
                pipeline_version=pipeline_version,
                prompt_version=prompt_version,
                model=model,
                content_hash=content_hash,
                timeout_seconds=timeout_seconds,
            )
            self._jobs[job.job_id] = job
            self._job_ids_by_key[idempotency_key] = job.job_id

        task = asyncio.create_task(self._run(job.job_id, work))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job, True

    def get(self, job_id: str) -> MeetingJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    async def _run(self, job_id: str, work: Callable[[], dict]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _now()
            job.segments_total = 1
        try:
            future = asyncio.to_thread(work)
            result = (
                await asyncio.wait_for(future, timeout=job.timeout_seconds)
                if job.timeout_seconds and job.timeout_seconds > 0
                else await future
            )
        except TimeoutError:
            LOGGER.error("Meeting job %s exceeded %.1f seconds", job_id, job.timeout_seconds)
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.completed_at = _now()
                job.error = f"Job exceeded {job.timeout_seconds:.1f} seconds"
                job.segments_failed = 1
            return
        except Exception as exc:  # Keep a failed job observable to the polling flow.
            LOGGER.exception("Meeting job %s failed", job_id)
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.completed_at = _now()
                job.error = str(exc)[:500]
                job.segments_failed = 1
            return

        with self._lock:
            job = self._jobs[job_id]
            job.status = "succeeded"
            job.completed_at = _now()
            job.result = result
            job.segments_succeeded = 1


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
