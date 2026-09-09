"""Regression coverage for independent cron dispatch and execution ownership."""
import asyncio

import pytest

from xbot.runtime.system.cron.service import CronService
from xbot.runtime.system.cron.types import CronSchedule


@pytest.mark.asyncio
async def test_crud_and_later_deadline_do_not_cancel_or_block_callback(tmp_path):
    started, release, later = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def callback(job):
        calls.append(job.name)
        if job.name == "slow":
            started.set()
            await release.wait()
        else:
            later.set()

    svc = CronService(tmp_path / "jobs.json", callback)
    slow = svc.add_job("slow", CronSchedule(kind="every", every_ms=60000), "")
    fast = svc.add_job("fast", CronSchedule(kind="every", every_ms=60000), "")
    slow.state.next_run_at_ms = 1
    from xbot.runtime.system.cron.service import _now_ms
    fast.state.next_run_at_ms = _now_ms() + 40
    svc._running = True
    svc._arm_timer()
    try:
        await asyncio.wait_for(started.wait(), 1)
        svc.add_job("unrelated", CronSchedule(kind="every", every_ms=60000), "")
        await asyncio.wait_for(later.wait(), 1)
        assert calls.count("slow") == 1
        assert not await svc.run_job(slow.id, force=True)
    finally:
        release.set()
        await svc.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["update", "disable", "remove"])
async def test_completion_preserves_changes_and_callback_snapshot(tmp_path, mutation):
    started, release = asyncio.Event(), asyncio.Event()
    snapshots = []

    async def callback(job):
        snapshots.append(job)
        started.set()
        await release.wait()

    svc = CronService(tmp_path / "jobs.json", callback)
    job = svc.add_job("original", CronSchedule(kind="every", every_ms=60000), "")
    task = asyncio.create_task(svc.run_job(job.id))
    await started.wait()
    if mutation == "update":
        svc.update_job(job.id, name="updated", schedule=CronSchedule(kind="every", every_ms=120000))
    elif mutation == "disable":
        svc.enable_job(job.id, False)
    else:
        svc.remove_job(job.id)
    expected_next = job.state.next_run_at_ms
    release.set()
    await task
    assert snapshots[0].name == "original"
    if mutation == "remove":
        assert svc.get_job(job.id) is None
    else:
        assert job.state.next_run_at_ms == expected_next
        assert job.state.last_status is None
    await svc.shutdown()


@pytest.mark.asyncio
async def test_capacity_and_shutdown_drain_jobs(tmp_path):
    started = asyncio.Event()
    count = 0
    cancelled = 0

    async def callback(job):
        nonlocal count, cancelled
        count += 1
        if count == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled += 1

    svc = CronService(tmp_path / "jobs.json", callback)
    svc.max_concurrent_jobs = 2
    for i in range(4):
        job = svc.add_job(str(i), CronSchedule(kind="every", every_ms=60000), "")
        job.state.next_run_at_ms = 1
    svc._running = True
    svc._arm_timer()
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.sleep(.03)
    assert count == 2
    assert svc._get_next_wake_ms() is None
    await svc.shutdown()
    assert cancelled == 2
    assert not svc._job_tasks
    assert not svc._inflight
    assert svc._timer_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["reschedule", "remove", "external"])
async def test_one_shot_completion_cannot_delete_changed_generation(tmp_path, mutation):
    import json
    import os
    from xbot.runtime.system.cron.service import _now_ms

    started, release = asyncio.Event(), asyncio.Event()

    async def callback(job):
        started.set()
        await release.wait()

    svc = CronService(tmp_path / "jobs.json", callback)
    job = svc.add_job("once", CronSchedule(kind="at", at_ms=_now_ms() + 60000), "", delete_after_run=True)
    task = asyncio.create_task(svc.run_job(job.id))
    await started.wait()
    if mutation == "remove":
        svc.remove_job(job.id)
    elif mutation == "reschedule":
        svc.update_job(job.id, schedule=CronSchedule(kind="every", every_ms=60000))
    else:
        data = json.loads(svc.store_path.read_text())
        data["jobs"][0]["enabled"] = False
        data["jobs"][0]["state"]["nextRunAtMs"] = None
        svc.store_path.write_text(json.dumps(data))
        os.utime(svc.store_path, (svc._last_mtime + 2, svc._last_mtime + 2))
    release.set()
    await task
    current = svc.get_job(job.id)
    if mutation == "remove":
        assert current is None
    else:
        assert current is not None
        assert current.state.last_status is None
        if mutation == "external":
            assert not current.enabled
            assert current.state.next_run_at_ms is None
        else:
            assert current.schedule.kind == "every"
    await svc.shutdown()


@pytest.mark.asyncio
async def test_capacity_release_dispatches_waiting_job_after_save_failure(tmp_path, monkeypatch):
    started, release, second = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def callback(job):
        if job.name == "first":
            started.set()
            await release.wait()
        else:
            second.set()

    svc = CronService(tmp_path / "jobs.json", callback)
    svc.max_concurrent_jobs = 1
    first = svc.add_job("first", CronSchedule(kind="every", every_ms=60000), "")
    last = svc.add_job("second", CronSchedule(kind="every", every_ms=60000), "")
    first.state.next_run_at_ms = last.state.next_run_at_ms = 1
    svc._running = True
    svc._arm_timer()
    try:
        await asyncio.wait_for(started.wait(), 1)
        monkeypatch.setattr(svc, "_save_store", lambda: (_ for _ in ()).throw(OSError("disk full")))
        release.set()
        await asyncio.wait_for(second.wait(), 1)
        assert first.state.last_status == "ok"
        assert first.state.next_run_at_ms > first.state.last_run_at_ms
    finally:
        await svc.shutdown()

@pytest.mark.asyncio
async def test_external_unrelated_update_preserves_one_shot_completion(tmp_path):
    import json
    import os
    from xbot.runtime.system.cron.service import _now_ms
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def callback(job):
        calls.append(job.id); started.set(); await release.wait()
    svc = CronService(tmp_path/'jobs.json', callback)
    once = svc.add_job('once', CronSchedule(kind='at', at_ms=_now_ms()+60000), '', delete_after_run=True)
    other = svc.add_job('other', CronSchedule(kind='every', every_ms=60000), '')
    running = asyncio.create_task(svc.run_job(once.id))
    await started.wait()
    data = json.loads(svc.store_path.read_text())
    next(j for j in data['jobs'] if j['id']==other.id)['name']='renamed'
    svc.store_path.write_text(json.dumps(data))
    os.utime(svc.store_path, (svc._last_mtime+2,svc._last_mtime+2))
    release.set(); await running
    assert svc.get_job(once.id) is None
    assert svc.get_job(other.id).name == 'renamed'
    await svc.shutdown()
