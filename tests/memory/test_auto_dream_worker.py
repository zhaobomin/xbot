from pathlib import Path

import pytest

from xbot.memory.workers.auto_dream import AutoDreamWorker


@pytest.mark.asyncio
async def test_auto_dream_worker_respects_time_and_session_gates(tmp_path: Path) -> None:
    runs: list[str] = []

    async def runner(session_key: str) -> bool:
        runs.append(session_key)
        return True

    worker = AutoDreamWorker(tmp_path, runner=runner, min_hours=24, min_sessions=2)

    await worker.maybe_run("s1")
    await worker.maybe_run("s2")

    assert runs == []


@pytest.mark.asyncio
async def test_auto_dream_worker_runs_after_enough_other_sessions(tmp_path: Path) -> None:
    runs: list[str] = []

    async def runner(session_key: str) -> bool:
        runs.append(session_key)
        return True

    worker = AutoDreamWorker(tmp_path, runner=runner, min_hours=0, min_sessions=2)

    await worker.maybe_run("s1")
    await worker.maybe_run("s2")
    await worker.maybe_run("s3")

    assert runs == ["s3"]
