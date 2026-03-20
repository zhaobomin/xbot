# nanobot 双Agent架构改造设计文档

> 版本: 2.0
> 日期: 2026-03-20
> 状态: 部分落地

## 当前状态说明

截至当前代码版本：

- `gateway` 和 `nanobot agent` 已经切到 router-backed runtime
- 统一入口是 `AgentRuntime -> AgentRouter -> backend`
- `nanobot/agent/claude_sdk_loop.py` 不再是主运行时入口，当前属于过渡/参考实现
- `nanobot.config.provider_registry` 已经退化为对主 provider registry 的兼容投影视图，不再维护第二份 provider 真相源
- Claude SDK 路径已经补回 `spawn`、session 持久化、`/new` reset/archive 以及 `/stop` 级联取消能力

仍待继续收敛的部分：

- Claude SDK native `agents/handoffs` 的产品化接入
- 过渡代码的继续清理

## 一、背景与目标

### 1.1 背景

nanobot 目前采用自建的 `AgentLoop` 实现，通过 LiteLLM 支持多种 LLM 供应商。随着 Claude Agent SDK 的发布，需要评估引入 SDK 的可行性，同时：

1. **保留现有能力**: LiteLLM 支持的 20+ 供应商仍需可用
2. **新增 SDK 能力**: 获得更原生的 Claude 集成、Hooks、Subagent 等特性
3. **支持阿里云**: 需要支持 Aliyun Coding Plan 等国产供应商

### 1.2 目标

- 支持通过配置切换 `litellm` 和 `claude_sdk` 两种 Agent 后端
- **共享供应商和模型配置**：只需修改 `agents.type` 即可切换 Agent 类型
- 保留现有 LiteLLM Agent 的全部功能
- 新增 Claude SDK Agent，支持 Anthropic 和 Aliyun Coding Plan
- 最小化对现有代码的侵入性
- Skills 全部转换为 MCP Tools（方案A）

### 1.3 核心设计原则

**单一配置源原则**：

```
┌─────────────────────────────────────────────────────────┐
│  providers.*.api_key     ← 唯一的凭证来源               │
│  agents.defaults.model   ← 唯一的模型来源               │
│  agents.defaults.provider← 唯一的供应商来源             │
│  agents.type             ← 唯一的 Agent 类型切换开关    │
└─────────────────────────────────────────────────────────┘
```

**配置分离原则**：

| 配置类别 | 位置 | 两种 Agent 共享 |
|---------|------|----------------|
| 供应商凭证 | `providers` | ✅ 共享 |
| 模型选择 | `agents.defaults` | ✅ 共享 |
| Agent 特有 | `agents.claude_sdk` | ❌ SDK 专用 |

这样设计的好处：
1. **切换简单**：只改 `agents.type` 即可切换 Agent
2. **凭证统一**：同一供应商只需配置一次 api_key
3. **行为一致**：同一个模型在不同 Agent 下表现一致
4. **向后兼容**：旧配置无需修改即可使用

---

## 二、整体架构

### 2.1 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Channel Layer                                │
│            Telegram / Discord / Feishu / WhatsApp / Slack ...       │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ InboundMessage
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         MessageBus                                   │  ← 无修改
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       AgentRouter (新增)                             │
│                                                                      │
│   agents.type ──────────────┬─────────────────┐                     │
│                              │                 │                     │
│                              ▼                 ▼                     │
│              ┌───────────────────────┐  ┌───────────────────────┐   │
│              │    LiteLLMBackend     │  │    ClaudeSDKBackend   │   │
│              │    (封装现有代码)      │  │    (新增实现)         │   │
│              └───────────────────────┘  └───────────────────────┘   │
│                              │                 │                     │
│                              └────────┬────────┘                     │
│                                       │                              │
│                                       ▼                              │
│              ┌─────────────────────────────────────────┐            │
│              │        Shared Config (共享配置)          │            │
│              │  providers: { anthropic, aliyun-... }   │            │
│              │  agents.defaults: { model, provider }   │            │
│              └─────────────────────────────────────────┘            │
│                                       │                              │
│                                       ▼                              │
│              ┌─────────────────────────────────────────┐            │
│              │        Shared Resources (共享资源)       │            │
│              │  - MemoryStore, SessionManager           │            │
│              │  - SkillsLoader, ChannelManager          │            │
│              └─────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 配置流向图

```
┌──────────────────────────────────────────────────────────────────────┐
│                           config.json                                 │
│                                                                       │
│  ┌─────────────────────────┐    ┌─────────────────────────────┐      │
│  │     providers           │    │        agents               │      │
│  │  ┌───────────────────┐  │    │  ┌───────────────────────┐  │      │
│  │  │ anthropic         │  │    │  │ type: "litellm"       │  │      │
│  │  │   api_key: "xxx"  │  │    │  │         ↓             │  │      │
│  │  ├───────────────────┤  │    │  │ defaults:             │  │      │
│  │  │ aliyun-codingplan │  │───►│  │   model: "claude-..." │  │      │
│  │  │   api_key: "yyy"  │  │    │  │   provider: "auto"    │  │      │
│  │  ├───────────────────┤  │    │  ├───────────────────────┤  │      │
│  │  │ deepseek          │  │    │  │ claude_sdk:           │  │      │
│  │  │   api_key: "zzz"  │  │    │  │   max_turns: 40       │  │      │
│  │  └───────────────────┘  │    │  │   permission_mode: ..│  │      │
│  └─────────────────────────┘    │  └───────────────────────┘  │      │
│                                  └─────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────────┘
                    │                              │
                    │ 供应商凭证                    │ Agent 配置
                    ▼                              ▼
         ┌──────────────────┐          ┌──────────────────┐
         │  两种 Backend    │          │  两种 Backend    │
         │  都从这里读取    │          │  共享模型/供应商 │
         │  api_key        │          │  配置            │
         └──────────────────┘          └──────────────────┘
```

