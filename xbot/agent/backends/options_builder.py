"""Options builder for Claude SDK backend.

This module provides utilities for building ClaudeAgentOptions from configuration.
"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from xbot.logging import get_logger

logger = get_logger(__name__)

from xbot.agent.mcp_config import resolve_mcp_server_config
from xbot.agent.capabilities.catalog import CapabilityCatalog
from xbot.config.provider_registry import get_provider_spec
from xbot.config.sdk_resolver import detect_provider_from_model, resolve_sdk_provider_and_model
from xbot.config.schema import ProviderConfig

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk.types import AgentDefinition as SDKAgentDefinition

    from xbot.agent.capabilities.policy import CapabilityPolicy
    from xbot.agent.context.builder import ContextBuilder
    from xbot.agent.capabilities.handoff import HandoffPolicy
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

    @staticmethod
    def _is_valid_compact_target(resolved_target: Any) -> bool:
        return (
            isinstance(resolved_target, tuple)
            and len(resolved_target) == 3
            and all(isinstance(part, str) and part for part in resolved_target)
        )

    def _resolve_compact_target_from_session_store(
        self, session_ref: str
    ) -> tuple[str, str, str] | None:
        """Resolve compact notification target from SessionStore when available."""
        session_store = self._shared_resources.get("session_store")
        if session_store is None:
            return None
        get_entry = getattr(session_store, "get", None)
        get_by_sdk_id = getattr(session_store, "get_by_sdk_id", None)
        if not callable(get_entry) or not callable(get_by_sdk_id):
            return None

        entry = get_entry(session_ref)
        session_key = session_ref
        if entry is None:
            entry = get_by_sdk_id(session_ref)
            if entry is None:
                return None
            session_key = entry.session_key

        if not entry.channel or not entry.chat_id:
            return None
        return (session_key, entry.channel, entry.chat_id)

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
        extra_args = dict(getattr(self._sdk_config, "extra_args", {}) or {})
        extra_args.setdefault("replay-user-messages", None)

        return ClaudeAgentOptions(
            cwd=self._shared_resources.get("workspace", defaults.workspace),
            model=model,
            max_turns=self._sdk_config.max_turns,
            permission_mode=self._sdk_config.permission_mode,
            # Isolate xbot's provider/runtime config from user-level Claude CLI settings
            # such as ~/.claude/settings.json, which can override base URL and model env.
            setting_sources=["local"],
            include_partial_messages=getattr(self._sdk_config, "include_partial_messages", False),
            resume=resume_session,
            mcp_servers=mcp_servers if mcp_servers else None,
            agents=sdk_agents,
            hooks=hooks,
            system_prompt=self._build_system_prompt(),
            env=env,
            extra_args=extra_args,
            can_use_tool=can_use_tool,
            disallowed_tools=disallowed_tools,
            # Skills: 通过 add_dirs 加载（CLI 自动扫描 .claude/skills/）
            add_dirs=self._build_add_dirs(),
            # Plugins: 显式加载
            plugins=self._build_plugins(),
        )

    def _build_hooks(self) -> dict[str, list] | None:
        """Build hooks configuration including compact notification."""
        from claude_agent_sdk.types import HookMatcher

        # Start with user-configured hooks
        try:
            hooks: dict[str, list] = copy.deepcopy(self._sdk_config.hooks or {})
        except Exception:
            hooks = dict(self._sdk_config.hooks or {})

        # Add PreCompact hook if compact_notify is enabled
        compact_notify = getattr(self._sdk_config, "compact_notify", True)
        logger.info("[Hooks] Building hooks, compact_notify=%s", compact_notify)
        if compact_notify:
            from xbot.agent.hooks import CompactHookHandler
            from xbot.bus.events import OutboundMessage

            def send_compact_notification(session_ref: str, message: str) -> None:
                """Send compact notification to the user's channel."""
                logger.info(
                    "[Compact Notification] Called with session_ref='%s', message='%s'",
                    session_ref,
                    message[:50] if message else "",
                )

                bus = self._shared_resources.get("bus")
                if bus is None:
                    logger.warning("[Compact Notification] No bus available for session: %s", session_ref)
                    return

                runtime = self._shared_resources.get("runtime")
                backend = getattr(runtime, "backend", None) if runtime is not None else None
                resolver = getattr(backend, "_resolve_compact_notification_target", None)
                if not callable(resolver) and runtime is not None:
                    resolver = getattr(runtime, "_resolve_compact_notification_target", None)
                resolved_target = None
                if callable(resolver):
                    try:
                        resolved_target = resolver(session_ref)
                    except Exception as e:
                        logger.debug(
                            "[Compact Notification] Backend target resolver failed for '%s': %s",
                            session_ref,
                            e,
                        )
                elif backend is not None:
                    legacy_resolver = getattr(backend, "_get_context_by_session_key", None)
                    if callable(legacy_resolver):
                        try:
                            context_info = legacy_resolver(session_ref)
                            if isinstance(context_info, tuple):
                                resolved_target = (session_ref, context_info[0], context_info[1])
                        except Exception as e:
                            logger.debug(
                                "[Compact Notification] Legacy backend context resolver failed for '%s': %s",
                                session_ref,
                                e,
                            )

                session_contexts = self._shared_resources.get("_session_contexts", {})
                if not self._is_valid_compact_target(resolved_target):
                    resolved_target = self._resolve_compact_target_from_session_store(session_ref)

                if not self._is_valid_compact_target(resolved_target):
                    context_info = session_contexts.get(session_ref)
                    if isinstance(context_info, tuple):
                        resolved_target = (session_ref, context_info[0], context_info[1])

                if not self._is_valid_compact_target(resolved_target):
                    resolved_target = None

                # DEBUG: Log all available session keys for troubleshooting
                logger.info(
                    "[Compact Notification] Looking up session_ref='%s'. "
                    "Resolved target=%s available legacy keys=%s",
                    session_ref,
                    resolved_target,
                    list(session_contexts.keys()),
                )

                if resolved_target is None:
                    logger.warning(
                        "[Compact Notification] No context info for session_ref='%s'. "
                        "Available keys: %s. The notification will NOT be delivered.",
                        session_ref,
                        list(session_contexts.keys()),
                    )
                    return

                session_key, channel, chat_id = resolved_target
                logger.info(
                    "[Compact Notification] Found target: session_key='%s', channel='%s', chat_id='%s'",
                    session_key,
                    channel,
                    chat_id,
                )
                # Fire and forget - send notification asynchronously

                async def _send():
                    try:
                        if runtime is not None:
                            emit_direct = getattr(runtime, "_emit_direct_progress_for_session", None)
                            if callable(emit_direct):
                                handled = await emit_direct(
                                    session_key,
                                    message,
                                    event_type="system",
                                    event_data={"subtype": "pre_compact"},
                                )
                                if handled:
                                    logger.debug(
                                        "Sent compact notification via direct progress for %s",
                                        session_key,
                                    )
                                    return
                        await bus.publish_outbound(
                            OutboundMessage(
                                channel=channel,
                                chat_id=chat_id,
                                content=message,
                                metadata={
                                    "_progress": True,
                                    "_event_type": "system",
                                    "_event_data": {"subtype": "pre_compact"},
                                },
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
                except RuntimeError as e:
                    logger.warning(
                        f"Cannot send compact notification for session {session_ref}: "
                        f"no running event loop available. Error: {e}"
                    )

            compact_handler = CompactHookHandler(
                enabled=True,
                message_callback=send_compact_notification,
            )
            hooks.setdefault("PreCompact", []).append(HookMatcher(hooks=[compact_handler]))
            logger.info("[Hooks] Added PreCompact hook with CompactHookHandler, hooks keys=%s", list(hooks.keys()))

        logger.info("[Hooks] Final hooks configuration: %s", hooks if hooks else "None")
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

                resolved_config, unresolved = resolve_mcp_server_config(mcp_servers[name])
                if unresolved:
                    mcp_servers.pop(name, None)
                    logger.warning(
                        "MCP server '%s' has unresolved env vars (%s), skipping for Claude SDK",
                        name,
                        ", ".join(unresolved),
                    )
                    continue

                mcp_servers[name] = self._sanitize_mcp_server_for_sdk(resolved_config)

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

    @staticmethod
    def _sanitize_mcp_server_for_sdk(server_config: dict[str, Any]) -> dict[str, Any]:
        """Translate xbot MCP config to the Claude CLI MCP schema.

        xbot supports a few local-only conveniences that the Claude CLI schema
        rejects, notably `streamableHttp`, `tool_timeout`, and `enabled_tools`.
        """
        cfg = copy.deepcopy(server_config)

        if cfg.get("type") == "streamableHttp":
            cfg["type"] = "http"

        # These are consumed by xbot's own MCP client layer, not by Claude Code.
        cfg.pop("tool_timeout", None)
        cfg.pop("enabled_tools", None)

        transport_type = cfg.get("type")
        if transport_type is None:
            if cfg.get("command"):
                transport_type = "stdio"
            elif cfg.get("url"):
                transport_type = "sse" if str(cfg.get("url", "")).rstrip("/").endswith("/sse") else "http"

        if transport_type in {"http", "sse"}:
            cfg["type"] = transport_type
            cfg.pop("command", None)
            cfg.pop("args", None)
            cfg.pop("env", None)
        elif transport_type == "stdio":
            cfg.pop("type", None)
            cfg.pop("url", None)
            cfg.pop("headers", None)

        return cfg

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

    def _build_add_dirs(self) -> list[str]:
        """构建 add_dirs 列表

        CLI 会自动扫描这些目录下的 .claude/skills/ 子目录。
        Skills 支持三级延迟加载和 Hot-Reload。
        """
        from pathlib import Path

        dirs = []
        config = self._shared_resources.get("config")

        if not config or not getattr(config.skills, "enabled", True):
            return []

        workspace = Path(config.agents.defaults.workspace)

        # 1. workspace 根目录（CLI 自动扫描 .claude/skills/）
        dirs.append(str(workspace))

        # 2. 额外的 skills 目录（非标准位置，如兼容旧目录）
        for dir_path in getattr(config.skills, "additional_dirs", []):
            expanded = self._expand_path(dir_path)
            if Path(expanded).exists():
                dirs.append(expanded)

        return dirs

    def _expand_path(self, path: str) -> str:
        """展开路径变量

        支持 $workspace, $home, $project 变量。
        """
        import os
        from pathlib import Path

        config = self._shared_resources.get("config")
        if not config:
            return path

        workspace = config.agents.defaults.workspace

        result = path
        result = result.replace("$workspace", workspace)
        result = result.replace("$home", str(Path.home()))
        result = result.replace("$project", os.getcwd())

        return result

    def _build_plugins(self) -> list[dict]:
        """构建 plugins 列表

        扫描配置的插件目录，过滤启用的插件。
        """
        from pathlib import Path

        plugins = []
        config = self._shared_resources.get("config")

        if not config or not getattr(config.plugins, "enabled", True):
            return []

        for plugin_dir in getattr(config.plugins, "dirs", []):
            expanded = self._expand_path(plugin_dir)
            plugin_base = Path(expanded)

            if not plugin_base.exists():
                continue

            # 扫描目录下的每个 plugin
            for plugin_path in plugin_base.iterdir():
                if not plugin_path.is_dir():
                    continue

                if self._is_valid_plugin(plugin_path):
                    plugin_name = plugin_path.name

                    if self._should_load_plugin(plugin_name, config):
                        plugins.append({
                            "type": "local",
                            "path": str(plugin_path)
                        })

        return plugins

    def _is_valid_plugin(self, path: Path) -> bool:
        """检查是否是有效的 plugin 目录"""
        return (path / ".claude-plugin" / "plugin.json").exists()

    def _should_load_plugin(self, name: str, config) -> bool:
        """检查 plugin 是否应该加载

        规则：
        1. 如果有 enabled_plugins 列表，只加载列表中的
        2. 如果在 disabled_plugins 列表中，跳过
        """
        enabled = getattr(config.plugins, "enabled_plugins", [])
        disabled = getattr(config.plugins, "disabled_plugins", [])

        # 如果有启用列表，只加载列表中的
        if enabled and name not in enabled:
            return False

        # 检查是否在禁用列表中
        if name in disabled:
            return False

        return True

    def _build_system_prompt(self) -> str:
        """Build the system prompt."""
        base_prompt = "你是 xbot，一个智能助手。"
        if self._context_builder is not None:
            base_prompt = self._context_builder.build_system_prompt()
        identity_section = self._build_runtime_identity_section()
        if identity_section:
            base_prompt = f"{base_prompt}\n\n{identity_section}"
        return base_prompt

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
