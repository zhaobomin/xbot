"""Unified adapter for session state reads/writes during SessionStore migration."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from xbot.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from xbot.agent.state.store import SessionEntry, SessionStore
    from xbot.session.manager import SessionManager


class SessionStateAdapter:
    """Single access point for backend session state.

    SessionStore is the preferred source of truth. Legacy mappings are mirrored
    only where older code paths still expect them.
    """

    def __init__(
        self,
        *,
        session_store: "SessionStore | None",
        use_session_store: bool,
        shared_resources: dict[str, Any] | None,
        sessions: "SessionManager | None",
        legacy_models: dict[str, str | None] | None = None,
        legacy_skills_versions: dict[str, str | None] | None = None,
        legacy_commands: dict[str, list[str]] | None = None,
        legacy_last_used: dict[str, float] | None = None,
        legacy_task_ids: dict[str, str] | None = None,
        legacy_request_ids: dict[str, str] | None = None,
        legacy_clients: dict[str, Any] | None = None,
        legacy_sdk_session_ids: dict[str, str] | None = None,
    ) -> None:
        self._session_store = session_store
        self._use_session_store = use_session_store and session_store is not None
        self._shared_resources = shared_resources if shared_resources is not None else {}
        self._sessions = sessions
        self._legacy_models = legacy_models if legacy_models is not None else {}
        self._legacy_skills_versions = (
            legacy_skills_versions if legacy_skills_versions is not None else {}
        )
        self._legacy_commands = legacy_commands if legacy_commands is not None else {}
        self._legacy_last_used = legacy_last_used if legacy_last_used is not None else {}
        self._legacy_task_ids = legacy_task_ids if legacy_task_ids is not None else {}
        self._legacy_request_ids = legacy_request_ids if legacy_request_ids is not None else {}
        self._legacy_clients = legacy_clients if legacy_clients is not None else {}
        self._legacy_sdk_session_ids = (
            legacy_sdk_session_ids if legacy_sdk_session_ids is not None else {}
        )

    def uses_session_store(self) -> bool:
        return self._use_session_store

    def _get_contexts(self) -> dict[str, Any]:
        return self._shared_resources.setdefault("_session_contexts", {})

    def _get_entry(self, session_key: str) -> "SessionEntry | None":
        if not self._use_session_store or self._session_store is None:
            return None
        return self._session_store.get(session_key)

    def get_or_create_session(self, session_key: str) -> "SessionEntry | None":
        if not self._use_session_store or self._session_store is None:
            return None
        return self._session_store.get_or_create(session_key)

    def set_context(self, session_key: str, channel: str, chat_id: str) -> None:
        if self._use_session_store:
            entry = self.get_or_create_session(session_key)
            if entry is not None:
                entry.channel = channel
                entry.chat_id = chat_id

        # Deprecated compatibility mirror for code paths still reading legacy mappings.
        self._get_contexts()[session_key] = (channel, chat_id)

    def get_context_by_session_key(self, session_key: str) -> tuple[str, str] | None:
        entry = self._get_entry(session_key)
        if entry is not None and entry.channel and entry.chat_id:
            return (entry.channel, entry.chat_id)

        result = self._get_contexts().get(session_key)
        return result if isinstance(result, tuple) else None

    def get_context_by_sdk_id(self, sdk_session_id: str) -> tuple[str, str] | None:
        if self._use_session_store and self._session_store is not None:
            entry = self._session_store.get_by_sdk_id(sdk_session_id)
            if entry is not None and entry.channel and entry.chat_id:
                return (entry.channel, entry.chat_id)

        result = self._get_contexts().get(sdk_session_id)
        return result if isinstance(result, tuple) else None

    def get_context(self, identifier: str) -> tuple[str, str] | None:
        result = self.get_context_by_session_key(identifier)
        if result is not None:
            return result
        return self.get_context_by_sdk_id(identifier)

    def get_session_key_by_sdk_id(self, sdk_session_id: str) -> str | None:
        if self._use_session_store and self._session_store is not None:
            entry = self._session_store.get_by_sdk_id(sdk_session_id)
            if entry is not None:
                return entry.session_key
        return self._legacy_sdk_session_ids.get(sdk_session_id)

    def resolve_compact_notification_target(
        self,
        session_ref: str,
    ) -> tuple[str, str, str] | None:
        mapped_session_key = self.get_session_key_by_sdk_id(session_ref)
        if isinstance(mapped_session_key, str):
            sdk_context = self.get_context_by_sdk_id(session_ref)
            if sdk_context is not None:
                channel, chat_id = sdk_context
                return (mapped_session_key, channel, chat_id)

            context_by_key = self.get_context_by_session_key(mapped_session_key)
            if context_by_key is not None:
                channel, chat_id = context_by_key
                return (mapped_session_key, channel, chat_id)
            return None

        context = self.get_context_by_session_key(session_ref)
        if context is not None:
            channel, chat_id = context
            return (session_ref, channel, chat_id)

        sdk_context = self.get_context_by_sdk_id(session_ref)
        if sdk_context is None:
            return None

        channel, chat_id = sdk_context
        for session_key, value in self._get_contexts().items():
            if value == session_ref:
                return (session_key, channel, chat_id)
        return None

    async def set_sdk_session_id(self, session_key: str, sdk_session_id: str | None) -> None:
        if self._use_session_store and self._session_store is not None:
            self.get_or_create_session(session_key)
            self._session_store.set_sdk_session_id(session_key, sdk_session_id)

        old_sdk_id = self.resolve_sdk_session_id(session_key)
        if old_sdk_id and old_sdk_id != sdk_session_id:
            self._legacy_sdk_session_ids.pop(old_sdk_id, None)
            if isinstance(self._get_contexts().get(old_sdk_id), tuple):
                self._get_contexts().pop(old_sdk_id, None)

        if sdk_session_id:
            self._legacy_sdk_session_ids[sdk_session_id] = session_key
        else:
            for sid, mapped_key in list(self._legacy_sdk_session_ids.items()):
                if mapped_key == session_key:
                    self._legacy_sdk_session_ids.pop(sid, None)

        if self._sessions is not None:
            session = self._sessions.get(session_key)
            if session is not None:
                if sdk_session_id:
                    session.metadata["sdk_session_id"] = sdk_session_id
                else:
                    session.metadata.pop("sdk_session_id", None)
                self._sessions.save(session)

    def set_sdk_context_mapping(self, sdk_session_id: str, channel: str, chat_id: str) -> None:
        if self._use_session_store and self._session_store is not None:
            for session_key in self._session_store.list_keys():
                entry = self._session_store.get(session_key)
                if entry is not None and entry.channel == channel and entry.chat_id == chat_id:
                    self._session_store.set_sdk_session_id(session_key, sdk_session_id)
                    break

        # Deprecated compatibility mirror for legacy context lookups by sdk_session_id.
        self._get_contexts()[sdk_session_id] = (channel, chat_id)

    def clear_context(self, session_key: str) -> None:
        sdk_session_id = self.resolve_sdk_session_id(session_key)
        contexts = self._get_contexts()
        contexts.pop(session_key, None)
        if sdk_session_id:
            contexts.pop(sdk_session_id, None)
            self._legacy_sdk_session_ids.pop(sdk_session_id, None)

        entry = self._get_entry(session_key)
        if entry is not None and self._session_store is not None:
            self._session_store.clear_sdk_session_id(session_key)
            entry.channel = ""
            entry.chat_id = ""

    def clear_tracking_state(
        self,
        session_key: str,
        *,
        sdk_session_id: str | None = None,
        clear_sdk_session_id: bool = False,
        clear_context: bool = False,
    ) -> None:
        entry = self._get_entry(session_key)
        if entry is not None:
            entry.client = None
            entry.model = ""
            entry.skills_version = None
            entry.commands = []
            entry.task_id = None
            entry.request_id = None
            entry.tasks.clear()
            if clear_context:
                entry.channel = ""
                entry.chat_id = ""

        self._legacy_clients.pop(session_key, None)
        self._legacy_last_used.pop(session_key, None)
        self._legacy_models.pop(session_key, None)
        self._legacy_skills_versions.pop(session_key, None)
        self._legacy_commands.pop(session_key, None)
        self._legacy_task_ids.pop(session_key, None)
        self._legacy_request_ids.pop(session_key, None)

        contexts = self._get_contexts()
        if clear_context:
            contexts.pop(session_key, None)

        resolved_sdk_session_id = sdk_session_id or self.resolve_sdk_session_id(session_key)
        if resolved_sdk_session_id:
            if clear_sdk_session_id:
                self._legacy_sdk_session_ids.pop(resolved_sdk_session_id, None)
                if self._use_session_store and self._session_store is not None:
                    self._session_store.clear_sdk_session_id(session_key)
            if clear_context and isinstance(contexts.get(resolved_sdk_session_id), tuple):
                contexts.pop(resolved_sdk_session_id, None)

    def detach_runtime_state(
        self,
        session_key: str,
        *,
        preserve_sdk_context: bool = True,
    ) -> Any | None:
        """Detach runtime state for a session and return the current client."""
        entry = self._get_entry(session_key)
        client = entry.client if entry is not None else self._legacy_clients.pop(session_key, None)

        self.clear_tracking_state(
            session_key,
            sdk_session_id=None,
            clear_sdk_session_id=not preserve_sdk_context,
            clear_context=not preserve_sdk_context,
        )

        if entry is not None:
            entry.last_used = time.time()

        return client

    def register_forked_session(
        self,
        new_session_key: str,
        new_sdk_session_id: str,
        original_context: tuple[str, str] | None,
    ) -> None:
        if original_context is not None:
            self.set_context(new_session_key, original_context[0], original_context[1])
        elif self._use_session_store and self._session_store is not None:
            self.get_or_create_session(new_session_key)
        else:
            # Deprecated compatibility mirror during SessionStore cutover.
            self._get_contexts()[new_session_key] = new_sdk_session_id

        if self._use_session_store and self._session_store is not None:
            self.get_or_create_session(new_session_key)
            self._session_store.set_sdk_session_id(new_session_key, new_sdk_session_id)
        else:
            # Deprecated compatibility mirror during SessionStore cutover.
            self._legacy_sdk_session_ids[new_sdk_session_id] = new_session_key

    def rollback_forked_session(
        self,
        new_session_key: str,
        new_sdk_session_id: str,
    ) -> None:
        if self._use_session_store and self._session_store is not None:
            entry = self._get_entry(new_session_key)
            if entry is not None:
                self._session_store.clear_sdk_session_id(new_session_key)
                entry.channel = ""
                entry.chat_id = ""
        else:
            contexts = self._get_contexts()
            contexts.pop(new_session_key, None)
            self._legacy_sdk_session_ids.pop(new_sdk_session_id, None)

    def enforce_legacy_context_limit(self, limit: int) -> None:
        if limit < 1:
            self._get_contexts().clear()
            self._legacy_sdk_session_ids.clear()
            return

        contexts = self._get_contexts()
        excess = len(contexts) - limit
        if excess <= 0:
            return

        for key in list(contexts.keys())[:excess]:
            contexts.pop(key, None)
            if key in self._legacy_sdk_session_ids:
                self._legacy_sdk_session_ids.pop(key, None)

    def list_context_keys(self) -> list[str]:
        return list(self._get_contexts().keys())

    def clear_all_contexts(self) -> None:
        self._get_contexts().clear()
        self._legacy_sdk_session_ids.clear()
        if self._use_session_store and self._session_store is not None:
            for session_key in self._session_store.list_keys():
                entry = self._session_store.get(session_key)
                if entry is None:
                    continue
                self._session_store.clear_sdk_session_id(session_key)
                entry.channel = ""
                entry.chat_id = ""

    def resolve_sdk_session_id(self, session_key: str) -> str | None:
        entry = self._get_entry(session_key)
        if entry is not None and entry.sdk_session_id:
            return entry.sdk_session_id

        legacy_value = self._get_contexts().get(session_key)
        if isinstance(legacy_value, str):
            return legacy_value

        for sdk_session_id, mapped_session_key in self._legacy_sdk_session_ids.items():
            if mapped_session_key == session_key:
                return sdk_session_id

        if self._sessions is not None:
            session = self._sessions.get(session_key)
            if session is not None:
                sdk_session_id = session.metadata.get("sdk_session_id")
                if sdk_session_id:
                    return sdk_session_id
        return None

    def get_model(self, session_key: str) -> str | None:
        entry = self._get_entry(session_key)
        return entry.model if entry is not None else self._legacy_models.get(session_key)

    def set_model(self, session_key: str, model: str | None) -> None:
        entry = self.get_or_create_session(session_key)
        if entry is not None:
            entry.model = model or ""
        self._legacy_models[session_key] = model

    def get_skills_version(self, session_key: str) -> str | None:
        entry = self._get_entry(session_key)
        return entry.skills_version if entry is not None else self._legacy_skills_versions.get(session_key)

    def set_skills_version(self, session_key: str, version: str | None) -> None:
        entry = self.get_or_create_session(session_key)
        if entry is not None:
            entry.skills_version = version
        self._legacy_skills_versions[session_key] = version

    def get_commands(self, session_key: str) -> list[str]:
        entry = self._get_entry(session_key)
        if entry is not None:
            return entry.commands
        return self._legacy_commands.get(session_key, [])

    def set_commands(self, session_key: str, commands: list[str]) -> None:
        entry = self.get_or_create_session(session_key)
        if entry is not None:
            entry.commands = commands
        self._legacy_commands[session_key] = commands

    def get_last_used(self, session_key: str) -> float | None:
        entry = self._get_entry(session_key)
        return entry.last_used if entry is not None else self._legacy_last_used.get(session_key)

    def touch(self, session_key: str) -> None:
        entry = self._get_entry(session_key)
        if entry is not None:
            entry.touch()
            self._legacy_last_used[session_key] = entry.last_used
            return
        self._legacy_last_used[session_key] = time.time()

    def get_task_id(self, session_key: str) -> str | None:
        entry = self._get_entry(session_key)
        return entry.task_id if entry is not None else self._legacy_task_ids.get(session_key)

    def set_task_id(self, session_key: str, task_id: str | None) -> None:
        entry = self.get_or_create_session(session_key)
        if entry is not None:
            entry.task_id = task_id
        if task_id is not None:
            self._legacy_task_ids[session_key] = task_id
        else:
            self._legacy_task_ids.pop(session_key, None)

    def get_request_id(self, session_key: str) -> str | None:
        entry = self._get_entry(session_key)
        return entry.request_id if entry is not None else self._legacy_request_ids.get(session_key)

    def set_request_id(self, session_key: str, request_id: str | None) -> None:
        entry = self.get_or_create_session(session_key)
        if entry is not None:
            entry.request_id = request_id
        if request_id is not None:
            self._legacy_request_ids[session_key] = request_id
        else:
            self._legacy_request_ids.pop(session_key, None)

    def get_client(self, session_key: str) -> Any | None:
        entry = self._get_entry(session_key)
        return entry.client if entry is not None else self._legacy_clients.get(session_key)

    def set_client(self, session_key: str, client: Any) -> None:
        entry = self.get_or_create_session(session_key)
        if entry is not None:
            entry.client = client
        self._legacy_clients[session_key] = client

    def has_client(self, session_key: str) -> bool:
        return self.get_client(session_key) is not None

    def list_client_session_keys(self) -> list[str]:
        if self._use_session_store and self._session_store is not None:
            return [
                session_key
                for session_key in self._session_store.list_keys()
                if (entry := self._session_store.get(session_key)) is not None and entry.client is not None
            ]
        return [session_key for session_key, client in self._legacy_clients.items() if client is not None]

    def get_client_last_used_map(self) -> dict[str, float]:
        if self._use_session_store and self._session_store is not None:
            return {
                session_key: entry.last_used
                for session_key in self._session_store.list_keys()
                if (entry := self._session_store.get(session_key)) is not None and entry.client is not None
            }
        return {
            session_key: last_used
            for session_key, last_used in self._legacy_last_used.items()
            if self._legacy_clients.get(session_key) is not None
        }

    def get_stale_client_session_keys(self, ttl_seconds: float, *, now: float | None = None) -> list[str]:
        cutoff = (time.time() if now is None else now) - ttl_seconds
        return [
            session_key
            for session_key, last_used in self.get_client_last_used_map().items()
            if last_used < cutoff
        ]

    def list_active_task_session_keys(self) -> list[str]:
        if self._use_session_store and self._session_store is not None:
            return [
                session_key
                for session_key in self._session_store.list_keys()
                if (entry := self._session_store.get(session_key)) is not None and entry.task_id
            ]
        return list(self._legacy_task_ids.keys())