### 2.3 模块依赖关系

```
                    ┌──────────────┐
                    │   Config     │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ AgentRouter  │  ← 新增：路由层
                    └──────┬───────┘
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
    ┌──────────────────┐      ┌──────────────────┐
    │  LiteLLMBackend  │      │ ClaudeSDKBackend │
    │    (封装层)       │      │    (新增)        │
    └────────┬─────────┘      └────────┬─────────┘
             │                         │
             ▼                         ▼
    ┌──────────────────┐      ┌──────────────────┐
    │   AgentLoop      │      │ ClaudeSDKClient  │
    │   (无修改)        │      │   (SDK封装)      │
    └──────────────────┘      └──────────────────┘
```

---

## 三、新增模块设计

### 3.1 协议层 (`nanobot/agent/protocol.py`)

定义 Agent 后端的统一接口，使两种实现可以互换。

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator
from dataclasses import dataclass

@dataclass
class AgentResponse:
    """统一的 Agent 响应格式"""
    content: str
    tool_calls: list[dict] | None = None
    finish_reason: str = "stop"  # stop | tool_use | error | max_iterations
    usage: dict | None = None
    raw_message: Any = None

class AgentBackend(ABC):
    """Agent 后端抽象接口"""

    @property
    @abstractmethod
    def name(self) -> str:
        """后端名称"""
        pass

    @abstractmethod
    async def initialize(self, config: "AgentsConfig", shared_resources: dict) -> None:
        """初始化 Agent"""
        pass

    @abstractmethod
    async def process(
        self,
        session_key: str,
        prompt: str,
        context: dict | None = None,
    ) -> AsyncIterator[AgentResponse]:
        """处理消息，流式返回响应"""
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """关闭 Agent"""
        pass

    # 可选方法
    async def execute_tool(self, tool_name: str, args: dict) -> str | None:
        """直接执行工具（可选）"""
        return None
```

### 3.2 路由层 (`nanobot/agent/router.py`)

根据配置选择并管理 Agent 后端。

```python
from typing import AsyncIterator
from nanobot.agent.protocol import AgentBackend, AgentResponse
from nanobot.config.schema import AgentsConfig

class AgentRouter:
    """Agent 路由器 - 根据配置选择后端"""

    _backends: dict[str, type[AgentBackend]] = {}  # 注册表

    def __init__(self, config: AgentsConfig, shared_resources: dict):
        self.config = config
        self.shared_resources = shared_resources
        self._backend: AgentBackend | None = None

    @property
    def backend_type(self) -> str:
        return self.config.type

    async def initialize(self) -> None:
        """初始化选定的后端"""
        backend_class = self._backends.get(self.config.type)
        if not backend_class:
            raise ValueError(f"Unknown backend: {self.config.type}")

        self._backend = backend_class()
        await self._backend.initialize(self.config, self.shared_resources)

    async def process(
        self,
        session_key: str,
        prompt: str,
        context: dict | None = None,
    ) -> AsyncIterator[AgentResponse]:
        """处理消息"""
        if not self._backend:
            await self.initialize()
        async for response in self._backend.process(session_key, prompt, context):
            yield response

    async def switch_backend(self, new_type: str) -> None:
        """动态切换后端"""
        if self._backend:
            await self._backend.shutdown()
        self.config.type = new_type
        self._backend = None
        await self.initialize()

    @classmethod
    def register_backend(cls, name: str, backend_class: type[AgentBackend]) -> None:
        """注册新的后端类型"""
        cls._backends[name] = backend_class
```

### 3.3 LiteLLM Backend (`nanobot/agent/backends/litellm_backend.py`)

**封装现有代码，零侵入**。

```python
from nanobot.agent.protocol import AgentBackend, AgentResponse
from nanobot.agent.loop import AgentLoop

class LiteLLMBackend(AgentBackend):
    """LiteLLM Agent 后端 - 封装现有 AgentLoop"""

    name = "litellm"

    def __init__(self):
        self.agent_loop: AgentLoop | None = None

    async def initialize(self, config: AgentsConfig, shared_resources: dict) -> None:
        # 复用现有 AgentLoop，不修改其代码
        self.agent_loop = AgentLoop(
            bus=shared_resources["bus"],
            provider=shared_resources["provider"],
            workspace=shared_resources["workspace"],
            # ... 其他参数从 config.litellm 获取
        )
        await self.agent_loop.initialize()

    async def process(self, session_key: str, prompt: str, context: dict | None = None):
        # 委托给现有 AgentLoop
        async for response in self.agent_loop.process_streaming(session_key, prompt, context):
            yield response

    async def shutdown(self) -> None:
        if self.agent_loop:
            await self.agent_loop.close_mcp()
