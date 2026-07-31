"""Soak test: CronService long-running execution cycles.

Focus areas:
1. Many `run_job` invocations of a recurring job → no accumulation in the
   ServiceTaskRegistry, next_run_at_ms keeps advancing.
2. Many one-shot "at" jobs with `delete_after_run=True` → jobs list
   returns to baseline after execution.
3. One-shot "at" jobs with `delete_after_run=False` → jobs list grows
   monotonically with disabled entries.  This is by-design behaviour
   worth documenting.
4. Exception path stress: on_job raises repeatedly → last_status="error"
   is set on every run, no exception escapes _execute_job, timer keeps
   ticking.
5. Timeout path: on_job hangs and gets timed out → next tick still fires.
6. Task registry cleanup: after N cycles, `_task_registry._tasks` has no
   dangling entries for owner="cron-service".
"""

from __future__ import annotations

import asyncio
import gc
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from xbot.runtime.system.cron.service import CronService, _now_ms
from xbot.runtime.system.cron.types import (
    CronJob,
    CronJobState,
    CronPayload,
    CronSchedule,
)

pytestmark = [pytest.mark.soak, pytest.mark.asyncio]


def _tmp_store() -> Path:
    return Path(tempfile.mkdtemp(prefix="cron_soak_")) / "jobs.json"


def _make_every(job_id: str, every_ms: int = 60_000) -> CronJob:
    now = _now_ms()
    return CronJob(
        id=job_id,
        name=job_id,
        enabled=True,
        schedule=CronSchedule(kind="every", every_ms=every_ms),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at_ms=now - 1000),  # already due
    )


def _make_at(job_id: str, at_ms: int | None = None, delete_after_run: bool = False) -> CronJob:
    if at_ms is None:
        at_ms = _now_ms() - 1000
    return CronJob(
        id=job_id,
        name=job_id,
        enabled=True,
        schedule=CronSchedule(kind="at", at_ms=at_ms),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at_ms=at_ms),
        delete_after_run=delete_after_run,
    )


class TestRecurringJobRunSoak:
    """Manually trigger _execute_job many times — verify next_run
    advances and no in-flight tasks leak."""

    async def test_1000_executions_no_leak(self):
        on_job = AsyncMock(return_value=None)
        svc = CronService(
            store_path=_tmp_store(),
            on_job=on_job,
            default_tz="UTC",
        )
        svc._load_store()
        job = _make_every("j-1", every_ms=1_000)
        svc._store.jobs.append(job)
        svc._save_store()

        for _ in range(1000):
            await svc._execute_job(job)

        assert on_job.await_count == 1000
        assert job.state.last_status == "ok"
        # Recurring job still enabled with a future next_run.
        assert job.enabled
        assert job.state.next_run_at_ms is not None
        assert job.state.next_run_at_ms > _now_ms() - 5_000
        # Store still has exactly one job.
        assert len(svc._store.jobs) == 1
        # No dangling background tasks.
        pending = svc._task_registry._tasks.get("cron-service", set())
        alive = [t for t in pending if not t.done()]
        assert alive == [], f"leaked cron-service tasks: {alive}"


class TestOneShotAutoDeleteSoak:
    """delete_after_run=True → jobs list returns to baseline."""

    async def test_500_oneshots_auto_delete(self):
        on_job = AsyncMock(return_value=None)
        svc = CronService(
            store_path=_tmp_store(),
            on_job=on_job,
            default_tz="UTC",
        )
        svc._load_store()

        for i in range(500):
            job = _make_at(f"once-{i}", delete_after_run=True)
            svc._store.jobs.append(job)
            await svc._execute_job(job)

        assert on_job.await_count == 500
        # All auto-delete one-shots gone.
        assert svc._store.jobs == []


class TestOneShotNoAutoDeleteAccumulates:
    """OBSERVATION: delete_after_run=False → disabled jobs accumulate.

    This is documented design behaviour — a soak test makes it visible.
    """

    async def test_500_oneshots_stay_as_disabled(self):
        on_job = AsyncMock(return_value=None)
        svc = CronService(
            store_path=_tmp_store(),
            on_job=on_job,
            default_tz="UTC",
        )
        svc._load_store()

        for i in range(500):
            job = _make_at(f"stick-{i}", delete_after_run=False)
            svc._store.jobs.append(job)
            await svc._execute_job(job)

        assert on_job.await_count == 500
        # All 500 sit disabled — by design.
        assert len(svc._store.jobs) == 500
        assert all(not j.enabled for j in svc._store.jobs)
        assert all(j.state.next_run_at_ms is None for j in svc._store.jobs)


class TestExceptionsAlwaysCaught:
    """on_job raises → _execute_job never propagates.  N cycles fine."""

    async def test_500_exceptions_do_not_break_service(self):
        async def flaky(job: CronJob) -> None:
            raise RuntimeError(f"boom on {job.id}")

        svc = CronService(
            store_path=_tmp_store(),
            on_job=flaky,
            default_tz="UTC",
        )
        svc._load_store()
        job = _make_every("flaky-1", every_ms=1_000)
        svc._store.jobs.append(job)
        svc._save_store()

        for _ in range(500):
            # Must not raise.
            await svc._execute_job(job)

        assert job.state.last_status == "error"
        assert "boom" in (job.state.last_error or "")
        # Recurring: next run keeps getting rescheduled despite errors.
        assert job.state.next_run_at_ms is not None
        assert job.state.next_run_at_ms > _now_ms() - 5_000


