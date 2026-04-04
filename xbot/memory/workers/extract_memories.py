from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.workers.extract_prompts import build_extract_memories_prompt
from xbot.memory.workers.operations import PERSIST_MEMORIES_TOOL, apply_memory_operations


def _find_uuid_index(messages: list[dict], target_uuid: str | None) -> int | None:
    """从尾部向前扫描找到匹配 UUID 的消息索引。"""
    if not target_uuid:
        return None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("uuid") == target_uuid:
            return i
    return None


class ExtractMemoriesWorker:
    """Minimal orchestration shell for Claude-style post-turn extraction."""

    def __init__(
        self,
        workspace: Path,
        *,
        runner: Callable[[str, list[dict], bool], Awaitable[bool]] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, tuple[list[dict], bool]] = {}
        self._state_path = self.workspace / "memory" / ".extract-state.json"
        self._runner = runner or self._noop_runner

    async def request_run(
        self,
        session_key: str,
        *,
        messages: list[dict] | None = None,
        direct_memory_write: bool = False,
    ) -> None:
        lock = self._locks.setdefault(session_key, asyncio.Lock())
        current_messages = list(messages or [])
        if lock.locked():
            pending_messages, pending_direct_write = self._pending.get(session_key, ([], False))
            self._pending[session_key] = (
                current_messages if len(current_messages) >= len(pending_messages) else pending_messages,
                pending_direct_write or direct_memory_write,
            )
            await lock.acquire()
            lock.release()
            return

        async with lock:
            pending_messages = current_messages
            pending_direct_write = direct_memory_write
            while True:
                session_state = self._load_state()["sessions"].get(session_key, {"cursor": 0, "failures": 0})
                cursor_uuid = session_state.get("cursor_uuid")
                cursor_int = int(session_state.get("cursor", 0))
                anchor = _find_uuid_index(pending_messages, cursor_uuid)
                if anchor is not None:
                    new_messages = pending_messages[anchor + 1:]
                else:
                    new_messages = pending_messages[cursor_int:]
                if new_messages:
                    success = True
                    if pending_direct_write:
                        success = True
                    else:
                        success = await self._runner(session_key, new_messages, pending_direct_write)
                    state = self._load_state()
                    sessions = state.setdefault("sessions", {})
                    current = sessions.setdefault(session_key, {"cursor": 0, "failures": 0})
                    if success:
                        last_msg = pending_messages[-1] if pending_messages else None
                        current["cursor_uuid"] = last_msg.get("uuid") if last_msg else cursor_uuid
                        current["cursor"] = len(pending_messages)
                        current["failures"] = 0
                        current.pop("last_error", None)
                    else:
                        current["failures"] = int(current.get("failures", 0)) + 1
                        current["last_error"] = "runner_failed"
                    self._save_state(state)

                next_pending = self._pending.pop(session_key, None)
                if next_pending is None:
                    break
                pending_messages, pending_direct_write = next_pending

    def _load_state(self) -> dict[str, dict[str, dict[str, int | str]]]:
        if not self._state_path.exists():
            return {"sessions": {}}
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"sessions": {}}
        if "sessions" in raw:
            return raw
        return {
            "sessions": {
                key: {"cursor": int(value), "failures": 0}
                for key, value in raw.items()
            }
        }

    def _save_state(self, state: dict[str, dict[str, dict[str, int | str]]]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _noop_runner(self, session_key: str, messages: list[dict], direct_memory_write: bool) -> bool:
        _ = (session_key, messages, direct_memory_write)
        await asyncio.sleep(0)
        return True


async def execute_extract_memories(
    backend: object,
    *,
    workspace: Path,
    session_key: str,
    messages: list[dict],
) -> bool:
    store = MemoryDirStore(Path(workspace))
    manifest = store.load_index_for_prompt()
    prompt = build_extract_memories_prompt(len(messages), manifest)
    response = await backend.call_for_auxiliary(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"session_key": session_key, "messages": messages}, ensure_ascii=False)},
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
