"""Goal state persistence for Goal mode.

Manages goal lifecycle state as JSON files under <workspace>/.goal/.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class GoalStatus(str, Enum):
    """Goal lifecycle states."""

    INITIALIZED = "initialized"
    ACTIVE = "active"
    PAUSED = "paused"
    ACHIEVED = "achieved"
    UNMET = "unmet"


class GoalPhase(str, Enum):
    """Current execution phase within a loop iteration."""

    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"


@dataclass
class GoalState:
    """Persistent state for a single goal."""

    goal_id: str
    objective: str
    workspace: str
    session_key: str
    status: GoalStatus = GoalStatus.INITIALIZED
    verify_cmd: Optional[str] = None
    max_loops: int = 10
    loop_count: int = 0
    current_phase: Optional[GoalPhase] = None
    checkpoint: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now(timezone.utc).isoformat()


def generate_goal_id() -> str:
    """Generate a unique goal ID."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    short_uuid = uuid.uuid4().hex[:8]
    return f"goal-{ts}-{short_uuid}"


class GoalStore:
    """Reads and writes GoalState to <workspace>/.goal/<goal_id>.json."""

    GOAL_DIR = ".goal"

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)
        self._goal_dir = self._workspace / self.GOAL_DIR

    def _ensure_dir(self) -> None:
        self._goal_dir.mkdir(parents=True, exist_ok=True)

    def _goal_path(self, goal_id: str) -> Path:
        return self._goal_dir / f"{goal_id}.json"

    def save(self, goal: GoalState) -> None:
        """Atomically write goal state to disk."""
        self._ensure_dir()
        target = self._goal_path(goal.goal_id)
        goal.touch()
        data = asdict(goal)
        # Serialize enums to their string values
        data["status"] = goal.status.value
        if goal.current_phase is not None:
            data["current_phase"] = goal.current_phase.value
        else:
            data["current_phase"] = None

        # Atomic write: write to temp file then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._goal_dir), suffix=".tmp", prefix=goal.goal_id
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_path, str(target))
        except BaseException:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load(self, goal_id: str) -> GoalState | None:
        """Load a goal by ID. Returns None if not found or corrupted."""
        path = self._goal_path(goal_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Deserialize enums
            data["status"] = GoalStatus(data["status"])
            phase = data.get("current_phase")
            data["current_phase"] = GoalPhase(phase) if phase else None
            return GoalState(**data)
        except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError):
            return None

    def list_goals(self) -> list[GoalState]:
        """List all goals in the workspace, sorted by updated_at descending."""
        if not self._goal_dir.exists():
            return []
        goals = []
        for path in self._goal_dir.glob("goal-*.json"):
            goal_id = path.stem
            goal = self.load(goal_id)
            if goal:
                goals.append(goal)
        goals.sort(key=lambda g: g.updated_at, reverse=True)
        return goals

    def delete(self, goal_id: str) -> bool:
        """Delete a goal state file. Returns True if deleted."""
        path = self._goal_path(goal_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def find_active(self) -> GoalState | None:
        """Find the currently active or paused goal (most recent)."""
        for goal in self.list_goals():
            if goal.status in (GoalStatus.ACTIVE, GoalStatus.PAUSED, GoalStatus.INITIALIZED):
                return goal
        return None