```

### 3.4 Claude SDK Backend (`nanobot/agent/backends/claude_sdk_backend.py`)

**新增实现**。

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from nanobot.agent.protocol import AgentBackend, AgentResponse
from nanobot.agent.skill_to_mcp import SkillToMCPConverter
from nanobot.agent.tool_adapter import ToolAdapter

class ClaudeSDKBackend(AgentBackend):
    """Claude Agent SDK 后端"""

    name = "claude_sdk"

    # 供应商默认 URL 映射
    PROVIDER_URLS = {
        "anthropic": "https://api.anthropic.com",
        "aliyun-codingplan": "https://coding.dashscope.aliyuncs.com/apps/anthropic",
        "alrun": "https://llm.alrun.cn/v1/messages",
    }

    # 供应商环境变量映射
    PROVIDER_ENV_KEYS = {
        "anthropic": "ANTHROPIC_API_KEY",
        "aliyun-codingplan": "ALIYUN_CODINGPLAN_API_KEY",
        "alrun": "ALRUN_API_KEY",
    }

    def __init__(self):
        self.sdk_config: ClaudeSDKAgentConfig | None = None
        self._skill_converter: SkillToMCPConverter | None = None
        self._tool_adapter: ToolAdapter | None = None
        self._shared_resources: dict = {}

    async def initialize(self, config: AgentsConfig, shared_resources: dict) -> None:
        self.sdk_config = config.claude_sdk
        self._shared_resources = shared_resources

        self._skill_converter = SkillToMCPConverter(shared_resources["workspace"])
        self._tool_adapter = ToolAdapter(shared_resources["workspace"], shared_resources.get("tools_config"))

    def _build_options(self) -> ClaudeAgentOptions:
        """构建 SDK 配置"""
        # 从共享配置获取模型和供应商
        defaults = self._shared_resources["config"].agents.defaults
        model = self._normalize_model_name(defaults.model, defaults.provider)

        # 从共享 providers 获取凭证
        api_key, base_url = self._get_provider_config()

        # Skills 转换为 MCP
        skills_mcp = self._skill_converter.convert_all_skills()

        # 工具转换为 MCP
        tools_mcp = self._tool_adapter.create_mcp_server()

        # 合并 MCP Servers
        mcp_servers = {}
        mcp_servers.update(skills_mcp)
        mcp_servers.update(tools_mcp)

        return ClaudeAgentOptions(
            cwd=self._shared_resources["workspace"],
            model=model,
            max_turns=self.sdk_config.max_turns,
            permission_mode=self.sdk_config.permission_mode,
            mcp_servers=mcp_servers,
            agents=self.sdk_config.agents,
            hooks=self.sdk_config.hooks,
            system_prompt=self._build_system_prompt(),
            extra_args={
                "api_key": api_key,
                "base_url": base_url,
            },
        )

    def _get_provider_config(self) -> tuple[str, str]:
        """从全局 providers 读取凭证"""
        defaults = self._shared_resources["config"].agents.defaults
        provider_name = defaults.provider

        # 如果 provider 是 "auto"，尝试自动检测
        if provider_name == "auto":
            provider_name = self._detect_provider_from_model(defaults.model)

        # 规范化供应商名称（配置中用 "-" 但 Python 属性用 "_"）
        provider_attr = provider_name.replace("-", "_")

        # 从全局 providers 读取
        providers = self._shared_resources["config"].providers
        provider_config = getattr(providers, provider_attr, None)

        # 获取 api_key
        api_key = ""
        if provider_config and provider_config.api_key:
            api_key = provider_config.api_key
        else:
            env_key = self.PROVIDER_ENV_KEYS.get(provider_name, "")
            api_key = os.environ.get(env_key, "")

        # 获取 base_url
        base_url = ""
        if provider_config and provider_config.api_base:
            base_url = provider_config.api_base
        else:
            base_url = self.PROVIDER_URLS.get(provider_name, "")

        return api_key, base_url

    def _normalize_model_name(self, model: str, provider: str) -> str:
        """标准化模型名称"""
        # Alrun 需要去掉前缀
        if provider == "alrun" and model.startswith("alrun-"):
            return model[len("alrun-"):]
        return model

    def _detect_provider_from_model(self, model: str) -> str:
        """从模型名称检测供应商"""
        model_lower = model.lower()
        if "qwen" in model_lower:
            return "aliyun-codingplan"
        elif "claude" in model_lower:
            return "anthropic"
        return "anthropic"  # 默认

    def _build_system_prompt(self) -> str:
        """构建简洁的系统提示词"""
        return "你是 nanobot，一个智能助手。"

    async def process(self, session_key: str, prompt: str, context: dict | None = None):
        """使用 SDK 处理消息"""
        options = self._build_options()

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_messages():
                yield self._convert_message(message)

    def _convert_message(self, message) -> AgentResponse:
        """转换 SDK 消息为统一格式"""
        # ... 转换逻辑
        pass
```

### 3.5 Skill → MCP 转换器 (`nanobot/agent/skill_to_mcp.py`)

将 Skills 转换为 MCP Tools。

```python
from claude_agent_sdk import tool, create_sdk_mcp_server
from pathlib import Path
import re

class SkillToMCPConverter:
    """将 Skill 文件转换为 MCP Tools"""

    def __init__(self, workspace: str):
        self.workspace = Path(workspace)
        self.skills_dir = self.workspace / ".nanobot" / "skills"

    def convert_all_skills(self) -> dict:
        """转换所有 Skills 为 MCP Server"""
        tools = []

        if self.skills_dir.exists():
            for skill_file in self.skills_dir.glob("**/SKILL.md"):
                tools.extend(self._convert_skill(skill_file))

        if not tools:
            return {}

        return {
            "skills": create_sdk_mcp_server(
                name="skills",
                version="1.0.0",
                tools=tools,
            )
        }

    def _convert_skill(self, skill_path: Path) -> list:
        """转换单个 Skill 为工具列表"""
        content = skill_path.read_text()
        frontmatter, body = self._parse_frontmatter(content)

        skill_name = skill_path.parent.name
        description = frontmatter.get("description", skill_name)

        tools = []

        # 1. 提取操作定义
        actions = self._extract_actions(body, skill_name)
        for action in actions:
            tools.append(self._create_action_tool(action))

        # 2. 如果没有操作定义，创建咨询工具
        if not tools:
            tools.append(self._create_consultation_tool(skill_name, description, body))

        return tools

    def _extract_actions(self, body: str, skill_name: str) -> list[dict]:
        """从 Skill 中提取操作定义"""
        # 查找 ### action_name 格式的操作定义
        pattern = r'###\s+(\w+)\s*\n([^#]+)'
        matches = re.findall(pattern, body)

        actions = []
        for name, content in matches:
            actions.append({
                "name": f"{skill_name}_{name}",
                "description": self._extract_description(content),
                "content": content,
            })

        return actions

    def _create_action_tool(self, action: dict):
        """创建操作型工具"""

        @tool(
            action["name"],
            action["description"],
            {"query": str},  # 简化参数
        )
        async def action_tool(args: dict) -> dict:
            return {
                "content": [{
                    "type": "text",
                    "text": f"执行 {action['name']}:\n{action['content']}"
                }]
            }

        return action_tool

    def _create_consultation_tool(self, name: str, description: str, body: str):
        """创建咨询型工具"""

        @tool(
            f"skill_{name}",
            f"{description} - 提供指导和最佳实践",
            {"query": str},
        )
        async def consultation_tool(args: dict) -> dict:
            query = args.get("query", "")
            # 返回相关指导内容
            relevant_content = self._find_relevant_section(body, query)
            return {
                "content": [{
                    "type": "text",
                    "text": f"根据 {name} 技能:\n\n{relevant_content or body}"
                }]
            }

        return consultation_tool

    def _parse_frontmatter(self, content: str) -> tuple[dict, str]:
        """解析 YAML frontmatter"""
        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
            if match:
                # 简单 YAML 解析
                metadata = {}
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        metadata[key.strip()] = value.strip().strip('"\'')
                return metadata, match.group(2)
        return {}, content
```

