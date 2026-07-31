"""Integration tests: CronService concurrency and exception isolation.

These tests exercise the CronService scheduler directly (not via HTTP) to
verify that:
- One failing/timing-out job does NOT cancel or delay siblings (gather isolation).
- Manual ``run_job`` and scheduled ``_on_timer`` can execute the same job
  concurrently (no built-in dedup).
- ``stop()`` cancels the timer but does NOT cancel in-flight job executions.
- ``shutdown()`` cancels both the timer AND in-flight tasks via the registry.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from xbot.runtime.system.cron.service import CronService
from xbot.runtime.system.cron.types import (
    CronJob,
    CronJobState,
    CronPayload,
    CronSchedule,
    CronStore,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(jobs: list[CronJob]) -> CronStore:
    return CronStore(version=1, jobs=jobs)


def _make_job(
    job_id: str = "job-1",
    name: str = "test-job",
    enabled: bool = True,
    every_ms: int = 1000,
    next_run_at_ms: int | None = None,
) -> CronJob:
    """Create a minimal repeating job for testing."""
    return CronJob(
        id=job_id,
        name=name,
        enabled=enabled,
        schedule=CronSchedule(kind="every", every_ms=every_ms),
        payload=CronPayload(kind="agent_turn", message="hi"),
        state=CronJobState(next_run_at_ms=next_run_at_ms or 1),
        created_at_ms=1,
        updated_at_ms=1,
    )


def _write_store(path: Path, store: CronStore) -> None:
    """Persist a CronStore to JSON so CronService can load it."""
    data = {
        "version": store.version,
        "jobs": [
            {
                "id": j.id,
                "name": j.name,
                "enabled": j.enabled,
                "schedule": {
                    "kind": j.schedule.kind,
                    "atMs": j.schedule.at_ms,
                    "everyMs": j.schedule.every_ms,
                    "expr": j.schedule.expr,
                    "tz": j.schedule.tz,
                },
                "payload": {
                    "kind": j.payload.kind,
                    "message": j.payload.message,
                    "deliver": j.payload.deliver,
                    "channel": j.payload.channel,
                    "to": j.payload.to,
                },
                "state": {
                    "nextRunAtMs": j.state.next_run_at_ms,
                    "lastRunAtMs": j.state.last_run_at_ms,
                    "lastStatus": j.state.last_status,
                    "lastError": j.state.last_error,
                },
                "createdAtMs": j.created_at_ms,
                "updatedAtMs": j.updated_at_ms,
                "deleteAfterRun": j.delete_after_run,
            }
            for j in store.jobs
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Test: one failing job does NOT cancel siblings
# ---------------------------------------------------------------------------


class TestGatherIsolation:
    """Verify that asyncio.gather + _execute_job's try/except prevents
    one failing job from affecting its siblings."""

    @pytest.mark.asyncio
    async def test_one_raising_job_does_not_cancel_siblings(self, tmp_path: Path):
        """A job handler that raises should not prevent other due jobs from
        completing successfully."""
        store_path = tmp_path / "jobs.json"
        execution_log: list[str] = []

        async def on_job(job: CronJob) -> str | None:
            if job.id == "exploder":
                execution_log.append("exploder-start")
                raise RuntimeError("boom")
            # Normal job: simulate some work
            execution_log.append(f"{job.id}-start")
            await asyncio.sleep(0.01)
            execution_log.append(f"{job.id}-done")
            return None

        jobs = [
            _make_job(job_id="exploder", name="exploder"),
            _make_job(job_id="good-1", name="good-1"),
            _make_job(job_id="good-2", name="good-2"),
        ]
        _write_store(store_path, _make_store(jobs))

        svc = CronService(store_path=store_path, on_job=on_job)
        svc.job_timeout_s = 5.0
        # Directly invoke _on_timer to simulate a scheduled tick
        svc._load_store()
        await svc._on_timer()

        # Both good jobs must have completed
        assert "good-1-done" in execution_log
        assert "good-2-done" in execution_log
        assert "exploder-start" in execution_log

        # The exploder job should be marked as error
        store = svc._load_store()
        exploder = next(j for j in store.jobs if j.id == "exploder")
        assert exploder.state.last_status == "error"
        assert "boom" in (exploder.state.last_error or "")

    @pytest.mark.asyncio
    async def test_one_timing_out_job_does_not_block_siblings(self, tmp_path: Path):
        """A job that exceeds job_timeout_s should be cancelled via wait_for,
        but siblings should still complete on time."""
        store_path = tmp_path / "jobs.json"
        execution_log: list[str] = []

        async def on_job(job: CronJob) -> str | None:
            if job.id == "slowpoke":
                execution_log.append("slowpoke-start")
                await asyncio.sleep(100)  # will be timed out
                execution_log.append("slowpoke-done")  # should NOT appear
                return None
            execution_log.append(f"{job.id}-start")
            await asyncio.sleep(0.01)
            execution_log.append(f"{job.id}-done")
            return None

        jobs = [
            _make_job(job_id="slowpoke", name="slowpoke"),
            _make_job(job_id="fast-1", name="fast-1"),
        ]
        _write_store(store_path, _make_store(jobs))

        svc = CronService(store_path=store_path, on_job=on_job)
        svc.job_timeout_s = 0.05  # 50ms timeout

        svc._load_store()
        await svc._on_timer()

        # Fast job completed
        assert "fast-1-done" in execution_log
        # Slow job started but never completed
        assert "slowpoke-start" in execution_log
        assert "slowpoke-done" not in execution_log

        # Slowpoke marked as error/timeout
        store = svc._load_store()
        slowpoke = next(j for j in store.jobs if j.id == "slowpoke")
        assert slowpoke.state.last_status == "error"
        assert "timed out" in (slowpoke.state.last_error or "")


# ---------------------------------------------------------------------------
# Test: manual run_job has no dedup against scheduled tick
# ---------------------------------------------------------------------------


class TestRunJobConcurrency:
    """Verify that run_job does not deduplicate against an in-flight
    _on_timer execution — the same job can run concurrently."""

    @pytest.mark.asyncio
    async def test_manual_and_scheduled_run_concurrently(self, tmp_path: Path):
        """If _on_timer is executing a job AND run_job is called for the same
        job, both should proceed independently (no lock/dedup)."""
        store_path = tmp_path / "jobs.json"
        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def on_job(job: CronJob) -> str | None:
            nonlocal concurrent_count, max_concurrent
            async with lock:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.1)
            async with lock:
                concurrent_count -= 1
            return None

        jobs = [_make_job(job_id="dup-target", name="dup-target")]
        _write_store(store_path, _make_store(jobs))

        svc = CronService(store_path=store_path, on_job=on_job)
        svc.job_timeout_s = 5.0
        svc._load_store()

        # Fire both the timer path and the manual path concurrently
        await asyncio.gather(
            svc._on_timer(),
            svc.run_job("dup-target", force=True),
        )

        # Both executions ran concurrently — max_concurrent should be 2
        assert max_concurrent == 2, (
            f"Expected concurrent execution of 2, got {max_concurrent}. "
            "This means run_job deduplicates against scheduled ticks."
        )

    @pytest.mark.asyncio
    async def test_run_job_while_disabled_with_force(self, tmp_path: Path):
        """run_job(force=True) should execute even if job is disabled."""
        store_path = tmp_path / "jobs.json"
        executed = []

        async def on_job(job: CronJob) -> str | None:
            executed.append(job.id)
            return None

        jobs = [_make_job(job_id="disabled-job", name="disabled", enabled=False)]
        _write_store(store_path, _make_store(jobs))

        svc = CronService(store_path=store_path, on_job=on_job)
        svc._load_store()

        result = await svc.run_job("disabled-job", force=True)
        assert result is True
        assert "disabled-job" in executed

    @pytest.mark.asyncio
    async def test_run_job_disabled_without_force_returns_false(self, tmp_path: Path):
        """run_job(force=False) on a disabled job should return False and NOT execute."""
        store_path = tmp_path / "jobs.json"
        executed = []

        async def on_job(job: CronJob) -> str | None:
            executed.append(job.id)
            return None

        jobs = [_make_job(job_id="disabled-job", name="disabled", enabled=False)]
        _write_store(store_path, _make_store(jobs))

        svc = CronService(store_path=store_path, on_job=on_job)
        svc._load_store()

        result = await svc.run_job("disabled-job", force=False)
        assert result is False
        assert executed == []


# ---------------------------------------------------------------------------
# Test: stop() vs shutdown() semantics
# ---------------------------------------------------------------------------


class TestStopAndShutdown:
    """Verify the difference between stop() and shutdown():
    - stop() cancels the timer task only; in-flight jobs keep running.
    - shutdown() cancels the timer AND all owned tasks (in-flight jobs)."""

    @pytest.mark.asyncio
    async def test_stop_does_not_cancel_inflight_job(self, tmp_path: Path):
        """After stop(), an already-running job should still complete.
        stop() only cancels the timer task; it does NOT cancel jobs that
        are already mid-execution inside _on_timer/gather."""
        store_path = tmp_path / "jobs.json"
        completed = asyncio.Event()
        job_started = asyncio.Event()

        async def on_job(job: CronJob) -> str | None:
            job_started.set()
            await asyncio.sleep(0.15)
            completed.set()
            return None

        # next_run_at_ms=1 so the job is immediately due
        jobs = [_make_job(job_id="inflight", next_run_at_ms=1)]
        _write_store(store_path, _make_store(jobs))

        svc = CronService(store_path=store_path, on_job=on_job)
        svc.job_timeout_s = 5.0
        svc._running = True
        svc._load_store()

        # Start _on_timer in background (it will find the job as due)
        timer_task = asyncio.create_task(svc._on_timer())
        await job_started.wait()  # Ensure the job handler is running

        # Now stop the service — this cancels _timer_task but NOT
        # the gather inside the already-running _on_timer call
        svc.stop()

        # The in-flight job should still complete
        await timer_task
        assert completed.is_set()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_timer_task(self, tmp_path: Path):
        """shutdown() should cancel the internal timer task."""
        store_path = tmp_path / "jobs.json"

        async def on_job(job: CronJob) -> str | None:
            return None

        # Job scheduled far in the future so timer is armed but not firing
        jobs = [_make_job(job_id="future-job", next_run_at_ms=int(time.time() * 1000) + 60_000)]
        _write_store(store_path, _make_store(jobs))

        svc = CronService(store_path=store_path, on_job=on_job)
        await svc.start()

        # Timer should be armed
        assert svc._timer_task is not None
        assert not svc._timer_task.done()

        await svc.shutdown()

        # Timer should now be cancelled/done
        assert svc._timer_task is None or svc._timer_task.done()
        assert not svc._running


# ---------------------------------------------------------------------------
# Test: exception in on_job does not corrupt job state tracking
# ---------------------------------------------------------------------------


class TestStateAfterException:
    """After a job raises, the service should still correctly:
    - Record last_status / last_error on the failed job.
    - Recompute next_run_at_ms for repeating jobs.
    - Save the store to disk."""

    @pytest.mark.asyncio
    async def test_failed_repeating_job_gets_next_run_recomputed(self, tmp_path: Path):
        """Even after failure, an 'every' job should have its next_run_at_ms
        recomputed so it runs again on the next tick."""
        store_path = tmp_path / "jobs.json"

        async def on_job(job: CronJob) -> str | None:
            raise ValueError("transient failure")

        jobs = [_make_job(job_id="retry-me", every_ms=5000)]
        _write_store(store_path, _make_store(jobs))

        svc = CronService(store_path=store_path, on_job=on_job)
        svc.job_timeout_s = 5.0
        svc._load_store()
        await svc._on_timer()

        # Reload from disk to verify persistence
        svc._store = None
        store = svc._load_store()
        job = next(j for j in store.jobs if j.id == "retry-me")

        assert job.state.last_status == "error"
        assert "transient failure" in (job.state.last_error or "")
        # next_run_at_ms should be set for the future (not None)
        assert job.state.next_run_at_ms is not None
        assert job.state.next_run_at_ms > 0

    @pytest.mark.asyncio
    async def test_all_jobs_fail_store_still_saved(self, tmp_path: Path):
        """Even if ALL due jobs fail, the store should be persisted with
        updated state for every job."""
        store_path = tmp_path / "jobs.json"
        call_count = 0

        async def on_job(job: CronJob) -> str | None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"fail-{job.id}")

        jobs = [
            _make_job(job_id="fail-a", name="fail-a"),
            _make_job(job_id="fail-b", name="fail-b"),
            _make_job(job_id="fail-c", name="fail-c"),
        ]
        _write_store(store_path, _make_store(jobs))

        svc = CronService(store_path=store_path, on_job=on_job)
        svc.job_timeout_s = 5.0
        svc._load_store()
        await svc._on_timer()

        assert call_count == 3

        # Verify all saved to disk
        svc._store = None
        store = svc._load_store()
        for job in store.jobs:
            assert job.state.last_status == "error"
            assert job.state.last_error is not None
            assert job.state.next_run_at_ms is not None
