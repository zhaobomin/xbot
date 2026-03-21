# Claude SDK 权限请求处理设计方案

> Date: 2026-03-21
> Status: 设计阶段

## 背景

Claude Code SDK Agent 在运行时可能需要用户交互：
1. **权限请求 (PermissionRequest)**：Agent 需要用户确认是否执行某个操作（如退出 plan 模式、执行危险工具等）
2. **Ask Question**：Agent 需要用户提供额外信息

当前 xbot 通过 Channel（Telegram、飞书等）与用户交互，但 SDK 的权限请求无法直接转发到 Channel。

## 问题分析

### SDK 权限处理机制

SDK 提供两种处理方式：

#### 方式 1：`can_use_tool` 回调
```python
async def can_use_tool(
    tool_name: str,
    tool_input: dict,
    context: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
    # 返回权限决策
    pass

options = ClaudeAgentOptions(
    can_use_tool=can_use_tool,
    ...
)
```

#### 方式 2：`PermissionRequest` Hook
```python
hooks = {
    "PermissionRequest": [{
        "matcher": {"toolName": "*"},
        "hooks": [permission_handler]
    }]
}
```

### 当前架构限制

1. **MessageBus 是单向的**：只能 inbound → agent → outbound
2. **权限请求需要同步等待**：SDK 期望回调返回结果
3. **Channel 是异步的**：用户回复可能需要很长时间

## 设计方案

### 核心组件

```
┌─────────────────────────────────────────────────────────────┐
│                    ClaudeSDKBackend                          │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                OptionsBuilder                         │    │
│  │  ┌───────────────────────────────────────────────┐  │    │
│  │  │         PermissionRequestHandler               │  │    │
│  │  │  - can_use_tool callback                       │  │    │
│  │  │  - pending_requests: dict[id, Event]           │  │    │
│  │  │  - wait_for_user_response()                    │  │    │
│  │  └───────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     MessageBus (扩展)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐    │
│  │   inbound   │  │  outbound   │  │ permission_queue │    │
│  │   Queue     │  │   Queue     │  │     Queue        │    │
│  └─────────────┘  └─────────────┘  └──────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    ChannelManager                            │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐    │
│  │  Telegram   │  │   Feishu    │  │    Discord       │    │
│  │  Channel    │  │  Channel    │  │    Channel       │    │
│  └─────────────┘  └─────────────┘  └──────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 1. 扩展 MessageBus

```python
# xbot/bus/queue.py

from dataclasses import dataclass
from typing import Any
import asyncio

@dataclass
class PermissionRequest:
    """权限请求消息"""
    request_id: str
    session_key: str
    channel: str
    chat_id: str
    tool_name: str
    tool_input: dict[str, Any]
    message: str  # 给用户的提示信息
    suggestions: list[str]  # 可选的建议回复

@dataclass
class PermissionResponse:
    """权限响应消息"""
    request_id: str
    session_key: str
    decision: Literal["allow", "deny"]
    reason: str = ""
    updated_input: dict[str, Any] | None = None


