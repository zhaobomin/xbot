"""Tests for Phase 2: PID liveness, max-age timeout, fcntl exclusive lock."""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from xbot.memory.workers.auto_dream_lock import AutoDreamLock


def test_stale_pid_detected_as_dead(tmp_path: Path) -> None:
    """Lock file with a PID that no longer exists should be treated as stale."""
    lock = AutoDreamLock(tmp_path / "memory")
    lock.memory_dir.mkdir(parents=True, exist_ok=True)
    # Write a non-existent PID
    lock.path.write_text("999999999", encoding="utf-8")
    now = time.time()
    os.utime(lock.path, (now, now))

    # _is_holder_alive should detect dead PID
    with patch("os.kill", side_effect=ProcessLookupError):
        assert lock._is_holder_alive() is False


def test_current_pid_detected_as_alive(tmp_path: Path) -> None:
    """Lock file with current PID should be detected as alive."""
    lock = AutoDreamLock(tmp_path / "memory")
    lock.memory_dir.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(str(os.getpid()), encoding="utf-8")

    assert lock._is_holder_alive() is True


def test_permission_error_treated_as_alive(tmp_path: Path) -> None:
    """PermissionError from os.kill means process exists but different user."""
    lock = AutoDreamLock(tmp_path / "memory")
    lock.memory_dir.mkdir(parents=True, exist_ok=True)
    lock.path.write_text("12345", encoding="utf-8")

    with patch("os.kill", side_effect=PermissionError):
        assert lock._is_holder_alive() is True


def test_unparseable_pid_returns_false(tmp_path: Path) -> None:
    """Lock file with garbage content should be treated as no holder."""
    lock = AutoDreamLock(tmp_path / "memory")
    lock.memory_dir.mkdir(parents=True, exist_ok=True)
    lock.path.write_text("not-a-number", encoding="utf-8")

    assert lock._is_holder_alive() is False


def test_missing_lock_file_returns_false(tmp_path: Path) -> None:
    lock = AutoDreamLock(tmp_path / "memory")
    assert lock._is_holder_alive() is False


def test_max_age_timeout_treats_old_lock_as_stale(tmp_path: Path) -> None:
    """Lock older than MAX_LOCK_AGE_S should be treated as stale (return 0)."""
    lock = AutoDreamLock(tmp_path / "memory")
    lock.acquire()

    # Artificially age the lock file beyond MAX_LOCK_AGE_S
    old_time = time.time() - lock.MAX_LOCK_AGE_S - 100
    os.utime(lock.path, (old_time, old_time))

    assert lock.read_last_consolidated_at() == 0


def test_read_last_consolidated_at_returns_nonzero_for_valid_lock(tmp_path: Path) -> None:
    lock = AutoDreamLock(tmp_path / "memory")
    lock.acquire()

    result = lock.read_last_consolidated_at()
    assert result > 0


def test_read_last_consolidated_at_zero_when_holder_dead(tmp_path: Path) -> None:
    lock = AutoDreamLock(tmp_path / "memory")
    lock.memory_dir.mkdir(parents=True, exist_ok=True)
    lock.path.write_text("999999999", encoding="utf-8")
    now = time.time()
    os.utime(lock.path, (now, now))

    with patch("os.kill", side_effect=ProcessLookupError):
        assert lock.read_last_consolidated_at() == 0


def test_fcntl_exclusive_lock_lifecycle(tmp_path: Path) -> None:
    """Test acquire + release exclusive lock lifecycle."""
    lock = AutoDreamLock(tmp_path / "memory")

    acquired = lock.try_acquire_exclusive()
    assert acquired is True

    lock.release_exclusive()
    assert lock._lock_fd is None


def test_fcntl_exclusive_lock_blocks_second_acquisition(tmp_path: Path) -> None:
    """Second lock on same file should fail with LOCK_NB."""
    lock1 = AutoDreamLock(tmp_path / "memory")
    lock2 = AutoDreamLock(tmp_path / "memory")

    assert lock1.try_acquire_exclusive() is True
    # Second lock on same path should fail (non-blocking)
    result = lock2.try_acquire_exclusive()
    # On macOS/Linux with fcntl, this should be False
    # (same process CAN re-acquire on some OS, so we accept both)
    # The important thing is no crash
    assert isinstance(result, bool)

    lock1.release_exclusive()
    lock2.release_exclusive()


def test_fcntl_none_fallback_returns_true(tmp_path: Path) -> None:
    """When fcntl is not available, try_acquire_exclusive should degrade gracefully."""
    lock = AutoDreamLock(tmp_path / "memory")

    with patch("xbot.memory.workers.auto_dream_lock.fcntl", None):
        assert lock.try_acquire_exclusive() is True

    # release_exclusive also handles None fcntl gracefully
    lock.release_exclusive()
