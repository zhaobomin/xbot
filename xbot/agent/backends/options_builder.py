"""Options builder for Claude SDK backend.

This module provides utilities for building ClaudeAgentOptions from configuration.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from loguru import logger

from xbot.agent.capabilities import CapabilityCatalog
from xbot.config.provider_registry import get_provider_spec
from xbot.config.sdk_resolver import detect_provider_from_model, resolve_sdk_provider_and_model
from xbot.config.schema import ProviderConfig

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk.types import AgentDefinition as SDKAgentDefinition

    from xbot.agent.capability_policy import CapabilityPolicy
    from xbot.agent.context import ContextBuilder
    from xbot.agent.handoff_policy import HandoffPolicy
    from xbot.session.manager import SessionManager


class OptionsBuilder:
    """Builds ClaudeAgentOptions from configuration.

    This class encapsulates the options building logic,
    separating concerns and improving testability.

    Attributes:
        _shared_resources: Shared resources dict containing config, runtime, bus, etc.
        _sdk_config: SDK configuration object
        _skill_converter: Skill converter for MCP servers
        _tool_adapter: Tool adapter for MCP servers
        _sessions: Session manager for resume support
        _context_builder: Context builder for system prompts
        _handoff_policy: Handoff policy for agent prompts
        _capability_policy: Capability policy for tool resolution
        _permission_handler: Permission handler for can_use_tool callback
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
        """Initialize the options builder.

        Args:
            shared_resources: Shared resources dict
            sdk_config: SDK configuration
            skill_converter: Skill converter instance
            tool_adapter: Tool adapter instance
            sessions: Session manager
            context_builder: Context builder
            handoff_policy: Handoff policy
            capability_policy: Capability policy
            permission_handler: Permission handler
        """
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
        """Build ClaudeAgentOptions from configuration.

        Args:
            session_key: Optional session key for resume support
            include_agents: Whether to include SDK agents

        Returns:
            ClaudeAgentOptions instance
        """
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
        compact_notify = getattr(self._sdk_config, "compact_notify", True)
        logger.info("[Hooks] Building hooks, compact_notify={}", compact_notify)
        if compact_notify:
            from xbot.agent.hooks import CompactHookHandler
            from xbot.bus.events import OutboundMessage

            def send_compact_notification(session_key: str, message: str) -> None:
                """Send compact notification to the user's channel."""
                logger.info(
                    "[Compact Notification] Called with session_key='{}', message='{}'",
                    session_key,
                    message[:50] if message else "",
                )

                bus = self._shared_resources.get("bus")
                if bus is None:
                    logger.warning("[Compact Notification] No bus available for session: {}", session_key)
                    return

                # Look up channel and chat_id for this session via backend helper first.
                context_info = None
                runtime = self._shared_resources.get("runtime")
                backend = getattr(runtime, "backend", None) if runtime is not None else None
                resolver = getattr(backend, "_get_context_by_session_key", None)
                if callable(resolver):
                    try:
                        context_info = resolver(session_key)
                    except Exception as e:
                        logger.debug(
                            "[Compact Notification] Backend context resolver failed for '{}': {}",
                            session_key,
                            e,
                        )

                # Fallback to legacy mapping during transition.
                session_contexts = self._shared_resources.get("_session_contexts", {})
                if context_info is None:
                    context_info = session_contexts.get(session_key)

                # DEBUG: Log all available session keys for troubleshooting
                logger.info(
                    "[Compact Notification] Looking up session_key='{}'. "
                    "Resolved context={} available legacy keys={}",
                    session_key,
                    context_info,
                    list(session_contexts.keys()),
                )

                if context_info is None:
                    logger.warning(
                        "[Compact Notification] No context info for session_key='{}'. "
                        "Available keys: {}. The notification will NOT be delivered.",
                        session_key,
                        list(session_contexts.keys()),
                    )
                    return

                channel, chat_id = context_info
                logger.info(
                    "[Compact Notification] Found context: channel='{}', chat_id='{}'",
                    channel,
                    chat_id,
                )
                # Fire and forget - send notification asynchronously

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
                        logger.warning(f"Failed to send compact notification to {channel}:{chat_id}: {e}")

                # Schedule the send without blocking the hook
                # Use ensure_future for better compatibility across contexts
                try:
                    loop = asyncio.get_running_loop()
                    asyncio.ensure_future(_send(), loop=loop)
                except RuntimeError:
                    # No running event loop - create a new one for this context
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(_send())
                        loop.close()
                    except Exception as e:
                        logger.warning(
                            f"Cannot send compact notification for session {session_key}: "
                            f"no event loop available. Error: {e}"
                        )

            compact_handler = CompactHookHandler(
                enabled=True,
                message_callback=send_compact_notification,
            )
            hooks.setdefault("PreCompact", []).append({"hooks": [compact_handler]})
            logger.info("[Hooks] Added PreCompact hook with CompactHookHandler, hooks keys={}", list(hooks.keys()))

        logger.info("[Hooks] Final hooks configuration: {}", hooks if hooks else "None")
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
                elif isinstance(server_config, dict):
                    mcp_servers[name] = server_config
                else:
                    # Try to convert to dict via dataclasses.asdict or __dict__
                    try:
                        if hasattr(server_config, "__dataclass_fields__"):
                            mcp_servers[name] = asdict(server_config)
                        elif hasattr(server_config, "__dict__"):
                            mcp_servers[name] = {
                                k: v for k, v in server_config.__dict__.items()
                                if not k.startswith("_")
                            }
                        else:
                            logger.warning(
                                f"MCP server '{name}' config type {type(server_config)} "
                                f"is not serializable, skipping"
                            )
                            continue
                    except Exception as e:
                        logger.warning(
                            f"Failed to serialize MCP server '{name}' config: {e}, skipping"
                        )
                        continue

                # Validate that the config is JSON serializable
                try:
                    json.dumps(mcp_servers[name])
                except (TypeError, ValueError) as e:
                    logger.warning(
                        f"MCP server '{name}' config is not JSON serializable: {e}, skipping"
                    )
                    mcp_servers.pop(name, None)

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

        return api_key_value, base_url

    def _get_model_name(self) -> str:
        """Get the model name with provider-specific transformations.

        优先使用 ModelManager 的当前模型（支持动态切换），
        回退到配置文件中的 model 字段。
        """
        # 优先使用 ModelManager 的当前模型
        runtime = self._shared_resources.get("runtime")
        if runtime and hasattr(runtime, "model_manager"):
            return runtime.model_manager.current_model

        # 回退到配置
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