class MessageBus:
    """扩展后的消息总线"""

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        # 新增：权限请求队列
        self._permission_requests: asyncio.Queue[PermissionRequest] = asyncio.Queue()
        # 新增：等待中的权限响应
        self._pending_permission_responses: dict[str, asyncio.Event] = {}
        self._permission_results: dict[str, PermissionResponse] = {}
        self._lock = asyncio.Lock()

    async def publish_permission_request(self, req: PermissionRequest) -> None:
        """发布权限请求到 outbound（会被 Channel 转发给用户）"""
        await self._permission_requests.put(req)
        # 同时发送消息通知用户
        await self.publish_outbound(OutboundMessage(
            channel=req.channel,
            chat_id=req.chat_id,
            content=req.message,
            metadata={"permission_request_id": req.request_id}
        ))

    async def wait_permission_response(
        self,
        request_id: str,
        timeout: float = 300.0
    ) -> PermissionResponse:
        """等待权限响应（SDK 回调中使用）"""
        event = asyncio.Event()
        async with self._lock:
            self._pending_permission_responses[request_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._permission_results.pop(request_id)
        except asyncio.TimeoutError:
            async with self._lock:
                self._pending_permission_responses.pop(request_id, None)
            return PermissionResponse(
                request_id=request_id,
                session_key="",
                decision="deny",
                reason="Timeout waiting for user response"
            )

    async def submit_permission_response(self, resp: PermissionResponse) -> bool:
        """提交权限响应（从 inbound 消息解析）"""
        async with self._lock:
            event = self._pending_permission_responses.get(resp.request_id)
            if event is None:
                return False
            self._permission_results[resp.request_id] = resp
            event.set()
        return True
```

### 2. PermissionRequestHandler

```python
# xbot/agent/permission_handler.py

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from loguru import logger

from xbot.bus.queue import MessageBus, PermissionRequest, PermissionResponse


class PermissionRequestHandler:
    """处理 Claude SDK 的权限请求。

    这个类实现了 `can_use_tool` 回调，将权限请求转发到 Channel，
    并等待用户响应。
    """

    def __init__(
        self,
        bus: MessageBus,
        timeout: float = 300.0,
        auto_approve_safe_tools: bool = True,
    ):
        """初始化权限请求处理器。

        Args:
            bus: 消息总线
            timeout: 等待用户响应的超时时间（秒）
            auto_approve_safe_tools: 是否自动批准安全工具
        """
        self.bus = bus
        self.timeout = timeout
        self.auto_approve_safe_tools = auto_approve_safe_tools

        # 安全工具列表（自动批准）
        self._safe_tools = {
            "read_file", "list_dir", "web_search", "web_fetch",
            "message", "cron", "spawn",
        }

        # 当前会话上下文
        self._session_context: dict[str, dict[str, str]] = {}
        # session_key -> {"channel": str, "chat_id": str}

    def set_session_context(
        self,
        session_key: str,
        channel: str,
        chat_id: str,
    ) -> None:
        """设置当前会话的上下文（用于发送权限请求消息）。"""
        self._session_context[session_key] = {
            "channel": channel,
            "chat_id": chat_id,
        }

    def clear_session_context(self, session_key: str) -> None:
        """清除会话上下文。"""
        self._session_context.pop(session_key, None)

    async def can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: Any,  # ToolPermissionContext
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """SDK can_use_tool 回调。

        Returns:
            ("allow", updated_input) 或 ("deny", reason)
        """
        # 1. 检查是否为安全工具
        if self.auto_approve_safe_tools and tool_name in self._safe_tools:
            logger.debug(f"Auto-approving safe tool: {tool_name}")
            return "allow", tool_input

        # 2. 获取会话上下文
        # 从 context 或内部获取 session_key
        session_key = self._get_current_session_key()
        if not session_key or session_key not in self._session_context:
            logger.warning(f"No session context for permission request: {tool_name}")
            return "deny", "No active session context"

        ctx = self._session_context[session_key]

        # 3. 创建权限请求
        request_id = str(uuid.uuid4())
        request = PermissionRequest(
            request_id=request_id,
            session_key=session_key,
            channel=ctx["channel"],
            chat_id=ctx["chat_id"],
            tool_name=tool_name,
            tool_input=tool_input,
            message=self._format_permission_message(tool_name, tool_input),
            suggestions=["允许", "拒绝"],
        )

        # 4. 发送请求并等待响应
        await self.bus.publish_permission_request(request)

        logger.info(f"Permission request sent: {tool_name} (id={request_id})")

        response = await self.bus.wait_permission_response(
            request_id,
            timeout=self.timeout
        )

        logger.info(f"Permission response received: {response.decision}")

        if response.decision == "allow":
            return "allow", response.updated_input or tool_input
        else:
            return "deny", response.reason or "User denied"

    def _get_current_session_key(self) -> str | None:
        """获取当前正在处理的会话 key。

        注意：这需要在 process() 中设置当前会话。
        """
        # 如果只有一个活跃会话，返回它
        if len(self._session_context) == 1:
            return list(self._session_context.keys())[0]
        # 否则返回 None（需要显式设置）
        return getattr(self, "_current_session_key", None)

    def set_current_session(self, session_key: str) -> None:
        """设置当前正在处理的会话（在 process() 开始时调用）。"""
        self._current_session_key = session_key

    def _format_permission_message(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> str:
        """格式化权限请求消息。"""
        # 简化工具输入显示
        input_summary = self._summarize_input(tool_input)
        return f"🔐 需要权限确认\n\n工具: {tool_name}\n参数: {input_summary}\n\n请回复「允许」或「拒绝」"

    def _summarize_input(self, tool_input: dict[str, Any], max_len: int = 200) -> str:
        """摘要工具输入。"""
        import json
        try:
            s = json.dumps(tool_input, ensure_ascii=False, indent=2)
            if len(s) > max_len:
                return s[:max_len] + "..."
            return s
        except Exception:
            return str(tool_input)[:max_len]

    def build_can_use_tool_callback(self):
        """构建 SDK 可用的回调函数。

        Returns:
            适用于 ClaudeAgentOptions.can_use_tool 的回调
        """
        from claude_agent_sdk.types import (
            PermissionResultAllow,
            PermissionResultDeny,
            ToolPermissionContext,
        )

        async def callback(
            tool_name: str,
            tool_input: dict[str, Any],
            context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            decision, result = await self.can_use_tool(tool_name, tool_input, context)
            if decision == "allow":
                return PermissionResultAllow(updated_input=result)
            else:
                return PermissionResultDeny(message=result)

        return callback
```

### 3. 集成到 OptionsBuilder

```python
# xbot/agent/backends/claude_sdk_backend.py (修改)

class OptionsBuilder:
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
        permission_handler: PermissionRequestHandler | None = None,  # 新增
    ):
        # ... 现有代码 ...
        self._permission_handler = permission_handler

    def build(
        self,
        session_key: str | None = None,
        *,
        include_agents: bool = True,
    ) -> "ClaudeAgentOptions":
        from claude_agent_sdk import ClaudeAgentOptions

        # ... 现有代码 ...

        # 构建 can_use_tool 回调
        can_use_tool = None
        if self._permission_handler:
            can_use_tool = self._permission_handler.build_can_use_tool_callback()

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
            can_use_tool=can_use_tool,  # 新增
        )
```

### 4. 集成到 ClaudeSDKBackend

```python
# xbot/agent/backends/claude_sdk_backend.py (修改)

class ClaudeSDKBackend(AgentBackend):
    def __init__(self):
        # ... 现有代码 ...
        self._permission_handler: PermissionRequestHandler | None = None

    async def initialize(self, config: AgentsConfig, shared_resources: dict[str, Any]) -> None:
        # ... 现有代码 ...

        # 初始化权限请求处理器
        bus = shared_resources.get("bus")
        if bus:
            permission_config = getattr(config.claude_sdk, "permission", {}) or {}
            self._permission_handler = PermissionRequestHandler(
                bus=bus,
                timeout=permission_config.get("timeout", 300.0),
                auto_approve_safe_tools=permission_config.get("auto_approve_safe_tools", True),
            )

        # 传递给 OptionsBuilder
        self._options_builder = OptionsBuilder(
            # ... 现有参数 ...
            permission_handler=self._permission_handler,
        )

    async def process(self, context: AgentContext) -> AsyncIterator[AgentResponse]:
        # 设置权限处理器的会话上下文
        if self._permission_handler:
            self._permission_handler.set_session_context(
                context.session_key,
                context.channel,
                context.chat_id,
            )
            self._permission_handler.set_current_session(context.session_key)

        try:
            # ... 现有处理逻辑 ...
            async for message in client.receive_response():
                # ... 处理消息 ...
                yield response
        finally:
            # 清理会话上下文
            if self._permission_handler:
                self._permission_handler.clear_session_context(context.session_key)
```

### 5. 处理用户回复

需要在 Channel 或 gateway 中检测用户的权限响应：

```python
# xbot/cli/commands.py 或 ChannelManager 中

async def _handle_inbound_message(self, msg: InboundMessage) -> None:
    """处理入站消息，包括权限响应。"""
    # 检查是否为权限响应
    if msg.metadata.get("permission_response"):
        request_id = msg.metadata.get("permission_request_id")
        decision = "allow" if "允许" in msg.content or "yes" in msg.content.lower() else "deny"

        response = PermissionResponse(
            request_id=request_id,
            session_key=msg.session_key,
            decision=decision,
            reason="" if decision == "allow" else "User denied",
        )
        await self.bus.submit_permission_response(response)
        return

    # 正常消息处理
    # ...
```

### 6. 配置扩展

```python
# xbot/config/schema.py (修改)

class PermissionConfig(Base):
    """权限请求处理配置。"""

    enabled: bool = True
    timeout: float = 300.0  # 等待用户响应的超时时间
    auto_approve_safe_tools: bool = True  # 自动批准安全工具
    safe_tools: list[str] = [  # 安全工具列表
        "read_file", "list_dir", "web_search", "web_fetch",
        "message", "cron", "spawn",
    ]


class ClaudeSDKAgentConfig(Base):
    """Claude SDK Agent 特有配置。"""

    max_turns: int = 40
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] = "acceptEdits"
    agents: dict[str, "AgentDefinition"] | None = None
    hooks: dict[str, list] | None = None
    permission: PermissionConfig = Field(default_factory=PermissionConfig)  # 新增