### 3.6 工具适配器 (`nanobot/agent/tool_adapter.py`)

将 nanobot 工具适配为 MCP 格式。

```python
from claude_agent_sdk import tool, create_sdk_mcp_server
from nanobot.agent.tools import (
    ReadFileTool, WriteFileTool, EditFileTool,
    ShellTool, WebSearchTool, WebFetchTool,
    MessageTool, CronTool,
)

class ToolAdapter:
    """将 nanobot 工具适配为 MCP 格式"""

    def __init__(self, workspace: str, config: "ToolsConfig" = None):
        self.workspace = workspace
        self.config = config
        self._tools: dict[str, Tool] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """注册默认工具"""
        # nanobot 特色工具
        self._tools["message"] = MessageTool()
        self._tools["cron"] = CronTool()
        self._tools["web_search"] = WebSearchTool()
        self._tools["web_fetch"] = WebFetchTool()

        # 文件操作（可选：使用 SDK 内置或自定义）
        self._tools["read_file"] = ReadFileTool()
        self._tools["write_file"] = WriteFileTool()
        self._tools["edit_file"] = EditFileTool()
        self._tools["list_dir"] = ListDirTool()

        # Shell
        self._tools["shell"] = ShellTool()

    def create_mcp_server(self) -> dict:
        """创建 MCP Server"""
        mcp_tools = [self._adapt_tool(t) for t in self._tools.values()]
        return {
            "nanobot": create_sdk_mcp_server(
                name="nanobot_tools",
                version="1.0.0",
                tools=mcp_tools,
            )
        }

    def _adapt_tool(self, nanobot_tool: Tool):
        """将 nanobot Tool 转换为 MCP Tool"""

        @tool(
            nanobot_tool.name,
            nanobot_tool.description,
            nanobot_tool.parameters,
        )
        async def adapted(args: dict) -> dict:
            result = await nanobot_tool.execute(**args)
            return {"content": [{"type": "text", "text": result}]}

        return adapted
```

---

## 四、供应商注册表设计

### 4.1 注册表结构 (`nanobot/config/provider_registry.py`)

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class ProviderSpec:
    """供应商规格定义"""
    name: str                          # 供应商标识符
    display_name: str                  # 显示名称
    protocol: Literal["anthropic", "openai", "litellm"]  # 协议类型
    default_base_url: str              # 默认 base URL
    supported_by_sdk: bool             # 是否被 Claude SDK 支持


# 供应商注册表
PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    # ========================================
    # LiteLLM 专用供应商（不支持 Claude SDK）
    # ========================================
    "openai": ProviderSpec(
        name="openai",
        display_name="OpenAI",
        protocol="litellm",
        default_base_url="https://api.openai.com/v1",
        supported_by_sdk=False,
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        display_name="DeepSeek",
        protocol="litellm",
        default_base_url="https://api.deepseek.com",
        supported_by_sdk=False,
    ),
    "dashscope": ProviderSpec(
        name="dashscope",
        display_name="阿里云 DashScope",
        protocol="litellm",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        supported_by_sdk=False,
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        display_name="OpenRouter",
        protocol="litellm",
        default_base_url="https://openrouter.ai/api/v1",
        supported_by_sdk=False,
    ),
    # ... 其他 LiteLLM 供应商 ...

    # ========================================
    # Claude SDK 兼容供应商
    # ========================================
    "anthropic": ProviderSpec(
        name="anthropic",
        display_name="Anthropic",
        protocol="anthropic",
        default_base_url="https://api.anthropic.com",
        supported_by_sdk=True,
    ),
    "aliyun-codingplan": ProviderSpec(
        name="aliyun-codingplan",
        display_name="阿里云 Coding Plan",
        protocol="anthropic",
        default_base_url="https://coding.dashscope.aliyuncs.com/apps/anthropic",
        supported_by_sdk=True,
    ),
    "alrun": ProviderSpec(
        name="alrun",
        display_name="Alrun",
        protocol="anthropic",
        default_base_url="https://llm.alrun.cn/v1/messages",
        supported_by_sdk=True,
    ),
}


def get_provider_spec(name: str) -> ProviderSpec | None:
    """获取供应商规格"""
    return PROVIDER_REGISTRY.get(name)


def get_sdk_compatible_providers() -> list[str]:
    """获取 Claude SDK 兼容的供应商列表"""
    return [name for name, spec in PROVIDER_REGISTRY.items() if spec.supported_by_sdk]


def is_provider_sdk_compatible(name: str) -> bool:
    """检查供应商是否兼容 Claude SDK"""
    spec = PROVIDER_REGISTRY.get(name)
    return spec.supported_by_sdk if spec else False
