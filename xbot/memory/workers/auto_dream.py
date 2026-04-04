from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Awaitable, Callable

from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.workers.auto_dream_lock import AutoDreamLock
from xbot.memory.workers.auto_dream_prompt import build_auto_dream_prompt
from xbot.memory.workers.operations import PERSIST_MEMORIES_TOOL, apply_memory_operations


class AutoDreamWorker:
    """Minimal orchestration shell for Claude-style auto dream."""

    def __init__(
        self,
        workspace: Path,
        *,
        runner: Callable[[str], Awaitable[bool]] | None = None,
        min_hours: int = 24,
        min_sessions: int = 5,
    ) -> None:
        self.workspace = Path(workspace)
        self.runner = runner or self._noop_runner
        self.min_hours = min_hours
        self.min_sessions = min_sessions
        self.memory_dir = self.workspace / "memory"
        self.lock = AutoDreamLock(self.memory_dir)
        self.state_path = self.memory_dir / ".auto-dream-state.json"

    async def maybe_run(self, session_key: str) -> None:
        state = self._load_state()
        seen = set(state.get("seen_sessions", []))
        seen.add(session_key)
        state["seen_sessions"] = sorted(seen)
        self._save_state(state)

        last_consolidated = self.lock.read_last_consolidated_at()
        enough_time = (
            self.min_hours == 0
            or last_consolidated == 0
            or ((time.time() * 1000 - last_consolidated) >= self.min_hours * 3600 * 1000)
        )
        if not enough_time:
            return

        if len(seen - {session_key}) < self.min_sessions:
            return

        if not self.lock.try_acquire_exclusive():
            return  # another process is consolidating, skip this cycle

        try:
            previous_mtime = self.lock.acquire()
            try:
                success = await self.runner(session_key)
                if success:
                    self._save_state({"seen_sessions": []})
                else:
                    self.lock.rollback(previous_mtime)
            except Exception:
                self.lock.rollback(previous_mtime)
                raise
        finally:
            self.lock.release_exclusive()

    def _load_state(self) -> dict[str, list[str]]:
        if not self.state_path.exists():
            return {"seen_sessions": []}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"seen_sessions": []}

    def _save_state(self, state: dict[str, list[str]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _noop_runner(self, session_key: str) -> bool:
        _ = session_key
        await asyncio.sleep(0)
        return True


async def execute_auto_dream(
    backend: object,
    *,
    workspace: Path,
    session_key: str,
) -> bool:
    workspace = Path(workspace)
    store = MemoryDirStore(workspace)
    prompt = build_auto_dream_prompt(
        str(workspace / "memory"),
        str(workspace / "sessions"),
        extra=store.load_index_for_prompt(),
    )
    response = await backend.call_for_auxiliary(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"session_key": session_key}, ensure_ascii=False)},
        ],
        tools=PERSIST_MEMORIES_TOOL,
        tool_choice={"type": "function", "function": {"name": "persist_memories"}},
        max_tokens=4096,
    )
    for tool_call in getattr(response, "tool_calls", None) or []:
        if tool_call.name != "persist_memories":
            continue
        apply_memory_operations(store, tool_call.arguments.get("operations", []))
        return True
    return response.finish_reason == "stop"
