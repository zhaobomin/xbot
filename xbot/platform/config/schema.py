"""Configuration schema using Pydantic."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

class ChannelsConfig(Base):
    """Configuration for chat channels.

    Built-in and plugin channel configs are stored as extra fields (dicts).
    Each channel parses its own config in __init__.
    """

    model_config = ConfigDict(extra="allow")

    send_progress: bool = True  # send progress events (thinking/task/system/content-delta)
    send_tool_hints: bool = True  # send tool-call hints (e.g. read_file("…"))
    send_usage_summary: bool = True  # send token usage summary when available


class AgentDefaults(Base):
    """Default agent configuration."""

    workspace: str = "~/.xbot/workspace"
    model: str = ""  # 空字符串表示使用供应商的第一个模型
    provider: str = (
        "auto"  # Provider name (e.g. "anthropic", "alrun") or "auto" for auto-detection
    )
    # available_models 已废弃，模型列表现在从 providers.{name}.models 读取
    # 保留此字段以兼容旧配置，但运行时不再使用
    available_models: list[str] = Field(default_factory=list, exclude=True)
    max_tokens: int = 8192
    context_window_tokens: int = Field(default=65_536, ge=1024, le=1_000_000)
    temperature: float = 0.1
    max_tool_iterations: int = 40
    # Deprecated compatibility field: accepted from old configs but ignored at runtime.
    memory_window: int | None = Field(default=None, exclude=True)
    reasoning_effort: str | None = None  # low / medium / high — enables LLM thinking mode
    # 是否将 AGENTS.md/SOUL.md/USER.md/TOOLS.md 加载到 system prompt
    # 设为 false 时仅禁用这 4 个 bootstrap 文件加载，identity/memory 等部分仍保留
    load_bootstrap_files: bool = True

    @property
    def should_warn_deprecated_memory_window(self) -> bool:
        """Return True when old memoryWindow is present without contextWindowTokens."""
        return self.memory_window is not None and "context_window_tokens" not in self.model_fields_set


class PermissionConfig(Base):
    """权限请求处理配置。"""

    enabled: bool = True  # 是否启用权限请求处理
    timeout: float = 300.0  # 等待用户响应的超时时间（秒）
    auto_approve_safe_tools: bool = True  # 是否自动批准安全工具
    safe_tools: list[str] = [  # 安全工具列表（自动批准）
        "read_file", "list_dir", "web_search", "web_fetch",
        "message", "cron",
        # SDK native aliases (case-sensitive names as seen by Claude SDK)
        "Read", "LS", "WebSearch", "WebFetch",
        # xbot MCP extension aliases (post-migration tool routing)
        "mcp__xbot__web_search",
        "mcp__xbot__web_fetch",
        "mcp__xbot__message",
        "mcp__xbot__cron",
    ]


class ClaudeSDKMemorySettings(Base):
    """Claude SDK settings payload for memory-related features."""

    auto_memory_enabled: bool | None = None
    auto_memory_directory: str | None = None
    claude_md_excludes: list[str] = Field(default_factory=list)


class ClaudeSDKSettingSourcesConfig(Base):
    """SDK setting_sources by runtime mode."""

    cli: list[Literal["user", "project", "local"]] = Field(
        default_factory=lambda: ["user", "project", "local"]
    )
    gateway: list[Literal["user", "project", "local"]] = Field(
        default_factory=lambda: ["user", "project", "local"]
    )


class ClaudeSDKMemoryIntegrationConfig(Base):
    """Claude SDK memory integration strategy."""

    mode: Literal["off", "auto", "on"] = "auto"
    setting_sources: ClaudeSDKSettingSourcesConfig = Field(default_factory=ClaudeSDKSettingSourcesConfig)
    sdk_settings: ClaudeSDKMemorySettings = Field(default_factory=ClaudeSDKMemorySettings)


class ClaudeSDKSystemPromptStrategyConfig(Base):
    """Claude SDK system prompt strategy."""

    preset: Literal["xbot", "claude_code"] = "xbot"
    append_xbot_prompt: bool = True


class ClaudeSDKAgentConfig(Base):
    """Claude SDK Agent 特有配置.

    注意: 供应商凭证(api_key/api_base)从全局 providers 读取
    """

    max_turns: int = Field(default=40, ge=1, le=1000)
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk"] = "acceptEdits"
    agents: dict[str, "AgentDefinition"] | None = None
    hooks: dict[str, list] | None = None
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    # 禁用 SDK 内置工具，避免与 xbot MCP 工具冲突
    # 默认禁用 WebFetch/WebSearch，让 agent 使用带代理配置的 mcp__xbot__web_fetch/web_search
    disallowed_tools: list[str] = Field(default_factory=lambda: ["WebFetch", "WebSearch"])
    # Context Compaction 通知配置
    compact_notify: bool = True  # 是否在压缩上下文时发送通知
    include_partial_messages: bool = False  # Disable SDK partial/delta messages by default for stable output
    extra_args: dict[str, str | None] = Field(default_factory=dict)  # Extra Claude Code CLI flags
    # Memory consolidation 策略："off"=禁用，"async"=异步后台，"sync"=同步阻塞
    # 默认禁用，避免异步任务阻塞和 ReMe 初始化超时问题。需要时可手动启用 "async"
    memory_consolidation_mode: Literal["off", "async", "sync"] = "off"
    # Claude SDK 内存集成策略
    memory_integration: ClaudeSDKMemoryIntegrationConfig = Field(default_factory=ClaudeSDKMemoryIntegrationConfig)
    # Claude SDK 系统提示策略
    system_prompt_strategy: ClaudeSDKSystemPromptStrategyConfig = Field(
        default_factory=ClaudeSDKSystemPromptStrategyConfig
    )
    # SDK 配置源 (auto_configure=true 时有效)
    auto_configure: bool = False
    setting_sources: ClaudeSDKSettingSourcesConfig = Field(default_factory=ClaudeSDKSettingSourcesConfig)

    # Client pool configuration
    max_clients: int = Field(default=100, description="Maximum number of concurrent SDK clients in the pool")
    client_ttl_seconds: int = Field(default=3600, description="Time-to-live for idle clients in seconds (1 hour)")
    client_disconnect_retries: int = Field(default=2, description="Number of retry attempts when disconnecting clients")
    client_lifecycle_enabled: bool = Field(default=True, description="Enable managed Claude client lifecycle tracking")
    client_scavenger_enabled: bool = Field(default=True, description="Enable background cleanup of idle managed Claude clients")
    client_cleanup_interval_seconds: int = Field(default=60, description="Background client cleanup scan interval in seconds")
    client_idle_ttl_seconds: int = Field(default=3600, description="Idle TTL for managed Claude clients in seconds")
    client_disconnect_timeout_seconds: float = Field(default=10.0, description="Timeout for Claude client disconnect operations")
    client_disconnect_max_retries: int = Field(default=2, description="Max retry attempts for managed disconnect before leak classification")
    client_force_kill_enabled: bool = Field(default=True, description="Enable force-kill fallback for leaked Claude client processes")
    ephemeral_immediate_release_enabled: bool = Field(default=True, description="Release cron/heartbeat clients immediately after turn completion")
    strict_process_tracking_required: bool = Field(default=False, description="Require stable process tracking for managed Claude clients")


class AgentDefinition(Base):
    """Subagent 定义."""

    description: str = ""
    prompt: str = ""
    when: str = ""
    tools: list[str] | None = None
    model: Literal["sonnet", "opus", "haiku", "inherit"] = "inherit"


class AgentsConfig(Base):
    """Agent configuration."""

    type: Literal["claude_sdk"] = "claude_sdk"  # Only Claude SDK is supported now
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    claude_sdk: ClaudeSDKAgentConfig = Field(default_factory=ClaudeSDKAgentConfig)


class SkillsConfig(Base):
    """Skills 配置

    Skills 通过 SDK 的 add_dirs 参数加载。
    CLI 会自动扫描 .claude/skills/ 子目录。
    """

    enabled: bool = True
    dirs: list[str] = Field(default_factory=lambda: ["$workspace/.claude/skills"])
    additional_dirs: list[str] = Field(default_factory=list)


class PluginsConfig(Base):
    """Plugins 配置

    Plugins 需要显式指定，CLI 不会自动扫描。
    """

    enabled: bool = True
    dirs: list[str] = Field(default_factory=lambda: ["$workspace/plugins"])
    enabled_plugins: list[str] = Field(default_factory=list)
    disabled_plugins: list[str] = Field(default_factory=list)


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: SecretStr = SecretStr("")
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)
    models: list[str] = Field(default_factory=list)  # Available models list


class ProvidersConfig(Base):
    """Configuration for LLM providers.

    Only Anthropic Messages API compatible providers are supported.
    """

    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)

    # 自定义供应商 (用户动态添加)
    custom_providers: dict[str, ProviderConfig] = Field(
        default_factory=dict,
        description="用户自定义的 Anthropic 兼容供应商"
    )

    @property
    def custom(self) -> ProviderConfig:
        """Compatibility alias for the legacy `providers.custom` config."""
        return self.custom_providers.setdefault("custom", ProviderConfig())

    @custom.setter
    def custom(self, value: ProviderConfig) -> None:
        self.custom_providers["custom"] = value

    def get_provider_config(self, name: str | None) -> ProviderConfig | None:
        """Return a provider config from fixed fields or custom_providers."""
        if not name:
            return None
        from xbot.platform.config.loader import _provider_name_to_snake
        provider_attr = _provider_name_to_snake(name)
        fixed = getattr(self, provider_attr, None)
        if isinstance(fixed, ProviderConfig):
            return fixed
        return self.custom_providers.get(provider_attr)


class HeartbeatConfig(Base):
    """Heartbeat service configuration."""

    enabled: bool = True
    interval_s: int = Field(default=30 * 60, ge=1)  # 30 minutes
    channel: str = ""
    chat_id: str = ""


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class WebSearchConfig(Base):
    """Web search tool configuration."""

    provider: str = "brave"  # brave, tavily, duckduckgo, searxng, jina
    api_key: SecretStr = SecretStr("")
    base_url: str = ""  # SearXNG base URL
    max_results: int = 5


class WebToolsConfig(Base):
    """Web tools configuration."""

    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )
    disable_security_checks: bool = False  # When true, skip SSRF/private-network URL validation for web tools
    web_fetch_use_jina: bool = True  # Enable Jina Reader path for web_fetch when supported
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(Base):
    """Shell exec tool configuration."""

    timeout: int = 60
    path_append: str = ""


class MemoryConfig(Base):
    """Memory system configuration."""

    provider: Literal["file", "reme"] = "reme"  # Memory backend
    enable_vector_search: bool = False  # Enable vector search (requires more memory)
    llm_model: str = "gpt-4.1-nano"  # LLM for summarization
    embedding_model: str = "text-embedding-3-small"  # Embedding model for vector search


class MCPServerConfig(Base):
    """MCP server connection configuration (stdio or HTTP)."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = None  # auto-detected if omitted
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    url: str = ""  # HTTP/SSE: endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP/SSE: custom headers
    tool_timeout: int = 30  # seconds before a tool call is cancelled
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])  # Only register these tools; accepts raw MCP names or wrapped mcp_<server>_<tool> names; ["*"] = all tools; [] = no tools