```

### 4.2 供应商兼容性矩阵

| 供应商 | 协议 | LiteLLM Agent | Claude SDK Agent |
|--------|------|---------------|------------------|
| anthropic | anthropic | ✅ | ✅ |
| aliyun-codingplan | anthropic | ❌ | ✅ |
| alrun | anthropic | ❌ | ✅ |
| openai | litellm | ✅ | ❌ |
| deepseek | litellm | ✅ | ❌ |
| dashscope | litellm | ✅ | ❌ |
| openrouter | litellm | ✅ | ❌ |
| moonshot | litellm | ✅ | ❌ |
| gemini | litellm | ✅ | ❌ |

**说明**:
- `aliyun-codingplan` 和 `alrun` 使用 Anthropic 兼容协议，仅支持 Claude SDK Agent
- LiteLLM Agent 通过 LiteLLM 统一接口支持多种供应商
- 配置时会校验供应商与 Agent 类型的兼容性

---

## 五、配置设计（共享供应商和模型）

### 5.1 设计原则

**核心原则**: 供应商凭证和模型配置全局共享，只需切换 `agents.type` 即可切换 Agent 类型。

```
┌──────────────────────────────────────────────────────────────┐
│                        Config                                 │
│                                                               │
│  providers (全局共享)          agents                        │
│  ├── anthropic                 ├── type: "litellm" | "claude_sdk"  ← 仅此字段切换
│  │   ├── api_key: "xxx"       │                              │
│  │   └── api_base: "..."      │                              │
│  ├── aliyun-codingplan         ├── defaults (共享模型配置)     │
│  │   ├── api_key: "yyy"       │   ├── model: "claude-sonnet" │
│  │   └── api_base: "..."      │   ├── provider: "anthropic"  │
│  └── deepseek                  │   └── workspace: "..."       │
│      ├── api_key: "zzz"       │                              │
│      └── api_base: "..."      └── claude_sdk (SDK特有配置)   │
│                                    ├── max_turns: 40          │
│                                    └── permission_mode: "..." │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

### 5.2 Schema 扩展 (`nanobot/config/schema.py`)

```python
# ============================================
# 1. 扩展 ProvidersConfig - 新增 Claude SDK 兼容供应商
# ============================================

class ProvidersConfig(Base):
    """供应商配置 - 全局共享"""

    # LiteLLM 专用供应商
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)
    volcengine_coding_plan: ProviderConfig = Field(default_factory=ProviderConfig)
    byteplus: ProviderConfig = Field(default_factory=ProviderConfig)
    byteplus_coding_plan: ProviderConfig = Field(default_factory=ProviderConfig)
    openai_codex: ProviderConfig = Field(default_factory=ProviderConfig)
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig)
    custom: ProviderConfig = Field(default_factory=ProviderConfig)
    azure_openai: ProviderConfig = Field(default_factory=ProviderConfig)

    # ⭐ Claude SDK 兼容供应商（与 LiteLLM 共享）
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)

    # ⭐ Claude SDK 专用供应商（仅支持 Claude SDK Agent）
    aliyun_codingplan: ProviderConfig = Field(
        default_factory=ProviderConfig,
        description="阿里云 Coding Plan (仅 Claude SDK Agent)"
    )
    alrun: ProviderConfig = Field(
        default_factory=ProviderConfig,
        description="Alrun API 网关 (仅 Claude SDK Agent)"
    )


# ============================================
# 2. 新增 Claude SDK 特有配置（不含凭证）
# ============================================

class ClaudeSDKAgentConfig(Base):
    """Claude SDK Agent 特有配置

    注意: 供应商凭证(api_key/api_base)从全局 providers 读取
    """

    # Agent 行为配置
    max_turns: int = 40
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] = "acceptEdits"

    # Subagent 配置
    agents: dict[str, "AgentDefinition"] | None = None

    # Hooks 配置
    hooks: dict[str, list] | None = None


class AgentDefinition(Base):
    """Subagent 定义"""
    description: str
    prompt: str
    tools: list[str] | None = None
    model: Literal["sonnet", "opus", "haiku", "inherit"] = "inherit"


# ============================================
# 3. 扩展 AgentsConfig - 新增 type 字段
# ============================================

class AgentsConfig(Base):
    """Agent 总配置"""

    # ⭐ 新增：Agent 类型切换（唯一需要修改的字段）
    type: Literal["litellm", "claude_sdk"] = "litellm"

    # 现有字段保留（共享模型配置）
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)

    # 新增：Claude SDK 特有配置
    claude_sdk: ClaudeSDKAgentConfig = Field(default_factory=ClaudeSDKAgentConfig)
```

### 5.3 配置文件示例

#### 示例 1: 使用 LiteLLM Agent + DeepSeek

```json
{
  "agents": {
    "type": "litellm",
    "defaults": {
      "model": "deepseek-chat",
      "provider": "deepseek",
      "workspace": "~/.nanobot/workspace"
    }
  },
  "providers": {
    "deepseek": {
      "api_key": "sk-deepseek-xxx"
    }
  }
}
```

#### 示例 2: 使用 Claude SDK Agent + Anthropic

```json
{
  "agents": {
    "type": "claude_sdk",
    "defaults": {
      "model": "claude-sonnet-4-20250514",
      "provider": "anthropic",
      "workspace": "~/.nanobot/workspace"
    },
    "claude_sdk": {
      "max_turns": 40,
      "permission_mode": "acceptEdits"
    }
  },
  "providers": {
    "anthropic": {
      "api_key": "sk-ant-xxx"
    }
  }
}
```

#### 示例 3: 使用 Claude SDK Agent + 阿里云 Coding Plan（指定 api_base）

```json
{
  "agents": {
    "type": "claude_sdk",
    "defaults": {
      "model": "qwen3.5-plus",
      "provider": "aliyun-codingplan",
      "workspace": "~/.nanobot/workspace"
    },
    "claude_sdk": {
      "max_turns": 40,
      "permission_mode": "acceptEdits",
      "agents": {
        "code_reviewer": {
          "description": "代码审查专家",
          "prompt": "你是一个代码审查专家...",
          "model": "sonnet"
        }
      }
    }
  },
  "providers": {
    "aliyun-codingplan": {
      "api_key": "sk-aliyun-xxx",
      "api_base": "https://coding.dashscope.aliyuncs.com/apps/anthropic"
    }
  }
}
```

