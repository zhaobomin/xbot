"""Claude SDK Agent Backend.

This backend uses the Claude Agent SDK to provide native Claude integration
with support for Anthropic and Anthropic-compatible providers (Aliyun Coding Plan, Alrun).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

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

logger = logging.getLogger(__name__)

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
# Type Definitions
# =============================================================================

@dataclass
class DelegationTrace:
    """Trace record for delegation decisions."""
    timestamp: str
    session_key: str
    decision_mode: str
    reason: str
    candidates: list[str]
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "session_key": self.session_key,
            "mode": self.decision_mode,
            "reason": self.reason,
            "candidates": self.candidates,
        }


# =============================================================================
# Message Converters
# =============================================================================

class MessageConverter:
    """Converts SDK messages to AgentResponse objects.
    
    This class encapsulates all message type conversion logic,
    making it easier to test and maintain.
    """
    
    def __init__(self, handoff_policy: HandoffPolicy | None, capabilities: CapabilityCatalog | None, config: Any):
        self._handoff_policy = handoff_policy
        self._capabilities = capabilities
        self._config = config
    
    def convert(self, message: Any) -> AgentResponse | None:
        """Convert SDK message to AgentResponse.
        
        Args:
            message: SDK message object
            
        Returns:
            AgentResponse or None if the message type is not relevant
        """
        if isinstance(message, AssistantMessage):
            return self._convert_assistant_message(message)
        elif isinstance(message, StreamEvent):
            return self._convert_stream_event(message)
        elif isinstance(message, TaskStartedMessage):
            return self._convert_task_started(message)
        elif isinstance(message, TaskProgressMessage):
            return self._convert_task_progress(message)
        elif isinstance(message, TaskNotificationMessage):
            return self._convert_task_notification(message)
        elif isinstance(message, SystemMessage):
            return self._convert_system_message(message)
        elif isinstance(message, ResultMessage):
            return self._convert_result_message(message)
        return None

    def _convert_system_message(self, message: "SystemMessage") -> AgentResponse | None:
        """Convert generic SystemMessage into user-visible progress when useful."""
        if message.subtype == "compact_boundary":
            compact_metadata = message.data.get("compact_metadata", {}) if isinstance(message.data, dict) else {}
            pre_tokens = compact_metadata.get("pre_tokens")
            post_tokens = compact_metadata.get("post_tokens")
            trigger = compact_metadata.get("trigger")
            text = format_compact_event(
                pre_tokens=pre_tokens if isinstance(pre_tokens, int) else None,
                post_tokens=post_tokens if isinstance(post_tokens, int) else None,
                trigger=trigger if isinstance(trigger, str) else None,
            )
            return AgentResponse(
                content="",
                progress_texts=[text],
                raw_message=message,
                event_type="system",
                event_data={
                    "subtype": "compact_boundary",
                    "compact_metadata": compact_metadata,
                },
            )

        # Keep other system events silent unless we have an explicit mapping.
        return None
    
    def _convert_assistant_message(self, message: "AssistantMessage") -> AgentResponse:
        """Convert AssistantMessage to AgentResponse."""
        text = ""
        progress_texts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for block in message.content:
            if isinstance(block, TextBlock):
                text += block.text
            elif isinstance(block, ThinkingBlock):
                if block.thinking:
                    progress_texts.append(f"Thinking: {block.thinking}")
            elif isinstance(block, ToolUseBlock):
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                    "kind": self._classify_tool_name(block.name),
                })

        event_type = ""
        event_data: dict[str, Any] | None = None
        if progress_texts and not text and not tool_calls:
            event_type = "thinking"
            event_data = {"thinking_chunks": len(progress_texts)}
        elif tool_calls:
            event_type = "tool_call"
            event_data = {"tool_calls": len(tool_calls)}
        elif text:
            event_type = "content"

        return AgentResponse(
            content=text,
            progress_texts=progress_texts,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason="tool_use" if tool_calls else "stop",
            raw_message=message,
            event_type=event_type,
            event_data=event_data,
        )
    
    def _convert_stream_event(self, message: "StreamEvent") -> AgentResponse | None:
        """Convert StreamEvent to AgentResponse."""
        event = message.event or {}
        if event.get("type") != "content_block_delta":
            return None
        delta = event.get("delta", {})
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            text = delta.get("text", "")
            if not text:
                return None
            return AgentResponse(
                content="",
                is_delta=True,
                delta_content=text,
                raw_message=message,
                event_type="content_delta",
            )
        if delta_type == "thinking_delta":
            thinking = delta.get("thinking", "") or delta.get("text", "")
            if thinking:
                return AgentResponse(
                    content="",
                    progress_texts=[f"Thinking: {thinking}"],
                    raw_message=message,
                    event_type="thinking",
                )
        return None
    
    def _convert_task_started(self, message: "TaskStartedMessage") -> AgentResponse:
        """Convert TaskStartedMessage to AgentResponse."""
        progress_texts = [f"Running: {message.description}"] if message.description else []
        if self._handoff_policy:
            if handoff_trace := self._handoff_policy.format_task_trace(
                message.description, message.task_type
            ):
                progress_texts.append(handoff_trace)
        return AgentResponse(
            content="",
            progress_texts=progress_texts,
            raw_message=message,
            event_type="task",
            event_data={
                "status": "started",
                "task_id": message.task_id,
                "task_type": message.task_type,
            },
        )
    
    def _convert_task_progress(self, message: "TaskProgressMessage") -> AgentResponse:
        """Convert TaskProgressMessage to AgentResponse."""
        tool_calls = None
        if message.last_tool_name:
            tool_calls = [{
                "name": message.last_tool_name,
                "input": {},
                "kind": self._classify_tool_name(message.last_tool_name),
            }]
        return AgentResponse(
            content="",
            progress_texts=[f"Running: {message.description}"] if message.description else [],
            tool_calls=tool_calls,
            finish_reason="tool_use" if tool_calls else "stop",
            raw_message=message,
            event_type="task",
            event_data={
                "status": "progress",
                "task_id": message.task_id,
                "last_tool_name": message.last_tool_name,
            },
        )
    
    def _convert_task_notification(self, message: "TaskNotificationMessage") -> AgentResponse:
        """Convert TaskNotificationMessage to AgentResponse."""
        progress_texts = [
            format_task_notification(
                status=message.status,
                summary=message.summary,
                task_id=message.task_id,
                output_file=message.output_file,
            )
        ]
        if self._handoff_policy:
            if handoff_trace := self._handoff_policy.format_task_trace(str(message.summary or message.status)):
                progress_texts.append(handoff_trace)
        return AgentResponse(
            content="",
            progress_texts=progress_texts,
            raw_message=message,
            event_type="task",
            event_data={
                "status": message.status,
                "task_id": message.task_id,
                "output_file": message.output_file,
            },
        )
    
    def _convert_result_message(self, message: "ResultMessage") -> AgentResponse:
        """Convert ResultMessage to AgentResponse."""
        usage = None
        if hasattr(message, "usage") and message.usage:
            if isinstance(message.usage, dict):
                usage = {
                    "input_tokens": int(message.usage.get("input_tokens", 0) or 0),
                    "output_tokens": int(message.usage.get("output_tokens", 0) or 0),
                }
            else:
                usage = {
                    "input_tokens": int(getattr(message.usage, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(message.usage, "output_tokens", 0) or 0),
                }
        content = message.result if isinstance(message.result, str) else ""
        return AgentResponse(
            content=content,
            finish_reason="stop",
            usage=usage,
            raw_message=message,
            event_type="result",
            event_data={
                "stop_reason": message.stop_reason,
                "num_turns": message.num_turns,
                "total_cost_usd": message.total_cost_usd,
            },
        )
    
    def _classify_tool_name(self, name: str) -> str:
        """Classify a tool name into its kind (tool, skill, mcp)."""
        normalized = canonical_tool_name(name)
        has_external_mcp = bool(
            getattr(getattr(self._config, "tools", None), "mcp_servers", None)
        ) if self._config else False
        if self._capabilities:
            kind = self._capabilities.classify_tool_name(
                normalized, assume_unknown_mcp=has_external_mcp
            )
            if kind != "tool" or normalized in self._capabilities.builtin_tool_names():
                return kind
        return "mcp" if normalized.startswith("mcp_") else "tool"


# =============================================================================
# Options Builder
# =============================================================================

class OptionsBuilder:
    """Builds ClaudeAgentOptions from configuration.

    This class encapsulates the options building logic,
    separating concerns and improving testability.
    """

    def __init__(
        self,
        shared_resources: dict[str, Any],
        sdk_config: Any,
        skill_converter: Any,
        tool_adapter: Any,
        sessions: SessionManager | None,
        context_builder: ContextBuilder | None,
        handoff_policy: HandoffPolicy | None,
        capability_policy: CapabilityPolicy | None,
        permission_handler: Any = None,
    ):
        self._shared_resources = shared_resources
        self._sdk_config = sdk_config
        self._skill_converter = skill_converter
        self._tool_adapter = tool_adapter
        self._sessions = sessions
        self._context_builder = context_builder
        self._handoff_policy = handoff_policy
        self._capability_policy = capability_policy
        self._permission_handler = permission_handler

    def build(
        self,
        session_key: str | None = None,
        *,
        include_agents: bool = True,
    ) -> "ClaudeAgentOptions":
        """Build ClaudeAgentOptions from configuration."""
        from claude_agent_sdk import ClaudeAgentOptions

        env = self._build_env_config()
        model = self._get_model_name()
        mcp_servers = self._build_mcp_servers()
        sdk_agents = self._build_sdk_agents() if include_agents else None
        resume_session = self._get_resume_session(session_key)

        # Build can_use_tool callback if permission handler is available
        can_use_tool = None
        if self._permission_handler:
            can_use_tool = self._permission_handler.build_can_use_tool_callback()

        config = self._shared_resources.get("config")
        defaults = config.agents.defaults

        # Get disallowed_tools from config (default: disable SDK WebFetch/WebSearch)
        disallowed_tools = list(getattr(self._sdk_config, "disallowed_tools", ["WebFetch", "WebSearch"]))

        # Build hooks including compact notification hook
        hooks = self._build_hooks()

        return ClaudeAgentOptions(
            cwd=self._shared_resources.get("workspace", defaults.workspace),
            model=model,
            max_turns=self._sdk_config.max_turns,
            permission_mode=self._sdk_config.permission_mode,
            include_partial_messages=getattr(self._sdk_config, "include_partial_messages", False),
            resume=resume_session,
            mcp_servers=mcp_servers if mcp_servers else None,
            agents=sdk_agents,
            hooks=hooks,
            system_prompt=self._build_system_prompt(),
            env=env,
            can_use_tool=can_use_tool,
            disallowed_tools=disallowed_tools,
        )

    def _build_hooks(self) -> dict[str, list] | None:
        """Build hooks configuration including compact notification."""
        # Start with user-configured hooks
        hooks: dict[str, list] = dict(self._sdk_config.hooks or {})

        # Add PreCompact hook if compact_notify is enabled
        if getattr(self._sdk_config, "compact_notify", True):
            from xbot.agent.hooks import CompactHookHandler
            from xbot.bus.events import OutboundMessage

            def send_compact_notification(session_key: str, message: str) -> None:
                """Send compact notification to the user's channel."""
                bus = self._shared_resources.get("bus")
                if bus is None:
                    logger.debug(f"No bus available for compact notification: {session_key}")
                    return

                # Look up channel and chat_id for this session
                session_contexts = self._shared_resources.get("_session_contexts", {})
                context_info = session_contexts.get(session_key)
                if context_info is None:
                    logger.debug(f"No context info for session: {session_key}")
                    return

                channel, chat_id = context_info
                # Fire and forget - send notification asynchronously
                import asyncio
                async def _send():
                    try:
                        await bus.publish_outbound(
                            OutboundMessage(
                                channel=channel,
                                chat_id=chat_id,
                                content=message,
                                metadata={"_progress": True, "_event_type": "system"},
                            )
                        )
                        logger.debug(f"Sent compact notification to {channel}:{chat_id}")
                    except Exception as e:
                        logger.warning(f"Failed to send compact notification: {e}")

                # Schedule the send without blocking the hook
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(_send())
                except RuntimeError:
                    logger.debug("No event loop available for compact notification")

            compact_handler = CompactHookHandler(
                enabled=True,
                message_callback=send_compact_notification,
            )
            hooks.setdefault("PreCompact", []).append({"hooks": [compact_handler]})

        return hooks if hooks else None
    
    def _build_env_config(self) -> dict[str, str]:
        """Build environment configuration for SDK."""
        api_key, base_url = self._get_provider_config()
        env = dict(getattr(self._sdk_config, "env", {}) or {})
        env["ANTHROPIC_API_KEY"] = api_key
        if base_url:
            normalized_base_url = base_url.rstrip("/")
            if normalized_base_url.endswith("/v1/messages"):
                normalized_base_url = normalized_base_url[: -len("/v1/messages")]
            elif normalized_base_url.endswith("/v1"):
                normalized_base_url = normalized_base_url[: -len("/v1")]
            env["ANTHROPIC_BASE_URL"] = normalized_base_url
        return env
    
    def _build_mcp_servers(self) -> dict[str, Any]:
        """Build MCP servers configuration."""
        config = self._shared_resources.get("config")
        mcp_servers: dict[str, Any] = {}

        if config.tools.mcp_servers:
            # Convert MCPServerConfig objects to dicts for JSON serialization
            for name, server_config in config.tools.mcp_servers.items():
                if hasattr(server_config, "model_dump"):
                    mcp_servers[name] = server_config.model_dump(exclude_none=True)
                else:
                    mcp_servers[name] = server_config
        
        if self._skill_converter:
            skills_mcp = self._skill_converter.convert_all_skills()
            mcp_servers.update(skills_mcp)
        
        if self._tool_adapter:
            tools_mcp = self._tool_adapter.create_mcp_server()
            mcp_servers.update(tools_mcp)
        
        return mcp_servers
    
    def _get_resume_session(self, session_key: str | None) -> str | None:
        """Get resume session ID if available."""
        if session_key and self._sessions:
            session = self._sessions.get_or_create(session_key)
            return session.metadata.get("sdk_session_id")
        return None
    
    def _get_provider_config(self) -> tuple[str, str]:
        """Get provider API key and base URL."""
        config = self._shared_resources.get("config")
        provider_name, _ = resolve_sdk_provider_and_model(config)

        spec = get_provider_spec(provider_name)
        if not spec:
            raise ValueError(f"Unknown provider: {provider_name}")

        provider_attr = provider_name.replace("-", "_")
        provider_config: ProviderConfig | None = getattr(config.providers, provider_attr, None)

        if not provider_config or not provider_config.api_key:
            raise ValueError(
                f"API key not configured for provider '{provider_name}'. "
                f"Please set providers.{provider_name}.api_key in config.json"
            )
        
        api_key = provider_config.api_key
        base_url = provider_config.api_base if provider_config.api_base else spec.default_base_url

        return api_key, base_url
    
    def _get_model_name(self) -> str:
        """Get the model name with provider-specific transformations."""
        config = self._shared_resources.get("config")
        _, model = resolve_sdk_provider_and_model(config)
        return model
    
    def _detect_provider_from_model(self, model: str) -> str:
        """Detect provider from model name."""
        return detect_provider_from_model(model)
    
    def _build_system_prompt(self) -> str:
        """Build the system prompt."""
        base_prompt = "你是 xbot，一个智能助手。"
        if self._context_builder is not None:
            base_prompt = self._context_builder.build_system_prompt()
        identity_section = self._build_runtime_identity_section()
        if identity_section:
            base_prompt = f"{base_prompt}\n\n{identity_section}"
        policy_section = self._handoff_policy.build_system_section() if self._handoff_policy else ""
        if not policy_section:
            return base_prompt
        return f"{base_prompt}\n\n{policy_section}"
    
    def _build_runtime_identity_section(self) -> str:
        """Build runtime identity section for system prompt."""
        config = self._shared_resources.get("config")
        if config is None:
            return ""

        defaults = config.agents.defaults
        lines = [
            "## Runtime Identity",
            "",
            "- Agent name: `xbot`",
            "- Agent backend: `claude_sdk`",
            f"- Configured model: `{defaults.model}`",
            f"- Configured provider: `{defaults.provider}`",
            "",
            "When the user asks which model, provider, or agent is running, "
            "report the configured values above exactly.",
            "Do not infer or substitute a different model name from the surrounding SDK or toolchain.",
        ]
        return "\n".join(lines)
    
    def _build_sdk_agents(self) -> dict[str, "SDKAgentDefinition"] | None:
        """Build SDK agent definitions from configuration."""
        from claude_agent_sdk.types import AgentDefinition as SDKAgentDefinition
        
        if not self._sdk_config or not self._sdk_config.agents:
            return None

        agents: dict[str, SDKAgentDefinition] = {}
        for name, definition in self._sdk_config.agents.items():
            description, prompt, tools, model = self._parse_agent_definition(definition)
            
            # Normalize tools
            resolution = (
                self._capability_policy.resolve_agent_tools(tools, backend="claude_sdk")
                if self._capability_policy else None
            )
            normalized_tools = (
                resolution.allowed if resolution 
                else CapabilityCatalog.normalize_tool_names(tools)
            )
            
            # Build agent prompt with handoff policy
            prompt = (
                self._handoff_policy.build_agent_prompt(name, prompt)
                if self._handoff_policy else prompt
            )
            
            # Add when clause to description if present
            when = self._get_agent_when(definition)
            if when and when not in description:
                description = f"{description} Use when: {when}".strip()
            
            # Add dropped tools info
            if resolution and resolution.dropped:
                description = f"{description} Dropped unavailable tools: {', '.join(resolution.dropped)}".strip()
            
            agents[name] = SDKAgentDefinition(
                description=description,
                prompt=prompt,
                tools=normalized_tools,
                model=model,
            )
        return agents
    
    def _parse_agent_definition(self, definition: Any) -> tuple[str, str, list[str] | None, str | None]:
        """Parse agent definition to extract components."""
        if isinstance(definition, dict):
            return (
                str(definition.get("description", "")),
                str(definition.get("prompt", "")),
                definition.get("tools") or None,
                definition.get("model"),
            )
        else:
            return (
                definition.description,
                definition.prompt,
                definition.tools or None,
                definition.model,
            )
    
    def _get_agent_when(self, definition: Any) -> str:
        """Get the 'when' clause from agent definition."""
        if isinstance(definition, dict):
            return str(definition.get("when", ""))
        return getattr(definition, "when", "")


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
                raise ValueError(f"Unknown provider: {provider_name}")
            if not spec.supported_by_sdk:
                raise ValueError(
                    f"Provider '{provider_name}' is not compatible with Claude SDK Agent. "
                    f"Compatible providers: anthropic, aliyun-codingplan, alrun"
                )

        # Initialize skill converter
        try:
            from xbot.agent.skill_to_mcp import SkillToMCPConverter
            workspace = shared_resources.get("workspace", config.defaults.workspace)
            self._skill_converter = SkillToMCPConverter(workspace)
        except ImportError:
            logger.warning("SkillToMCPConverter not available")

        # Initialize tool adapter
        try:
            from xbot.agent.tool_adapter import ToolAdapter
            tools_config = shared_resources.get("tools_config")
            # Pass memory_store from ContextBuilder to ToolAdapter
            memory_store = self._context_builder.memory if self._context_builder else None
            self._tool_adapter = ToolAdapter(
                workspace=shared_resources.get("workspace", config.defaults.workspace),
                tools_config=tools_config,
                shared_resources={**shared_resources, "model": config.defaults.model, "memory_store": memory_store},
            )
            self.tools = self._tool_adapter
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
            raise RuntimeError("Backend not initialized")
        return self._options_builder.build(session_key, include_agents=include_agents)

    async def _get_or_create_client(self, session_key: str) -> "ClaudeSDKClient":
        """Get or create a Claude SDK client for the session.

        This method is thread-safe and prevents race conditions when
        multiple concurrent requests arrive for the same session.

        Args:
            session_key: Session identifier

        Returns:
            ClaudeSDKClient instance for the session
        """
        async with self._clients_lock:
            client = self._clients.get(session_key)
            if client is not None:
                return client

            client = _ClaudeSDKClient(options=self._build_options(session_key))
            await client.connect()
            await self._refresh_session_commands(session_key, client)
            self._clients[session_key] = client
            return client

    async def _refresh_session_commands(self, session_key: str, client: "ClaudeSDKClient") -> None:
        """Refresh slash commands discovered from SDK init metadata."""
        try:
            info = await client.get_server_info()
            # Debug: log full server info
            import json
            logger.info(f"SDK server info for session {session_key}: {json.dumps(info, indent=2, default=str) if info else 'None'}")
        except Exception as e:
            logger.warning(f"Failed to get SDK server info for session {session_key}: {e}")
            return
        commands = self._extract_slash_commands(info)
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
        traces = self._delegation_traces
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
        # Store session context for compact notifications
        session_contexts = self._shared_resources.setdefault("_session_contexts", {})
        session_contexts[context.session_key] = (context.channel, context.chat_id)

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

        # Detect triggered skills based on user message
        triggered_skills_prefix = ""
        if self._context_builder:
            triggered_skills = self._context_builder.skills.get_triggered_skills(
                user_message=context.prompt,
                code_context="",  # Could be enhanced to include file content
                file_paths=None,
            )
            if triggered_skills:
                triggered_content = self._context_builder.skills.load_skills_for_context(triggered_skills)
                if triggered_content:
                    triggered_skills_prefix = f"[Triggered Skills]\n\n{triggered_content}\n\n---\n\n"
                    logger.info(f"Triggered skills for session {context.session_key}: {triggered_skills}")

        session = self.sessions.get_or_create(context.session_key) if self.sessions else None
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
            
            client = await self._get_or_create_client(context.session_key)
            query_sent_at = time.perf_counter()
            append_session_trace(
                self.sessions,
                context.session_key,
                "sdk_query_sent",
                {
                    "backend": self.name,
                },
            )
            await client.query(
                prompt,
                session_id=self._query_session_id(context.session_key, session),
            )

            first_sdk_message_logged = False
            async for message in client.receive_response():
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
                    logger.info(f"SDK init message for session {context.session_key}, discovered {len(commands)} commands: {commands}")
                    self._session_commands[context.session_key] = commands
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
                        break
                if is_terminal_result:
                    self._active_task_ids.pop(context.session_key, None)
                    if session is not None and message.session_id:
                        session.metadata["sdk_session_id"] = message.session_id
                        self.sessions.save(session)

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
            client = self._clients.pop(context.session_key, None)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    logger.debug(f"Ignoring error while disconnecting failed Claude SDK session {context.session_key}")

            # Clear sdk_session_id to prevent resume with invalid session
            if session is not None:
                session.metadata.pop("sdk_session_id", None)
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
                                logger.info(f"SDK init message (fallback) for session {context.session_key}, discovered {len(commands)} commands: {commands}")
                                self._session_commands[context.session_key] = commands
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
                except Exception:
                    logger.exception("Claude SDK fallback to main agent failed")
                    # Clear sdk_session_id on fallback failure too
                    if session is not None:
                        session.metadata.pop("sdk_session_id", None)
                        self.sessions.save(session)
            yield AgentResponse(
                content=f"Error: {str(e)}",
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
        summary = str(message.summary or "").strip()
        prompt = summary or "Task requires your input. Please reply to continue."
        if not summary:
            prompt = f"Task `{message.task_id or ''}` requires your input. Please reply to continue.".strip()

        status = str(message.status or "").lower()
        if status == "approval_required":
            interaction_kind = "approval"
            suggestions = ["允许", "拒绝"]
        elif status == "confirmation_required":
            interaction_kind = "confirmation"
            suggestions = ["确认", "取消"]
        else:
            interaction_kind = "question"
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

            request = InteractionRequest(
                request_id=str(uuid.uuid4()),
                session_key=context.session_key,
                channel=context.channel,
                chat_id=context.chat_id,
                kind=interaction_kind,
                prompt=prompt,
                suggestions=suggestions,
                metadata=dict(context.metadata or {}),
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
        for session_key, client in list(self._clients.items()):
            try:
                await client.disconnect()
            except Exception:
                logger.debug(f"Ignoring error while disconnecting Claude SDK session {session_key}")
        self._clients.clear()
        self._session_commands.clear()
        self._active_task_ids.clear()
        # Clear session contexts for compact notifications
        self._shared_resources.pop("_session_contexts", None)
        logger.info("Claude SDK backend shutdown complete")

    async def reset_session(self, session_key: str) -> None:
        """Reset a session, disconnecting client and clearing state."""
        client = self._clients.pop(session_key, None)
        self._session_commands.pop(session_key, None)
        self._active_task_ids.pop(session_key, None)
        # Clear session context for compact notifications
        session_contexts = self._shared_resources.get("_session_contexts", {})
        session_contexts.pop(session_key, None)
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
        if session_key not in self._session_commands:
            try:
                await self._get_or_create_client(session_key)
            except Exception:
                return []
        return list(self._session_commands.get(session_key, []))

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
        client = self._clients.pop(session_key, None)
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
        self._active_task_ids.pop(session_key, None)

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
        if not provider_config or not provider_config.api_key:
            raise ValueError(
                f"API key not configured for provider '{provider_name}'. "
                f"Please set providers.{provider_name}.api_key in config.json"
            )

        api_key = provider_config.api_key
        base_url = provider_config.api_base if provider_config.api_base else spec.default_base_url
        return api_key, base_url, provider_config.extra_headers

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
