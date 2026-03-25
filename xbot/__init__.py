"""
xbot - A lightweight AI agent framework
"""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "0.3.1"
__logo__ = "🐈"


def _git_info() -> dict[str, str]:
    """Collect git metadata from the source tree at import time."""
    repo = Path(__file__).resolve().parent.parent
    info: dict[str, str] = {}
    try:
        run = lambda cmd: subprocess.check_output(
            cmd, cwd=repo, stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()

        info["commit"] = run(["git", "rev-parse", "--short=8", "HEAD"])
        info["branch"] = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        info["commit_time"] = run(["git", "log", "-1", "--format=%ci"])
        info["commit_msg"] = run(["git", "log", "-1", "--format=%s"])
        info["dirty"] = "(dirty)" if run(["git", "status", "--porcelain"]) else ""
    except Exception:
        pass
    return info


_boot_time = datetime.now(timezone.utc)
_git = _git_info()


def version_text() -> str:
    """Return a formatted version info string for the !ver command."""
    commit = _git.get("commit", "unknown")
    dirty = _git.get("dirty", "")
    branch = _git.get("branch", "unknown")
    commit_time = _git.get("commit_time", "")
    commit_msg = _git.get("commit_msg", "")

    boot_local = _boot_time.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    lines = [
        f"🐈 xbot v{__version__}",
        f"Commit  : {commit} {dirty}".rstrip(),
        f"Branch  : {branch}",
    ]
    if commit_msg:
        lines.append(f"Message : {commit_msg}")
    if commit_time:
        lines.append(f"Date    : {commit_time}")
    lines.append(f"Boot    : {boot_local}")
    lines.append(f"Python  : {sys.version.split()[0]}")
    return "\n".join(lines)