#### 示例 4: 不指定 api_base（使用注册表默认值）

```json
{
  "agents": {
    "type": "claude_sdk",
    "defaults": {
      "model": "qwen3.5-plus",
      "provider": "aliyun-codingplan"
    }
  },
  "providers": {
    "aliyun-codingplan": {
      "api_key": "sk-aliyun-xxx"
      // 不指定 api_base，使用注册表中的默认值
    }
  }
}
```

#### 示例 5: 配置错误 - SDK 不支持的供应商

```json
{
  "agents": {
    "type": "claude_sdk",
    "defaults": {
      "model": "deepseek-chat",
      "provider": "deepseek"  // ❌ 错误：deepseek 不支持 Claude SDK
    }
  },
  "providers": {
    "deepseek": {
      "api_key": "sk-deepseek-xxx"
    }
  }
}

// 启动时会报错：
// ConfigurationError: Provider 'deepseek' is not compatible with Claude SDK Agent.
// Compatible providers: anthropic, aliyun-codingplan, alrun
```

#### 示例 6: 多供应商配置（同时配置多个）

```json
{
  "agents": {
    "type": "claude_sdk",
    "defaults": {
      "model": "claude-sonnet-4-20250514",
      "provider": "anthropic"
    }
  },
  "providers": {
    "anthropic": {
      "api_key": "sk-ant-xxx"
    },
    "aliyun-codingplan": {
      "api_key": "sk-aliyun-xxx",
      "api_base": "https://coding.dashscope.aliyuncs.com/apps/anthropic"
    },
    "deepseek": {
      "api_key": "sk-deepseek-xxx"
    }
  }
}
// 切换供应商只需修改 agents.defaults.provider
```

### 5.4 配置字段职责划分

| 配置项 | 位置 | 说明 | 两种 Agent 共享 |
|--------|------|------|----------------|
| `providers.*.api_key` | 全局 | 供应商凭证 | ✅ 共享 |
| `providers.*.api_base` | 全局 | 供应商端点 | ✅ 共享 |
| `agents.defaults.model` | 全局 | 模型名称 | ✅ 共享 |
| `agents.defaults.provider` | 全局 | 供应商名称 | ✅ 共享 |
| `agents.defaults.workspace` | 全局 | 工作目录 | ✅ 共享 |
| `agents.defaults.max_tokens` | 全局 | 最大 tokens | ✅ 共享 |
| `agents.defaults.temperature` | 全局 | 温度参数 | LiteLLM 专用 |
| `agents.defaults.max_tool_iterations` | 全局 | 最大迭代 | LiteLLM 专用 |
| `agents.defaults.reasoning_effort` | 全局 | 思考模式 | LiteLLM 专用 |
| `agents.type` | Agent | Agent 类型 | 切换开关 |
| `agents.claude_sdk.max_turns` | Agent | 最大迭代 | SDK 专用 |
| `agents.claude_sdk.permission_mode` | Agent | 权限模式 | SDK 专用 |
| `agents.claude_sdk.agents` | Agent | Subagent | SDK 专用 |
| `agents.claude_sdk.hooks` | Agent | Hooks | SDK 专用 |

### 5.5 配置校验逻辑

```python
# nanobot/config/validator.py

from nanobot.config.provider_registry import (
    get_provider_spec,
    is_provider_sdk_compatible,
    get_sdk_compatible_providers,
)
from nanobot.config.schema import Config

class ConfigurationError(Exception):
    """配置错误"""
    pass

def validate_config(config: Config) -> None:
    """校验配置的合法性"""

    agent_type = config.agents.type
    provider_name = config.agents.defaults.provider

    # 1. 检查供应商是否存在
    spec = get_provider_spec(provider_name)
    if not spec:
        raise ConfigurationError(
            f"Unknown provider: '{provider_name}'. "
            f"Available providers: {', '.join(get_sdk_compatible_providers())}"
        )

    # 2. 检查供应商与 Agent 类型的兼容性
    if agent_type == "claude_sdk" and not spec.supported_by_sdk:
        raise ConfigurationError(
            f"Provider '{provider_name}' is not compatible with Claude SDK Agent. "
            f"Compatible providers: {', '.join(get_sdk_compatible_providers())}"
        )

    # 3. 检查 api_key 是否配置
    provider_attr = provider_name.replace("-", "_")
    provider_config = getattr(config.providers, provider_attr, None)

    if not provider_config or not provider_config.api_key:
        raise ConfigurationError(
            f"API key not configured for provider '{provider_name}'. "
            f"Please set providers.{provider_name}.api_key in config.json"
        )
```

### 5.6 Backend 读取供应商配置逻辑

