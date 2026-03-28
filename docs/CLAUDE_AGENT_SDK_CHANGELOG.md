# Claude Agent SDK Python 版本变更详解

本文档详细记录 Claude Agent SDK Python 的版本升级变化，帮助开发者了解新功能和 API 变更。

---

## v0.1.50 → v0.1.51 (2026-03-29)

### 📦 版本信息

| 项目 | v0.1.50 | v0.1.51 |
|------|---------|---------|
| **SDK 版本** | 0.1.50 | 0.1.51 |
| **Bundled CLI** | 2.1.81 | 2.1.85 |
| **Claude Code 兼容** | v2.0.50 | v2.0.51 |

---

### ✨ 新增功能

#### 1. Session 管理 API

**位置**: `_internal/session_mutations.py`

##### `fork_session()` - 分支会话

```python
def fork_session(
    session_id: str,
    directory: str | None = None,
    up_to_message_id: str | None = None,
    title: str | None = None,
) -> ForkSessionResult:
    """Fork a session into a new branch with fresh UUIDs.

    复制转录消息到新会话文件，重映射所有 UUID，保留 parentUuid 链。

    Args:
        session_id: 源会话 UUID
        directory: 项目目录路径（可选）
        up_to_message_id: 分支点消息 UUID（可选，省略则复制全部）
        title: 自定义标题（可选，默认为"原标题 (fork)"）

    Returns:
        ForkSessionResult: 包含新会话 UUID

    Raises:
        ValueError: session_id 或 up_to_message_id 无效
        FileNotFoundError: 源会话文件不存在
        ValueError: 会话无消息可分支

    Example:
        >>> # 完整分支
        >>> result = fork_session("550e8400-e29b-41d4-a716-446655440000")
        >>> print(result.session_id)

        >>> # 从特定消息点分支
        >>> result = fork_session(
        ...     "550e8400-e29b-41d4-a716-446655440000",
        ...     up_to_message_id="660e8400-e29b-41d4-a716-446655440001",
        ... )
    """
```

**新增数据类型**:
```python
@dataclass
class ForkSessionResult:
    """Result of a fork operation."""
    session_id: str  # 新分支会话的 UUID
```

##### `delete_session()` - 删除会话

```python
def delete_session(
    session_id: str,
    directory: str | None = None,
) -> None:
    """Delete a session by removing its JSONL file.

    硬删除会话文件。需要软删除可使用 tag_session(id, '__hidden')。

    Args:
        session_id: 要删除的会话 UUID
        directory: 项目目录路径（可选）

    Raises:
        ValueError: session_id 无效
        FileNotFoundError: 会话文件不存在

    Example:
        >>> delete_session("550e8400-e29b-41d4-a716-446655440000")
    """
```

---

#### 2. SystemPromptFile 支持

**位置**: `types.py`

```python
class SystemPromptFile(TypedDict):
    """System prompt file configuration."""
    type: Literal["file"]
    path: str
```

**使用方式**:
```python
# 之前只支持字符串或 preset
options = ClaudeAgentOptions(
    system_prompt="You are a helpful assistant"
)

# 现在支持从文件加载
options = ClaudeAgentOptions(
    system_prompt={
        "type": "file",
        "path": "/path/to/system_prompt.md"
    }
)
```

---

#### 3. Task Budget（任务预算）

**位置**: `types.py`

```python
class TaskBudget(TypedDict):
    """API-side task budget in tokens.

    设置后，模型会感知剩余 token 预算，合理分配工具使用并在接近限制时收尾。

    作为 `output_config.task_budget` 发送，需要 `task-budgets-2026-03-13` beta header。
    """
    total: int
```

**使用方式**:
```python
options = ClaudeAgentOptions(
    task_budget={"total": 50000}  # 50K tokens 预算
)
```

---

#### 4. AgentDefinition 扩展

**位置**: `types.py`

```python
class AgentDefinition(TypedDict):
    """Agent 定义配置"""

    # 新增字段
    disallowedTools: list[str] | None   # 禁用的工具列表
    initialPrompt: str | None           # 初始提示词
    maxTurns: int | None                # 最大回合数

    # 扩展字段
    model: str | None  # 现在支持完整 model ID，不仅是别名
```

**完整定义**:
```python
class AgentDefinition(TypedDict):
    name: str
    description: str
    prompt: str
    tools: list[str] | None = None
    disallowedTools: list[str] | None = None      # NEW
    model: str | None = None                       # EXTENDED
    skills: list[str] | None = None
    memory: Literal["user", "project", "local"] | None = None
    mcpServers: list[str | dict[str, Any]] | None = None
    initialPrompt: str | None = None               # NEW
    maxTurns: int | None = None                    # NEW
```

---

#### 5. Permission Mode 扩展

**位置**: `types.py`, `client.py`, `query.py`

