"""Background jobs for long-running meeting processing.

The default store remains in-memory for backwards compatibility.  A local
SQLite file can be enabled for the single-process Uvicorn deployment with
``MEETING_JOB_SQLITE_PATH``; this stores the status envelope only and never
stores the submitted transcript or callable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Callable
from uuid import uuid4


LOGGER = logging.getLogger(__name__)
MEETING_JOB_SQLITE_PATH_ENV = "MEETING_JOB_SQLITE_PATH"
INTERRUPTED_BY_RESTART_ERROR = "Job interrupted by process restart"


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
    """Run blocking pipeline work in background threads and retain job state.

    Without a path this is the original in-memory store. With a path it uses a
    small SQLite status database suitable for one local Uvicorn process.
    """

    def __init__(self, sqlite_path: str | os.PathLike[str] | None = None) -> None:
        self._jobs: dict[str, MeetingJob] = {}
        self._job_ids_by_key: dict[str, str] = {}
        self._lock = Lock()
        self._tasks: set[asyncio.Task] = set()
        configured_path = (
            os.getenv(MEETING_JOB_SQLITE_PATH_ENV, "")
            if sqlite_path is None
            else os.fspath(sqlite_path)
        )
        self._sqlite_path = (
            str(Path(configured_path).expanduser())
            if configured_path and configured_path != ":memory:"
            else configured_path or None
        )
        if self._sqlite_path:
            self._initialize_sqlite()
            self._load_sqlite_jobs()

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
            if self._sqlite_path:
                existing = self._read_sqlite_job_by_key(idempotency_key)
                if existing is not None:
                    self._jobs[existing.job_id] = existing
                    self._job_ids_by_key[idempotency_key] = existing.job_id
                    return existing, False

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
            self._persist_job_locked(job)

        task = asyncio.create_task(self._run(job.job_id, work))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job, True

    def get(self, job_id: str) -> MeetingJob | None:
        with self._lock:
            if self._sqlite_path:
                persisted = self._read_sqlite_job(job_id)
                if persisted is None:
                    return None
                self._jobs[job_id] = persisted
                self._job_ids_by_key[persisted.idempotency_key] = job_id
                return persisted
            return self._jobs.get(job_id)

    async def _run(self, job_id: str, work: Callable[[], dict]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _now()
            job.segments_total = 1
            self._persist_job_locked(job)
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
                self._persist_job_locked(job)
            return
        except Exception as exc:  # Keep a failed job observable to the polling flow.
            LOGGER.exception("Meeting job %s failed", job_id)
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.completed_at = _now()
                job.error = str(exc)[:500]
                job.segments_failed = 1
                self._persist_job_locked(job)
            return

        with self._lock:
            job = self._jobs[job_id]
            job.status = "succeeded"
            job.completed_at = _now()
            job.result = result
            job.segments_succeeded = 1
            self._persist_job_locked(job)

    def _connect_sqlite(self) -> sqlite3.Connection:
        assert self._sqlite_path is not None
        connection = sqlite3.connect(self._sqlite_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_sqlite(self) -> None:
        assert self._sqlite_path is not None
        if self._sqlite_path != ":memory:":
            Path(self._sqlite_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect_sqlite()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS meeting_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    result_json TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    pipeline_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    model TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    segments_total INTEGER NOT NULL DEFAULT 0,
                    segments_succeeded INTEGER NOT NULL DEFAULT 0,
                    segments_failed INTEGER NOT NULL DEFAULT 0,
                    segment_results_json TEXT,
                    timeout_seconds REAL
                )
                """
            )
            connection.commit()
            # A process restart cannot safely resume a Python callable.  Mark
            # these terminal before loading them, so they are never rerun.
            connection.execute(
                """
                UPDATE meeting_jobs
                   SET status = 'failed',
                       completed_at = ?,
                       error = ?,
                       segments_failed = CASE WHEN segments_failed < 1 THEN 1 ELSE segments_failed END
                 WHERE status IN ('queued', 'running')
                """,
                (_now(), INTERRUPTED_BY_RESTART_ERROR),
            )
            connection.commit()

    def _load_sqlite_jobs(self) -> None:
        assert self._sqlite_path is not None
        with closing(self._connect_sqlite()) as connection:
            rows = connection.execute("SELECT * FROM meeting_jobs").fetchall()
        for row in rows:
            job = _job_from_row(row)
            self._jobs[job.job_id] = job
            self._job_ids_by_key[job.idempotency_key] = job.job_id

    def _read_sqlite_job(self, job_id: str) -> MeetingJob | None:
        assert self._sqlite_path is not None
        with closing(self._connect_sqlite()) as connection:
            row = connection.execute(
                "SELECT * FROM meeting_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def _read_sqlite_job_by_key(self, idempotency_key: str) -> MeetingJob | None:
        assert self._sqlite_path is not None
        with closing(self._connect_sqlite()) as connection:
            row = connection.execute(
                "SELECT * FROM meeting_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def _persist_job_locked(self, job: MeetingJob) -> None:
        if not self._sqlite_path:
            return
        result_json = json.dumps(job.result, ensure_ascii=False, separators=(",", ":")) if job.result is not None else None
        segment_results_json = (
            json.dumps(job.segment_results, ensure_ascii=False, separators=(",", ":"))
            if job.segment_results is not None
            else None
        )
        with closing(self._connect_sqlite()) as connection:
            connection.execute(
                """
                INSERT INTO meeting_jobs (
                    job_id, idempotency_key, status, created_at, started_at,
                    completed_at, result_json, error, pipeline_version,
                    prompt_version, model, content_hash, segments_total,
                    segments_succeeded, segments_failed, segment_results_json,
                    timeout_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    started_at = excluded.started_at,
                    completed_at = excluded.completed_at,
                    result_json = excluded.result_json,
                    error = excluded.error,
                    segments_total = excluded.segments_total,
                    segments_succeeded = excluded.segments_succeeded,
                    segments_failed = excluded.segments_failed,
                    segment_results_json = excluded.segment_results_json
                """,
                (
                    job.job_id,
                    job.idempotency_key,
                    job.status,
                    job.created_at,
                    job.started_at,
                    job.completed_at,
                    result_json,
                    job.error,
                    job.pipeline_version,
                    job.prompt_version,
                    job.model,
                    job.content_hash,
                    job.segments_total,
                    job.segments_succeeded,
                    job.segments_failed,
                    segment_results_json,
                    job.timeout_seconds,
                ),
            )
            connection.commit()


def _job_from_row(row: sqlite3.Row) -> MeetingJob:
    """Decode one SQLite row while keeping malformed optional JSON harmless."""
    result = _decode_json_object(row["result_json"])
    segment_results = _decode_json_object(row["segment_results_json"])
    return MeetingJob(
        job_id=row["job_id"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        created_at=row["created_at"],
        started_at=row["started_at"] or "",
        completed_at=row["completed_at"] or "",
        result=result,
        error=row["error"] or "",
        pipeline_version=row["pipeline_version"] or "v1",
        prompt_version=row["prompt_version"] or "v1",
        model=row["model"] or "",
        content_hash=row["content_hash"] or "",
        segments_total=int(row["segments_total"] or 0),
        segments_succeeded=int(row["segments_succeeded"] or 0),
        segments_failed=int(row["segments_failed"] or 0),
        segment_results=segment_results,
        timeout_seconds=row["timeout_seconds"],
    )


def _decode_json_object(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