```python
# nanobot/agent/backends/claude_sdk_backend.py

from nanobot.config.provider_registry import get_provider_spec

class ClaudeSDKBackend(AgentBackend):

    async def initialize(self, config: AgentsConfig, shared_resources: dict) -> None:
        # 校验供应商兼容性
        provider_name = shared_resources["config"].agents.defaults.provider
        spec = get_provider_spec(provider_name)

        if not spec:
            raise ValueError(f"Unknown provider: {provider_name}")

        if not spec.supported_by_sdk:
            raise ValueError(
                f"Provider '{provider_name}' is not compatible with Claude SDK Agent. "
                f"Compatible providers: anthropic, aliyun-codingplan, alrun"
            )

        # 继续初始化...
        self.sdk_config = config.claude_sdk
        self._shared_resources = shared_resources

    def _get_provider_config(self) -> tuple[str, str]:
        """从全局 providers 读取凭证"""
        config = self._shared_resources["config"]
        provider_name = config.agents.defaults.provider

        # 1. 从注册表获取供应商规格
        spec = get_provider_spec(provider_name)
        if not spec:
            raise ValueError(f"Unknown provider: {provider_name}")

        # 2. 规范化供应商名称
        provider_attr = provider_name.replace("-", "_")

        # 3. 从全局 providers 读取配置
        provider_config = getattr(config.providers, provider_attr, None)

        # 4. 获取 api_key（必须配置）
        if not provider_config or not provider_config.api_key:
            raise ValueError(
                f"API key not configured for provider '{provider_name}'. "
                f"Please set providers.{provider_name}.api_key"
            )
        api_key = provider_config.api_key

        # 5. 获取 base_url（优先配置值，否则使用注册表默认值）
        if provider_config.api_base:
            base_url = provider_config.api_base
        else:
            base_url = spec.default_base_url

        return api_key, base_url

    def _get_default_url(self, provider: str) -> str:
        """供应商默认 URL"""
        URL_MAP = {
            "anthropic": "https://api.anthropic.com",
            "aliyun-codingplan": "https://coding.dashscope.aliyuncs.com/apps/anthropic",
            "alrun": "https://llm.alrun.cn/v1/messages",
        }
        return URL_MAP.get(provider, "")
```

### 5.7 模型名称处理

不同供应商对模型名称的处理：

```python
def _get_model_name(self) -> str:
    """获取实际模型名称"""
    model = self._shared_resources["config"].agents.defaults.model
    provider = self._shared_resources["config"].agents.defaults.provider

    # Alrun 需要去掉前缀
    if provider == "alrun" and model.startswith("alrun-"):
        return model[len("alrun-"):]

    # 其他情况直接返回
    return model
```

---

## 六、对现有代码的侵入性分析

### 6.1 侵入性评估表

| 模块 | 文件 | 侵入级别 | 修改内容 |
|------|------|---------|---------|
| **配置** | `config/schema.py` | 🟡 低 | 新增 `ClaudeSDKAgentConfig` 类、`AgentsConfig.type` 字段、`ProvidersConfig` 新增供应商 |
| **供应商注册表** | `config/provider_registry.py` | 🆕 新增 | 新文件，定义供应商规格和兼容性 |
| **配置校验** | `config/validator.py` | 🆕 新增 | 新文件，校验配置合法性 |
| **AgentLoop** | `agent/loop.py` | 🟢 无 | 零修改，由 `LiteLLMBackend` 封装 |
| **ContextBuilder** | `agent/context.py` | 🟢 无 | 零修改，LiteLLM Backend 继续使用 |
| **MemoryStore** | `agent/memory.py` | 🟢 无 | 零修改，共享资源 |
| **SkillsLoader** | `agent/skills.py` | 🟢 无 | 零修改，`SkillToMCPConverter` 调用它 |
| **ToolRegistry** | `agent/tools/registry.py` | 🟢 无 | 零修改，`ToolAdapter` 调用它 |
| **工具实现** | `agent/tools/*.py` | 🟢 无 | 零修改，由 `ToolAdapter` 封装 |
| **Providers** | `providers/*.py` | 🟢 无 | 零修改，LiteLLM Backend 继续使用 |
| **Channels** | `channels/*.py` | 🟢 无 | 零修改，完全透明 |
| **MessageBus** | `bus/*.py` | 🟢 无 | 零修改，共享资源 |
| **SessionManager** | `session/manager.py` | 🟢 无 | 零修改，共享资源 |

### 6.2 新增文件清单

```
nanobot/
├── config/
│   ├── provider_registry.py   # 新增：供应商注册表
│   └── validator.py           # 新增：配置校验
│
└── agent/
    ├── protocol.py            # 新增：Agent 协议定义
    ├── router.py              # 新增：Agent 路由器
    ├── skill_to_mcp.py        # 新增：Skills → MCP 转换器
    ├── tool_adapter.py        # 新增：工具适配器
    │
    └── backends/              # 新增：后端实现目录
        ├── __init__.py
        ├── litellm_backend.py     # 新增：LiteLLM 封装
        └── claude_sdk_backend.py  # 新增：Claude SDK 实现
```

### 6.3 配置兼容性

**旧配置完全兼容**：

```json
// 旧配置（无 type 字段）- 继续工作
{
  "agents": {
    "defaults": {
      "model": "deepseek-chat",
      "workspace": "~/.nanobot/workspace"
    }
  },
  "providers": {
    "deepseek": { "api_key": "xxx" }
  }
}

// 默认行为：type = "litellm"，使用原有 AgentLoop
```

### 6.4 核心原则

1. **零修改原则**: 现有 `AgentLoop`、`ToolRegistry`、`MemoryStore` 等核心模块不做任何修改
2. **封装原则**: 新增 `LiteLLMBackend` 封装现有代码，而非修改它
3. **共享原则**: Memory、Session、Provider 配置作为共享资源，两种 Backend 都可使用
4. **配置驱动**: 所有行为差异通过配置控制，不硬编码
5. **注册表驱动**: 供应商信息统一管理，便于扩展

### 6.5 调用链变化

**改造前**:
```
Gateway → AgentLoop → Provider → LLM
                ↓
          ToolRegistry
                ↓
             Tools
```

**改造后**:
```
Gateway → AgentRouter → LiteLLMBackend → AgentLoop → Provider → LLM
              │                ↓
              │          ToolRegistry
              │                ↓
              │             Tools
              │
              └──→ ClaudeSDKBackend → ClaudeSDKClient → LLM
                         ↓
                   ProviderRegistry (校验兼容性)
                   SkillToMCPConverter
                   ToolAdapter
                         ↓
                      MCP Tools
```

---

## 七、文件结构变化

### 7.1 改造后的目录结构

