"""Claude SDK Agent Backend.

This backend uses the Claude Agent SDK to provide native Claude integration
with support for Anthropic and Anthropic-compatible providers (Aliyun Coding Plan, Alrun).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import inspect
import uuid
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from xbot.logging import get_logger

logger = get_logger(__name__)

from xbot.agent.backends.delegation import DelegationTrace
from xbot.agent.backends.client_lifecycle import ClientLifecycleManager
from xbot.agent.backends.message_converter import MessageConverter
from xbot.agent.backends.options_builder import OptionsBuilder
from xbot.agent.capabilities.catalog import CapabilityCatalog, canonical_tool_name
from xbot.agent.capabilities.policy import CapabilityPolicy
from xbot.agent.context.builder import ContextBuilder
from xbot.agent.capabilities.handoff import HandoffDecision, HandoffPolicy
from xbot.agent.memory.store import MemoryConsolidator
from xbot.agent.interaction.event_formatter import (
    format_compact_event,
    format_task_notification,
)
from xbot.agent.protocol import AgentBackend, AgentContext, AgentResponse
from xbot.agent.monitoring.trace import append_session_trace
from xbot.agent.tools.base import Tool
from xbot.config.provider_registry import get_provider_spec
from xbot.config.sdk_resolver import detect_provider_from_model, resolve_sdk_provider_and_model
from xbot.config.schema import AgentsConfig, ProviderConfig
from xbot.session.manager import SessionManager
from xbot.utils.helpers import detect_audio_mime, detect_image_mime
from xbot.utils.file_reader import FileType, classify_file, format_file_reference

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from claude_agent_sdk.types import (
        AgentDefinition as SDKAgentDefinition,
        AssistantMessage,
        ResultMessage,
        StreamEvent,
        SystemMessage,
        TaskNotificationMessage,
        TaskProgressMessage,
        TaskStartedMessage,
        UserMessage,
    )
    from xbot.agent.state.store import SessionEntry, SessionStore

# Try to import Claude SDK
try:
    from claude_agent_sdk import ClaudeSDKClient as _ClaudeSDKClient
    from claude_agent_sdk.types import (
        AgentDefinition as SDKAgentDefinition,
        AssistantMessage,
        ResultMessage,
        StreamEvent,
        SystemMessage,
        TaskNotificationMessage,
        TaskProgressMessage,
        TaskStartedMessage,
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
        UserMessage,
    )

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    logger.warning("claude-agent-sdk not installed. Claude SDK backend will not be available.")


# =============================================================================
# Main Backend Class
# =============================================================================


@dataclass
class ClientReleaseResult:
    outcome: str
    preserved_sdk_session: bool
    disconnect_attempts: int = 0
    force_kill_attempted: bool = False
    process_tracking_available: bool = False
    error_summary: str = ""

    @property
    def succeeded(self) -> bool:
        return self.outcome in {"noop", "disconnected", "killed"}


class ClaudeSDKBackend(AgentBackend):
    """Claude Agent SDK backend.

    This backend uses the official Claude Agent SDK for native Claude integration.

    Features:
    - Native Claude support with optimal performance
    - Anthropic Messages API compatible providers
    - MCP tool integration
    - Skills as MCP tools
    - Built-in subagent support
    """

    name = "claude_sdk"
    _MAX_QUERY_RETRIES = 3  # Max retries when SDK returns stale task notifications instead of ResultMessage

    # Default client pool constants (can be overridden by config)
    DEFAULT_MAX_CLIENTS = 100
    DEFAULT_CLIENT_TTL_SECONDS = 3600  # 1 hour TTL for idle clients
    DEFAULT_DISCONNECT_RETRIES = 2
    DEFAULT_CLIENT_CLEANUP_INTERVAL_SECONDS = 60
    DEFAULT_CLIENT_DISCONNECT_TIMEOUT_SECONDS = 10.0

    def __init__(self):
        """Initialize the backend."""
        if not SDK_AVAILABLE:
            raise ImportError(
                "claude-agent-sdk is not installed. "
                "Install it with: pip install claude-agent-sdk"
            )

        self.sdk_config: Any = None
        self._shared_resources: dict[str, Any] = {}
        self._skill_converter: Any = None
        self._tool_adapter: Any = None
        self._capabilities: CapabilityCatalog | None = None
        self._clients: dict[str, ClaudeSDKClient] = {}
        self._clients_lock = asyncio.Lock()
        self._client_last_used: dict[str, float] = {}  # Track last usage time for TTL cleanup
        self._client_models: dict[str, str] = {}  # Track model used for each client (for model change detection)
        self._active_task_ids: dict[str, str] = {}
        self._active_request_ids: dict[str, str] = {}  # Track request IDs for request-response validation
        self._session_commands: dict[str, list[str]] = {}
        self._delegation_traces: list[DelegationTrace] = []
        self._delegation_traces_lock = asyncio.Lock()
        self.tools: Any = None
        self.sessions: SessionManager | None = None
        self.memory_consolidator: MemoryConsolidator | None = None
        self._context_builder: ContextBuilder | None = None
        self._handoff_policy: HandoffPolicy | None = None
        self._capability_policy: CapabilityPolicy | None = None
        self._options_builder: OptionsBuilder | None = None
        self._message_converter: MessageConverter | None = None
        self._permission_handler: Any = None
        self._skill_manager: Any = None
        self._client_skills_versions: dict[str, str | None] = {}  # Track skills version per client

        # SessionStore reference for unified state management
        self._session_store: "SessionStore | None" = None
        self._use_session_store: bool = False  # Feature flag for gradual migration
        self._client_lifecycle = ClientLifecycleManager()
        self._client_scavenger_task: asyncio.Task | None = None
        self._fallback_release_diagnostics: dict[str, Any] = {
            "counts": {"disconnected": 0, "killed": 0, "leaked": 0},
            "last_failure": None,
        }

    @property
    def max_clients(self) -> int:
        """Get max clients from config or use default."""
        if self.sdk_config and hasattr(self.sdk_config, "max_clients"):
            return self.sdk_config.max_clients
        return self.DEFAULT_MAX_CLIENTS

    @property
    def client_ttl_seconds(self) -> int:
        """Get client TTL from config or use default."""
        if self.sdk_config and hasattr(self.sdk_config, "client_idle_ttl_seconds"):
            return self.sdk_config.client_idle_ttl_seconds
        if self.sdk_config and hasattr(self.sdk_config, "client_ttl_seconds"):
            return self.sdk_config.client_ttl_seconds
        return self.DEFAULT_CLIENT_TTL_SECONDS

    @property
    def disconnect_retries(self) -> int:
        """Get disconnect retries from config or use default."""
        if self.sdk_config and hasattr(self.sdk_config, "client_disconnect_max_retries"):
            return self.sdk_config.client_disconnect_max_retries
        if self.sdk_config and hasattr(self.sdk_config, "client_disconnect_retries"):
            return self.sdk_config.client_disconnect_retries
        return self.DEFAULT_DISCONNECT_RETRIES

    @property
    def client_lifecycle_enabled(self) -> bool:
        if self.sdk_config and hasattr(self.sdk_config, "client_lifecycle_enabled"):
            return bool(self.sdk_config.client_lifecycle_enabled)
        return True

    @property
    def client_scavenger_enabled(self) -> bool:
        if self.sdk_config and hasattr(self.sdk_config, "client_scavenger_enabled"):
            return bool(self.sdk_config.client_scavenger_enabled)
        return True

    @property
    def client_cleanup_interval_seconds(self) -> int:
        if self.sdk_config and hasattr(self.sdk_config, "client_cleanup_interval_seconds"):
            return int(self.sdk_config.client_cleanup_interval_seconds)
        return self.DEFAULT_CLIENT_CLEANUP_INTERVAL_SECONDS

    @property
    def client_disconnect_timeout_seconds(self) -> float:
        if self.sdk_config and hasattr(self.sdk_config, "client_disconnect_timeout_seconds"):
            return float(self.sdk_config.client_disconnect_timeout_seconds)
        return self.DEFAULT_CLIENT_DISCONNECT_TIMEOUT_SECONDS

    @property
    def client_force_kill_enabled(self) -> bool:
        if self.sdk_config and hasattr(self.sdk_config, "client_force_kill_enabled"):
            return bool(self.sdk_config.client_force_kill_enabled)
        return True

    @property
    def ephemeral_immediate_release_enabled(self) -> bool:
        if self.sdk_config and hasattr(self.sdk_config, "ephemeral_immediate_release_enabled"):
            return bool(self.sdk_config.ephemeral_immediate_release_enabled)
        return True

    @property
    def strict_process_tracking_required(self) -> bool:
        if self.sdk_config and hasattr(self.sdk_config, "strict_process_tracking_required"):
            return bool(self.sdk_config.strict_process_tracking_required)
        return False

    # === SessionStore helper methods ===

    def _uses_session_store(self) -> bool:
        """Return whether SessionStore-backed state is active."""
        return bool(getattr(self, "_use_session_store", False) and getattr(self, "_session_store", None))

    def _get_session_contexts(self) -> dict[str, Any]:
        """Return legacy session context mapping."""
        return getattr(self, "_shared_resources", {}).setdefault("_session_contexts", {})

    def _get_sdk_session_index(self) -> dict[str, str]:
        """Return legacy reverse SDK session index."""
        if not hasattr(self, "_sdk_session_ids"):
            self._sdk_session_ids = {}
        return self._sdk_session_ids

    def _get_entry(self, session_key: str) -> "SessionEntry | None":
        """Get SessionEntry from SessionStore or return None."""
        if self._uses_session_store():
            return self._session_store.get(session_key)
        return None

    def _get_or_create_entry(self, session_key: str) -> "SessionEntry":
        """Get or create SessionEntry from SessionStore."""
        if self._uses_session_store():
            return self._session_store.get_or_create(session_key)
        # Fallback - shouldn't happen when _use_session_store is True
        raise RuntimeError("SessionStore not available")

    def _get_model_from_entry(self, session_key: str) -> str | None:
        """Get model name from SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            return entry.model if entry else None
        return self._client_models.get(session_key)

    def _set_model_in_entry(self, session_key: str, model: str) -> None:
        """Set model name in SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_or_create_entry(session_key)
            entry.model = model
        else:
            self._client_models[session_key] = model

    def _get_skills_version_from_entry(self, session_key: str) -> str | None:
        """Get skills version from SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            return entry.skills_version if entry else None
        return self._client_skills_versions.get(session_key)

    def _set_skills_version_in_entry(self, session_key: str, version: str | None) -> None:
        """Set skills version in SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_or_create_entry(session_key)
            entry.skills_version = version
        else:
            self._client_skills_versions[session_key] = version

    def _get_commands_from_entry(self, session_key: str) -> list[str]:
        """Get commands list from SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            return entry.commands if entry else []
        return self._session_commands.get(session_key, [])

    def _set_commands_in_entry(self, session_key: str, commands: list[str]) -> None:
        """Set commands list in SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_or_create_entry(session_key)
            entry.commands = commands
        else:
            self._session_commands[session_key] = commands

    def _get_last_used_from_entry(self, session_key: str) -> float | None:
        """Get last used timestamp from SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            return entry.last_used if entry else None
        return self._client_last_used.get(session_key)

    def _touch_entry(self, session_key: str) -> None:
        """Update last_used timestamp in SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            if entry:
                entry.touch()
        else:
            self._client_last_used[session_key] = time.time()

    def _get_task_id_from_entry(self, session_key: str) -> str | None:
        """Get active task ID from SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            return entry.task_id if entry else None
        return self._active_task_ids.get(session_key)

    def _set_task_id_in_entry(self, session_key: str, task_id: str | None) -> None:
        """Set active task ID in SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_or_create_entry(session_key)
            entry.task_id = task_id
        else:
            if task_id is not None:
                self._active_task_ids[session_key] = task_id
            else:
                self._active_task_ids.pop(session_key, None)

    def _get_request_id_from_entry(self, session_key: str) -> str | None:
        """Get request ID from SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            return entry.request_id if entry else None
        return self._active_request_ids.get(session_key)

    def _set_request_id_in_entry(self, session_key: str, request_id: str | None) -> None:
        """Set request ID in SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_or_create_entry(session_key)
            entry.request_id = request_id
        else:
            if request_id is not None:
                self._active_request_ids[session_key] = request_id
            else:
                self._active_request_ids.pop(session_key, None)

    def _get_client_from_entry(self, session_key: str) -> "ClaudeSDKClient | None":
        """Get SDK client from SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            return entry.client if entry else None
        return self._clients.get(session_key)

    def _set_client_in_entry(self, session_key: str, client: "ClaudeSDKClient") -> None:
        """Set SDK client in SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_or_create_entry(session_key)
            entry.client = client
        else:
            self._clients[session_key] = client

    def _has_client_in_entry(self, session_key: str) -> bool:
        """Check if session has client in SessionEntry or legacy dict."""
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            return entry is not None and entry.client is not None
        return session_key in self._clients

    # === SDK session ID and context helpers ===

    def _get_sdk_session_id_from_entry(self, session_key: str) -> str | None:
        """Get SDK session ID from SessionEntry or legacy mappings."""
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            return entry.sdk_session_id if entry else None

        session_contexts = self._get_session_contexts()
        value = session_contexts.get(session_key)
        if isinstance(value, str):
            return value

        for sdk_session_id, mapped_session_key in self._get_sdk_session_index().items():
            if mapped_session_key == session_key:
                return sdk_session_id

        return None

    async def _set_sdk_session_id_in_entry(
        self, session_key: str, sdk_session_id: str | None
    ) -> None:
        """Set SDK session ID in SessionEntry and update index.

        Also syncs to session.metadata for persistence.
        """
        if self._uses_session_store():
            self._get_or_create_entry(session_key)
            self._session_store.set_sdk_session_id(session_key, sdk_session_id)

            # Sync to session.metadata for persistence
            if self.sessions:
                session = self.sessions.get(session_key)
                if session:
                    if sdk_session_id:
                        session.metadata["sdk_session_id"] = sdk_session_id
                    else:
                        session.metadata.pop("sdk_session_id", None)
                    self.sessions.save(session)
            await self._client_lifecycle.update_sdk_session_id(session_key, sdk_session_id)
            return

        session_contexts = self._get_session_contexts()
        sdk_session_ids = self._get_sdk_session_index()

        old_sdk_id = self._get_sdk_session_id_from_entry(session_key)
        if old_sdk_id and old_sdk_id != sdk_session_id:
            sdk_session_ids.pop(old_sdk_id, None)
            if session_contexts.get(session_key) == old_sdk_id:
                session_contexts.pop(session_key, None)
            if isinstance(session_contexts.get(old_sdk_id), tuple):
                session_contexts.pop(old_sdk_id, None)

        if sdk_session_id:
            session_contexts[session_key] = sdk_session_id
            sdk_session_ids[sdk_session_id] = session_key
        else:
            if session_contexts.get(session_key) == old_sdk_id or isinstance(session_contexts.get(session_key), str):
                session_contexts.pop(session_key, None)

        if self.sessions:
            session = self.sessions.get(session_key)
            if session:
                if sdk_session_id:
                    session.metadata["sdk_session_id"] = sdk_session_id
                else:
                    session.metadata.pop("sdk_session_id", None)
                self.sessions.save(session)
        await self._client_lifecycle.update_sdk_session_id(session_key, sdk_session_id)

    def _get_context_by_session_key(self, session_key: str) -> tuple[str, str] | None:
        """Get (channel, chat_id) context by session_key.

        Uses SessionStore if available, falls back to _session_contexts.
        """
        if self._uses_session_store():
            entry = self._get_entry(session_key)
            if entry and entry.channel and entry.chat_id:
                return (entry.channel, entry.chat_id)

        # Fallback to _session_contexts
        session_contexts = self._get_session_contexts()
        result = session_contexts.get(session_key)
        return result if isinstance(result, tuple) else None

    def _get_context_by_sdk_id(self, sdk_session_id: str) -> tuple[str, str] | None:
        """Get (channel, chat_id) context by SDK session ID.

        Uses SessionStore's _sdk_id_index if available, falls back to _session_contexts.
        """
        if self._uses_session_store():
            entry = self._session_store.get_by_sdk_id(sdk_session_id)
            if entry and entry.channel and entry.chat_id:
                return (entry.channel, entry.chat_id)

        # Fallback to _session_contexts
        session_contexts = self._get_session_contexts()
        result = session_contexts.get(sdk_session_id)
        return result if isinstance(result, tuple) else None

    def _resolve_compact_notification_target(
        self, session_ref: str
    ) -> tuple[str, str, str] | None:
        """Resolve compact-hook target from either session_key or SDK session_id."""
        context = self._get_context_by_session_key(session_ref)
        if context is not None:
            channel, chat_id = context
            return (session_ref, channel, chat_id)

        sdk_context = self._get_context_by_sdk_id(session_ref)
        if sdk_context is None:
            return None

        channel, chat_id = sdk_context
        if self._uses_session_store():
            entry = self._session_store.get_by_sdk_id(session_ref)
            if entry is not None:
                for session_key in self._session_store.list_keys():
                    if self._session_store.get(session_key) is entry:
                        return (session_key, channel, chat_id)

        mapped_session_key = self._get_sdk_session_index().get(session_ref)
        if isinstance(mapped_session_key, str):
            return (mapped_session_key, channel, chat_id)

        session_contexts = self._get_session_contexts()
        for session_key, value in session_contexts.items():
            if value == session_ref:
                return (session_key, channel, chat_id)

        return None

    def _set_context_in_entry(self, session_key: str, channel: str, chat_id: str) -> None:
        """Set channel and chat_id in SessionEntry.

        Also updates _session_contexts for backward compatibility during migration.
        """
        if self._uses_session_store():
            entry = self._get_or_create_entry(session_key)
            entry.channel = channel
            entry.chat_id = chat_id

        # Also update _session_contexts for backward compatibility
        session_contexts = self._get_session_contexts()
        session_contexts[session_key] = (channel, chat_id)

    def _set_sdk_context_mapping(self, sdk_session_id: str, channel: str, chat_id: str) -> None:
        """Set SDK session ID to context mapping.

        In SessionStore mode, updates the entry's sdk_session_id.
        Also updates _session_contexts for backward compatibility.
        """
        if self._uses_session_store():
            # Find entry by context and set sdk_session_id
            for session_key in self._session_store.list_keys():
                entry = self._session_store.get(session_key)
                if entry and entry.channel == channel and entry.chat_id == chat_id:
                    # Use sync version since we're in sync context
                    old_sdk_id = entry.sdk_session_id
                    if old_sdk_id and old_sdk_id != sdk_session_id:
                        self._session_store._sdk_id_index.pop(old_sdk_id, None)
                    entry.sdk_session_id = sdk_session_id
                    self._session_store._sdk_id_index[sdk_session_id] = session_key
                    break

        # Also update _session_contexts for backward compatibility
        session_contexts = self._get_session_contexts()
        session_contexts[sdk_session_id] = (channel, chat_id)

    def _resolve_sdk_session_id(self, session_key: str) -> str | None:
        """Resolve SDK session ID from entry, metadata, or legacy mappings."""
        sdk_session_id = self._get_sdk_session_id_from_entry(session_key)
        if sdk_session_id:
            return sdk_session_id

        session_contexts = self._get_session_contexts()
        legacy_sdk_id = session_contexts.get(session_key)
        if isinstance(legacy_sdk_id, str):
            return legacy_sdk_id

        for candidate_sdk_id, mapped_session_key in self._get_sdk_session_index().items():
            if mapped_session_key == session_key:
                return candidate_sdk_id

        if self.sessions:
            session = self.sessions.get(session_key)
            if session:
                sdk_session_id = session.metadata.get("sdk_session_id")
                if sdk_session_id:
                    return sdk_session_id

        return None

    def _is_ephemeral_session(self, session_key: str) -> bool:
        return session_key.startswith("cron:") or session_key == "heartbeat"

    def _can_cleanup_session(self, session_key: str) -> bool:
        if self._get_task_id_from_entry(session_key):
            return False
        if self._get_request_id_from_entry(session_key):
            return False
        bus = self._shared_resources.get("bus")
        if bus is not None:
            with contextlib.suppress(Exception):
                if bus.get_pending_request_for_session(session_key):
                    return False
            with contextlib.suppress(Exception):
                if bus.get_pending_interaction_for_session(session_key):
                    return False
        return True

    def _extract_process_tracking(self, client: Any) -> tuple[int | None, Any | None, bool]:
        candidates = [
            getattr(client, "process", None),
            getattr(client, "_process", None),
            getattr(client, "proc", None),
            getattr(client, "_proc", None),
        ]
        transport = getattr(client, "_transport", None)
        if transport is not None:
            candidates.extend([
                getattr(transport, "process", None),
                getattr(transport, "_process", None),
            ])

        for process in candidates:
            if process is None:
                continue
            pid = getattr(process, "pid", None)
            if isinstance(pid, int):
                return pid, process, True
        return None, None, False

    async def _register_managed_client(self, session_key: str, client: Any) -> None:
        pid, process_handle, tracked = self._extract_process_tracking(client)
        if self.strict_process_tracking_required and not tracked:
            logger.warning("Process tracking unavailable for managed Claude client %s", session_key)
        await self._client_lifecycle.register(
            session_key,
            client,
            sdk_session_id=self._get_sdk_session_id_from_entry(session_key),
            pid=pid,
            process_handle=process_handle,
            process_tracking_available=tracked,
            is_ephemeral=self._is_ephemeral_session(session_key),
        )

    async def _run_client_scavenger_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.client_cleanup_interval_seconds)
                await self._run_client_scavenger_iteration()
        except asyncio.CancelledError:
            raise

    async def _run_client_scavenger_iteration(self) -> None:
        if not self.client_lifecycle_enabled or not self.client_scavenger_enabled:
            return
        idle_candidates = set(
            await self._client_lifecycle.list_idle_candidates(
                idle_ttl_seconds=self.client_ttl_seconds,
                can_cleanup=self._can_cleanup_session,
            )
        )
        now = time.time()
        if self._uses_session_store():
            for session_key in self._session_store.list_keys():
                entry = self._session_store.get(session_key)
                if (
                    entry is not None
                    and entry.client is not None
                    and now - entry.last_used > self.client_ttl_seconds
                    and self._can_cleanup_session(session_key)
                ):
                    idle_candidates.add(session_key)
        else:
            for session_key, last_used in self._client_last_used.items():
                if (
                    session_key in self._clients
                    and now - last_used > self.client_ttl_seconds
                    and self._can_cleanup_session(session_key)
                ):
                    idle_candidates.add(session_key)

        for session_key in idle_candidates:
            await self.release_client(session_key, reason="idle_ttl")

    def _ensure_client_scavenger_started(self) -> None:
        if not self.client_lifecycle_enabled or not self.client_scavenger_enabled:
            return
        if self._client_scavenger_task is not None and not self._client_scavenger_task.done():
            return
        self._client_scavenger_task = asyncio.create_task(self._run_client_scavenger_loop())

    async def _stop_client_scavenger(self) -> None:
        if self._client_scavenger_task is None:
            return
        task = self._client_scavenger_task
        self._client_scavenger_task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _attempt_disconnect_client(
        self,
        client: Any,
        session_key: str,
        *,
        context: str,
        retries: int | None = None,
    ) -> tuple[bool, int, str]:
        if client is None:
            return True, 0, ""
        attempts = self.disconnect_retries if retries is None else retries
        for attempt in range(attempts + 1):
            try:
                await asyncio.wait_for(client.disconnect(), timeout=self.client_disconnect_timeout_seconds)
                return True, attempt + 1, ""
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if attempt >= attempts:
                    logger.warning(
                        "Managed disconnect failed for session %s (%s): %s",
                        session_key,
                        context,
                        e,
                    )
                    return False, attempt + 1, str(e)
                await asyncio.sleep(0.1)
        return False, attempts + 1, "disconnect attempts exhausted"

    async def _disconnect_client_with_timeout(
        self,
        client: Any,
        session_key: str,
        *,
        context: str,
        retries: int | None = None,
    ) -> bool:
        disconnected, _attempts, _error = await self._attempt_disconnect_client(
            client,
            session_key,
            context=context,
            retries=retries,
        )
        return disconnected

    async def _force_kill_process(self, session_key: str, client: Any | None = None) -> bool:
        record = await self._client_lifecycle.get(session_key)
        process = record.process_handle if record is not None else None
        if process is None and client is not None:
            _pid, process, _tracked = self._extract_process_tracking(client)
        if process is None:
            return False
        try:
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                terminate()
            wait = getattr(process, "wait", None)
            if callable(wait):
                result = wait()
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, timeout=1.0)
            await self._client_lifecycle.mark_killed(session_key)
            return True
        except Exception:
            kill = getattr(process, "kill", None)
            if callable(kill):
                with contextlib.suppress(Exception):
                    kill()
                    await self._client_lifecycle.mark_killed(session_key)
                    return True
        return False

    def _should_preserve_sdk_context(
        self,
        session_key: str,
        preserve_sdk_context: bool | None,
    ) -> bool:
        if preserve_sdk_context is not None:
            return preserve_sdk_context
        return not self._is_ephemeral_session(session_key)

    def _record_fallback_release_result(
        self,
        session_key: str,
        reason: str,
        result: ClientReleaseResult,
    ) -> None:
        counts = self._fallback_release_diagnostics.setdefault(
            "counts",
            {"disconnected": 0, "killed": 0, "leaked": 0},
        )
        if result.outcome in counts:
            counts[result.outcome] += 1
        if result.outcome == "leaked":
            self._fallback_release_diagnostics["last_failure"] = {
                "session_key": session_key,
                "reason": reason,
                "error": result.error_summary,
                "preserved_sdk_session": result.preserved_sdk_session,
            }

    def _log_release_result(
        self,
        session_key: str,
        reason: str,
        result: ClientReleaseResult,
    ) -> None:
        logger_method = logger.info if result.succeeded else logger.error
        logger_method(
            "[Client Release] session=%s, reason=%s, outcome=%s, preserved_sdk_session=%s, "
            "disconnect_attempts=%d, force_kill_attempted=%s, process_tracking_available=%s, error=%s",
            session_key,
            reason,
            result.outcome,
            result.preserved_sdk_session,
            result.disconnect_attempts,
            result.force_kill_attempted,
            result.process_tracking_available,
            result.error_summary or "none",
        )

    async def release_client(
        self,
        session_key: str,
        *,
        reason: str,
        preserve_sdk_context: bool | None = None,
    ) -> bool:
        preserve_sdk_context = self._should_preserve_sdk_context(
            session_key,
            preserve_sdk_context,
        )
        if not self.client_lifecycle_enabled:
            client = self._remove_client_state(
                session_key,
                preserve_sdk_context=preserve_sdk_context,
            )
            if client is None:
                result = ClientReleaseResult(
                    outcome="noop",
                    preserved_sdk_session=preserve_sdk_context,
                )
                self._record_fallback_release_result(session_key, reason, result)
                self._log_release_result(session_key, reason, result)
                return True

            disconnected, disconnect_attempts, error_summary = await self._attempt_disconnect_client(
                client,
                session_key,
                context=reason,
                retries=self.disconnect_retries,
            )
            if disconnected:
                result = ClientReleaseResult(
                    outcome="disconnected",
                    preserved_sdk_session=preserve_sdk_context,
                    disconnect_attempts=disconnect_attempts,
                )
                self._record_fallback_release_result(session_key, reason, result)
                self._log_release_result(session_key, reason, result)
                return True

            process_tracking_available = self._extract_process_tracking(client)[2]
            if self.client_force_kill_enabled and await self._force_kill_process(session_key, client):
                result = ClientReleaseResult(
                    outcome="killed",
                    preserved_sdk_session=preserve_sdk_context,
                    disconnect_attempts=disconnect_attempts,
                    force_kill_attempted=True,
                    process_tracking_available=process_tracking_available,
                    error_summary=error_summary,
                )
                self._record_fallback_release_result(session_key, reason, result)
                self._log_release_result(session_key, reason, result)
                return True

            result = ClientReleaseResult(
                outcome="leaked",
                preserved_sdk_session=preserve_sdk_context,
                disconnect_attempts=disconnect_attempts,
                force_kill_attempted=self.client_force_kill_enabled,
                process_tracking_available=process_tracking_available,
                error_summary=error_summary,
            )
            self._record_fallback_release_result(session_key, reason, result)
            self._log_release_result(session_key, reason, result)
            return False

        record = await self._client_lifecycle.get(session_key)
        if record is None:
            client = self._get_client_from_entry(session_key)
            if client is not None:
                await self._register_managed_client(session_key, client)
                record = await self._client_lifecycle.get(session_key)
        if record is None:
            return True

        record = await self._client_lifecycle.begin_disconnect(session_key)
        if record is None:
            latest = await self._client_lifecycle.get(session_key)
            if latest is None:
                return True
            return latest.disconnect_state in {"disconnected", "killed"}

        async with self._clients_lock:
            client = self._remove_client_state(
                session_key,
                preserve_sdk_context=preserve_sdk_context,
            )
        if client is None:
            client = record.client

        if client is None:
            await self._client_lifecycle.mark_disconnected(session_key)
            result = ClientReleaseResult(
                outcome="noop",
                preserved_sdk_session=preserve_sdk_context,
                process_tracking_available=bool(record.process_tracking_available),
            )
            self._log_release_result(session_key, reason, result)
            return True

        disconnected, disconnect_attempts, error_summary = await self._attempt_disconnect_client(
            client,
            session_key,
            context=reason,
            retries=self.disconnect_retries,
        )
        if disconnected:
            await self._client_lifecycle.mark_disconnected(session_key)
            on_cleanup = self._shared_resources.get("on_backend_client_cleanup")
            if on_cleanup:
                with contextlib.suppress(Exception):
                    await on_cleanup(session_key)
            result = ClientReleaseResult(
                outcome="disconnected",
                preserved_sdk_session=preserve_sdk_context,
                disconnect_attempts=disconnect_attempts,
                process_tracking_available=bool(record.process_tracking_available),
            )
            self._log_release_result(session_key, reason, result)
            return True

        await self._client_lifecycle.mark_leaked(session_key)
        if self.client_force_kill_enabled and await self._force_kill_process(session_key, client):
            result = ClientReleaseResult(
                outcome="killed",
                preserved_sdk_session=preserve_sdk_context,
                disconnect_attempts=disconnect_attempts,
                force_kill_attempted=True,
                process_tracking_available=bool(record.process_tracking_available),
                error_summary=error_summary,
            )
            self._log_release_result(session_key, reason, result)
            return True
        result = ClientReleaseResult(
            outcome="leaked",
            preserved_sdk_session=preserve_sdk_context,
            disconnect_attempts=disconnect_attempts,
            force_kill_attempted=self.client_force_kill_enabled,
            process_tracking_available=bool(record.process_tracking_available),
            error_summary=error_summary,
        )
        self._log_release_result(session_key, reason, result)
        return False

    async def _finalize_detached_client_cleanup(
        self,
        session_key: str,
        client: Any,
        *,
        reason: str,
    ) -> None:
        disconnected, _disconnect_attempts, _error_summary = await self._attempt_disconnect_client(
            client,
            session_key,
            context=reason,
            retries=self.disconnect_retries,
        )
        if disconnected:
            await self._client_lifecycle.mark_disconnected_if_current(session_key, client)
        else:
            leaked = await self._client_lifecycle.mark_leaked_if_current(session_key, client)
            if leaked is not None and self.client_force_kill_enabled:
                await self._force_kill_process(session_key)

    def get_client_lifecycle_diagnostics(self) -> dict[str, Any]:
        diagnostics = self._client_lifecycle.snapshot_sync()
        diagnostics["fallback"] = {
            "counts": dict(self._fallback_release_diagnostics.get("counts", {})),
            "last_failure": self._fallback_release_diagnostics.get("last_failure"),
        }
        return diagnostics

    def get_client_lifecycle_snapshot(self, session_key: str | None = None) -> dict[str, Any]:
        snapshot = self._client_lifecycle.snapshot_sync()
        if session_key is None:
            return snapshot
        return snapshot["clients"].get(session_key, {})

    async def forget_client_lifecycle(self, session_key: str) -> None:
        await self._client_lifecycle.remove(session_key)

    def _clear_legacy_tracking_state(self, session_key: str, sdk_session_id: str | None) -> None:
        """Clear legacy tracking dictionaries for a session."""
        self._clients.pop(session_key, None)
        self._client_last_used.pop(session_key, None)
        self._client_models.pop(session_key, None)
        self._client_skills_versions.pop(session_key, None)
        self._session_commands.pop(session_key, None)
        self._active_task_ids.pop(session_key, None)
        self._active_request_ids.pop(session_key, None)

        session_contexts = self._get_session_contexts()
        session_contexts.pop(session_key, None)

        if sdk_session_id:
            self._get_sdk_session_index().pop(sdk_session_id, None)
            if isinstance(session_contexts.get(sdk_session_id), tuple):
                session_contexts.pop(sdk_session_id, None)

    async def _clear_sdk_session_state(
        self,
        session_key: str,
        sdk_session_id: str | None,
        *,
        persist_metadata: bool = True,
    ) -> None:
        """Clear SDK session state from SessionStore or legacy dicts."""
        if self._uses_session_store():
            entry = self._get_or_create_entry(session_key)
            entry.client = None
            entry.model = ""
            entry.skills_version = None
            entry.commands = []
            entry.task_id = None
            entry.request_id = None
            entry.tasks.clear()

        self._clear_legacy_tracking_state(session_key, sdk_session_id)

        if persist_metadata:
            await self._set_sdk_session_id_in_entry(session_key, None)
            if self.sessions:
                session = self.sessions.get(session_key)
                if session:
                    session.metadata.pop("sdk_session_id", None)
                    self.sessions.save(session)
        else:
            if self._uses_session_store():
                entry = self._get_entry(session_key)
                if entry:
                    self._session_store.clear_sdk_session_id(session_key)

    async def initialize(self, config: AgentsConfig, shared_resources: dict[str, Any]) -> None:
        """Initialize the backend.

        Args:
            config: Agent configuration
            shared_resources: Shared resources

        Raises:
            ValueError: If provider is not compatible with Claude SDK
        """
        self._shared_resources = shared_resources
        self.sdk_config = config.claude_sdk
        self.sessions = shared_resources.get("session_manager") or SessionManager(
            Path(shared_resources.get("workspace", config.defaults.workspace))
        )
        workspace_path = Path(shared_resources.get("workspace", config.defaults.workspace))
        runtime_config = shared_resources.get("config")
        memory_config = getattr(getattr(runtime_config, "tools", None), "memory", None)
        memory_provider = getattr(memory_config, "provider", "file")
        use_reme = memory_provider == "reme"
        enable_vector_search = bool(getattr(memory_config, "enable_vector_search", False))
        llm_model = getattr(memory_config, "llm_model", None)
        llm_config = {"model_name": llm_model} if llm_model else None

        self._context_builder = ContextBuilder(
            workspace_path,
            use_reme=use_reme,
            llm_config=llm_config,
            enable_vector_search=enable_vector_search,
        )
        self._capabilities = CapabilityCatalog(workspace_path)
        self._handoff_policy = HandoffPolicy(self.sdk_config.agents if self.sdk_config else None)
        self._capability_policy = CapabilityPolicy(
            self._capabilities,
            mcp_servers=shared_resources.get("config").tools.mcp_servers if shared_resources.get("config") else None,
        )

        # Validate provider compatibility
        provider_name = config.defaults.provider
        if provider_name != "auto":
            spec = get_provider_spec(provider_name)
            if not spec:
                from xbot.exceptions import ProviderConfigError
                raise ProviderConfigError(
                    f"Unknown provider: {provider_name}",
                    details={"provider": provider_name},
                )
            if not spec.supported_by_sdk:
                from xbot.exceptions import ProviderNotSupportedError
                raise ProviderNotSupportedError(
                    f"Provider '{provider_name}' is not compatible with Claude SDK Agent",
                    details={
                        "provider": provider_name,
                        "supported_providers": ["anthropic", "aliyun-codingplan", "alrun"],
                    },
                )

        # Initialize skill converter
        try:
            from xbot.agent.capabilities.skill_to_mcp import SkillToMCPConverter
            workspace = shared_resources.get("workspace", config.defaults.workspace)
            self._skill_converter = SkillToMCPConverter(workspace)
        except ImportError:
            logger.warning("SkillToMCPConverter not available")

        # Initialize SkillManager for hot-reload and Python skill support
        try:
            from xbot.agent.capabilities.skill_manager import SkillManager
            self._skill_manager = SkillManager(workspace_path)
            # Replace the ContextBuilder's skills_loader with the one managed by SkillManager
            if self._context_builder:
                self._context_builder.skills = self._skill_manager.skills_loader
            logger.info("[Backend] SkillManager initialized, version=%s", self._skill_manager.version)
        except Exception as e:
            logger.warning("SkillManager not available: %s", e)

        # Initialize tool adapter
        try:
            from xbot.agent.capabilities.tool_adapter import ToolAdapter
            tools_config = shared_resources.get("tools_config")
            # Pass memory_store from ContextBuilder to ToolAdapter
            memory_store = self._context_builder.memory if self._context_builder else None

            # Create skill loading progress callback for CLI/Channel visibility
            skill_progress_callback = self._create_skill_progress_callback(shared_resources)

            self._tool_adapter = ToolAdapter(
                workspace=shared_resources.get("workspace", config.defaults.workspace),
                tools_config=tools_config,
                shared_resources={**shared_resources, "model": config.defaults.model, "memory_store": memory_store},
                skills_loader=self._skill_manager.skills_loader if self._skill_manager else (self._context_builder.skills if self._context_builder else None),
                skill_progress_callback=skill_progress_callback,
            )
            self.tools = self._tool_adapter

            # Sync initial Python skill tools
            if self._skill_manager:
                self._skill_manager.sync_tools_to_adapter(self._tool_adapter)
        except ImportError:
            logger.warning("ToolAdapter not available")

        # Initialize memory consolidator (use backend directly, no separate provider)
        if self.sessions and self._context_builder:
            self.memory_consolidator = MemoryConsolidator(
                workspace=Path(shared_resources.get("workspace", config.defaults.workspace)),
                backend=self,
                sessions=self.sessions,
                context_window_tokens=config.defaults.context_window_tokens,
                build_messages=self._context_builder.build_messages,
                get_tool_definitions=self._get_tool_definitions,
                memory_store=self._context_builder.memory,
            )

        # Initialize permission handler
        self._permission_handler = shared_resources.get("permission_handler")
        if self._permission_handler is None:
            # Create default permission handler based on mode
            bus = shared_resources.get("bus")
            permission_config = getattr(self.sdk_config, "permission", None) or {}
            enabled = getattr(permission_config, "enabled", True)

            if enabled:
                from xbot.agent.interaction.permission import create_permission_handler

                if bus is not None:
                    # Channel mode (gateway)
                    self._permission_handler = create_permission_handler(
                        mode="channel",
                        bus=bus,
                        timeout=getattr(permission_config, "timeout", 300.0),
                        auto_approve_safe_tools=getattr(permission_config, "auto_approve_safe_tools", True),
                    )
                    logger.info("Permission handler initialized for channel mode")
                else:
                    # CLI mode
                    self._permission_handler = create_permission_handler(
                        mode="cli",
                        auto_approve_safe_tools=getattr(permission_config, "auto_approve_safe_tools", True),
                    )
                    logger.info("Permission handler initialized for CLI mode")

        # Initialize helpers
        self._options_builder = OptionsBuilder(
            shared_resources=self._shared_resources,
            sdk_config=self.sdk_config,
            skill_converter=self._skill_converter,
            tool_adapter=self._tool_adapter,
            sessions=self.sessions,
            context_builder=self._context_builder,
            handoff_policy=self._handoff_policy,
            capability_policy=self._capability_policy,
            permission_handler=self._permission_handler,
        )
        
        self._message_converter = MessageConverter(
            handoff_policy=self._handoff_policy,
            capabilities=self._capabilities,
            config=self._shared_resources.get("config"),
        )

        # Get SessionStore reference for unified state management
        self._session_store = shared_resources.get("session_store")
        if self._session_store:
            self._use_session_store = True
            logger.info("Backend using SessionStore for session state management")

        self._ensure_client_scavenger_started()

        logger.info(f"Claude SDK backend initialized with provider: {provider_name}")
        logger.info(f"Claude SDK capabilities: {self.get_tools_summary()}")

    def _create_skill_progress_callback(self, shared_resources: dict[str, Any]) -> Any:
        """Create a callback for skill loading progress notifications.

        This callback is used to notify users (CLI/Channel) when a skill is being loaded,
        making the lazy loading process visible and transparent.

        Args:
            shared_resources: Shared resources containing bus and session context

        Returns:
            Async callback function(skill_name: str, status: str)
        """
        bus = shared_resources.get("bus")

        async def skill_progress_callback(skill_name: str, status: str) -> None:
            """Send skill loading progress notification.

            Args:
                skill_name: Name of the skill being loaded
                status: Loading status ('loading', 'loaded', 'not_found')
            """
            # Log for debugging
            logger.debug(f"Skill loading: {skill_name} - {status}")

            # For Channel mode, send progress notification via bus
            if bus is None:
                return

            # Get current session context (channel, chat_id)
            # Use the last session context or return if not available
            session_contexts = shared_resources.get("_session_contexts", {})
            if not session_contexts:
                return

            # Get the most recent session context
            # This is a simplification - ideally we'd track the current session
            last_context = list(session_contexts.values())[-1] if session_contexts else None
            if not last_context:
                return

            channel, chat_id = last_context

            # Build progress message based on status
            if status == "loading":
                message = f"📚 Loading skill: {skill_name}..."
            elif status == "loaded":
                message = f"✅ Skill loaded: {skill_name}"
            elif status == "not_found":
                message = f"❌ Skill not found: {skill_name}"
            else:
                message = f"📦 Skill {skill_name}: {status}"

            # Send notification
            try:
                from xbot.bus.events import OutboundMessage
                await bus.publish_outbound(
                    OutboundMessage(
                        channel=channel,
                        chat_id=chat_id,
                        content=message,
                        metadata={"_progress": True, "_event_type": "skill_load"},
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to send skill progress notification: {e}")

        return skill_progress_callback

    def _get_session(self, session_key: str):
        if not self.sessions:
            return None
        return self.sessions.get_or_create(session_key)

    def _get_tool_definitions(self) -> list[dict[str, Any]]:
        if not self._tool_adapter:
            return []
        definitions: list[dict[str, Any]] = []
        for tool_name, tool_instance in self._tool_adapter._tools.items():
            if not isinstance(tool_instance, Tool):
                continue
            definitions.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool_instance.description,
                    "parameters": tool_instance.parameters,
                },
            })
        return definitions

    def _build_options(
        self,
        session_key: str | None = None,
        *,
        include_agents: bool = True,
    ) -> "ClaudeAgentOptions":
        """Build ClaudeAgentOptions from configuration."""
        if self._options_builder is None:
            from xbot.exceptions import BackendNotInitializedError
            raise BackendNotInitializedError(
                "Cannot build options: backend not initialized. Call initialize() first."
            )
        return self._options_builder.build(session_key, include_agents=include_agents)

    async def _get_or_create_client(self, session_key: str) -> "ClaudeSDKClient":
        """Get or create a Claude SDK client for the session.

        This method is thread-safe and prevents race conditions when
        multiple concurrent requests arrive for the same session.

        Implements LRU eviction when MAX_CLIENTS is reached and TTL-based
        cleanup for idle clients to prevent memory leaks.

        Args:
            session_key: Session identifier

        Returns:
            ClaudeSDKClient instance for the session
        """
        # Collect clients that need to be disconnected (outside lock)
        clients_to_disconnect: list[tuple["ClaudeSDKClient", str, str]] = []
        resolved_client: ClaudeSDKClient | None = None

        async with self._clients_lock:
            # Get current model for model change detection
            current_model = self._options_builder._get_model_name() if self._options_builder else None
            # Get current skills version for hot-reload detection
            current_skills_version = self._skill_manager.version if self._skill_manager else None

            # Check if client exists and model/skills haven't changed
            if self._has_client_in_entry(session_key):
                cached_model = self._get_model_from_entry(session_key)
                cached_skills = self._get_skills_version_from_entry(session_key)
                model_ok = cached_model == current_model
                skills_ok = cached_skills == current_skills_version

                if model_ok and skills_ok:
                    self._touch_entry(session_key)
                    await self._client_lifecycle.touch(session_key)
                    logger.debug(f"[Client] Reusing existing client for session={session_key}, model={current_model}")
                    resolved_client = self._get_client_from_entry(session_key)
                    if resolved_client is not None:
                        return resolved_client
                else:
                    # Model or skills changed, need to recreate client
                    reasons = []
                    if not model_ok:
                        reasons.append(f"model {cached_model}->{current_model}")
                    if not skills_ok:
                        reasons.append(f"skills {cached_skills[:8] if cached_skills else 'None'}->{current_skills_version[:8] if current_skills_version else 'None'}")
                    logger.info(f"[Client] Recreating client for session={session_key}: {', '.join(reasons)}")
                    old_client = self._remove_client_state(session_key)
                    if old_client is not None:
                        # Defer disconnect to outside the lock
                        clients_to_disconnect.append((old_client, session_key, "model/skills change"))

            # Evict LRU client if at capacity
            if self._uses_session_store():
                # Count sessions with clients
                client_count = 0
                for k in self._session_store.list_keys():
                    session_entry = self._session_store.get(k)
                    if session_entry and session_entry.client:
                        client_count += 1
            else:
                client_count = len(self._clients)
            if client_count >= self.max_clients:
                evicted_client, evicted_key = await self._evict_lru_client_unlocked()
                if evicted_client is not None:
                    clients_to_disconnect.append((evicted_client, evicted_key, "LRU eviction"))

            # Create new client with timing
            client_start = time.perf_counter()
            logger.info(f"[Client] Creating new client for session={session_key}")

            client = _ClaudeSDKClient(options=self._build_options(session_key))

            connect_start = time.perf_counter()
            await client.connect()
            connect_time = time.perf_counter() - connect_start
            logger.info(f"[Client] connect() took {connect_time:.2f}s for session={session_key}")

            refresh_start = time.perf_counter()
            await self._refresh_session_commands(session_key, client)
            refresh_time = time.perf_counter() - refresh_start
            logger.info(f"[Client] get_server_info() took {refresh_time:.2f}s for session={session_key}")

            total_time = time.perf_counter() - client_start
            logger.info(f"[Client] Total client creation took {total_time:.2f}s for session={session_key}")

            # Store client and metadata using helper methods
            self._set_client_in_entry(session_key, client)
            self._touch_entry(session_key)
            self._set_model_in_entry(session_key, current_model)
            self._set_skills_version_in_entry(session_key, current_skills_version)
            logger.info(f"[Client] Client created for session={session_key}, model={current_model}")
            await self._register_managed_client(session_key, client)
            resolved_client = client

        # Disconnect old clients outside the lock
        for old_client, client_key, reason in clients_to_disconnect:
            await self._finalize_detached_client_cleanup(
                client_key,
                old_client,
                reason=f"client recreation ({reason})",
            )
            # Notify runtime to sync state
            on_cleanup = self._shared_resources.get("on_backend_client_cleanup")
            if on_cleanup:
                try:
                    await on_cleanup(client_key)
                except Exception as e:
                    logger.debug(f"Error in backend client cleanup callback: {e}")

        if resolved_client is None:
            raise RuntimeError(f"Client resolution failed for session {session_key}")
        return resolved_client

    def _remove_client_state(
        self,
        session_key: str,
        *,
        preserve_sdk_context: bool = True,
    ) -> "ClaudeSDKClient | None":
        """Remove all tracked state for a session and return the client (if any).

        Centralises the cleanup of the six parallel per-session dicts so that
        every eviction / shutdown / error path stays in sync.  The caller is
        responsible for disconnecting the returned client.
        """
        # === 诊断日志: 状态清理 ===
        had_client = self._has_client_in_entry(session_key)
        had_task_id = self._get_task_id_from_entry(session_key) is not None
        had_sdk_sid = False
        if self.sessions:
            session = self.sessions.get(session_key)
            if session and session.metadata.get("sdk_session_id"):
                had_sdk_sid = True

        # Get client and clear all state
        client = None
        if self._uses_session_store():
            # Use SessionStore - clear entry state
            entry = self._get_entry(session_key)
            if entry:
                client = entry.client
                entry.client = None
                entry.model = ""
                entry.skills_version = None
                entry.commands = []
                entry.task_id = None
                entry.request_id = None
                entry.tasks.clear()  # Clear tasks list
                if not preserve_sdk_context:
                    self._session_store.clear_sdk_session_id(session_key)
                entry.touch()  # Update last_used
        else:
            # Legacy dict cleanup
            client = self._clients.pop(session_key, None)
            self._client_last_used.pop(session_key, None)
            self._client_models.pop(session_key, None)
            self._client_skills_versions.pop(session_key, None)
            self._session_commands.pop(session_key, None)
            self._active_task_ids.pop(session_key, None)
            self._active_request_ids.pop(session_key, None)  # Clear request ID tracking

        # Clear session context for compact notifications
        session_contexts = self._shared_resources.get("_session_contexts")
        if session_contexts is not None and not preserve_sdk_context:
            # Clear both session_key and sdk_session_id mappings
            session_contexts.pop(session_key, None)
            # Also clear sdk_session_id mapping if we can find it
            if had_sdk_sid and self.sessions:
                session = self.sessions.get(session_key)
                if session:
                    sdk_sid = session.metadata.get("sdk_session_id")
                    if sdk_sid and sdk_sid in session_contexts:
                        session_contexts.pop(sdk_sid, None)
                        logger.debug(f"[State Cleanup] Also cleared sdk_sid mapping: {sdk_sid}")

        # Log state cleanup for debugging
        if had_client or had_task_id:
            logger.info(
                f"[State Cleanup] session={session_key}, removed_client={had_client}, "
                f"removed_task={had_task_id}, had_sdk_sid={had_sdk_sid}"
            )

        return client

    async def _safe_disconnect_client(
        self,
        client: "ClaudeSDKClient | None",
        session_key: str,
        context: str = "",
        retries: int = 2,
    ) -> bool:
        """Safely disconnect a client with retries and proper error logging.

        Args:
            client: The client to disconnect (can be None)
            session_key: Session identifier for logging
            context: Additional context for logging (e.g., "stale client", "shutdown")
            retries: Number of retry attempts

        Returns:
            True if disconnect succeeded or client was None, False otherwise
        """
        if client is None:
            return True
        return await self._disconnect_client_with_timeout(
            client,
            session_key,
            context=context,
            retries=retries,
        )

    # -- Multimodal (image) helpers ----------------------------------------

    _MAX_IMAGE_BYTES = 20 * 1024 * 1024  # Anthropic 20 MB limit
    _SUPPORTED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
    _MAX_AUDIO_BYTES = 25 * 1024 * 1024  # Anthropic audio limit
    _SUPPORTED_AUDIO_MIMES = {"audio/mp3", "audio/wav", "audio/ogg", "audio/flac", "audio/mp4"}

    def _build_image_content_blocks(self, media: list[str]) -> list[dict[str, Any]]:
        """Convert local image files to Anthropic-format image content blocks."""
        blocks: list[dict[str, Any]] = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                logger.warning("Image file not found, skipping: %s", path)
                continue
            if p.stat().st_size > self._MAX_IMAGE_BYTES:
                logger.warning("Image too large (>20 MB), skipping: %s", path)
                continue
            raw = p.read_bytes()
            mime = detect_image_mime(raw)
            if not mime or mime not in self._SUPPORTED_IMAGE_MIMES:
                logger.warning("Unsupported image format (%s), skipping: %s", mime, path)
                continue
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.b64encode(raw).decode(),
                },
            })
        return blocks

    def _build_audio_content_blocks(self, media: list[str]) -> list[dict[str, Any]]:
        """Convert local audio files to Anthropic-format audio content blocks."""
        blocks: list[dict[str, Any]] = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                logger.warning("Audio file not found, skipping: %s", path)
                continue
            if p.stat().st_size > self._MAX_AUDIO_BYTES:
                logger.warning("Audio too large (>25 MB), skipping: %s", path)
                continue
            raw = p.read_bytes()
            mime = detect_audio_mime(raw[:12])
            if not mime or mime not in self._SUPPORTED_AUDIO_MIMES:
                logger.warning("Unsupported audio format (%s), skipping: %s", mime, path)
                continue
            blocks.append({
                "type": "audio",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.b64encode(raw).decode(),
                },
            })
        return blocks

    def _classify_media_paths(
        self, media: list[str]
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """Classify media into file references, image paths, and audio paths.

        Returns ``(file_ref_blocks, image_paths, audio_paths)`` where *file_ref_blocks*
        is a list of text content blocks containing path references for
        regular files, while *image_paths* and *audio_paths* are the subsets
        that should be sent as base64 content blocks.
        """
        image_paths: list[str] = []
        audio_paths: list[str] = []
        file_refs: list[str] = []
        for path in media:
            ft = classify_file(path)
            if ft is FileType.IMAGE:
                image_paths.append(path)
            elif ft is FileType.AUDIO:
                audio_paths.append(path)
            else:
                ref = format_file_reference(path)
                file_refs.append(ref)
                logger.info("[Backend] File reference: %s", ref)
        if not file_refs:
            return [], image_paths, audio_paths
        header = "用户附加了以下文件，你可以通过工具读取或修改这些文件:"
        block_text = header + "\n" + "\n".join(file_refs)
        return [{"type": "text", "text": block_text}], image_paths, audio_paths

    def _build_file_content_blocks(
        self, media: list[str]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Backward-compatible wrapper returning file refs and image paths."""
        file_ref_blocks, image_paths, _audio_paths = self._classify_media_paths(media)
        return file_ref_blocks, image_paths

    async def _build_multimodal_query(
        self,
        prompt: str,
        media: list[str],
        session_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield a single user message with image blocks and/or file references.

        Args:
            prompt: The text prompt
            media: List of media file paths
            session_id: SDK session identifier
        """
        file_ref_blocks, image_paths, audio_paths = self._classify_media_paths(media)
        image_blocks = self._build_image_content_blocks(image_paths)
        audio_blocks = self._build_audio_content_blocks(audio_paths)

        all_blocks = image_blocks + audio_blocks + file_ref_blocks
        if not all_blocks:
            # Everything failed – fall back to plain text
            content: str | list[dict[str, Any]] = prompt
        else:
            content = all_blocks + [{"type": "text", "text": prompt}]

        message = {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": session_id,
        }

        yield message

    async def _cleanup_stale_clients_unlocked(self) -> list[tuple["ClaudeSDKClient", str]]:
        """Remove clients that have been idle longer than TTL.

        Must be called while holding _clients_lock.

        Returns:
            List of (client, session_key) tuples that need to be disconnected.
            Caller is responsible for disconnecting these clients outside the lock.
        """
        now = time.time()
        stale_keys = []

        if self._uses_session_store():
            # Use SessionStore for TTL cleanup
            for session_key in self._session_store.list_keys():
                entry = self._session_store.get(session_key)
                if entry and entry.client is not None:
                    if now - entry.last_used > self.client_ttl_seconds:
                        stale_keys.append(session_key)
        else:
            # Legacy dict cleanup
            stale_keys = [
                key for key, last_used in self._client_last_used.items()
                if now - last_used > self.client_ttl_seconds
            ]

        clients_to_disconnect = []
        for key in stale_keys:
            client = self._remove_client_state(key)
            if client is not None:
                clients_to_disconnect.append((client, key))

        if stale_keys:
            logger.info(f"Found {len(stale_keys)} stale client(s) to cleanup (TTL={self.client_ttl_seconds}s)")

        return clients_to_disconnect

    async def _evict_lru_client_unlocked(self) -> tuple["ClaudeSDKClient | None", str | None]:
        """Evict the least recently used client.

        Must be called while holding _clients_lock.

        Returns:
            Tuple of (client, session_key) that was evicted.
            Caller is responsible for disconnecting the client outside the lock.
        """
        # Find sessions with clients
        client_sessions = {}
        if self._uses_session_store():
            for session_key in self._session_store.list_keys():
                entry = self._session_store.get(session_key)
                if entry and entry.client is not None:
                    client_sessions[session_key] = entry.last_used
        else:
            client_sessions = {k: v for k, v in self._client_last_used.items() if k in self._clients}

        if not client_sessions:
            return None, None

        # Find the oldest (LRU) client
        lru_key = min(client_sessions, key=client_sessions.get)

        client = self._remove_client_state(lru_key)

        if client is not None:
            logger.info(f"Evicting LRU client for session {lru_key} (pool at capacity)")

        return client, lru_key

    async def _refresh_session_commands(self, session_key: str, client: "ClaudeSDKClient") -> None:
        """Refresh slash commands discovered from SDK init metadata."""
        try:
            info = await client.get_server_info()
        except Exception as e:
            logger.warning(f"Failed to get SDK server info for session {session_key}: {e}")
            return
        commands = self._extract_slash_commands(info)
        if isinstance(info, dict):
            logger.debug(
                f"SDK server info keys for session {session_key}: {sorted(info.keys())}"
            )
        logger.info(f"Discovered {len(commands)} SDK slash commands for session {session_key}: {commands}")
        self._set_commands_in_entry(session_key, commands)

    @staticmethod
    def _extract_slash_commands(info: Any) -> list[str]:
        """Extract slash commands from SDK initialization payload."""
        if not isinstance(info, dict):
            return []

        def _to_slash(name: str) -> str | None:
            raw = name.strip()
            if not raw:
                return None
            return raw if raw.startswith("/") else f"/{raw}"

        candidates = info.get("slash_commands")
        if isinstance(candidates, list):
            normalized = {
                c.strip()
                for c in candidates
                if isinstance(c, str)
                if c.strip().startswith("/")
            }
            return sorted(normalized)

        commands = info.get("commands")
        result: set[str] = set()
        if isinstance(commands, list):
            for cmd in commands:
                if isinstance(cmd, str):
                    normalized = _to_slash(cmd)
                    if normalized:
                        result.add(normalized)
                elif isinstance(cmd, dict):
                    name = cmd.get("name")
                    if isinstance(name, str):
                        normalized = _to_slash(name)
                        if normalized:
                            result.add(normalized)
        return sorted(result)

    async def _create_temp_client(
        self,
        session_key: str,
        *,
        include_agents: bool,
    ) -> "ClaudeSDKClient":
        """Create a temporary client for fallback scenarios."""
        client = _ClaudeSDKClient(options=self._build_options(session_key, include_agents=include_agents))
        await client.connect()
        return client

    @staticmethod
    def _query_session_id(context_session_key: str, session: Any | None) -> str:
        if session and isinstance(session.metadata.get("sdk_session_id"), str):
            return session.metadata["sdk_session_id"]
        return context_session_key

    async def _record_delegation_trace(
        self,
        session_key: str,
        decision: HandoffDecision,
    ) -> None:
        """Record a delegation decision trace."""
        trace = DelegationTrace(
            timestamp=datetime.now().isoformat(),
            session_key=session_key,
            decision_mode=decision.mode,
            reason=decision.reason,
            candidates=list(decision.candidate_agents),
        )

        async with self._delegation_traces_lock:
            self._delegation_traces.append(trace)
            if len(self._delegation_traces) > 100:
                self._delegation_traces = self._delegation_traces[-100:]
        
        logger.info(
            f"Delegation decision: session={session_key}, mode={decision.mode}, "
            f"reason={decision.reason}, candidates={list(decision.candidate_agents)}"
        )

    def get_delegation_traces(self, session_key: str | None = None) -> list[dict[str, Any]]:
        """Get delegation traces, optionally filtered by session.
        
        Args:
            session_key: Optional session key to filter traces
            
        Returns:
            List of delegation trace dictionaries
        """
        # Snapshot to avoid race with concurrent _add_trace writes
        traces = list(self._delegation_traces)
        if session_key:
            traces = [t for t in traces if t.session_key == session_key]
        return [t.to_dict() for t in traces]

    # -- Stale message boundary detection ------------------------------------
    _MAX_STALE_DISCARD = 50  # safety valve: force boundary after this many discards

    async def _receive_with_boundary(
        self,
        client: "ClaudeSDKClient",
        session_key: str,
    ) -> AsyncIterator["ResultMessage | AssistantMessage | SystemMessage | StreamEvent | TaskStartedMessage | TaskNotificationMessage | TaskProgressMessage"]:
        """Wrap ``client.receive_messages()`` with stale-message boundary detection.

        Each ``query()`` triggers a ``SystemMessage(subtype='init')`` as the very
        first message of the new response.  Any messages arriving *before* that
        ``init`` are residual output from the previous request still sitting in the
        ``MemoryObjectStream`` buffer and must be discarded.

        ``UserMessage`` is a protocol-level echo (history replay or tool-result
        acknowledgement) and is **always** filtered out, regardless of boundary
        state.

        After the ``init`` message is seen (or the safety-valve fires), all
        subsequent messages are yielded normally.  ``ResultMessage`` terminates
        the iterator – mirroring the behaviour of ``receive_response()``.
        """
        boundary_crossed = False
        stale_count = 0

        async for message in client.receive_messages():
            # --- protocol filter: UserMessage is never yielded upstream ---
            if isinstance(message, UserMessage):
                continue

            # --- boundary detection phase ---
            if not boundary_crossed:
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    boundary_crossed = True
                    logger.debug(
                        "[SDK Boundary] init boundary crossed for session=%s "
                        "(discarded %d stale message(s))",
                        session_key,
                        stale_count,
                    )
                    yield message
                    continue

                stale_count += 1
                logger.warning(
                    "[SDK Boundary] Discarding stale pre-boundary message #%d: "
                    "type=%s, session=%s",
                    stale_count,
                    type(message).__name__,
                    session_key,
                )
                if stale_count >= self._MAX_STALE_DISCARD:
                    logger.error(
                        "[SDK Boundary] Safety valve: forcing boundary after %d "
                        "stale messages for session=%s",
                        stale_count,
                        session_key,
                    )
                    boundary_crossed = True
                continue

            # --- normal phase ---
            yield message
            if isinstance(message, ResultMessage):
                return

    async def process(self, context: AgentContext) -> AsyncIterator[AgentResponse]:
        """Process a message using Claude SDK.

        Args:
            context: Processing context

        Yields:
            AgentResponse objects
        """
        process_start = time.perf_counter()
        # Debug: Log entry to backend process
        logger.info(f"[Backend Process] Starting process for session={context.session_key}, prompt_preview={context.prompt[:50]!r}")

        # Store session context for compact notifications
        # Use helper method that handles both SessionStore and legacy dict
        self._set_context_in_entry(context.session_key, context.channel, context.chat_id)

        # Size limiting for backward compatibility dict
        session_contexts = self._shared_resources.setdefault("_session_contexts", {})
        _MAX_SESSION_CONTEXTS = 500
        if len(session_contexts) > _MAX_SESSION_CONTEXTS:
            # Remove oldest entries (dict preserves insertion order in Python 3.7+)
            excess = len(session_contexts) - _MAX_SESSION_CONTEXTS
            for _ in range(excess):
                session_contexts.pop(next(iter(session_contexts)))
        logger.info(
            "[Session Context] Set mapping: session_key='%s' -> (channel='%s', chat_id='%s'). "
            "Current keys in session_contexts: %s",
            context.session_key,
            context.channel,
            context.chat_id,
            list(session_contexts.keys()),
        )

        if self._tool_adapter:
            if not self._tool_adapter._tools:
                self._tool_adapter._register_xbot_tools()
            self._tool_adapter.set_tool_context(
                channel=context.channel,
                chat_id=context.chat_id,
                session_key=context.session_key,
                message_id=context.metadata.get("message_id"),
            )
            message_tool = self._tool_adapter.get_tool("message")
            if message_tool and hasattr(message_tool, "start_turn"):
                message_tool.start_turn()

        # Set permission handler session context
        if self._permission_handler and hasattr(self._permission_handler, "set_session_context"):
            self._permission_handler.set_session_context(
                context.session_key,
                context.channel,
                context.chat_id,
                context.metadata,
            )
            if hasattr(self._permission_handler, "set_current_session"):
                self._permission_handler.set_current_session(context.session_key)

        # Check for skill changes on disk (hot-reload)
        if self._skill_manager:
            try:
                if self._skill_manager.check_for_changes() and self._tool_adapter:
                    self._skill_manager.sync_tools_to_adapter(self._tool_adapter)
            except Exception as e:
                logger.warning(f"[Backend Process] Skill change check failed: {e}")

        # Detect triggered skills based on user message
        triggered_skills_prefix = ""
        if self._context_builder:
            skills_start = time.perf_counter()
            triggered_skills = self._context_builder.skills.get_triggered_skills(
                user_message=context.prompt,
                code_context="",  # Could be enhanced to include file content
                file_paths=None,
            )
            skills_time = time.perf_counter() - skills_start
            logger.info(f"[Backend Process] get_triggered_skills took {skills_time:.3f}s for session={context.session_key}")
            if triggered_skills:
                triggered_content = self._context_builder.skills.load_skills_for_context(triggered_skills)
                if triggered_content:
                    triggered_skills_prefix = f"[Triggered Skills]\n\n{triggered_content}\n\n---\n\n"
                    logger.info(f"Triggered skills for session {context.session_key}: {triggered_skills}")

        session = self.sessions.get_or_create(context.session_key) if self.sessions else None

        # === 诊断日志: Backend 处理入口 ===
        sdk_session_id = session.metadata.get("sdk_session_id") if session else None
        active_task_id = self._get_task_id_from_entry(context.session_key)
        reconnect_pending = session.metadata.get("_reconnect_pending") if session else None
        prompt_preview = context.prompt[:50].replace('\n', ' ') if context.prompt else ""
        logger.info(
            f"[Backend] session={context.session_key}, sdk_sid={sdk_session_id or 'none'}, "
            f"task_id={active_task_id or 'none'}, reconnect={reconnect_pending or False}, "
            f'prompt="{prompt_preview}..."'
        )

        # If SDK session ID already exists from previous turn, also map it
        # This ensures hooks can find context when SDK returns the UUID instead of session_key
        if session is not None:
            existing_sdk_id = session.metadata.get("sdk_session_id")
            if existing_sdk_id:
                session_contexts[existing_sdk_id] = (context.channel, context.chat_id)

        # Check for reconnect pending state from previous error
        reconnect_hint = None
        fresh_start = False

        # 首先检查是否需要强制新会话（不可恢复错误后）
        if session is not None and session.metadata.pop("_fresh_start_required", None):
            fresh_start = True
            # 清除sdk_session_id，强制开始新会话
            old_sdk_sid = session.metadata.pop("sdk_session_id", None)
            session.metadata.pop("_last_error", None)
            session.metadata.pop("_error_timestamp", None)
            session.metadata.pop("_fallback_error", None)
            self.sessions.save(session)
            logger.info(
                f"[Backend] session={context.session_key} requires fresh start, "
                f"cleared sdk_session_id={old_sdk_sid or 'none'}"
            )
            reconnect_hint = "🔄 会话已重置，开始新的对话..."

        elif session is not None and session.metadata.pop("_reconnect_pending", None):
            # Session had a previous error, attempting recovery
            last_error = session.metadata.pop("_last_error", "")
            session.metadata.pop("_error_timestamp", None)
            session.metadata.pop("_fallback_error", None)
            self.sessions.save(session)

            # Provide recovery hint to user
            if session.metadata.get("sdk_session_id"):
                reconnect_hint = "🔄 正在尝试恢复之前的会话上下文..."
                logger.info(
                    f"Session {context.session_key} reconnecting with existing sdk_session_id: "
                    f"{session.metadata.get('sdk_session_id')}"
                )
            else:
                logger.info(f"Session {context.session_key} reconnecting without sdk_session_id")

        if session is not None:
            session.add_message("user", context.prompt)
            self.sessions.save(session)
            mode = getattr(self.sdk_config, "memory_consolidation_mode", "off")
            if self.memory_consolidator and mode == "sync":
                await self.memory_consolidator.maybe_consolidate_by_tokens(session)
            elif self.memory_consolidator and mode == "async":
                # Wrap consolidation in a task with error handling to prevent unhandled exceptions
                async def _safe_consolidate():
                    try:
                        await self.memory_consolidator.maybe_consolidate_by_tokens(session)
                    except asyncio.CancelledError:
                        logger.debug(f"Memory consolidation cancelled for session {context.session_key}")
                        raise
                    except Exception as e:
                        # Log but don't propagate - consolidation is non-critical
                        logger.warning(f"Async memory consolidation failed for {context.session_key}: {e}")
                asyncio.create_task(_safe_consolidate())

        try:
            final_content = ""
            decision = None
            prompt = f"{triggered_skills_prefix}{context.prompt}" if triggered_skills_prefix else context.prompt

            # Send reconnect hint if this is a recovery attempt
            if reconnect_hint:
                yield AgentResponse(
                    content="",
                    progress_texts=[reconnect_hint],
                )

            if self._handoff_policy and self._handoff_policy.has_agents():
                decision = self._handoff_policy.decide(context.prompt)
                
                # Record delegation trace
                await self._record_delegation_trace(context.session_key, decision)
                
                yield AgentResponse(
                    content="",
                    progress_texts=[
                        self._handoff_policy.build_activation_trace(),
                        self._handoff_policy.build_decision_trace(decision),
                    ],
                )
                prompt = f"{self._handoff_policy.build_request_prefix(decision)}\n{context.prompt}"

            # Retry loop: SDK may return stale task notifications instead of ResultMessage
            # We need to retry the query in such cases, with a limit to avoid infinite loops
            query_retry = 0
            received_result = False
            while query_retry < self._MAX_QUERY_RETRIES:
                client = await self._get_or_create_client(context.session_key)
                if self.client_lifecycle_enabled:
                    await self._register_managed_client(context.session_key, client)
                query_sent_at = time.perf_counter()
                logger.info(f"[Backend Process] Client obtained for session={context.session_key}")

                # Debug: Log slash commands being sent to SDK
                if prompt.strip().startswith("/"):
                    logger.info(f"[SDK Query] Slash command detected: {prompt.strip()[:50]!r} (session={context.session_key})")

                append_session_trace(
                    self.sessions,
                    context.session_key,
                    "sdk_query_sent",
                    {
                        "backend": self.name,
                    },
                )
                logger.info(f"[Backend Process] Sending query to SDK for session={context.session_key}")
                _query_session_id = self._query_session_id(context.session_key, session)

                # Clear task_id before new request to detect stale messages
                self._set_task_id_in_entry(context.session_key, None)

                if context.media:
                    logger.info(f"[Backend Process] Multimodal query with {len(context.media)} media file(s)")
                    await client.query(
                        self._build_multimodal_query(prompt, context.media, _query_session_id),
                        session_id=_query_session_id,
                    )
                else:
                    # For non-media queries, we can't set uuid via string prompt
                    # The SDK will generate its own uuid
                    await client.query(prompt, session_id=_query_session_id)
                logger.info(f"[Backend Process] Query sent, waiting for response for session={context.session_key}")

                first_sdk_message_logged = False
                received_result = False  # Reset for each retry

                async for message in self._receive_with_boundary(client, context.session_key):
                    logger.info(f"[Backend Process] Received message type={type(message).__name__} for session={context.session_key}")
                    is_terminal_result = isinstance(message, ResultMessage)
                    if not first_sdk_message_logged:
                        first_sdk_message_logged = True
                        append_session_trace(
                            self.sessions,
                            context.session_key,
                            "sdk_first_message",
                            {
                                "backend": self.name,
                                "latency_ms": int((time.perf_counter() - query_sent_at) * 1000),
                                "message_type": message.__class__.__name__,
                                "subtype": getattr(message, "subtype", None),
                            },
                        )
                    if isinstance(message, SystemMessage) and message.subtype == "init":
                        commands = self._extract_slash_commands(message.data)
                        # DEBUG: Log full init message data to check for session_id
                        logger.info(
                            "[SDK Init] session=%s, commands=%s, data_keys=%s, data=%s",
                            context.session_key,
                            commands,
                            list(message.data.keys()) if isinstance(message.data, dict) else "N/A",
                            message.data,
                        )
                        self._set_commands_in_entry(context.session_key, commands)

                        # Try to extract session_id from init message for early mapping
                        # This ensures hooks can find context from the first message
                        sdk_session_id = message.data.get("session_id") if isinstance(message.data, dict) else None
                        if sdk_session_id:
                            logger.info(
                                "[SDK Init] Found session_id in init message: sdk_session_id='%s', mapping to context",
                                sdk_session_id,
                            )
                            session_contexts[sdk_session_id] = (context.channel, context.chat_id)
                            # Also save to session metadata
                            if session is not None:
                                session.metadata["sdk_session_id"] = sdk_session_id
                                self.sessions.save(session)
                    if isinstance(message, TaskStartedMessage) and message.task_id:
                        # === 诊断日志: TaskStarted ===
                        prev_task_id = self._get_task_id_from_entry(context.session_key)
                        logger.info(
                            f"[SDK TaskStarted] session={context.session_key}, task_id={message.task_id}, "
                            f"prev_task_id={prev_task_id or 'none'}"
                        )
                        self._set_task_id_in_entry(context.session_key, message.task_id)
                    if (
                        isinstance(message, TaskNotificationMessage)
                        and message.status in {"completed", "failed", "stopped"}
                    ):
                        # === 诊断日志 + Task ID验证: TaskNotification 终态 ===
                        current_task_id = self._get_task_id_from_entry(context.session_key)
                        logger.info(
                            f"[SDK Notification] session={context.session_key}, task_id={message.task_id}, "
                            f"status={message.status}, current_task_id={current_task_id or 'none'}"
                        )
                        # Stale detection: if message.task_id and task_ids don't match
                        # or current_task_id is None (no TaskStarted received for this request)
                        if message.task_id and (
                            current_task_id is None or message.task_id != current_task_id
                        ):
                            logger.warning(
                                f"[SDK Notification] Stale task notification detected: "
                                f"session={context.session_key}, received_task={message.task_id}, "
                                f"expected_task={current_task_id or 'none'}. Ignoring."
                            )
                            continue
                        self._set_task_id_in_entry(context.session_key, None)
                    if is_terminal_result:
                        # === 诊断日志: ResultMessage ===
                        current_task_id = self._get_task_id_from_entry(context.session_key)

                        logger.info(
                            f"[SDK Result] session={context.session_key}, task_id={current_task_id or 'none'}, "
                            f"is_error={message.is_error if hasattr(message, 'is_error') else 'N/A'}"
                        )

                        self._set_task_id_in_entry(context.session_key, None)
                        if session is not None and message.session_id:
                            # Update SDK session ID mapping using helper method
                            # This also syncs to session.metadata and saves
                            await self._set_sdk_session_id_in_entry(context.session_key, message.session_id)

                    response = self._message_converter.convert(message) if self._message_converter else None

                    if response:
                        # Accumulate content: delta content or final content
                        if response.is_delta and response.delta_content:
                            final_content += response.delta_content
                        elif response.content:
                            final_content = response.content
                        yield response
                    if is_terminal_result:
                        received_result = True  # Mark that we received a ResultMessage
                        break  # Exit async for loop

                # After async for loop ends, check if we got a ResultMessage
                if received_result:
                    break  # Got valid result, exit retry loop

                # No ResultMessage received - likely stale task notification
                query_retry += 1
                if query_retry < self._MAX_QUERY_RETRIES:
                    logger.warning(
                        f"SDK returned no ResultMessage for session={context.session_key}, "
                        f"retrying ({query_retry}/{self._MAX_QUERY_RETRIES})"
                    )
                    # Clear the final_content for retry
                    final_content = ""
                else:
                    logger.error(
                        f"SDK returned no ResultMessage after {self._MAX_QUERY_RETRIES} attempts "
                        f"for session={context.session_key}. Last content: {final_content[:200] if final_content else '(empty)'}"
                    )

            if session is not None and final_content:
                session.add_message("assistant", final_content)
                self.sessions.save(session)
                mode = getattr(self.sdk_config, "memory_consolidation_mode", "off")
                if self.memory_consolidator and mode == "sync":
                    await self.memory_consolidator.maybe_consolidate_by_tokens(session)
                elif self.memory_consolidator and mode == "async":
                    async def _safe_consolidate():
                        try:
                            await self.memory_consolidator.maybe_consolidate_by_tokens(session)
                        except asyncio.CancelledError:
                            logger.debug(f"Memory consolidation cancelled for session {context.session_key}")
                            raise
                        except Exception as e:
                            logger.warning(f"Async memory consolidation failed for {context.session_key}: {e}")
                    asyncio.create_task(_safe_consolidate())

        except Exception as e:
            # === 诊断日志: 错误恢复 ===
            error_type = type(e).__name__
            await self.release_client(context.session_key, reason="error recovery")

            # Determine if this is a recoverable error
            # Recoverable: network/timeout issues - preserve sdk_session_id for reconnection
            # Non-recoverable: state errors, cancellations - clear sdk_session_id for fresh start
            recoverable_errors = {
                "ConnectionError", "TimeoutError", "asyncio.TimeoutError",
                "ConnectionResetError", "BrokenPipeError",
            }
            is_recoverable = error_type in recoverable_errors

            logger.info(
                f"[Error Recovery] session={context.session_key}, error={error_type}: {str(e)[:100]}, "
                f"action={'preserve' if is_recoverable else 'clear'}, sdk_sid_preserved={is_recoverable}"
            )

            # Don't immediately clear sdk_session_id - preserve context for potential recovery
            # Instead, mark the session as needing recovery
            if session is not None:
                session.metadata["_reconnect_pending"] = True
                session.metadata["_last_error"] = str(e)[:500]
                session.metadata["_error_timestamp"] = datetime.now().isoformat()
                # For non-recoverable errors, mark for fresh start instead of recovery
                if not is_recoverable:
                    session.metadata["_fresh_start_required"] = True
                self.sessions.save(session)

            logger.exception("Error in Claude SDK backend")

            can_fallback = (
                self._handoff_policy is not None
                and self._handoff_policy.has_agents()
            )
            if can_fallback:
                yield AgentResponse(
                    content="",
                    progress_texts=[self._handoff_policy.build_fallback_trace(str(e))],
                )
                try:
                    final_content = ""
                    fallback_client = await self._create_temp_client(
                        context.session_key,
                        include_agents=False,
                    )
                    try:
                        # Clear task_id before fallback request for consistency
                        self._set_task_id_in_entry(context.session_key, None)

                        fallback_prompt = (
                            "[Runtime Policy]\n"
                            "Continue on the main agent. Do not use specialist handoff for this retry.\n\n"
                            f"{context.prompt}"
                        )
                        await fallback_client.query(
                            fallback_prompt,
                            session_id=self._query_session_id(context.session_key, session),
                        )
                        async for message in self._receive_with_boundary(fallback_client, context.session_key):
                            is_terminal_result = isinstance(message, ResultMessage)
                            if isinstance(message, SystemMessage) and message.subtype == "init":
                                commands = self._extract_slash_commands(message.data)
                                logger.info(
                                    "[SDK Init Fallback] session=%s, commands=%s, data=%s",
                                    context.session_key,
                                    commands,
                                    message.data,
                                )
                                self._set_commands_in_entry(context.session_key, commands)
                                # Early mapping for hooks
                                sdk_session_id = message.data.get("session_id") if isinstance(message.data, dict) else None
                                if sdk_session_id:
                                    logger.info(
                                        "[SDK Init Fallback] Found session_id='%s', mapping to context",
                                        sdk_session_id,
                                    )
                                    # Use helper method for SDK session ID mapping
                                    # This also syncs to session.metadata and saves
                                    await self._set_sdk_session_id_in_entry(context.session_key, sdk_session_id)
                            if isinstance(message, TaskStartedMessage) and message.task_id:
                                self._set_task_id_in_entry(context.session_key, message.task_id)
                            if (
                                isinstance(message, TaskNotificationMessage)
                                and message.status in {"completed", "failed", "stopped"}
                            ):
                                # === Fallback 路径 Task ID 验证 ===
                                current_task_id = self._get_task_id_from_entry(context.session_key)
                                logger.info(
                                    f"[Fallback Notification] session={context.session_key}, task_id={message.task_id}, "
                                    f"status={message.status}, current_task_id={current_task_id or 'none'}"
                                )
                                # Stale detection: if message.task_id and task_ids don't match
                                # or current_task_id is None (no TaskStarted received for this request)
                                if message.task_id and (
                                    current_task_id is None or message.task_id != current_task_id
                                ):
                                    logger.warning(
                                        f"[Fallback Notification] Stale task notification detected: "
                                        f"session={context.session_key}, received_task={message.task_id}, "
                                        f"expected_task={current_task_id or 'none'}. Ignoring."
                                    )
                                    continue
                                self._set_task_id_in_entry(context.session_key, None)
                            if is_terminal_result:
                                self._set_task_id_in_entry(context.session_key, None)
                                if session is not None and message.session_id:
                                    # Use helper method for SDK session ID mapping
                                    # This also syncs to session.metadata and saves
                                    await self._set_sdk_session_id_in_entry(context.session_key, message.session_id)
                            
                            response = self._message_converter.convert(message) if self._message_converter else None
                                
                            if response:
                                # Accumulate content: delta content or final content
                                if response.is_delta and response.delta_content:
                                    final_content += response.delta_content
                                elif response.content:
                                    final_content = response.content
                                yield response
                            if is_terminal_result:
                                break
                    finally:
                        await fallback_client.disconnect()

                    if session is not None and final_content:
                        session.add_message("assistant", final_content)
                        self.sessions.save(session)
                        mode = getattr(self.sdk_config, "memory_consolidation_mode", "off")
                        if self.memory_consolidator and mode == "sync":
                            await self.memory_consolidator.maybe_consolidate_by_tokens(session)
                        elif self.memory_consolidator and mode == "async":
                            async def _safe_consolidate():
                                try:
                                    await self.memory_consolidator.maybe_consolidate_by_tokens(session)
                                except asyncio.CancelledError:
                                    logger.debug(f"Memory consolidation cancelled for session {context.session_key}")
                                    raise
                                except Exception as e:
                                    logger.warning(f"Async memory consolidation failed for {context.session_key}: {e}")
                            asyncio.create_task(_safe_consolidate())
                    return
                except Exception as fallback_error:
                    logger.exception("Claude SDK fallback to main agent failed")
                    # Don't clear sdk_session_id - preserve context for potential recovery
                    if session is not None:
                        session.metadata["_fallback_error"] = str(fallback_error)[:500]
                        self.sessions.save(session)

            # Provide a user-friendly error message
            error_hint = "连接遇到问题，会话状态已保存。"
            if session is not None and session.metadata.get("sdk_session_id"):
                error_hint += " 请继续对话，我会尝试恢复上下文。"
            else:
                error_hint += " 请重新描述您的需求。"

            yield AgentResponse(
                content=f"⚠️ {error_hint}\n\n错误详情: {str(e)[:200]}",
                finish_reason="error",
            )
        finally:
            # Clear permission handler session context
            if self._permission_handler and hasattr(self._permission_handler, "clear_session_context"):
                self._permission_handler.clear_session_context(context.session_key)
            if self.ephemeral_immediate_release_enabled and self._is_ephemeral_session(context.session_key):
                await self.release_client(context.session_key, reason="ephemeral_turn_end")

    async def _wait_for_user_input(
        self,
        context: AgentContext,
        message: "TaskNotificationMessage",
    ) -> str | None:
        """TaskNotification-based interactive input is unsupported in SDK 0.1.52."""
        raise NotImplementedError(
            "Interactive input is hook-based in claude-agent-sdk 0.1.52; "
            "TaskNotificationMessage no longer enters input-required states."
        )

    async def shutdown(self) -> None:
        """Shutdown the backend."""
        await self._stop_client_scavenger()
        session_keys = set(self._clients)
        if self._uses_session_store():
            session_keys.update(
                key
                for key in self._session_store.list_keys()
                if (entry := self._session_store.get(key)) is not None and entry.client is not None
            )
        for session_key in session_keys:
            await self.release_client(session_key, reason="shutdown")
        # Clear session contexts for compact notifications
        self._shared_resources.pop("_session_contexts", None)
        logger.info("Claude SDK backend shutdown complete")

    async def reset_session(self, session_key: str) -> None:
        """Reset a session, disconnecting client and clearing state."""
        await self.release_client(
            session_key,
            reason="session reset",
            preserve_sdk_context=False,
        )

        if self.sessions:
            session = self.sessions.get_or_create(session_key)
            snapshot = session.messages[session.last_consolidated:]
            if snapshot and self.memory_consolidator:
                await self.memory_consolidator.archive_messages(snapshot)
            session.clear()
            session.metadata.pop("sdk_session_id", None)
            self.sessions.save(session)
            self.sessions.invalidate(session_key)

    async def cancel_session(self, session_key: str) -> int:
        """Cancel active work for a session."""
        _ = session_key
        # SDK-native delegation no longer uses the legacy local spawn manager.
        return 0

    async def get_session_commands(self, session_key: str) -> list[str]:
        """Return discovered SDK slash commands for a session."""
        cached = self._get_commands_from_entry(session_key)
        if not cached:
            try:
                client = await self._get_or_create_client(session_key)
                # Long-lived clients can survive while command cache is evicted or stale.
                # Always attempt a refresh when cache is missing/empty to avoid sticky fallback.
                await self._refresh_session_commands(session_key, client)
            except Exception as e:
                logger.warning(
                    f"Failed to discover SDK slash commands for session {session_key}: {e}"
                )
                return []
        commands = self._get_commands_from_entry(session_key)
        if not commands:
            logger.warning(
                f"SDK slash commands empty for session {session_key}; /help will use fallback commands"
            )
        return commands

    async def stop_active_task(self, session_key: str) -> bool:
        """Stop the latest active SDK task for a session."""
        task_id = self._get_task_id_from_entry(session_key)
        if not task_id:
            return False
        client = self._get_client_from_entry(session_key)
        if client is None:
            return False
        try:
            await client.stop_task(task_id)
            self._set_task_id_in_entry(session_key, None)
            logger.info(f"Stopped SDK task for session {session_key}: {task_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to stop SDK task for session {session_key}: {e}")
            return False

    async def interrupt_session(self, session_key: str) -> dict[str, Any]:
        """Interrupt the SDK client for a session and return usage info.

        This immediately stops any ongoing LLM request and returns the
        token usage for the interrupted session.

        Args:
            session_key: Session identifier

        Returns:
            Dict with 'interrupted' bool and 'usage' dict (if available)
        """
        client = self._get_client_from_entry(session_key)
        if client is None:
            return {"interrupted": False, "usage": None}

        usage_info = None
        try:
            # Send interrupt signal
            await client.interrupt()
            logger.info(f"Interrupted SDK client for session {session_key}")

            # Wait for ResultMessage to get usage info (with timeout)
            from claude_agent_sdk import ResultMessage

            try:
                async with asyncio.timeout(3.0):
                    async for message in client.receive_messages():
                        if isinstance(message, ResultMessage):
                            # Extract usage info
                            if hasattr(message, "usage") and message.usage:
                                usage_info = {
                                    "input_tokens": int(getattr(message.usage, "input_tokens", 0) or 0),
                                    "output_tokens": int(getattr(message.usage, "output_tokens", 0) or 0),
                                }
                            break
            except asyncio.TimeoutError:
                logger.debug(f"Timeout waiting for ResultMessage after interrupt for session {session_key}")

        except Exception as e:
            logger.warning(f"Failed to interrupt SDK client: {e}")
            return {"interrupted": False, "usage": None}
        finally:
            # Always remove client to force fresh connection on next request
            # This prevents state inconsistency after interrupt
            await self.release_client(session_key, reason="interrupt")
            logger.info(f"[State Cleanup] session={session_key} cleaned up after interrupt")

        return {"interrupted": True, "usage": usage_info}

    async def compact_session(self, session_key: str) -> dict[str, Any]:
        """Force SDK-native context compaction for a session.

        Args:
            session_key: Session identifier

        Returns:
            Dict with compaction stats
        """
        session = self.sessions.get_or_create(session_key) if self.sessions else None
        client = await self._get_or_create_client(session_key)

        compact_stats = {
            "messages_consolidated": 0,
            "tokens_before": 0,
            "tokens_after": 0,
            "success": True,
            "message": "SDK compaction requested",
            "usage": None,
        }
        saw_result = False

        await client.query(
            "/compact",
            session_id=self._query_session_id(session_key, session),
        )

        saw_boundary = False
        saw_init = False
        stream = client.receive_response().__aiter__()
        while True:
            timeout_s = 0.35 if saw_result else 10.0
            try:
                async with asyncio.timeout(timeout_s):
                    message = await anext(stream)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                # After ResultMessage, give boundary events a short grace window only.
                if saw_result:
                    break
                # No result in a long window: stop waiting and mark failure below.
                break

            # --- protocol filter: UserMessage is never yielded upstream ---
            if isinstance(message, UserMessage):
                continue

            # --- boundary detection: discard residual pre-init messages ---
            if not saw_init:
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    saw_init = True
                    continue  # init itself is not needed for compact
                logger.warning(
                    "[Compact] Discarding stale pre-boundary message: "
                    "type=%s, session=%s",
                    type(message).__name__,
                    session_key,
                )
                continue

            if isinstance(message, ResultMessage):
                saw_result = True
                compact_stats["usage"] = getattr(message, "usage", None)
                if session is not None and message.session_id:
                    session.metadata["sdk_session_id"] = message.session_id
                    self.sessions.save(session)
                if message.is_error:
                    compact_stats["success"] = False
                    compact_stats["message"] = message.result or "SDK compact request failed"

            if isinstance(message, SystemMessage) and message.subtype == "compact_boundary":
                metadata = message.data.get("compact_metadata", {}) if isinstance(message.data, dict) else {}
                pre_tokens = metadata.get("pre_tokens")
                post_tokens = metadata.get("post_tokens")
                if isinstance(pre_tokens, int):
                    compact_stats["tokens_before"] = pre_tokens
                if isinstance(post_tokens, int):
                    compact_stats["tokens_after"] = post_tokens
                saw_boundary = True

            if saw_result and saw_boundary:
                break

        if not saw_result:
            compact_stats["success"] = False
            compact_stats["message"] = "SDK compact request did not return a result"

        return compact_stats

    async def _reset_session_client_state(self, session_key: str) -> None:
        """Reset SDK client/task state for a session after incomplete interaction."""
        task_id = self._get_task_id_from_entry(session_key)
        client = self._get_client_from_entry(session_key)
        if client is not None and task_id:
            try:
                await client.stop_task(task_id)
                logger.info(f"[Reset Session] session={session_key}, stopped task={task_id}")
            except Exception:
                logger.debug("Failed to stop active task while resetting session state")
        if client is not None:
            await self.release_client(session_key, reason="force reset session state")
        logger.info(f"[Reset Session] session={session_key} client state reset complete")

    def get_tools_summary(self) -> str:
        """Get a summary of available tools and capabilities."""
        config = self._shared_resources.get("config")
        mcp_servers = getattr(getattr(config, "tools", None), "mcp_servers", None) if config else None
        capability_summary = (
            self._capabilities.build_summary(mcp_servers=mcp_servers)
            if self._capabilities
            else "capabilities=unavailable"
        )
        policy_summary = (
            self._capability_policy.build_backend_trace("claude_sdk")
            if self._capability_policy
            else "policy=unavailable"
        )
        agent_names = []
        if self.sdk_config and self.sdk_config.agents:
            agent_names = sorted(self.sdk_config.agents.keys())
        handoff = f"handoff_agents={','.join(agent_names)}" if agent_names else "handoff_agents=0"
        # Count sessions with clients
        if self._uses_session_store():
            client_count = 0
            for k in self._session_store.list_keys():
                session_entry = self._session_store.get(k)
                if session_entry and session_entry.client:
                    client_count += 1
        else:
            client_count = len(self._clients)
        lifecycle = self._client_lifecycle.snapshot_sync()
        runtime = (
            f"connected_sessions={client_count} | "
            f"managed_clients={lifecycle['counts']['connected']} | "
            f"leaked_clients={lifecycle['counts']['leaked']} | "
            f"force_kill_total={lifecycle['force_kill_total']} | "
            f"local_tools={len(self._tool_adapter._tools) if self._tool_adapter else 0}"
        )
        return f"{capability_summary} | {policy_summary} | {handoff} | {runtime}"

    def _resolve_consolidation_provider(self) -> tuple[str, str, dict[str, str] | None]:
        """Resolve API credentials/base URL for direct consolidation calls."""
        config = self._shared_resources.get("config")
        if config is None:
            raise ValueError("Missing runtime config for consolidation")

        provider_name, _ = resolve_sdk_provider_and_model(config)
        spec = get_provider_spec(provider_name)
        if not spec:
            raise ValueError(f"Unknown provider: {provider_name}")

        provider_attr = provider_name.replace("-", "_")
        provider_config: ProviderConfig | None = getattr(config.providers, provider_attr, None)

        def _get_api_key_value(api_key):
            """Safely get API key value from either SecretStr or str."""
            if api_key is None:
                return ""
            if hasattr(api_key, "get_secret_value"):
                return api_key.get_secret_value()
            return str(api_key)

        api_key_value = _get_api_key_value(provider_config.api_key) if provider_config else ""
        if not provider_config or not api_key_value:
            raise ValueError(
                f"API key not configured for provider '{provider_name}'. "
                f"Please set providers.{provider_name}.api_key in config.json"
            )

        base_url = provider_config.api_base if provider_config.api_base else spec.default_base_url
        return api_key_value, base_url, provider_config.extra_headers

    def _resolve_consolidation_model(self) -> str:
        """Resolve model name for direct consolidation calls."""
        config = self._shared_resources.get("config")
        if config is None:
            raise ValueError("Missing runtime config for consolidation")
        _, model = resolve_sdk_provider_and_model(config)
        return model

    async def call_for_auxiliary(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        *,
        max_tokens: int = 2048,
        temperature: float | None = None,
    ) -> "LLMResponse":
        """Call LLM API directly for auxiliary tasks (memory/heartbeat/evaluator).

        This method bypasses the SDK stream loop and uses httpx to call the
        Anthropic-compatible Messages API endpoint directly.

        Args:
            messages: Chat messages in OpenAI format
            tools: Optional tools for the LLM to call
            tool_choice: Optional tool choice (e.g., "auto", "required", or {"type": "function", "function": {"name": "..."}})
            max_tokens: Max tokens for this auxiliary request
            temperature: Optional temperature override

        Returns:
            LLMResponse with content, tool_calls, finish_reason, and usage
        """
        from xbot.providers.base import LLMResponse, ToolCallRequest

        import httpx

        try:
            api_key, base_url, extra_headers = self._resolve_consolidation_provider()
            model = self._resolve_consolidation_model()
        except Exception as e:
            logger.warning(f"Consolidation config resolution failed: {e}")
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )

        # Build Anthropic API request
        # Convert OpenAI format messages to Anthropic format
        anthropic_messages = []
        system_content = None

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_content = content
            elif role in ("user", "assistant"):
                anthropic_messages.append({
                    "role": role,
                    "content": content,
                })

        request_body: dict[str, Any] = {
            "model": model,
            "max_tokens": max(1, int(max_tokens)),
            "messages": anthropic_messages,
        }
        if temperature is not None:
            request_body["temperature"] = temperature

        if system_content:
            request_body["system"] = system_content

        if tools:
            # Convert OpenAI tools format to Anthropic format
            anthropic_tools = []
            for tool in tools:
                if tool.get("type") == "function":
                    func = tool.get("function", {})
                    anthropic_tools.append({
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {"type": "object"}),
                    })
            request_body["tools"] = anthropic_tools

            # Handle tool_choice
            if tool_choice:
                if isinstance(tool_choice, dict):
                    # Forced tool call: {"type": "function", "function": {"name": "..."}}
                    if tool_choice.get("type") == "function":
                        func_name = tool_choice.get("function", {}).get("name")
                        if func_name:
                            request_body["tool_choice"] = {"type": "tool", "name": func_name}
                elif tool_choice == "auto":
                    request_body["tool_choice"] = {"type": "auto"}
                elif tool_choice == "required":
                    request_body["tool_choice"] = {"type": "any"}

        # Determine API endpoint
        # Anthropic API: https://api.anthropic.com/v1/messages
        # Compatible APIs: {base_url}/v1/messages
        if base_url:
            api_endpoint = base_url.rstrip("/")
            if not api_endpoint.endswith("/v1/messages"):
                if api_endpoint.endswith("/v1"):
                    api_endpoint = f"{api_endpoint}/messages"
                else:
                    api_endpoint = f"{api_endpoint}/v1/messages"
        else:
            api_endpoint = "https://api.anthropic.com/v1/messages"

        headers: dict[str, str] = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items()})

        retry_delays = (0.5, 1.0, 2.0)
        retryable_statuses = {429, 500, 502, 503, 504}
        data: dict[str, Any] | None = None
        for attempt in range(len(retry_delays) + 1):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        api_endpoint,
                        headers=headers,
                        json=request_body,
                    )
                    response.raise_for_status()
                    body = response.json()
                data = body if isinstance(body, dict) else {}
                break
            except httpx.HTTPStatusError as e:
                code = e.response.status_code if e.response is not None else 0
                if code in retryable_statuses and attempt < len(retry_delays):
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                logger.warning(f"Consolidation HTTP error {code}: {e}")
                return LLMResponse(
                    content=f"Error calling LLM: {str(e)}",
                    finish_reason="error",
                )
            except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as e:
                if attempt < len(retry_delays):
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                logger.warning(f"Consolidation network error: {e}")
                return LLMResponse(
                    content=f"Error calling LLM: {str(e)}",
                    finish_reason="error",
                )
            except Exception as e:
                logger.warning(f"Consolidation request failed: {e}")
                return LLMResponse(
                    content=f"Error calling LLM: {str(e)}",
                    finish_reason="error",
                )

        if data is None:
            return LLMResponse(
                content="Error calling LLM: no response received",
                finish_reason="error",
            )

        # Parse Anthropic response
        content_parts = []
        tool_calls = []
        finish_reason = "stop"

        for block in data.get("content", []):
            block_type = block.get("type")

            if block_type == "text":
                content_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                tool_calls.append(ToolCallRequest(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input", {}),
                ))

        # Map stop_reason to finish_reason
        stop_reason = data.get("stop_reason")
        if stop_reason == "tool_use":
            finish_reason = "tool_calls"
        elif stop_reason == "end_turn":
            finish_reason = "stop"
        elif stop_reason == "max_tokens":
            finish_reason = "length"

        usage = data.get("usage", {})

        return LLMResponse(
            content="".join(content_parts) if content_parts else None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage={
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            },
        )

    async def call_for_consolidation(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> "LLMResponse":
        """Backward-compatible wrapper for memory consolidation calls."""
        return await self.call_for_auxiliary(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=2048,
        )

    # === SDK Session Management ===

    async def delete_sdk_session(self, session_key: str) -> dict[str, Any]:
        """Delete an SDK session file.

        This removes the SDK session's JSONL file permanently.

        Args:
            session_key: The session key (e.g., "telegram:123456")

        Returns:
            Dict with keys:
            - deleted: True if successful
            - sdk_session_id: The SDK session ID that was deleted
            - error: Error message if failed
        """
        if not session_key:
            return {
                "deleted": False,
                "sdk_session_id": None,
                "error": "No SDK session found",
            }

        async with self._clients_lock:
            sdk_session_id = self._resolve_sdk_session_id(session_key)

        if not sdk_session_id:
            return {
                "deleted": False,
                "sdk_session_id": None,
                "error": "No SDK session found",
            }

        try:
            from claude_agent_sdk import delete_session

            delete_session(sdk_session_id)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(f"[SDK Session] Failed to delete SDK session: {e}")
            return {
                "deleted": False,
                "sdk_session_id": sdk_session_id,
                "error": str(e),
            }

        async with self._clients_lock:
            await self._clear_sdk_session_state(session_key, sdk_session_id)

        logger.info(f"[SDK Session] Deleted SDK session {sdk_session_id} for {session_key}")

        return {
            "deleted": True,
            "sdk_session_id": sdk_session_id,
            "error": None,
        }

    async def fork_sdk_session(
        self,
        session_key: str,
        up_to_message_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Fork an SDK session into a new branch.

        Args:
            session_key: The session key to fork
            up_to_message_id: Optional message ID to fork up to
            title: Optional title for the forked session

        Returns:
            Dict with keys:
            - forked: True if successful
            - new_sdk_session_id: The new SDK session ID
            - error: Error message if failed
        """
        if not session_key:
            return {
                "forked": False,
                "original_sdk_session_id": None,
                "new_sdk_session_id": None,
                "new_session_key": None,
                "error": "No SDK session found",
            }

        async with self._clients_lock:
            sdk_session_id = self._resolve_sdk_session_id(session_key)

        if not sdk_session_id:
            return {
                "forked": False,
                "original_sdk_session_id": None,
                "new_sdk_session_id": None,
                "new_session_key": None,
                "error": "No SDK session found",
            }

        try:
            from claude_agent_sdk import fork_session

            result = fork_session(
                sdk_session_id,
                up_to_message_id=up_to_message_id,
                title=title,
            )
        except Exception as e:
            logger.error(f"[SDK Session] Failed to fork SDK session: {e}")
            return {
                "forked": False,
                "original_sdk_session_id": sdk_session_id,
                "new_sdk_session_id": None,
                "new_session_key": None,
                "error": str(e),
            }

        new_sdk_session_id = result.session_id
        new_session_key = f"{session_key}_fork_{uuid.uuid4().hex[:8]}"
        original_context = self._get_context_by_session_key(session_key)

        async with self._clients_lock:
            if self._uses_session_store():
                new_entry = self._get_or_create_entry(new_session_key)
                if original_context:
                    new_entry.channel, new_entry.chat_id = original_context
                self._session_store.set_sdk_session_id(new_session_key, new_sdk_session_id)
            else:
                self._get_session_contexts()[new_session_key] = new_sdk_session_id
                self._get_sdk_session_index()[new_sdk_session_id] = new_session_key

            if self.sessions:
                try:
                    session = self.sessions.get_or_create(new_session_key)
                    session.metadata["sdk_session_id"] = new_sdk_session_id
                    session.metadata["forked_from"] = session_key
                    session.metadata["forked_up_to"] = up_to_message_id
                    session.metadata["forked_at"] = datetime.now().isoformat()
                    self.sessions.save(session)
                except Exception as e:
                    if self._uses_session_store():
                        entry = self._get_entry(new_session_key)
                        if entry:
                            self._session_store.clear_sdk_session_id(new_session_key)
                    else:
                        self._get_session_contexts().pop(new_session_key, None)
                        self._get_sdk_session_index().pop(new_sdk_session_id, None)
                    logger.error(f"[SDK Session] Failed to persist fork metadata: {e}")
                    return {
                        "forked": False,
                        "original_sdk_session_id": sdk_session_id,
                        "new_sdk_session_id": None,
                        "new_session_key": new_session_key,
                        "error": f"Failed to save fork metadata: {e}",
                    }

        logger.info(
            f"[SDK Session] Forked SDK session {sdk_session_id} -> {new_sdk_session_id}"
        )

        return {
            "forked": True,
            "original_sdk_session_id": sdk_session_id,
            "new_sdk_session_id": new_sdk_session_id,
            "new_session_key": new_session_key,
            "error": None,
        }

    async def list_sdk_sessions(
        self,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List SDK sessions.

        Args:
            limit: Maximum number of sessions to return
            offset: Number of sessions to skip

        Returns:
            Dict with keys:
            - sessions: List of session info dicts
            - has_more: True if more sessions available
            - error: Error message if failed
        """
        limit = max(1, min(limit, 100))
        offset = max(offset, 0)

        try:
            from claude_agent_sdk import list_sessions

            sessions = list_sessions(limit=limit + 1, offset=offset)

            # Convert to dict format
            session_list = []
            for s in sessions[:limit]:
                session_list.append({
                    "session_id": s.session_id,
                    "title": s.custom_title or s.summary,
                    "created_at": datetime.fromtimestamp(s.created_at).isoformat() if s.created_at else None,
                    "updated_at": datetime.fromtimestamp(s.last_modified).isoformat() if s.last_modified else None,
                    "file_size": s.file_size,
                })

            has_more = len(sessions) > limit

            return {
                "sessions": session_list,
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "error": None,
            }

        except Exception as e:
            logger.error(f"[SDK Session] Failed to list SDK sessions: {e}")
            return {
                "sessions": [],
                "limit": limit,
                "offset": offset,
                "has_more": False,
                "error": str(e),
            }
