"""Runtime session registry backed by SessionCoordinator.

This module intentionally exposes a reduced, state-machine-centric API.
Old direct transition APIs are removed to enforce single-write dispatch.
"""

from __future__ import annotations

import time
from typing import Any

from xbot.runtime.state.coordinator import SessionCoordinator, SessionEvent, SessionPhase, SessionState


class RuntimeSessionRegistry:
    """Runtime registry with coordinator-managed session phases."""

    def __init__(self) -> None:
        self._coordinator = SessionCoordinator()
        self._sdk_index: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Core state access
    # ------------------------------------------------------------------

    def get(self, session_key: str) -> SessionState | None:
        return self._coordinator.get(session_key)

    def get_or_create(self, session_key: str) -> SessionState:
        return self._coordinator.get_or_create(session_key)

    def get_phase(self, session_key: str) -> SessionPhase:
        return self._coordinator.get_phase(session_key)

    def dispatch(self, session_key: str, event: SessionEvent, *, reason: str = "", strict: bool = True) -> bool:
        return self._coordinator.dispatch(session_key, event, reason=reason, strict=strict)

    def list_keys(self) -> list[str]:
        return self._coordinator.list_keys()

    list_sessions = list_keys

    # ------------------------------------------------------------------
    # Session lifecycle / cleanup
    # ------------------------------------------------------------------

    async def delete(self, session_key: str, delete_sdk_file: bool = False) -> bool:
        _ = delete_sdk_file
        state = self.get(session_key)
        if state and state.sdk_session_id:
            self._sdk_index.pop(state.sdk_session_id, None)
        self._coordinator.remove(session_key)
        return True

    async def cleanup_session(self, session_key: str) -> None:
        await self.delete(session_key)

    def has_session(self, session_key: str) -> bool:
        return self.get(session_key) is not None

    def touch(self, session_key: str) -> None:
        state = self.get_or_create(session_key)
        state.last_active = time.time()

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def set_routing(self, session_key: str, channel: str, chat_id: str) -> None:
        state = self.get_or_create(session_key)
        state.channel = channel
        state.chat_id = chat_id
        state.last_active = time.time()

    def get_routing(self, session_key: str) -> tuple[str, str] | None:
        state = self.get(session_key)
        if state is None:
            return None
        return (state.channel, state.chat_id)

    def set_context(self, session_key: str, channel: str, chat_id: str) -> None:
        self.set_routing(session_key, channel, chat_id)

    def clear_context(self, session_key: str) -> None:
        state = self.get(session_key)
        if state is None:
            return
        state.channel = ""
        state.chat_id = ""

    def clear_all_contexts(self) -> None:
        for key in self.list_keys():
            self.clear_context(key)

    def resolve_routing(self, identifier: str) -> tuple[str, str, str] | None:
        state = self.get(identifier)
        if state is not None:
            return (state.session_key, state.channel, state.chat_id)

        state = self.get_by_sdk_id(identifier)
        if state is not None:
            return (state.session_key, state.channel, state.chat_id)
        return None

    def resolve_compact_notification_target(self, session_ref: str) -> tuple[str, str, str] | None:
        return self.resolve_routing(session_ref)

    # ------------------------------------------------------------------
    # SDK session mapping
    # ------------------------------------------------------------------

    def _set_sdk_session_id_impl(self, session_key: str, sdk_id: str | None) -> None:
        state = self.get_or_create(session_key)
        old = state.sdk_session_id
        if old:
            self._sdk_index.pop(old, None)
        state.sdk_session_id = sdk_id
        if sdk_id:
            self._sdk_index[sdk_id] = session_key

    async def set_sdk_session_id(self, session_key: str, sdk_id: str | None) -> None:
        self._set_sdk_session_id_impl(session_key, sdk_id)

    def resolve_sdk_session_id(self, session_key: str) -> str | None:
        state = self.get(session_key)
        return state.sdk_session_id if state else None

    def get_by_sdk_id(self, sdk_session_id: str) -> SessionState | None:
        key = self._sdk_index.get(sdk_session_id)
        if key is None:
            return None
        return self.get(key)

    def get_context_by_session_key(self, session_key: str) -> tuple[str, str] | None:
        return self.get_routing(session_key)

    def get_context_by_sdk_id(self, sdk_session_id: str) -> tuple[str, str] | None:
        state = self.get_by_sdk_id(sdk_session_id)
        if state is None:
            return None
        return (state.channel, state.chat_id)

    # ------------------------------------------------------------------
    # Session metadata
    # ------------------------------------------------------------------

    def set_execution_cwd(self, session_key: str, cwd: str | None) -> None:
        state = self.get_or_create(session_key)
        state.execution_cwd = cwd

    def get_execution_cwd(self, session_key: str) -> str | None:
        state = self.get(session_key)
        return state.execution_cwd if state else None

    def set_workspace_dir(self, session_key: str, workspace_dir: str | None) -> None:
        state = self.get_or_create(session_key)
        state.workspace_dir = workspace_dir

    def get_workspace_dir(self, session_key: str) -> str | None:
        state = self.get(session_key)
        return state.workspace_dir if state else None

    def set_commands(self, session_key: str, commands: list[str]) -> None:
        state = self.get_or_create(session_key)
        state.commands = list(commands)

    def get_commands(self, session_key: str) -> list[str]:
        state = self.get(session_key)
        if state is None:
            return []
        return list(state.commands)

    def set_sdk_capabilities(
        self,
        session_key: str,
        *,
        skills: list[str] | None = None,
        tools: list[str] | None = None,
        slash_commands: list[str] | None = None,
        skill_source: str = "sdk_only",
    ) -> None:
        state = self.get_or_create(session_key)
        if skills is not None:
            state.sdk_skills = list(skills)
        if tools is not None:
            state.sdk_tools = list(tools)
        if slash_commands is not None:
            state.sdk_slash_commands = list(slash_commands)
        state.skill_source = skill_source

    def get_sdk_capabilities(self, session_key: str) -> dict[str, Any]:
        state = self.get(session_key)
        if state is None:
            return {
                "skills": [],
                "tools": [],
                "slash_commands": [],
                "skill_source": "sdk_only",
            }
        return {
            "skills": list(state.sdk_skills),
            "tools": list(state.sdk_tools),
            "slash_commands": list(state.sdk_slash_commands),
            "skill_source": state.skill_source,
        }

    def set_task_id(self, session_key: str, task_id: str | None) -> None:
        state = self.get_or_create(session_key)
        state.task_id = task_id

    def get_task_id(self, session_key: str) -> str | None:
        state = self.get(session_key)
        return state.task_id if state else None

    def set_request_id(self, session_key: str, request_id: str | None) -> None:
        state = self.get_or_create(session_key)
        state.request_id = request_id

    def get_request_id(self, session_key: str) -> str | None:
        state = self.get(session_key)
        return state.request_id if state else None

    # ------------------------------------------------------------------
    # Recovery counters and diagnostics
    # ------------------------------------------------------------------

    def note_recovery_failure(self, session_key: str) -> int:
        state = self.get_or_create(session_key)
        state.recovery_fail_count += 1
        return state.recovery_fail_count

    def reset_recovery_failures(self, session_key: str) -> None:
        state = self.get(session_key)
        if state:
            state.recovery_fail_count = 0

    def get_recovery_failures(self, session_key: str) -> int:
        state = self.get(session_key)
        if state is None:
            return 0
        return state.recovery_fail_count

    def check_session(self, session_key: str) -> dict[str, Any]:
        state = self.get(session_key)
        if state is None:
            return {"exists": False}
        return {
            "exists": True,
            "phase": state.phase.value,
            "sdk_session_id": state.sdk_session_id,
            "channel": state.channel,
            "chat_id": state.chat_id,
            "execution_cwd": state.execution_cwd,
            "workspace_dir": state.workspace_dir,
            "commands": len(state.commands),
            "illegal_transition_count": state.illegal_transition_count,
            "recovery_fail_count": state.recovery_fail_count,
            "last_active": state.last_active,
        }

    def snapshot(self) -> dict[str, Any]:
        return self._coordinator.snapshot()