class TestTimeoutPathSoak:
    """N slow-job timeouts must never leak in-flight tasks."""

    async def test_100_timeouts_do_not_leak(self):
        async def slow(job: CronJob) -> None:
            await asyncio.sleep(10)  # will be timed out

        svc = CronService(
            store_path=_tmp_store(),
            on_job=slow,
            default_tz="UTC",
        )
        svc.job_timeout_s = 0.01  # 10ms
        svc._load_store()
        job = _make_every("slow-1", every_ms=1_000)
        svc._store.jobs.append(job)
        svc._save_store()

        for _ in range(100):
            await svc._execute_job(job)

        assert job.state.last_status == "error"
        assert "timed out" in (job.state.last_error or "")
        pending = svc._task_registry._tasks.get("cron-service", set())
        alive = [t for t in pending if not t.done()]
        assert alive == [], f"leaked tasks after timeout soak: {alive}"


class TestTimerRearmingSoak:
    """`_arm_timer` should replace, not accumulate, the timer task.

    Two variants:
    - Realistic (yielding): _arm_timer with an event-loop yield between
      calls, exercising the cancellation cleanup path.  Must stay <= 1.
    - Adversarial (tight sync): 1000 rapid re-arms with NO yields — the
      cancelled tasks remain in the registry until the loop next yields.
      Documents the accumulation window but asserts full cleanup after a
      single yield.
    """

    async def test_realistic_rearms_bounded_to_1(self):
        svc = CronService(
            store_path=_tmp_store(),
            on_job=AsyncMock(return_value=None),
            default_tz="UTC",
        )
        svc._running = True
        svc._load_store()
        job = _make_every("j-yield", every_ms=60_000)
        job.state.next_run_at_ms = _now_ms() + 60_000_000
        svc._store.jobs.append(job)

        for _ in range(1000):
            svc._arm_timer()
            await asyncio.sleep(0)  # let the cancelled task's _done fire

        pending = svc._task_registry._tasks.get("cron-service", set())
        alive = [t for t in pending if not t.done()]
        assert len(alive) <= 1, (
            f"realistic timer accumulated: {len(alive)} alive"
        )
        svc.stop()
        await svc.shutdown()

    async def test_adversarial_tight_rearm_cleans_up_after_yield(self):
        """Tight synchronous re-arm loop — cleanup deferred until yield.

        This test documents that a burst of _arm_timer() calls without
        yielding leaves cancelled tasks in the registry until the event
        loop next runs.  In production this would only happen inside a
        long CPU-bound stretch — but it IS observable, so worth flagging.
        """
        svc = CronService(
            store_path=_tmp_store(),
            on_job=AsyncMock(return_value=None),
            default_tz="UTC",
        )
        svc._running = True
        svc._load_store()
        job = _make_every("j-tight", every_ms=60_000)
        job.state.next_run_at_ms = _now_ms() + 60_000_000
        svc._store.jobs.append(job)

        # Tight synchronous loop — no yielding.
        for _ in range(1000):
            svc._arm_timer()

        # BEFORE yielding: registry accumulated cancelled-but-not-yet-done
        # tasks.  This is a real observation, though bounded to a single
        # burst window.
        pending_before = svc._task_registry._tasks.get("cron-service", set())
        assert len(pending_before) > 1, (
            "expected accumulation without yield — has _arm_timer "
            "gained synchronous cleanup?"
        )

        # AFTER a single yield: registry should drain.
        for _ in range(10):
            await asyncio.sleep(0)
        pending_after = svc._task_registry._tasks.get("cron-service", set())
        alive = [t for t in pending_after if not t.done()]
        assert len(alive) <= 1, (
            f"registry did not drain after yield: {len(alive)} alive"
        )

        svc.stop()
        await svc.shutdown()


class TestRunJobLoopSoak:
    """Public `run_job(force=True)` many times — verify no leaks and
    updates persist."""

    async def test_run_job_force_500_times(self):
        on_job = AsyncMock(return_value=None)
        svc = CronService(
            store_path=_tmp_store(),
            on_job=on_job,
            default_tz="UTC",
        )
        svc._load_store()
        job = _make_every("j-force", every_ms=60_000)
        # Not yet due so only force=True will run.
        job.state.next_run_at_ms = _now_ms() + 60_000_000
        svc._store.jobs.append(job)
        svc._save_store()

        for _ in range(500):
            ok = await svc.run_job(job.id, force=True)
            assert ok is True

        assert on_job.await_count == 500
        # No lingering tasks.
        pending = svc._task_registry._tasks.get("cron-service", set())
        alive = [t for t in pending if not t.done()]
        assert alive == [], f"leaked tasks: {alive}"


class TestStartShutdownCycles:
    """N start→shutdown cycles must not leak asyncio tasks."""

    async def test_100_start_shutdown_cycles(self):
        for _ in range(100):
            svc = CronService(
                store_path=_tmp_store(),
                on_job=AsyncMock(return_value=None),
                default_tz="UTC",
            )
            # Add a far-future job so start actually arms a timer.
            svc._load_store()
            job = _make_every("j-life", every_ms=60_000)
            job.state.next_run_at_ms = _now_ms() + 60_000_000
            svc._store.jobs.append(job)
            svc._save_store()

            await svc.start()
            await svc.shutdown()

            pending = svc._task_registry._tasks.get("cron-service", set())
            alive = [t for t in pending if not t.done()]
            assert alive == [], f"leaked after shutdown: {alive}"

        gc.collect()