```

## CLI 模式处理

CLI 有两种运行模式，需要分别处理权限请求：

### 1. 命令模式 (`xbot agent -m "message"`)

单次执行模式，使用 `process_direct()`，无交互循环。

**特点**：
- 直接在终端显示询问
- 使用 `input()` 或 `prompt_toolkit` 获取用户回复
- 同步等待，简单直接

**实现方案**：

```python
# xbot/agent/permission_handler.py

class CLIPermissionHandler:
    """CLI 模式的权限请求处理器。

    直接在终端与用户交互，无需 MessageBus。
    """

    def __init__(
        self,
        auto_approve_safe_tools: bool = True,
        interactive: bool = True,
    ):
        self.auto_approve_safe_tools = auto_approve_safe_tools
        self.interactive = interactive  # 是否允许交互式询问

        self._safe_tools = {
            "read_file", "list_dir", "web_search", "web_fetch",
            "message", "cron", "spawn",
        }

    async def can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: Any,
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """处理权限请求。"""
        # 1. 自动批准安全工具
        if self.auto_approve_safe_tools and tool_name in self._safe_tools:
            return "allow", tool_input

        # 2. 非交互模式：拒绝需要确认的工具
        if not self.interactive:
            return "deny", f"Non-interactive mode: tool '{tool_name}' requires permission"

        # 3. 交互模式：在终端询问用户
        return await self._ask_user_in_terminal(tool_name, tool_input)

    async def _ask_user_in_terminal(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """在终端询问用户。"""
        import asyncio
        from rich.console import Console
        from rich.prompt import Prompt

        console = Console()

        # 显示请求信息
        console.print()
        console.print(f"[yellow]🔐 权限请求[/yellow]")
        console.print(f"  工具: [cyan]{tool_name}[/cyan]")

        # 简化显示参数
        input_summary = self._summarize_input(tool_input)
        if input_summary:
            console.print(f"  参数: [dim]{input_summary}[/dim]")

        console.print()

        # 在线程中运行同步的 prompt
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: Prompt.ask(
                    "允许执行？",
                    choices=["y", "n", "a"],  # yes, no, always
                    default="y",
                )
            )
        except (KeyboardInterrupt, EOFError):
            return "deny", "User cancelled"

        if response == "y":
            return "allow", tool_input
        elif response == "a":
            # 记住选择，添加到安全工具
            self._safe_tools.add(tool_name)
            return "allow", tool_input
        else:
            return "deny", "User denied"

    def _summarize_input(self, tool_input: dict[str, Any], max_len: int = 100) -> str:
        """摘要工具输入。"""
        import json
        try:
            s = json.dumps(tool_input, ensure_ascii=False)
            if len(s) > max_len:
                return s[:max_len] + "..."
            return s
        except Exception:
            return str(tool_input)[:max_len]

    def build_can_use_tool_callback(self):
        """构建 SDK 回调。"""
        from claude_agent_sdk.types import (
            PermissionResultAllow,
            PermissionResultDeny,
            ToolPermissionContext,
        )

        async def callback(
            tool_name: str,
            tool_input: dict[str, Any],
            context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            decision, result = await self.can_use_tool(tool_name, tool_input, context)
            if decision == "allow":
                return PermissionResultAllow(updated_input=result)
            else:
                return PermissionResultDeny(message=result)

        return callback
```

### 2. 交互模式 (`xbot agent`)

REPL 模式，使用 `run()` 循环处理消息。

**特点**：
- 使用 `prompt_toolkit` 获取用户输入
- 需要 pause spinner 来显示询问
- 保持交互上下文

**实现方案**：

```python
# xbot/agent/permission_handler.py

class InteractivePermissionHandler(CLIPermissionHandler):
    """交互模式的权限请求处理器。

    与 prompt_toolkit 集成，支持暂停 spinner、
    历史记录和更好的用户体验。
    """

    def __init__(
        self,
        auto_approve_safe_tools: bool = True,
        thinking_spinner: Any = None,  # _ThinkingSpinner
    ):
        super().__init__(auto_approve_safe_tools, interactive=True)
        self._thinking = thinking_spinner

    def set_thinking_spinner(self, spinner: Any) -> None:
        """设置当前的 spinner 引用。"""
        self._thinking = spinner

    async def _ask_user_in_terminal(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """在终端询问用户（交互模式）。"""
        import asyncio
        from rich.console import Console
        from rich.prompt import Prompt

        console = Console()

        # 暂停 spinner
        context_manager = self._thinking.pause() if self._thinking else nullcontext()
        with context_manager:
            # 显示请求信息
            console.print()
            console.print(f"[yellow]🔐 权限请求[/yellow]")
            console.print(f"  工具: [cyan]{tool_name}[/cyan]")

            input_summary = self._summarize_input(tool_input)
            if input_summary:
                console.print(f"  参数: [dim]{input_summary}[/dim]")

            console.print()

            # 获取用户输入
            loop = asyncio.get_event_loop()
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: Prompt.ask(
                        "允许执行？",
                        choices=["y", "n", "a"],
                        default="y",
                    )
                )
            except (KeyboardInterrupt, EOFError):
                return "deny", "User cancelled"

            if response == "y":
                return "allow", tool_input
            elif response == "a":
                self._safe_tools.add(tool_name)
                return "allow", tool_input
            else:
                return "deny", "User denied"
```

### 3. 集成到 CLI Commands

```python
# xbot/cli/commands.py (修改)

@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show xbot runtime logs during chat"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Non-interactive mode (deny all permission requests)"),
):
    """Interact with the agent directly."""
    # ... 现有代码 ...

    # 根据模式选择权限处理器
    if config.agents.type == "claude_sdk":
        if message:
            # 命令模式
            permission_handler = CLIPermissionHandler(
                auto_approve_safe_tools=True,
                interactive=not non_interactive,
            )
        else:
            # 交互模式
            permission_handler = InteractivePermissionHandler(
                auto_approve_safe_tools=True,
                thinking_spinner=_thinking,  # 稍后设置
            )

        # 传递给 AgentRuntime
        shared_resources["permission_handler"] = permission_handler

    # ... 后续代码 ...

    if message:
        # 命令模式
        async def run_once():
            nonlocal _thinking
            _thinking = _ThinkingSpinner(enabled=not logs)
            if isinstance(permission_handler, InteractivePermissionHandler):
                permission_handler.set_thinking_spinner(_thinking)
            with _thinking:
                response = await agent_loop.process_direct(message, session_id, on_progress=_cli_progress)
            _thinking = None
            _print_agent_response(response, render_markdown=markdown)
            await agent_loop.close_mcp()

        asyncio.run(run_once())
    else:
        # 交互模式
        # ... 现有交互模式代码，在循环中更新 spinner 引用 ...
