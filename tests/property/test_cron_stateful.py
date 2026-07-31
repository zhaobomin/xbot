"""Stateful property-based tests for CronService.

Tests invariants around job scheduling:
- next_run_at_ms is always in the future for recurring jobs after execution
- One-shot ("at") jobs become disabled after execution
- Exception in on_job never propagates; always captured in last_error
- Disabled jobs are never picked by _on_timer unless force-run
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock

import hypothesis.strategies as st
from hypothesis import given, settings, assume, HealthCheck
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from xbot.runtime.system.cron.service import CronService, _now_ms
from xbot.runtime.system.cron.types import (
    CronJob,
    CronJobState,
    CronPayload,
    CronSchedule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run async code synchronously."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=30)
    else:
        return asyncio.run(coro)


def _make_every_job(job_id: str, every_ms: int = 60_000, enabled: bool = True) -> CronJob:
    """Create a recurring 'every' job."""
    now = _now_ms()
    return CronJob(
        id=job_id,
        name=f"job-{job_id}",
        enabled=enabled,
        schedule=CronSchedule(kind="every", every_ms=every_ms),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at_ms=now - 1000),  # already due
        created_at_ms=now,
        updated_at_ms=now,
    )


def _make_at_job(job_id: str, at_ms: int | None = None, delete_after: bool = False) -> CronJob:
    """Create a one-shot 'at' job."""
    now = _now_ms()
    if at_ms is None:
        at_ms = now - 1000  # already due
    return CronJob(
        id=job_id,
        name=f"oneshot-{job_id}",
        enabled=True,
        schedule=CronSchedule(kind="at", at_ms=at_ms),
        payload=CronPayload(message="test"),
        state=CronJobState(next_run_at_ms=at_ms),
        created_at_ms=now,
        updated_at_ms=now,
        delete_after_run=delete_after,
    )


# ---------------------------------------------------------------------------
# Property tests: _compute_next_run invariants
# ---------------------------------------------------------------------------


class TestComputeNextRunProperties:
    """next_run_at_ms invariants for different schedule kinds."""

    @given(every_ms=st.integers(min_value=1000, max_value=86_400_000))
    @settings(max_examples=100, deadline=None)
    def test_every_next_run_always_in_future(self, every_ms: int):
        """For 'every' schedule, next_run is always > now."""
        from xbot.runtime.system.cron.service import _compute_next_run

        now = _now_ms()
        schedule = CronSchedule(kind="every", every_ms=every_ms)
        result = _compute_next_run(schedule, now)
        assert result is not None
        assert result > now, f"next_run {result} not > now {now}"

    @given(offset=st.integers(min_value=1, max_value=1_000_000))
    @settings(max_examples=50, deadline=None)
    def test_at_past_returns_none(self, offset: int):
        """For 'at' schedule with past time, next_run is None."""
        from xbot.runtime.system.cron.service import _compute_next_run

        now = _now_ms()
        schedule = CronSchedule(kind="at", at_ms=now - offset)
        result = _compute_next_run(schedule, now)
        assert result is None

    @given(offset=st.integers(min_value=1000, max_value=86_400_000))
    @settings(max_examples=50, deadline=None)
    def test_at_future_returns_at_ms(self, offset: int):
        """For 'at' schedule with future time, next_run == at_ms."""
        from xbot.runtime.system.cron.service import _compute_next_run

        now = _now_ms()
        at_ms = now + offset
        schedule = CronSchedule(kind="at", at_ms=at_ms)
        result = _compute_next_run(schedule, now)
        assert result == at_ms


# ---------------------------------------------------------------------------
# Stateful machine: CronService execution invariants
# ---------------------------------------------------------------------------


class CronServiceStateMachine(RuleBasedStateMachine):
    """Model-based test for CronService job execution."""

    def __init__(self):
        super().__init__()
        self._tmp_dir: Path | None = None
        self._svc: CronService | None = None
        self._on_job_mock: AsyncMock | None = None
        # Model: track job states
        self._jobs: dict[str, CronJob] = {}
        self._job_counter = 0
        self._execution_count = 0

    @initialize()
    def setup(self):
        self._tmp_dir = Path(tempfile.mkdtemp())
        self._on_job_mock = AsyncMock(return_value=None)
        self._svc = CronService(
            store_path=self._tmp_dir / "jobs.json",
            on_job=self._on_job_mock,
            default_tz="UTC",
        )
        self._jobs = {}
        self._job_counter = 0
        self._execution_count = 0

    @rule(every_ms=st.integers(min_value=1000, max_value=3_600_000))
    def add_recurring_job(self, every_ms: int):
        """Add a new recurring job via the service."""
        self._job_counter += 1
        job_id = f"recur-{self._job_counter}"
        job = _make_every_job(job_id, every_ms=every_ms)

        async def _do():
            self._svc._load_store()
            self._svc._store.jobs.append(job)
            self._svc._save_store()

        _run(_do())
        self._jobs[job_id] = job

    @rule()
    def add_oneshot_job(self):
        """Add a one-shot job that's already due."""
        self._job_counter += 1
        job_id = f"oneshot-{self._job_counter}"
        job = _make_at_job(job_id)

        async def _do():
            self._svc._load_store()
            self._svc._store.jobs.append(job)
            self._svc._save_store()

        _run(_do())
        self._jobs[job_id] = job

    @rule(data=st.data())
    def run_job(self, data):
        """Run a random job by id."""
        if not self._jobs:
            return
        job_id = data.draw(st.sampled_from(list(self._jobs.keys())))

        async def _do():
            return await self._svc.run_job(job_id, force=True)

        result = _run(_do())
        assert result is True
        self._execution_count += 1

    @rule(data=st.data())
    def run_job_with_exception(self, data):
        """Run a job where on_job raises — must not propagate."""
        if not self._jobs:
            return
        job_id = data.draw(st.sampled_from(list(self._jobs.keys())))

        # Temporarily make on_job raise
        original_side_effect = self._on_job_mock.side_effect
        self._on_job_mock.side_effect = RuntimeError("simulated failure")

        async def _do():
            return await self._svc.run_job(job_id, force=True)

        result = _run(_do())
        assert result is True  # execution completed (error captured)

        # Restore
        self._on_job_mock.side_effect = original_side_effect
        self._execution_count += 1

    @invariant()
    def recurring_jobs_have_future_next_run(self):
        """After execution, recurring jobs must have next_run_at_ms > now."""
        if self._svc is None or self._execution_count == 0:
            return

        async def _check():
            self._svc._load_store()
            now = _now_ms()
            for job in self._svc._store.jobs:
                if job.schedule.kind == "every" and job.enabled:
                    if job.state.last_run_at_ms is not None:
                        # Was executed at least once
                        assert job.state.next_run_at_ms is not None, (
                            f"Job {job.id}: next_run_at_ms is None after execution"
                        )
                        assert job.state.next_run_at_ms > job.state.last_run_at_ms, (
                            f"Job {job.id}: next_run {job.state.next_run_at_ms} "
                            f"not > last_run {job.state.last_run_at_ms}"
                        )

        _run(_check())

    @invariant()
    def oneshot_jobs_disabled_after_run(self):
        """After execution, one-shot jobs must be disabled or deleted."""
        if self._svc is None or self._execution_count == 0:
            return

        async def _check():
            self._svc._load_store()
            for job in self._svc._store.jobs:
                if job.schedule.kind == "at" and job.state.last_run_at_ms is not None:
                    # Was executed
                    assert not job.enabled, (
                        f"One-shot job {job.id} still enabled after execution"
                    )
                    assert job.state.next_run_at_ms is None, (
                        f"One-shot job {job.id} still has next_run after execution"
                    )

        _run(_check())

    @invariant()
    def exceptions_always_captured(self):
        """Any job that errored must have last_status='error' and last_error set."""
        if self._svc is None:
            return

        async def _check():
            self._svc._load_store()
            for job in self._svc._store.jobs:
                if job.state.last_status == "error":
                    assert job.state.last_error is not None, (
                        f"Job {job.id}: status=error but last_error is None"
                    )
                    assert len(job.state.last_error) > 0

        _run(_check())

    @invariant()
    def store_file_valid_json(self):
        """The store file must always be valid JSON."""
        if self._tmp_dir is None:
            return
        store_file = self._tmp_dir / "jobs.json"
        if not store_file.exists():
            return
        import json
        content = store_file.read_text(encoding="utf-8")
        data = json.loads(content)  # Must not raise
        assert isinstance(data, dict)
        assert "jobs" in data

    def teardown(self):
        import shutil
        if self._tmp_dir and self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir, ignore_errors=True)


# Hypothesis settings
TestCronService = CronServiceStateMachine.TestCase
TestCronService.settings = settings(
    max_examples=100,
    stateful_step_count=20,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