class TimeoutsConfig(Base):
    """Timeout configuration for various tools."""

    web_search: float = 10.0
    web_fetch: float = 20.0
    mcp_tool: float = 30.0
    shell_exec: float = 60.0
    sdk_query: float = 30.0


class ToolsConfig(Base):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)


class Config(BaseSettings):
    """Root configuration for xbot."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from xbot.platform.providers.registry import PROVIDERS

        forced = self.agents.defaults.provider
        if forced != "auto":
            p = self.providers.get_provider_config(forced)
            return (p, forced) if p else (None, None)

        model_lower = (model or self.agents.defaults.model).lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")

        def _kw_matches(kw: str) -> bool:
            kw = kw.lower()
            return kw in model_lower or kw.replace("-", "_") in model_normalized

        def _get_api_key_value(p: ProviderConfig) -> str:
            """Safely get API key value, handling both SecretStr and str types."""
            api_key = p.api_key
            if api_key is None:
                return ""
            if hasattr(api_key, "get_secret_value"):
                return api_key.get_secret_value()
            return str(api_key)

        # Explicit provider prefix wins — prevents `github-copilot/...codex` matching openai_codex.
        for spec in PROVIDERS:
            p = self.providers.get_provider_config(spec.name)
            if p and model_prefix and normalized_prefix == spec.name:
                if spec.is_oauth or spec.is_local or _get_api_key_value(p):
                    return p, spec.name

        # Match by keyword (order follows PROVIDERS registry)
        for spec in PROVIDERS:
            p = self.providers.get_provider_config(spec.name)
            if p and any(_kw_matches(kw) for kw in spec.keywords):
                if spec.is_oauth or spec.is_local or _get_api_key_value(p):
                    return p, spec.name

        # Fallback: configured local providers can route models without
        # provider-specific keywords (for example plain "llama3.2" on Ollama).
        # Prefer providers whose detect_by_base_keyword matches the configured api_base
        # (e.g. Ollama's "11434" in "http://localhost:11434") over plain registry order.
        local_fallback: tuple[ProviderConfig, str] | None = None
        for spec in PROVIDERS:
            if not spec.is_local:
                continue
            p = self.providers.get_provider_config(spec.name)
            if not (p and p.api_base):
                continue
            if spec.detect_by_base_keyword and spec.detect_by_base_keyword in p.api_base:
                return p, spec.name
            if local_fallback is None:
                local_fallback = (p, spec.name)
        if local_fallback:
            return local_fallback

        # Fallback: gateways first, then others (follows registry order)
        # OAuth providers are NOT valid fallbacks — they require explicit model selection
        for spec in PROVIDERS:
            if spec.is_oauth:
                continue
            p = self.providers.get_provider_config(spec.name)
            if p and _get_api_key_value(p):
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model)
        if p and p.api_key:
            # Handle both SecretStr and str types
            if hasattr(p.api_key, "get_secret_value"):
                return p.api_key.get_secret_value()
            return str(p.api_key)
        return None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for gateway/local providers."""
        from xbot.platform.providers.registry import find_by_name

        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        # Only gateways get a default api_base here. Standard providers
        # (like Moonshot) set their base URL via env vars in backend env setup
        # to avoid polluting shared global client defaults.
        if name:
            spec = find_by_name(name)
            if spec and (spec.is_gateway or spec.is_local) and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(env_prefix="XBOT_", env_nested_delimiter="__")