```

### 4. 统一的 PermissionHandler 工厂

```python
# xbot/agent/permission_handler.py

def create_permission_handler(
    mode: Literal["channel", "cli", "interactive"],
    *,
    bus: MessageBus | None = None,
    auto_approve_safe_tools: bool = True,
    timeout: float = 300.0,
    thinking_spinner: Any = None,
    non_interactive: bool = False,
) -> PermissionRequestHandler | CLIPermissionHandler | InteractivePermissionHandler:
    """创建适合当前模式的权限处理器。

    Args:
        mode: 运行模式
        bus: 消息总线（channel 模式必需）
        auto_approve_safe_tools: 是否自动批准安全工具
        timeout: 等待用户响应的超时时间
        thinking_spinner: 交互模式的 spinner
        non_interactive: CLI 非交互模式

    Returns:
        对应模式的权限处理器实例
    """
    if mode == "channel":
        if bus is None:
            raise ValueError("Channel mode requires a MessageBus")
        return PermissionRequestHandler(
            bus=bus,
            timeout=timeout,
            auto_approve_safe_tools=auto_approve_safe_tools,
        )
    elif mode == "interactive":
        return InteractivePermissionHandler(
            auto_approve_safe_tools=auto_approve_safe_tools,
            thinking_spinner=thinking_spinner,
        )
    else:  # cli
        return CLIPermissionHandler(
            auto_approve_safe_tools=auto_approve_safe_tools,
            interactive=not non_interactive,
        )
