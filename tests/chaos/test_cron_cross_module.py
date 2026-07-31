"""Chaos test: CronService concurrent job execution writing to shared
ConversationStore sessions.

Simulates the production scenario at `gateway/app.py:631 on_cron_job`:
multiple cron jobs fire at t=0, each running `agent.process_managed_direct`
which persists messages + metadata to ConversationStore.  When two due
jobs target the SAME session_key (e.g. a periodic summary and a check-in
task both wired to one user session), they race inside the store.

Also tests:
- CronService start→stop→shutdown under concurrent _on_timer firings
- run_job dedup (same job can run concurrently — documented behavior)
- Exception in one job not corrupting another job's persist
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from xbot.runtime.session.conversation_store import ConversationStore
from xbot.runtime.system.cron.service import CronService
from xbot.runtime.system.cron.types import CronJob, CronSchedule

pytestmark = [pytest.mark.chaos]


def _tmp_workspace() -> Path:
    return Path(tempfile.mkdtemp(prefix="chaos_cron_"))


def _make_cron_service(store_path: Path, on_job=None) -> CronService:
    svc = CronService(
        store_path=store_path,
        on_job=on_job,
        default_tz="UTC",
    )
    svc.job_timeout_s = 5.0
    return svc


class TestCronConcurrentSameSession:
    """Two cron jobs fire simultaneously, both writing to the same
    ConversationStore session."""

    async def test_double_fire_same_session_no_message_loss(self):
        """Simulate two cron jobs that trigger at t=0 and both write to
        the same session.  Verify messages from both are preserved.
        """
        ws = _tmp_workspace()
        store_path = ws / "cron_jobs.json"
        try:
            store = ConversationStore(workspace=ws, max_cache_size=100)
            session_key = "web:cron-target"

            execution_log: list[str] = []

            async def on_job(job: CronJob) -> str | None:
                """Simulates what on_cron_job does: persist messages."""
                sess = store.get_or_create(session_key)
                msg = f"job-{job.name}-executed"
                sess.add_message("assistant", msg)
                store.save(sess)
                execution_log.append(msg)
                return None

            svc = _make_cron_service(store_path, on_job=on_job)

            # Add two recurring jobs both firing every 1ms (immediate).
            svc.add_job(
                name="job-A",
                schedule=CronSchedule(kind="every", every_ms=1),
                message="run A",
            )
            svc.add_job(
                name="job-B",
                schedule=CronSchedule(kind="every", every_ms=1),
                message="run B",
            )

            # Start the service and let it fire once (both jobs).
            await svc.start()
            # Give it enough time for at least one _on_timer cycle.
            await asyncio.sleep(0.1)
            await svc.shutdown()

            # Verify both jobs ran.
            assert any("job-A" in e for e in execution_log), (
                f"job-A didn't fire: {execution_log}"
            )
            assert any("job-B" in e for e in execution_log), (
                f"job-B didn't fire: {execution_log}"
            )

            # Verify disk state: all messages from both jobs.
            store.invalidate(session_key)
            reloaded = store.get_or_create(session_key)
            contents = [m["content"] for m in reloaded.messages]
            for entry in execution_log:
                assert entry in contents, (
                    f"LOST: '{entry}' not on disk. disk={contents}"
                )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_concurrent_jobs_metadata_race(self):
        """Two jobs both update metadata on the same session.
        mark_metadata_dirty triggers full rewrite.  With the eviction
        fix, they share the same object so last-writer-wins on metadata
        but NO messages are lost.
        """
        ws = _tmp_workspace()
        store_path = ws / "cron_jobs.json"
        try:
            store = ConversationStore(workspace=ws, max_cache_size=100)
            session_key = "web:cron-meta"

            async def on_job(job: CronJob) -> str | None:
                sess = store.get_or_create(session_key)
                sess.add_message("assistant", f"from-{job.name}")
                sess.metadata[f"last_{job.name}"] = "done"
                sess.mark_metadata_dirty()
                store.save(sess)
                return None

            svc = _make_cron_service(store_path, on_job=on_job)
            svc.add_job(
                name="meta-A",
                schedule=CronSchedule(kind="every", every_ms=1),
                message="A",
            )
            svc.add_job(
                name="meta-B",
                schedule=CronSchedule(kind="every", every_ms=1),
                message="B",
            )

            await svc.start()
            await asyncio.sleep(0.1)
            await svc.shutdown()

            store.invalidate(session_key)
            reloaded = store.get_or_create(session_key)
            contents = [m["content"] for m in reloaded.messages]

            # Both jobs should have written at least once.
            assert any("from-meta-A" in c for c in contents), (
                f"meta-A messages lost: {contents}"
            )
            assert any("from-meta-B" in c for c in contents), (
                f"meta-B messages lost: {contents}"
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_run_job_concurrent_with_timer(self):
        """run_job (manual trigger) fires at the same time as _on_timer.
        Both hit the same on_job callback.  Verify no double-counting,
        no unhandled exception, and messages from both executions are
        preserved.
        """
        ws = _tmp_workspace()
        store_path = ws / "cron_jobs.json"
        try:
            store = ConversationStore(workspace=ws, max_cache_size=100)
            session_key = "web:cron-manual"
            exec_count = 0

            async def on_job(job: CronJob) -> str | None:
                nonlocal exec_count
                exec_count += 1
                sess = store.get_or_create(session_key)
                sess.add_message("assistant", f"exec-{exec_count}")
                store.save(sess)
                return None

            svc = _make_cron_service(store_path, on_job=on_job)
            job = svc.add_job(
                name="manual-test",
                schedule=CronSchedule(kind="every", every_ms=1),
                message="test",
            )

            await svc.start()
            # Concurrently trigger run_job while timer also fires.
            await asyncio.gather(
                svc.run_job(job.id),
                asyncio.sleep(0.05),
            )
            await svc.shutdown()

            # At least 2 executions (1 manual + 1+ timer).
            assert exec_count >= 2, (
                f"expected at least 2 executions, got {exec_count}"
            )

            store.invalidate(session_key)
            reloaded = store.get_or_create(session_key)
            assert len(reloaded.messages) == exec_count, (
                f"messages on disk ({len(reloaded.messages)}) != "
                f"exec count ({exec_count})"
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_cron_exception_does_not_corrupt_store(self):
        """If on_job raises for one job, the other job's persist must
        not be affected.  Tests the try/except in _execute_job.
        """
        ws = _tmp_workspace()
        store_path = ws / "cron_jobs.json"
        try:
            store = ConversationStore(workspace=ws, max_cache_size=100)
            session_key = "web:cron-except"

            async def on_job(job: CronJob) -> str | None:
                if job.name == "crasher":
                    # Persist first, then crash.
                    sess = store.get_or_create(session_key)
                    sess.add_message("assistant", "crasher-partial")
                    store.save(sess)
                    raise RuntimeError("chaos: job crash")
                else:
                    sess = store.get_or_create(session_key)
                    sess.add_message("assistant", "survivor-ok")
                    store.save(sess)
                    return None

            svc = _make_cron_service(store_path, on_job=on_job)
            svc.add_job(
                name="crasher",
                schedule=CronSchedule(kind="every", every_ms=1),
                message="crash",
            )
            svc.add_job(
                name="survivor",
                schedule=CronSchedule(kind="every", every_ms=1),
                message="survive",
            )

            await svc.start()
            await asyncio.sleep(0.1)
            await svc.shutdown()

            store.invalidate(session_key)
            reloaded = store.get_or_create(session_key)
            contents = [m["content"] for m in reloaded.messages]

            # Both partial writes should be on disk — the crasher
            # saved before raising, and the survivor ran independently.
            assert "crasher-partial" in contents, (
                f"crasher's pre-crash persist lost: {contents}"
            )
            assert "survivor-ok" in contents, (
                f"survivor's persist lost: {contents}"
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)