```python
# v0.1.50
PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]

# v0.1.51
PermissionMode = Literal[
    "default",
    "acceptEdits",
    "plan",              # 仅计划模式（无工具执行）
    "bypassPermissions",
    "dontAsk"            # NEW: 允许所有工具不提示
]
```

**模式说明**:
| 模式 | 说明 |
|------|------|
| `default` | CLI 会提示危险工具 |
| `acceptEdits` | 自动接受文件编辑 |
| `plan` | 仅计划模式，不执行工具 |
| `bypassPermissions` | 允许所有工具（谨慎使用） |
| `dontAsk` | **新增** - 允许所有工具不提示 |

**使用方式**:
```python
# 类型安全的权限模式设置
await client.set_permission_mode("dontAsk")  # 现在有类型检查
```

---

### 🐛 Bug 修复

#### 1. Python 3.10 兼容性修复

**位置**: `types.py`, `__init__.py`

```python
# v0.1.50 - 直接使用 typing_extensions
from typing_extensions import NotRequired

# v0.1.51 - 根据 Python 版本选择
if sys.version_info >= (3, 11):
    from typing import NotRequired, TypedDict
else:
    # PEP 655: stdlib TypedDict on 3.10 doesn't process NotRequired correctly
    from typing_extensions import NotRequired, TypedDict
```

**问题**: Python 3.10 的 stdlib `TypedDict` 不处理 `NotRequired`，导致 `__required_keys__` 包含 NotRequired 字段。

---

#### 2. ResultMessage 字段缺失修复

**位置**: `types.py`

```python
@dataclass
class ResultMessage:
    # ... existing fields ...

    # NEW in v0.1.51
    model_usage: dict[str, Any] | None = None
    permission_denials: list[Any] | None = None
    errors: list[str] | None = None      # 修复：添加缺失的 errors 字段
    uuid: str | None = None
```

---

#### 3. AssistantMessage 字段扩展

**位置**: `types.py`

```python
@dataclass
class AssistantMessage:
    # ... existing fields ...

    # NEW in v0.1.51
    message_id: str | None = None
    stop_reason: str | None = None
    session_id: str | None = None
    uuid: str | None = None
```

---

#### 4. Async Generator 清理改进

**位置**: `query.py`

```python
# v0.1.50
class Query:
    def __init__(self):
        self._tg: anyio.abc.TaskGroup | None = None

# v0.1.51
class Query:
    def __init__(self):
        self._read_task: asyncio.Task[None] | None = None
        self._child_tasks: set[asyncio.Task[Any]] = set()

    def spawn_task(self, coro: Any) -> None:
        """Spawn a child task that will be cancelled on close()."""
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
        self._child_tasks.add(task)
        task.add_done_callback(self._child_tasks.discard)
```

**问题**: 修复跨任务 cancel scope 的 `RuntimeError`，改进任务生命周期管理。

---

#### 5. MCP Tool input_schema 转换修复

**位置**: `__init__.py`

**新增工具函数**:
```python
def _python_type_to_json_schema(py_type: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema dict."""
    if py_type is str:
        return {"type": "string"}
    if py_type is int:
        return {"type": "integer"}
    if py_type is float:
        return {"type": "number"}
    if py_type is bool:
        return {"type": "boolean"}

    origin = getattr(py_type, "__origin__", None)
    if origin is list:
        item_args = getattr(py_type, "__args__", None)
        if item_args:
            return {"type": "array", "items": _python_type_to_json_schema(item_args[0])}
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}

    if is_typeddict(py_type):
        return _typeddict_to_json_schema(py_type)

    return {"type": "string"}


def _typeddict_to_json_schema(td_class: type) -> dict[str, Any]:
    """Convert a TypedDict class to a JSON Schema dict."""
    hints = _get_type_hints(td_class, include_extras=False)
    properties: dict[str, Any] = {}
    for field_name, field_type in hints.items():
        properties[field_name] = _python_type_to_json_schema(field_type)

    required_keys = getattr(td_class, "__required_keys__", set(properties.keys()))
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(required_keys) if required_keys else [],
    }
```

**问题**: 修复 MCP 工具 schema 生成，将 `TypedDict` 正确转换为 JSON Schema。

---

#### 6. MCP 内容类型支持扩展

**位置**: `__init__.py`

```python
# v0.1.50
content: list[TextContent | ImageContent]

# v0.1.51
content: list[
    TextContent
    | ImageContent
    | AudioContent      # NEW
    | ResourceLink      # NEW
    | EmbeddedResource  # NEW
]
```

**处理逻辑**:
```python
for item in result.root.content:
    item_type = getattr(item, "type", None)
    if item_type == "text":
        content.append({"type": "text", "text": getattr(item, "text", "")})
    elif item_type == "image":
        content.append({
            "type": "image",
            "data": getattr(item, "data", ""),
            "mimeType": getattr(item, "mimeType", ""),
        })
    elif item_type == "resource_link":
        # NEW: 处理 resource_link
        parts = []
        name = getattr(item, "name", None)
        uri = getattr(item, "uri", None)
        desc = getattr(item, "description", None)
        if name: parts.append(name)
        if uri: parts.append(str(uri))
        if desc: parts.append(desc)
        content.append({"type": "text", "text": "\n".join(parts) if parts else "Resource link"})
    elif item_type == "resource":
        # NEW: 处理 embedded resource
        resource = getattr(item, "resource", None)
        if resource and hasattr(resource, "text"):
            content.append({"type": "text", "text": resource.text})
```