```

### 5. 在 AgentRuntime 中集成

```python
# xbot/agent/runtime.py (修改)

class AgentRuntime:
    def __init__(self, config: Any, shared_resources: dict[str, Any]):
        # ... 现有代码 ...

        # 创建权限处理器
        self._permission_handler = self._create_permission_handler()

    def _create_permission_handler(self):
        """根据运行模式创建权限处理器。"""
        if self.config.agents.type != "claude_sdk":
            return None

        # 检测运行模式
        if self.bus is not None:
            # Gateway 模式（有 bus）
            return create_permission_handler(
                mode="channel",
                bus=self.bus,
                auto_approve_safe_tools=True,
            )
        else:
            # CLI 模式（无 bus）
            return create_permission_handler(
                mode="cli",
                auto_approve_safe_tools=True,
            )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress=None,
    ) -> str:
        # 设置权限处理器的 spinner（交互模式）
        if isinstance(self._permission_handler, InteractivePermissionHandler):
            self._permission_handler.set_thinking_spinner(getattr(self, "_thinking", None))

        # ... 现有代码 ...
```

## Ask Question 支持

除了权限请求，SDK 还可能发起 Ask Question（请求用户提供信息）。

### SDK 机制

```python
# SDK 通过 control_request 发送问题
{
    "type": "control_request",
    "request_id": "...",
    "request": {
        "subtype": "ask_question",  # 或类似机制
        "question": "请提供文件路径",
        "options": ["a", "b", "c"],  # 可选
    }
}
```

### 实现方案

Ask Question 的处理与 Permission Request 类似：

```python
# xbot/agent/permission_handler.py

