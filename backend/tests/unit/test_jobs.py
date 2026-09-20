import asyncio
from pathlib import Path

from backend.app.jobs import MeetingJobStore


def test_job_store_runs_work_and_deduplicates_retries() -> None:
    async def scenario() -> None:
        store = MeetingJobStore()
        first, created = store.submit("same-upload", lambda: {"ok": True})
        duplicate, created_again = store.submit("same-upload", lambda: {"ok": False})

        assert created is True
        assert created_again is False
        assert duplicate.job_id == first.job_id

        for _ in range(50):
            result = store.get(first.job_id)
            assert result is not None
            if result.status == "succeeded":
                break
            await asyncio.sleep(0.01)

        assert result.status == "succeeded"
        assert result.result == {"ok": True}

    asyncio.run(scenario())


def test_job_store_marks_timed_out_work_failed() -> None:
    async def scenario() -> None:
        store = MeetingJobStore()

        def slow_work() -> dict:
            import time
            time.sleep(0.05)
            return {"ok": True}

        job, _ = store.submit("slow", slow_work, timeout_seconds=0.01)
        for _ in range(50):
            result = store.get(job.job_id)
            assert result is not None
            if result.status == "failed":
                break
            await asyncio.sleep(0.005)
        assert result.status == "failed"
        assert "exceeded" in result.error

    asyncio.run(scenario())


def test_sqlite_job_store_keeps_completed_result_and_idempotency_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "jobs.sqlite3"
        first_store = MeetingJobStore(database)
        first, created = first_store.submit(
            "same-upload", lambda: {"ok": True, "nested": {"value": 3}},
            pipeline_version="v2",
        )
        assert created is True
        for _ in range(50):
            result = first_store.get(first.job_id)
            assert result is not None
            if result.status == "succeeded":
                break
            await asyncio.sleep(0.01)
        assert result.status == "succeeded"

        restarted = MeetingJobStore(database)
        restored = restarted.get(first.job_id)
        assert restored is not None
        assert restored.status == "succeeded"
        assert restored.result == {"ok": True, "nested": {"value": 3}}
        duplicate, duplicate_created = restarted.submit("same-upload", lambda: {"ok": False})
        assert duplicate_created is False
        assert duplicate.job_id == first.job_id

    asyncio.run(scenario())


def test_sqlite_restart_marks_queued_job_failed_without_rerunning(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "jobs.sqlite3"
        original = MeetingJobStore(database)
        submitted, created = original.submit("queued-upload", lambda: {"must_not": "run"})
        assert created is True

        restarted = MeetingJobStore(database)
        restored = restarted.get(submitted.job_id)
        assert restored is not None
        assert restored.status == "failed"
        assert restored.error == "Job interrupted by process restart"
        assert restored.segments_failed == 1
        duplicate, duplicate_created = restarted.submit("queued-upload", lambda: {"rerun": True})
        assert duplicate_created is False
        assert duplicate.job_id == submitted.job_id
        assert duplicate.status == "failed"

    asyncio.run(scenario())


def test_sqlite_job_store_persists_failure_and_counters(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "failed-jobs.sqlite3"
        store = MeetingJobStore(database)

        def fail() -> dict:
            raise ValueError("provider unavailable")

        submitted, _ = store.submit("failed-upload", fail)
        for _ in range(50):
            result = store.get(submitted.job_id)
            assert result is not None
            if result.status == "failed":
                break
            await asyncio.sleep(0.01)
        assert result.status == "failed"
        assert result.error == "provider unavailable"
        assert result.segments_failed == 1

        restored = MeetingJobStore(database).get(submitted.job_id)
        assert restored is not None
        assert restored.status == "failed"
        assert restored.error == "provider unavailable"
        assert restored.segments_failed == 1

    asyncio.run(scenario())
