"""Claude SDK Agent Backend.

This backend uses the Claude Agent SDK to provide native Claude integration
with support for Anthropic and Anthropic-compatible providers (Aliyun Coding Plan, Alrun).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from xbot.agent.capabilities import CapabilityCatalog, canonical_tool_name
from xbot.agent.capability_policy import CapabilityPolicy
from xbot.agent.context import ContextBuilder
from xbot.agent.handoff_policy import HandoffDecision, HandoffPolicy
from xbot.agent.memory import MemoryConsolidator
from xbot.agent.protocol import AgentBackend, AgentContext, AgentResponse
from xbot.agent.tools.base import Tool
from xbot.config.provider_registry import get_provider_spec
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
            return None
        elif isinstance(message, ResultMessage):
            return self._convert_result_message(message)
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

        return AgentResponse(
            content=text,
            progress_texts=progress_texts,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason="tool_use" if tool_calls else "stop",
            raw_message=message,
        )
    
    def _convert_stream_event(self, message: "StreamEvent") -> AgentResponse | None:
        """Convert StreamEvent to AgentResponse."""
        event = message.event or {}
        if event.get("type") != "content_block_delta":
            return None
        delta = event.get("delta", {})
        if delta.get("type") != "text_delta":
            return None
        text = delta.get("text", "")
        if not text:
            return None
        return AgentResponse(
            content="",
            is_delta=True,
            delta_content=text,
            raw_message=message,
        )
    
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
        )
    
    def _convert_task_notification(self, message: "TaskNotificationMessage") -> AgentResponse:
        """Convert TaskNotificationMessage to AgentResponse."""
        summary = message.summary or message.status
        progress_texts = [f"Running: {summary}"] if summary else []
        if self._handoff_policy:
            if handoff_trace := self._handoff_policy.format_task_trace(str(summary)):
                progress_texts.append(handoff_trace)
        return AgentResponse(
            content="",
            progress_texts=progress_texts,
            raw_message=message,
        )
    
    def _convert_result_message(self, message: "ResultMessage") -> AgentResponse:
        """Convert ResultMessage to AgentResponse."""
        usage = None
        if hasattr(message, "usage") and message.usage:
            usage = {
                "input_tokens": getattr(message.usage, "input_tokens", 0),
                "output_tokens": getattr(message.usage, "output_tokens", 0),
            }
        return AgentResponse(
            content="",
            finish_reason="stop",
            usage=usage,
            raw_message=message,
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

        return ClaudeAgentOptions(
            cwd=self._shared_resources.get("workspace", defaults.workspace),
            model=model,
            max_turns=self._sdk_config.max_turns,
            permission_mode=self._sdk_config.permission_mode,
            resume=resume_session,
            mcp_servers=mcp_servers if mcp_servers else None,
            agents=sdk_agents,
            hooks=self._sdk_config.hooks,
            system_prompt=self._build_system_prompt(),
            env=env,
            can_use_tool=can_use_tool,
            disallowed_tools=disallowed_tools,
        )
    
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
        provider_name = config.agents.defaults.provider

        if provider_name == "auto":
            provider_name = self._detect_provider_from_model(config.agents.defaults.model)

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
        model = config.agents.defaults.model
        provider = config.agents.defaults.provider

        if provider == "auto":
            provider = self._detect_provider_from_model(model)

        if provider == "alrun" and model.startswith("alrun-"):
            return model[len("alrun-"):]

        return model
    
    def _detect_provider_from_model(self, model: str) -> str:
        """Detect provider from model name."""
        model_lower = model.lower()
        
        # Check alrun prefix first (most specific)
        if model_lower.startswith("alrun-"):
            return "alrun"
        
        # Then check other patterns
        if "claude" in model_lower:
            return "anthropic"
        elif "qwen" in model_lower or "glm" in model_lower:
            return "aliyun_coding_plan"
        
        return "anthropic"
    
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
            f"- Agent backend: `{config.agents.type}`",
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
        self._context_builder = ContextBuilder(workspace_path)
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
            self._tool_adapter = ToolAdapter(
                workspace=shared_resources.get("workspace", config.defaults.workspace),
                tools_config=tools_config,
                shared_resources={**shared_resources, "model": config.defaults.model},
            )
            self.tools = self._tool_adapter
        except ImportError:
            logger.warning("ToolAdapter not available")

        # Initialize memory consolidator
        provider = shared_resources.get("provider")
        if provider and self.sessions and self._context_builder:
            self.memory_consolidator = MemoryConsolidator(
                workspace=Path(shared_resources.get("workspace", config.defaults.workspace)),
                provider=provider,
                model=config.defaults.model,
                sessions=self.sessions,
                context_window_tokens=config.defaults.context_window_tokens,
                build_messages=self._context_builder.build_messages,
                get_tool_definitions=self._get_tool_definitions,
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
            self._clients[session_key] = client
            return client

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
        if self._tool_adapter:
            if not self._tool_adapter._tools:
                self._tool_adapter._register_xbot_tools()
            self._tool_adapter.set_tool_context(
                channel=context.channel,
                chat_id=context.chat_id,
                message_id=context.metadata.get("message_id"),
            )

        # Set permission handler session context
        if self._permission_handler and hasattr(self._permission_handler, "set_session_context"):
            self._permission_handler.set_session_context(
                context.session_key,
                context.channel,
                context.chat_id,
            )
            if hasattr(self._permission_handler, "set_current_session"):
                self._permission_handler.set_current_session(context.session_key)

        session = self.sessions.get_or_create(context.session_key) if self.sessions else None
        if session is not None:
            session.add_message("user", context.prompt)
            self.sessions.save(session)
            if self.memory_consolidator:
                await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        try:
            final_content = ""
            decision = None
            prompt = context.prompt
            
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
            await client.query(
                prompt,
                session_id=self._query_session_id(context.session_key, session),
            )

            async for message in client.receive_response():
                if isinstance(message, ResultMessage) and session is not None and message.session_id:
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

            if session is not None and final_content:
                session.add_message("assistant", final_content)
                self.sessions.save(session)
                if self.memory_consolidator:
                    await self.memory_consolidator.maybe_consolidate_by_tokens(session)

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
                            if isinstance(message, ResultMessage) and session is not None and message.session_id:
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
                    finally:
                        await fallback_client.disconnect()

                    if session is not None and final_content:
                        session.add_message("assistant", final_content)
                        self.sessions.save(session)
                        if self.memory_consolidator:
                            await self.memory_consolidator.maybe_consolidate_by_tokens(session)
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
        logger.info("Claude SDK backend shutdown complete")

    async def reset_session(self, session_key: str) -> None:
        """Reset a session, disconnecting client and clearing state."""
        client = self._clients.pop(session_key, None)
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
        if not self._tool_adapter:
            return 0
        spawn_tool = self._tool_adapter.get_tool("spawn")
        manager = getattr(spawn_tool, "_manager", None)
        if manager is None:
            return 0
        return await manager.cancel_by_session(session_key)

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