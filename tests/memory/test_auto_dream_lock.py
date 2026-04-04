from pathlib import Path

from xbot.memory.workers.auto_dream_lock import AutoDreamLock


def test_auto_dream_lock_uses_mtime_as_last_consolidated_at(tmp_path: Path) -> None:
    lock = AutoDreamLock(tmp_path / "memory")

    assert lock.read_last_consolidated_at() == 0
    prior = lock.acquire()
    assert prior == 0
    assert lock.read_last_consolidated_at() > 0


def test_auto_dream_lock_can_rollback_to_prior_mtime(tmp_path: Path) -> None:
    lock = AutoDreamLock(tmp_path / "memory")

    prior = lock.acquire()
    current = lock.read_last_consolidated_at()
    assert current > 0
    lock.rollback(prior)

    assert lock.read_last_consolidated_at() == 0