```
nanobot/
├── config/
│   ├── schema.py              # 低侵入：新增类，保留现有
│   ├── provider_registry.py   # 新增
│   ├── validator.py           # 新增
│   ├── loader.py              # 无修改
│   └── paths.py               # 无修改
│
├── agent/
│   ├── __init__.py
│   ├── protocol.py            # 新增
│   ├── router.py              # 新增
│   ├── skill_to_mcp.py        # 新增
│   ├── tool_adapter.py        # 新增
│   │
│   ├── backends/              # 新增目录
│   │   ├── __init__.py
│   │   ├── litellm_backend.py
│   │   └── claude_sdk_backend.py
│   │
│   ├── loop.py                # 无修改
│   ├── context.py             # 无修改
│   ├── memory.py              # 无修改
│   ├── skills.py              # 无修改
│   ├── subagent.py            # 无修改
│   │
│   └── tools/                 # 无修改
│       ├── base.py
│       ├── registry.py
│       ├── filesystem.py
│       ├── shell.py
│       ├── web.py
│       ├── message.py
│       ├── cron.py
│       ├── spawn.py
│       └── mcp.py
│
├── providers/                 # 无修改
├── channels/                  # 无修改
├── bus/                       # 无修改
├── session/                   # 无修改
└── ...
```

---

## 八、实施计划

### Phase 1: 基础框架 (1-2天)

| 任务 | 说明 |
|------|------|
| 新增 `provider_registry.py` | 定义供应商注册表 |
| 新增 `validator.py` | 配置校验逻辑 |
| 新增 `protocol.py` | 定义 `AgentBackend` 抽象接口 |
| 新增 `router.py` | 实现路由逻辑 |
| 新增 `backends/` 目录 | 创建目录结构 |
| 新增 `litellm_backend.py` | 封装现有 `AgentLoop` |

### Phase 2: Claude SDK Backend (2-3天)

| 任务 | 说明 |
|------|------|
| 新增 `claude_sdk_backend.py` | SDK 核心封装 |
| 新增 `skill_to_mcp.py` | Skills 转换器 |
| 新增 `tool_adapter.py` | 工具适配器 |
| 实现供应商校验 | 限制 SDK 只能用兼容供应商 |

### Phase 3: 配置扩展 (1天)

| 任务 | 说明 |
|------|------|
| 扩展 `schema.py` | 新增 `ClaudeSDKAgentConfig`、供应商字段 |
| 更新配置加载 | 支持新配置格式 |
| 配置验证 | 启动时校验供应商兼容性 |

### Phase 4: 集成测试 (1-2天)

| 任务 | 说明 |
|------|------|
| Gateway 集成 | 替换 AgentLoop 为 AgentRouter |
| 端到端测试 | 两种 Backend 都测试 |
| Channel 兼容测试 | 确保各 Channel 正常 |
| 供应商兼容性测试 | 测试 Anthropic、Aliyun Coding Plan、Alrun |

### Phase 5: 文档与清理 (1天)

| 任务 | 说明 |
|------|------|
| 更新 README | 说明新配置方式 |
| 更新配置示例 | 添加两种模式示例 |
| 代码清理 | 移除调试代码 |

---

## 九、风险与缓解

### 9.1 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| SDK 与 Aliyun 不兼容 | 高 | 参考 xbot-claw 实现，已验证可行 |
| Skills 转换丢失信息 | 中 | 支持咨询型工具保留叙述内容 |
| 性能差异 | 低 | 两种 Backend 独立运行，互不影响 |
| 供应商配置错误 | 中 | 启动时校验，明确错误提示 |

### 9.2 兼容性风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 旧配置不兼容 | 低 | 默认 `type: litellm`，旧配置继续工作 |
| 工具行为差异 | 中 | 详细测试，必要时保留自定义工具 |

---

## 十、总结

### 10.1 改造收益

1. **灵活性**: 支持两种 Agent 后端，只需修改 `agents.type` 即可切换
2. **配置共享**: 供应商凭证和模型配置全局共享，避免重复配置
3. **扩展性**: 可轻松添加新的 Backend 实现和供应商
4. **最小侵入**: 现有核心代码零修改
5. **供应商支持**: 新增 Aliyun Coding Plan、Alrun 等 Claude SDK 兼容供应商
6. **向后兼容**: 旧配置完全兼容，默认使用 LiteLLM Agent
7. **统一管理**: 供应商注册表统一管理供应商信息和兼容性

### 10.2 配置切换示例

**场景：从 LiteLLM 切换到 Claude SDK**

```json
// 修改前 - 使用 LiteLLM Agent
{
  "agents": {
    "type": "litellm",  // ← 改这里
    "defaults": {
      "model": "claude-sonnet-4-20250514",
      "provider": "anthropic"
    }
  },
  "providers": {
    "anthropic": { "api_key": "sk-ant-xxx" }
  }
}

// 修改后 - 使用 Claude SDK Agent
{
  "agents": {
    "type": "claude_sdk",  // ← 只改这里
    "defaults": {
      "model": "claude-sonnet-4-20250514",  // 不变
      "provider": "anthropic"                // 不变
    },
    "claude_sdk": {
      "max_turns": 40,                       // SDK 特有配置
      "permission_mode": "acceptEdits"
    }
  },
  "providers": {                             // 不变
    "anthropic": { "api_key": "sk-ant-xxx" }
  }
}
```

### 10.3 改造成本

| 项目 | 估算 |
|------|------|
| 新增代码行数 | ~1000-1200 行 |
| 修改现有代码 | ~80 行（schema.py 新增配置类和供应商） |
| 新增文件数 | 8 个 |
| 开发时间 | 5-7 天 |
| 测试时间 | 2-3 天 |

### 10.4 后续扩展

1. 支持更多 Claude SDK 兼容供应商（只需在注册表添加）
2. 实现 Backend 热切换（运行时动态切换）
3. 支持混合模式（前台 LiteLLM + 后台 Claude SDK）
4. 支持 CLI 命令快速切换：`nanobot config set agents.type claude_sdk`