@dataclass
class QuestionRequest:
    """问题请求"""
    request_id: str
    session_key: str
    channel: str
    chat_id: str
    question: str
    options: list[str] | None = None

@dataclass
class QuestionResponse:
    """问题响应"""
    request_id: str
    answer: str

class PermissionRequestHandler:
    # ... 现有代码 ...

    async def handle_question(
        self,
        question: str,
        options: list[str] | None = None,
    ) -> str:
        """处理问题请求。"""
        # CLI 模式：直接在终端询问
        # Channel 模式：发送到 Channel 等待回复
        pass
```

## 实现优先级

### Phase 1: 基础权限处理
1. 扩展 MessageBus 添加权限请求/响应支持
2. 实现 PermissionRequestHandler 基本功能
3. 集成到 ClaudeSDKBackend

### Phase 2: CLI 模式支持
1. 实现 CLIPermissionHandler
2. 实现 InteractivePermissionHandler
3. 集成到 CLI commands

### Phase 3: 用户体验优化
1. 格式化权限请求消息（Markdown、按钮等）
2. 支持快捷回复（如 Telegram inline keyboard）
3. 超时处理和重试

### Phase 4: 高级功能
1. Hook 系统集成
2. Ask Question 支持
3. 权限规则持久化

## 测试计划

1. **单元测试**：PermissionRequestHandler 的逻辑
2. **集成测试**：SDK 回调到 Channel 的完整流程
3. **CLI 测试**：命令模式和交互模式的权限处理
4. **超时测试**：验证超时行为
5. **多会话测试**：多个并发权限请求

## 风险和注意事项

1. **死锁风险**：如果 SDK 在等待权限响应时阻塞了事件循环
2. **超时处理**：需要合理设置默认超时时间
3. **会话隔离**：确保权限响应只匹配对应的请求
4. **Channel 兼容性**：不同 Channel 的交互方式不同（按钮 vs 纯文本）
5. **CLI 信号处理**：用户按 Ctrl+C 时的清理逻辑
6. **非交互模式**：确保脚本/自动化场景下能正确拒绝或跳过权限请求