---

### 🔧 代码改进

#### 1. 客户端流式处理优化

**位置**: `client.py`

```python
# v0.1.50
if prompt is not None and isinstance(prompt, AsyncIterable) and self._query._tg:
    self._query._tg.start_soon(self._query.stream_input, prompt)

# v0.1.51
if prompt is not None and isinstance(prompt, AsyncIterable):
    self._query.spawn_task(self._query.stream_input(prompt))
```

**改进**: 简化条件检查，使用新的 `spawn_task` 方法统一管理任务生命周期。

---

#### 2. 权限模式类型安全

**位置**: `client.py`, `query.py`, `types.py`

```python
# v0.1.50
async def set_permission_mode(self, mode: str) -> None:
    """Change permission mode."""
    await self._send_control_request({"subtype": "set_permission_mode", "mode": mode})

# v0.1.51
async def set_permission_mode(self, mode: PermissionMode) -> None:
    """Change permission mode with type safety."""
    await self._send_control_request({
        "subtype": "set_permission_mode",
        "mode": mode  # 现在有类型检查
    })
```

---

#### 3. MCP 工具 Schema 预计算

**位置**: `__init__.py`

```python
# v0.1.50 - 每次 list_tools 都重新计算 schema
@server.list_tools()
async def list_tools() -> list[Tool]:
    tool_list = []
    for tool_def in tools:
        # 每次调用都转换 schema
        schema = convert_to_json_schema(tool_def.input_schema)
        tool_list.append(Tool(..., inputSchema=schema))
    return tool_list

# v0.1.51 - 创建时预计算，缓存结果
def create_sdk_mcp_server(...):
    # 预计算所有工具 schema
    cached_tool_list = [
        Tool(
            name=tool_def.name,
            description=tool_def.description,
            inputSchema=_build_schema(tool_def),  # 一次计算
            annotations=tool_def.annotations,
        )
        for tool_def in tools
    ]

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return cached_tool_list  # 直接返回缓存
```

---

#### 4. 内部工具改进

**位置**: `_internal/session_mutations.py`

新增辅助函数支持 fork 操作：

```python
def _find_session_file(
    session_id: str,
    directory: str | None,
) -> Path | None:
    """Find the path to a session's JSONL file."""

def _find_session_file_with_dir(
    session_id: str,
    directory: str | None,
) -> tuple[Path, Path] | None:
    """Find a session file and its containing project directory."""

def _parse_fork_transcript(
    content: bytes, session_id: str
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Parse JSONL content into transcript entries + content-replacement records."""
```

---

### 📋 变更统计

| 类别 | 变更数 | 文件 |
|------|--------|------|
| **新增功能** | 5 项 | session_mutations.py, types.py, client.py |
| **Bug 修复** | 6+ 项 | types.py, query.py, __init__.py |
| **类型改进** | 多处 | types.py, client.py, query.py |
| **性能优化** | 2 项 | __init__.py (schema 预计算), query.py (任务管理) |

**修改的文件**:
- `types.py` - 类型定义扩展
- `client.py` - 客户端 API 改进
- `query.py` - 查询处理优化
- `__init__.py` - MCP 工具支持
- `_internal/session_mutations.py` - Session 管理
- `_internal/query.py` - 内部查询实现

---

### 🚀 迁移指南

#### 升级到 v0.1.51

```bash
pip install --upgrade claude-agent-sdk==0.1.51
```

#### API 变更影响

1. **PermissionMode 现在需要精确匹配**:
   ```python
   # 之前可能工作（字符串）
   await client.set_permission_mode("dontAsk")

   # 现在有类型检查，IDE 会提示有效选项
   await client.set_permission_mode("dontAsk")  # ✅
   await client.set_permission_mode("invalid")  # ❌ 类型错误
   ```

2. **新增 Session 管理 API**:
   ```python
   from claude_agent_sdk import fork_session, delete_session

   # 分支会话
   result = fork_session(session_id, up_to_message_id=message_id)

   # 删除会话
   delete_session(session_id)
   ```

3. **Task Budget 使用**:
   ```python
   from claude_agent_sdk import query

   async with query(options={"task_budget": {"total": 50000}}) as q:
       # ...
   ```

---

### 📚 相关资源

- [GitHub Repository](https://github.com/anthropics/claude-agent-sdk)
- [PyPI Package](https://pypi.org/project/claude-agent-sdk/)
- [官方文档](https://docs.anthropic.com/en/agent-sdk)

---

*文档更新时间：2026-03-29*
