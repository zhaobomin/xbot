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
    model: str = "claude-sonnet-4-5"
    provider: str = (
        "auto"  # Provider name (e.g. "anthropic", "openrouter") or "auto" for auto-detection
    )
    # 可用模型列表，用于动态切换。为空时只用 model 字段
    available_models: list[str] = Field(default_factory=list)
    max_tokens: int = 8192
    context_window_tokens: int = 65_536
    temperature: float = 0.1
    max_tool_iterations: int = 40
    # Deprecated compatibility field: accepted from old configs but ignored at runtime.
    memory_window: int | None = Field(default=None, exclude=True)
    reasoning_effort: str | None = None  # low / medium / high — enables LLM thinking mode

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
    ]


class ClaudeSDKAgentConfig(Base):
    """Claude SDK Agent 特有配置.

    注意: 供应商凭证(api_key/api_base)从全局 providers 读取
    """

    max_turns: int = 40
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
    # Memory consolidation 策略："off"=禁用，"async"=异步后台，"sync"=同步阻塞
    # 默认禁用，避免异步任务阻塞和 ReMe 初始化超时问题。需要时可手动启用 "async"
    memory_consolidation_mode: Literal["off", "async", "sync"] = "off"

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
    client_force_kill_enabled: bool = Field(default=False, description="Enable force-kill fallback for leaked Claude client processes")
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


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: SecretStr = SecretStr("")
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(Base):
    """Configuration for LLM providers."""

    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    azure_openai: ProviderConfig = Field(default_factory=ProviderConfig)  # Azure OpenAI (model = deployment name)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)  # Ollama local models
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)  # SiliconFlow (硅基流动)
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)  # VolcEngine (火山引擎)
    volcengine_coding_plan: ProviderConfig = Field(default_factory=ProviderConfig)  # VolcEngine Coding Plan
    byteplus: ProviderConfig = Field(default_factory=ProviderConfig)  # BytePlus (VolcEngine international)
    byteplus_coding_plan: ProviderConfig = Field(default_factory=ProviderConfig)  # BytePlus Coding Plan
    openai_codex: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenAI Codex (OAuth)
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig)  # Github Copilot (OAuth)

    # Claude SDK 兼容供应商 (Anthropic Messages API 兼容)
    aliyun_coding_plan: ProviderConfig = Field(
        default_factory=ProviderConfig,
        description="阿里云 Coding Plan (Anthropic 兼容, 仅 Claude SDK Agent)"
    )
    alrun: ProviderConfig = Field(
        default_factory=ProviderConfig,
        description="Alrun API 网关 (Anthropic 兼容, 仅 Claude SDK Agent)"
    )


class HeartbeatConfig(Base):
    """Heartbeat service configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes
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

class ToolsConfig(Base):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class Config(BaseSettings):
    """Root configuration for xbot."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from xbot.providers.registry import PROVIDERS

        forced = self.agents.defaults.provider
        if forced != "auto":
            p = getattr(self.providers, forced, None)
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
            p = getattr(self.providers, spec.name, None)
            if p and model_prefix and normalized_prefix == spec.name:
                if spec.is_oauth or spec.is_local or _get_api_key_value(p):
                    return p, spec.name

        # Match by keyword (order follows PROVIDERS registry)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
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
            p = getattr(self.providers, spec.name, None)
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
            p = getattr(self.providers, spec.name, None)
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
        from xbot.providers.registry import find_by_name

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
