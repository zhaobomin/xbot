"""Claude SDK Agent Backend.

This backend uses the Claude Agent SDK to provide native Claude integration
with support for Anthropic and Anthropic-compatible providers (Aliyun Coding Plan, Alrun).
"""

from __future__ import annotations

import asyncio
import base64
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from loguru import logger

from xbot.agent.backends.delegation import DelegationTrace
from xbot.agent.backends.message_converter import MessageConverter
from xbot.agent.backends.options_builder import OptionsBuilder
from xbot.agent.capabilities import CapabilityCatalog, canonical_tool_name
from xbot.agent.capability_policy import CapabilityPolicy
from xbot.agent.context import ContextBuilder
from xbot.agent.handoff_policy import HandoffDecision, HandoffPolicy
from xbot.agent.memory import MemoryConsolidator
from xbot.agent.event_formatter import (
    format_compact_event,
    format_task_notification,
)
from xbot.agent.protocol import AgentBackend, AgentContext, AgentResponse
from xbot.agent.trace import append_session_trace
from xbot.agent.tools.base import Tool
from xbot.config.provider_registry import get_provider_spec
from xbot.config.sdk_resolver import detect_provider_from_model, resolve_sdk_provider_and_model
from xbot.config.schema import AgentsConfig, ProviderConfig
from xbot.session.manager import SessionManager
from xbot.utils.helpers import detect_image_mime
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
    )

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
    )

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    logger.warning("claude-agent-sdk not installed. Claude SDK backend will not be available.")


