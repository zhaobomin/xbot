from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import time

try:
    import fcntl  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    fcntl = None


class AutoDreamLock:
    """File-based lock for auto-dream consolidation.

    Provides three layers of safety:
    1. PID liveness check — detects crashed holders via os.kill(pid, 0)
    2. Max-age timeout — treats locks older than MAX_LOCK_AGE_S as stale
    3. fcntl exclusive lock — OS-level mutual exclusion (auto-released on crash)
    """

    MAX_LOCK_AGE_S: int = 7200  # 2 hours

    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.path = self.memory_dir / ".consolidate-lock"
        self._lock_fd: Any = None

    # ---- PID liveness ----

    def _is_holder_alive(self) -> bool:
        """Check whether the process that wrote the lock file is still running."""
        if not self.path.exists():
            return False
        try:
            pid = int(self.path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return False
        if pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)  # signal 0 = existence check only
            return True
        except ProcessLookupError:
            return False  # process is dead
        except PermissionError:
            return True   # process exists but owned by another user
        except OSError:
            return True   # platform quirk, be conservative

    # ---- mtime-based consolidation timestamp ----

    def read_last_consolidated_at(self) -> int:
        if not self.path.exists():
            return 0
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return 0
        age = time.time() - mtime
        if not self._is_holder_alive() or age > self.MAX_LOCK_AGE_S:
            return 0
        return int(mtime * 1000)

    def acquire(self) -> int:
        """Write PID and update mtime. Returns prior mtime_ms for rollback."""
        prior = self.read_last_consolidated_at()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        now = time.time()
        os.utime(self.path, (now, now))
        return prior

    def rollback(self, prior_mtime_ms: int) -> None:
        if prior_mtime_ms == 0:
            self.path.unlink(missing_ok=True)
            return
        if not self.path.exists():
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")
        t = prior_mtime_ms / 1000
        os.utime(self.path, (t, t))

    # ---- fcntl exclusive lock ----

    def try_acquire_exclusive(self) -> bool:
        """Non-blocking attempt to acquire OS-level exclusive lock.

        Returns True if lock acquired, False if already held by another process.
        On platforms without fcntl, always returns True (degrades to PID-only).
        """
        if fcntl is None:
            return True
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_fd = open(self.path, "a+")
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, BlockingIOError):
            if self._lock_fd is not None:
                self._lock_fd.close()
                self._lock_fd = None
            return False

    def release_exclusive(self) -> None:
        """Release the OS-level exclusive lock if held."""
        if self._lock_fd is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
                self._lock_fd.close()
            except Exception:
                pass
            self._lock_fd = None
