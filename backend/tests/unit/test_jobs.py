import asyncio

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