# =============================================================================
# Main Backend Class
# =============================================================================

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
    _INPUT_REQUIRED_STATUSES = {
        "input_required",
        "awaiting_input",
        "waiting_for_input",
        "confirmation_required",
        "approval_required",
    }
    _MAX_QUERY_RETRIES = 3  # Max retries when SDK returns stale task notifications instead of ResultMessage

    # Client pool management constants
    MAX_CLIENTS = 100  # Maximum number of concurrent clients
    CLIENT_TTL_SECONDS = 3600  # 1 hour TTL for idle clients

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
            from xbot.agent.skill_to_mcp import SkillToMCPConverter
            workspace = shared_resources.get("workspace", config.defaults.workspace)
            self._skill_converter = SkillToMCPConverter(workspace)
        except ImportError:
            logger.warning("SkillToMCPConverter not available")

        # Initialize SkillManager for hot-reload and Python skill support
        try:
            from xbot.agent.skill_manager import SkillManager
            self._skill_manager = SkillManager(workspace_path)
            # Replace the ContextBuilder's skills_loader with the one managed by SkillManager
            if self._context_builder:
                self._context_builder.skills = self._skill_manager.skills_loader
            logger.info("[Backend] SkillManager initialized, version={}", self._skill_manager.version)
        except Exception as e:
            logger.warning("SkillManager not available: {}", e)

        # Initialize tool adapter
        try:
            from xbot.agent.tool_adapter import ToolAdapter
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
                from xbot.agent.permission_handler import create_permission_handler

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
        session_contexts = shared_resources.get("_session_contexts", {})

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
        async with self._clients_lock:
            # Get current model for model change detection
            current_model = self._options_builder._get_model_name() if self._options_builder else None
            # Get current skills version for hot-reload detection
            current_skills_version = self._skill_manager.version if self._skill_manager else None

            # Check if client exists and model/skills haven't changed
            if session_key in self._clients:
                cached_model = self._client_models.get(session_key)
                cached_skills = self._client_skills_versions.get(session_key)
                model_ok = cached_model == current_model
                skills_ok = cached_skills == current_skills_version

                if model_ok and skills_ok:
                    self._client_last_used[session_key] = time.time()
                    logger.debug(f"[Client] Reusing existing client for session={session_key}, model={current_model}")
                    return self._clients[session_key]
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
                        try:
                            await old_client.disconnect()
                        except Exception:
                            pass

            # Cleanup expired clients before creating new one
            await self._cleanup_stale_clients_unlocked()

            # Evict LRU client if at capacity
            if len(self._clients) >= self.MAX_CLIENTS:
                await self._evict_lru_client_unlocked()

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

            self._clients[session_key] = client
            self._client_last_used[session_key] = time.time()
            self._client_models[session_key] = current_model  # Track the model used
            self._client_skills_versions[session_key] = current_skills_version  # Track skills version
            logger.info(f"[Client] Client created for session={session_key}, model={current_model}")
            return client

    def _remove_client_state(self, session_key: str) -> "ClaudeSDKClient | None":
        """Remove all tracked state for a session and return the client (if any).

        Centralises the cleanup of the six parallel per-session dicts so that
        every eviction / shutdown / error path stays in sync.  The caller is
        responsible for disconnecting the returned client.
        """
        client = self._clients.pop(session_key, None)
        self._client_last_used.pop(session_key, None)
        self._client_models.pop(session_key, None)
        self._client_skills_versions.pop(session_key, None)
        self._session_commands.pop(session_key, None)
        self._active_task_ids.pop(session_key, None)
        # Clear session context for compact notifications
        session_contexts = self._shared_resources.get("_session_contexts")
        if session_contexts is not None:
            session_contexts.pop(session_key, None)
        return client

    # -- Multimodal (image) helpers ----------------------------------------

    _MAX_IMAGE_BYTES = 20 * 1024 * 1024  # Anthropic 20 MB limit
    _SUPPORTED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

    def _build_image_content_blocks(self, media: list[str]) -> list[dict[str, Any]]:
        """Convert local image files to Anthropic-format image content blocks."""
        blocks: list[dict[str, Any]] = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                logger.warning("Image file not found, skipping: {}", path)
                continue
            if p.stat().st_size > self._MAX_IMAGE_BYTES:
                logger.warning("Image too large (>20 MB), skipping: {}", path)
                continue
            raw = p.read_bytes()
            mime = detect_image_mime(raw)
            if not mime or mime not in self._SUPPORTED_IMAGE_MIMES:
                logger.warning("Unsupported image format ({}), skipping: {}", mime, path)
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

    def _build_file_content_blocks(
        self, media: list[str]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Classify media into file reference blocks and image paths.

        Returns ``(file_ref_blocks, image_paths)`` where *file_ref_blocks*
        is a list of text content blocks containing path references for
        non-image files, and *image_paths* is the subset that should be
        sent as base64 image content blocks.
        """
        image_paths: list[str] = []
        file_refs: list[str] = []
        for path in media:
            ft = classify_file(path)
            if ft is FileType.IMAGE:
                image_paths.append(path)
            else:
                ref = format_file_reference(path)
                file_refs.append(ref)
                logger.info("[Backend] File reference: {}", ref)
        if not file_refs:
            return [], image_paths
        header = "用户附加了以下文件，你可以通过工具读取或修改这些文件:"
        block_text = header + "\n" + "\n".join(file_refs)
        return [{"type": "text", "text": block_text}], image_paths

    async def _build_multimodal_query(
        self,
        prompt: str,
        media: list[str],
        session_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield a single user message with image blocks and/or file references."""
        file_ref_blocks, image_paths = self._build_file_content_blocks(media)
        image_blocks = self._build_image_content_blocks(image_paths)

        all_blocks = image_blocks + file_ref_blocks
        if not all_blocks:
            # Everything failed – fall back to plain text
            content: str | list[dict[str, Any]] = prompt
        else:
            content = all_blocks + [{"type": "text", "text": prompt}]
        yield {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": session_id,
        }

    async def _cleanup_stale_clients_unlocked(self) -> int:
        """Remove clients that have been idle longer than TTL.

        Must be called while holding _clients_lock.

        Returns:
            Number of clients removed.
        """
        now = time.time()
        stale_keys = [
            key for key, last_used in self._client_last_used.items()
            if now - last_used > self.CLIENT_TTL_SECONDS
        ]

        for key in stale_keys:
            client = self._remove_client_state(key)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    logger.debug(f"Ignoring error while disconnecting stale client for session {key}")

            # Notify runtime to sync state
            on_cleanup = self._shared_resources.get("on_backend_client_cleanup")
            if on_cleanup:
                try:
                    await on_cleanup(key)
                except Exception as e:
                    logger.debug(f"Error in backend client cleanup callback: {e}")

        if stale_keys:
            logger.info(f"Cleaned up {len(stale_keys)} stale client(s) (TTL={self.CLIENT_TTL_SECONDS}s)")

        return len(stale_keys)

    async def _evict_lru_client_unlocked(self) -> None:
        """Evict the least recently used client.

        Must be called while holding _clients_lock.
        """
        if not self._client_last_used:
            return

        # Find the oldest (LRU) client
        lru_key = min(self._client_last_used, key=self._client_last_used.get)

        client = self._remove_client_state(lru_key)

        if client is not None:
            try:
                await client.disconnect()
                logger.info(f"Evicted LRU client for session {lru_key} (pool at capacity)")
            except Exception:
                logger.debug(f"Ignoring error while disconnecting evicted client for session {lru_key}")

        # Notify runtime to sync state
        on_cleanup = self._shared_resources.get("on_backend_client_cleanup")
        if on_cleanup:
            try:
                await on_cleanup(lru_key)
            except Exception as e:
                logger.debug(f"Error in backend client cleanup callback: {e}")

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
        self._session_commands[session_key] = commands

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

    def _record_delegation_trace(
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
        
        async def _add_trace():
            async with self._delegation_traces_lock:
                self._delegation_traces.append(trace)
                # Keep only last 100 traces
                if len(self._delegation_traces) > 100:
                    self._delegation_traces = self._delegation_traces[-100:]
        
        # Fire and forget
        asyncio.create_task(_add_trace())
        
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
        # Maps session_key to (channel, chat_id) tuple
        # This ensures hooks can find the context when processing messages
        session_contexts = self._shared_resources.setdefault("_session_contexts", {})
        context_tuple = (context.channel, context.chat_id)
        session_contexts[context.session_key] = context_tuple
        # Prevent unbounded growth: keep only the most recent entries
        _MAX_SESSION_CONTEXTS = 500
        if len(session_contexts) > _MAX_SESSION_CONTEXTS:
            # Remove oldest entries (dict preserves insertion order in Python 3.7+)
            excess = len(session_contexts) - _MAX_SESSION_CONTEXTS
            for _ in range(excess):
                session_contexts.pop(next(iter(session_contexts)))
        logger.info(
            "[Session Context] Set mapping: session_key='{}' -> (channel='{}', chat_id='{}'). "
            "Current keys in session_contexts: {}",
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

        # If SDK session ID already exists from previous turn, also map it
        # This ensures hooks can find context when SDK returns the UUID instead of session_key
        if session is not None:
            existing_sdk_id = session.metadata.get("sdk_session_id")
            if existing_sdk_id:
                session_contexts[existing_sdk_id] = context_tuple

        # Check for reconnect pending state from previous error
        reconnect_hint = None
        if session is not None and session.metadata.pop("_reconnect_pending", None):
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
                asyncio.create_task(
                    self.memory_consolidator.maybe_consolidate_by_tokens(session)
                )

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
                self._record_delegation_trace(context.session_key, decision)
                
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
            user_cancelled = False  # Track if user cancelled during input_required

            while query_retry < self._MAX_QUERY_RETRIES:
                client = await self._get_or_create_client(context.session_key)
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
                if context.media:
                    logger.info(f"[Backend Process] Multimodal query with {len(context.media)} media file(s)")
                    await client.query(
                        self._build_multimodal_query(prompt, context.media, _query_session_id),
                        session_id=_query_session_id,
                    )
                else:
                    await client.query(prompt, session_id=_query_session_id)
                logger.info(f"[Backend Process] Query sent, waiting for response for session={context.session_key}")

                first_sdk_message_logged = False
                received_result = False  # Reset for each retry

                async for message in client.receive_response():
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
                            "[SDK Init] session={}, commands={}, data_keys={}, data={}",
                            context.session_key,
                            commands,
                            list(message.data.keys()) if isinstance(message.data, dict) else "N/A",
                            message.data,
                        )
                        self._session_commands[context.session_key] = commands

                        # Try to extract session_id from init message for early mapping
                        # This ensures hooks can find context from the first message
                        sdk_session_id = message.data.get("session_id") if isinstance(message.data, dict) else None
                        if sdk_session_id:
                            logger.info(
                                "[SDK Init] Found session_id in init message: sdk_session_id='{}', mapping to context",
                                sdk_session_id,
                            )
                            session_contexts[sdk_session_id] = context_tuple
                            # Also save to session metadata
                            if session is not None:
                                session.metadata["sdk_session_id"] = sdk_session_id
                                self.sessions.save(session)
                    if isinstance(message, TaskStartedMessage) and message.task_id:
                        self._active_task_ids[context.session_key] = message.task_id
                    if (
                        isinstance(message, TaskNotificationMessage)
                        and message.status in {"completed", "failed", "stopped"}
                    ):
                        self._active_task_ids.pop(context.session_key, None)
                    if (
                        isinstance(message, TaskNotificationMessage)
                        and str(message.status or "").lower() in self._INPUT_REQUIRED_STATUSES
                    ):
                        user_input = await self._wait_for_user_input(context, message)
                        if user_input:
                            await client.query(
                                user_input,
                                session_id=self._query_session_id(context.session_key, session),
                            )
                        else:
                            # User cancelled or no actionable input: end current turn to avoid
                            # holding the session lock while the SDK waits for further input.
                            # Also reset client/task state to avoid dirty carry-over into next turn.
                            await self._reset_session_client_state(context.session_key)
                            user_cancelled = True  # Mark as cancelled to skip retry
                            break
                    if is_terminal_result:
                        self._active_task_ids.pop(context.session_key, None)
                        if session is not None and message.session_id:
                            session.metadata["sdk_session_id"] = message.session_id
                            self.sessions.save(session)
                            # Also update session_contexts mapping for SDK session ID
                            # This ensures hooks can find context when SDK returns the UUID
                            session_contexts = self._shared_resources.get("_session_contexts")
                            if session_contexts is not None:
                                context_tuple = (context.channel, context.chat_id)
                                session_contexts[message.session_id] = context_tuple

                    if self._message_converter:
                        response = self._message_converter.convert(message)
                    else:
                        response = self._convert_message_legacy(message)

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

                # If user cancelled during input_required, don't retry
                if user_cancelled:
                    break  # User cancelled, exit retry loop

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
                    asyncio.create_task(
                        self.memory_consolidator.maybe_consolidate_by_tokens(session)
                    )

        except Exception as e:
            client = self._remove_client_state(context.session_key)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    logger.debug(f"Ignoring error while disconnecting failed Claude SDK session {context.session_key}")

            # Don't immediately clear sdk_session_id - preserve context for potential recovery
            # Instead, mark the session as needing recovery
            if session is not None:
                session.metadata["_reconnect_pending"] = True
                session.metadata["_last_error"] = str(e)[:500]
                session.metadata["_error_timestamp"] = datetime.now().isoformat()
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
                        fallback_prompt = (
                            "[Runtime Policy]\n"
                            "Continue on the main agent. Do not use specialist handoff for this retry.\n\n"
                            f"{context.prompt}"
                        )
                        await fallback_client.query(
                            fallback_prompt,
                            session_id=self._query_session_id(context.session_key, session),
                        )
                        async for message in fallback_client.receive_response():
                            is_terminal_result = isinstance(message, ResultMessage)
                            if isinstance(message, SystemMessage) and message.subtype == "init":
                                commands = self._extract_slash_commands(message.data)
                                logger.info(
                                    "[SDK Init Fallback] session={}, commands={}, data={}",
                                    context.session_key,
                                    commands,
                                    message.data,
                                )
                                self._session_commands[context.session_key] = commands
                                # Early mapping for hooks
                                sdk_session_id = message.data.get("session_id") if isinstance(message.data, dict) else None
                                if sdk_session_id:
                                    logger.info(
                                        "[SDK Init Fallback] Found session_id='{}', mapping to context",
                                        sdk_session_id,
                                    )
                                    session_contexts = self._shared_resources.get("_session_contexts")
                                    if session_contexts is not None:
                                        session_contexts[sdk_session_id] = (context.channel, context.chat_id)
                                    if session is not None:
                                        session.metadata["sdk_session_id"] = sdk_session_id
                                        self.sessions.save(session)
                            if isinstance(message, TaskStartedMessage) and message.task_id:
                                self._active_task_ids[context.session_key] = message.task_id
                            if (
                                isinstance(message, TaskNotificationMessage)
                                and message.status in {"completed", "failed", "stopped"}
                            ):
                                self._active_task_ids.pop(context.session_key, None)
                            if is_terminal_result:
                                self._active_task_ids.pop(context.session_key, None)
                                if session is not None and message.session_id:
                                    session.metadata["sdk_session_id"] = message.session_id
                                    self.sessions.save(session)
                                    # Also update session_contexts mapping for SDK session ID
                                    session_contexts = self._shared_resources.get("_session_contexts")
                                    if session_contexts is not None:
                                        context_tuple = (context.channel, context.chat_id)
                                        session_contexts[message.session_id] = context_tuple
                            
                            if self._message_converter:
                                response = self._message_converter.convert(message)
                            else:
                                response = self._convert_message_legacy(message)
                                
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
                            asyncio.create_task(
                                self.memory_consolidator.maybe_consolidate_by_tokens(session)
                            )
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

    async def _wait_for_user_input(
        self,
        context: AgentContext,
        message: "TaskNotificationMessage",
    ) -> str | None:
        """Ask user for input when SDK task enters input-required state."""
        import json

        summary = str(message.summary or "").strip()
        prompt = summary or "Task requires your input. Please reply to continue."
        if not summary:
            prompt = f"Task `{message.task_id or ''}` requires your input. Please reply to continue.".strip()

        status = str(message.status or "").lower()
        
        # Extract options from AskUserQuestion if available
        options = []
        question_headers = []
        multi_select = False
        
        # Try to extract questions from task_result or summary
        task_result = getattr(message, "task_result", None)
        if task_result:
            try:
                # Check if this is an AskUserQuestion tool call
                if isinstance(task_result, dict):
                    questions = task_result.get("questions", [])
                    if isinstance(questions, list):
                        for q in questions:
                            if isinstance(q, dict):
                                header = q.get("header", "")
                                if header:
                                    question_headers.append(header)
                                for opt in q.get("options", []):
                                    if isinstance(opt, dict):
                                        # Prefer label, fallback to value if label is missing
                                        option_value = opt.get("label") or opt.get("value")
                                        if option_value:
                                            options.append(option_value)
                                if q.get("multiSelect", False):
                                    multi_select = True
            except (TypeError, AttributeError, json.JSONDecodeError):
                pass
        
        # Also try to parse from summary if it looks like JSON
        if not options and summary.startswith("{"):
            try:
                data = json.loads(summary)
                questions = data.get("questions", [])
                if isinstance(questions, list):
                    for q in questions:
                        if isinstance(q, dict):
                            header = q.get("header", "")
                            if header:
                                question_headers.append(header)
                            for opt in q.get("options", []):
                                if isinstance(opt, dict):
                                    # Prefer label, fallback to value if label is missing
                                    option_value = opt.get("label") or opt.get("value")
                                    if option_value:
                                        options.append(option_value)
                            if q.get("multiSelect", False):
                                multi_select = True
            except (json.JSONDecodeError, TypeError):
                pass

        # Determine interaction kind and suggestions
        if status == "approval_required":
            interaction_kind = "approval"
            suggestions = ["允许", "拒绝"]
        elif status == "confirmation_required":
            interaction_kind = "confirmation"
            suggestions = ["确认", "取消"]
        else:
            interaction_kind = "question"
            # Use extracted options as suggestions if available
            if options:
                suggestions = options
            else:
                suggestions = ["继续", "取消"]

        timeout = float(getattr(getattr(self.sdk_config, "permission", None), "timeout", 300.0))
        response = None
        if self._permission_handler and hasattr(self._permission_handler, "request_interaction"):
            response = await self._permission_handler.request_interaction(
                kind=interaction_kind,
                prompt=prompt,
                suggestions=suggestions,
                session_key=context.session_key,
                channel=context.channel,
                chat_id=context.chat_id,
                metadata=dict(context.metadata or {}),
                timeout=timeout,
            )
        else:
            bus = self._shared_resources.get("bus")
            if bus is None:
                return None
            from xbot.bus.queue import InteractionRequest
            import uuid

            # Build metadata with valid_options for answer validation
            metadata_dict = dict(context.metadata or {})
            if options:
                metadata_dict["valid_options"] = options
            if question_headers:
                metadata_dict["question_headers"] = question_headers
            metadata_dict["multi_select"] = multi_select

            request = InteractionRequest(
                request_id=str(uuid.uuid4()),
                session_key=context.session_key,
                channel=context.channel,
                chat_id=context.chat_id,
                kind=interaction_kind,
                prompt=prompt,
                suggestions=suggestions,
                metadata=metadata_dict,
            )
            await bus.publish_interaction_request(request)
            response = await bus.wait_interaction_response(request.request_id, timeout=timeout)

        if response is None:
            return None

        content = (response.content or "").strip()

        if response.action in {"cancel", "deny"}:
            task_id = self._active_task_ids.get(context.session_key)
            if task_id:
                client = self._clients.get(context.session_key)
                if client is not None:
                    try:
                        await client.stop_task(task_id)
                    except Exception:
                        logger.debug("Failed to stop task after user cancelled interaction")
            return None

        if interaction_kind == "approval" and response.action == "allow" and not content:
            return "allow"
        if interaction_kind == "confirmation" and response.action == "confirm" and not content:
            return "confirm"
        if not content:
            return None
        return content

    def _convert_message_legacy(self, message: Any) -> AgentResponse | None:
        """Legacy message converter for backward compatibility."""
        if self._message_converter:
            return self._message_converter.convert(message)
        return None

    async def shutdown(self) -> None:
        """Shutdown the backend."""
        for session_key in list(self._clients):
            client = self._remove_client_state(session_key)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    logger.debug(f"Ignoring error while disconnecting Claude SDK session {session_key}")
        # Clear session contexts for compact notifications
        self._shared_resources.pop("_session_contexts", None)
        logger.info("Claude SDK backend shutdown complete")

    async def reset_session(self, session_key: str) -> None:
        """Reset a session, disconnecting client and clearing state."""
        client = self._remove_client_state(session_key)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                logger.debug(f"Ignoring error while disconnecting Claude SDK session {session_key}")

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
        cached = self._session_commands.get(session_key)
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
        commands = list(self._session_commands.get(session_key, []))
        if not commands:
            logger.warning(
                f"SDK slash commands empty for session {session_key}; /help will use fallback commands"
            )
        return commands

    async def stop_active_task(self, session_key: str) -> bool:
        """Stop the latest active SDK task for a session."""
        task_id = self._active_task_ids.get(session_key)
        if not task_id:
            return False
        client = self._clients.get(session_key)
        if client is None:
            return False
        try:
            await client.stop_task(task_id)
            self._active_task_ids.pop(session_key, None)
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
        client = self._clients.get(session_key)
        if client is None:
            return {"interrupted": False, "usage": None}

        usage_info = None
        try:
            # Send interrupt signal
            await client.interrupt()
            logger.info(f"Interrupted SDK client for session {session_key}")

            # Wait for ResultMessage to get usage info (with timeout)
            from claude_agent_sdk import ResultMessage
            import asyncio

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
            self._clients.pop(session_key, None)
            self._active_task_ids.pop(session_key, None)
            logger.debug(f"Removed client for session {session_key} after interrupt")

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
        }
        saw_result = False

        await client.query(
            "/compact",
            session_id=self._query_session_id(session_key, session),
        )

        saw_boundary = False
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

            if isinstance(message, ResultMessage):
                saw_result = True
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
        task_id = self._active_task_ids.get(session_key)
        client = self._remove_client_state(session_key)
        if client is not None and task_id:
            try:
                await client.stop_task(task_id)
            except Exception:
                logger.debug("Failed to stop active task while resetting session state")
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                logger.debug("Failed to disconnect client while resetting session state")

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
        runtime = (
            f"connected_sessions={len(self._clients)} | "
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
