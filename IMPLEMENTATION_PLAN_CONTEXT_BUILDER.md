# 恢复 ContextBuilder 到 System Prompt 链路 - 实施方案

## 一、背景分析

### 1.1 v0.3.35 版本的实现（主干稳定版本）

在 v0.3.35 版本中，系统存在完整的 ContextBuilder → System Prompt 链路：

**架构流程:**
```
AgentRuntime
  └─ OptionsBuilder (xbot/agent/backends/options_builder.py)
       ├─ 初始化时接收 ContextBuilder 实例
       ├─ _build_system_prompt() 方法
       │   └─ 调用 self._context_builder.build_system_prompt()
       └─ 构建 ClaudeAgentOptions.system_prompt
```

**关键代码位置（v0.3.35）:**

1. **OptionsBuilder._build_system_prompt()** (options_builder.py)
   ```python
   def _build_system_prompt(self) -> str:
       """Build the system prompt."""
       base_prompt = "你是 xbot,一个智能助手。"
       if self._context_builder is not None:
           base_prompt = self._context_builder.build_system_prompt()
       identity_section = self._build_runtime_identity_section()
       if identity_section:
           base_prompt = f"{base_prompt}\n\n{identity_section}"
       return base_prompt
   ```

2. **ContextBuilder.build_system_prompt()** (xbot/agent/context/builder.py)
   - 组装 identity + bootstrap files + memory + skills
   - 无条件加载 4 个 bootstrap 文件：AGENTS.md, SOUL.md, USER.md, TOOLS.md

3. **配置项**: v0.3.35 中**没有** `loadBootstrapFiles` 配置项

### 1.2 当前重构版本的问题（refactor/domain-restructure 分支）

经过域重构后，链路断裂：

**现状:**
- ✅ ContextBuilder 已迁移到新位置：`xbot/runtime/core/context/builder.py`
- ✅ AgentService 初始化时创建了 ContextBuilder 实例（service.py:218）
- ❌ **AgentService 中没有 _build_system_prompt() 方法**
- ❌ **ContextBuilder 没有被调用**来构建 system prompt
- ❌ **缺少配置项**：无法控制是否加载 bootstrap 文件

**影响:**
- AGENTS.md / SOUL.md / USER.md / TOOLS.md 不会被加载到 system prompt
- 用户自定义的 agent 行为配置失效
- 与 v0.3.35 行为不一致

---

## 二、实施方案

### 2.1 方案概述

在 AgentService 中恢复 `_build_system_prompt()` 方法，建立 ContextBuilder 到 system prompt 的完整链路，并新增配置项支持按需关闭 bootstrap 文件加载。

### 2.2 详细改动清单

#### 改动 1: 新增配置项 `loadBootstrapFiles`

**文件**: `xbot/platform/config/schema.py`

**位置**: `AgentDefaults` 类（约 line 46 之后）

**改动内容**:
```python
class AgentDefaults(Base):
    """Default agent configuration."""
    
    workspace: str = "~/.xbot/workspace"
    model: str = "claude-sonnet-4-5"
    provider: str = "auto"
    available_models: list[str] = Field(default_factory=list)
    max_tokens: int = 8192
    context_window_tokens: int = 65_536
    temperature: float = 0.1
    max_tool_iterations: int = 40
    memory_window: int | None = Field(default=None, exclude=True)
    reasoning_effort: str | None = None
    
    # ===== 新增配置项 =====
    load_bootstrap_files: bool = Field(
        default=True,
        description="Load AGENTS.md, SOUL.md, USER.md, TOOLS.md into system prompt"
    )
```

**说明**:
- 默认值为 `True`，保持与 v0.3.35 兼容
- 使用 snake_case 命名（Pydantic 会自动转换为 camelCase `loadBootstrapFiles`）
- 用户可在配置文件中设置为 `false` 关闭 bootstrap 文件加载

---

#### 改动 2: ContextBuilder 支持跳过 bootstrap 文件加载

**文件**: `xbot/runtime/core/context/builder.py`

**位置 1**: `__init__` 方法签名（line 26-32）

**改动内容**:
```python
def __init__(
    self,
    workspace: Path,
    use_reme: bool = True,
    llm_config: dict[str, Any] | None = None,
    enable_vector_search: bool = False,
    load_bootstrap_files: bool = True,  # ← 新增参数
):
    """Initialize context builder.
    
    Args:
        workspace: Workspace directory
        use_reme: Use ReMe memory backend if available
        llm_config: LLM configuration for memory summarization
        enable_vector_search: Enable vector-based memory search
        load_bootstrap_files: Load AGENTS.md, SOUL.md, USER.md, TOOLS.md
    """
    self.workspace = workspace
    self.commands = CommandsLoader(workspace)
    self._load_bootstrap_files_flag = load_bootstrap_files  # ← 保存标志
    
    # ... 后续代码保持不变
```

**位置 2**: `_load_bootstrap_files()` 方法（line 155-165）

**改动内容**:
```python
def _load_bootstrap_files(self) -> str:
    """Load all bootstrap files from workspace."""
    # ← 新增：检查标志
    if not self._load_bootstrap_files_flag:
        return ""
    
    parts = []
    for filename in self.BOOTSTRAP_FILES:
        file_path = self.workspace / filename
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            parts.append(f"## {filename}\n\n{content}")
    
    return "\n\n".join(parts) if parts else ""
```

**说明**:
- 当 `load_bootstrap_files=False` 时，直接返回空字符串
- 其他部分（identity、memory、skills）仍正常加载

---

#### 改动 3: AgentService 初始化时传递配置

**文件**: `xbot/runtime/core/service.py`

**位置**: ContextBuilder 初始化（line 218-223）

**改动内容**:
```python
# 获取配置
agents_defaults = getattr(runtime_config.agents, "defaults", None)
load_bootstrap = True
if agents_defaults is not None:
    load_bootstrap = getattr(agents_defaults, "load_bootstrap_files", True)

self._context_builder = ContextBuilder(
    workspace=workspace_path,
    use_reme=use_reme,
    llm_config=llm_config,
    enable_vector_search=enable_vector_search,
    load_bootstrap_files=load_bootstrap,  # ← 传递配置
)
```

**说明**:
- 从 `runtime_config.agents.defaults.load_bootstrap_files` 读取配置
- 如果配置不存在，默认为 `True`（保持向后兼容）

---

#### 改动 4: AgentService 新增 `_build_system_prompt()` 方法

**文件**: `xbot/runtime/core/service.py`

**位置**: 在类中添加新方法（建议放在 line 678 附近，与 memory 配置相关方法相邻）

**改动内容**:
```python
def _build_system_prompt(self, agent_config: AgentConfig | None = None) -> str:
    """Build the system prompt for the agent.
    
    This method restores the ContextBuilder → system prompt link that was
    present in v0.3.35 (OptionsBuilder._build_system_prompt).
    
    Args:
        agent_config: Optional agent config with explicit system_prompt.
                     If provided and non-empty, takes precedence.
    
    Returns:
        The complete system prompt string.
    """
    # 优先级 1: 使用显式 system_prompt（兼容旧行为）
    if agent_config and agent_config.system_prompt:
        logger.debug("Using explicit system_prompt from AgentConfig")
        return agent_config.system_prompt
    
    # 优先级 2: 使用 ContextBuilder 构建
    if self._context_builder is not None:
        base_prompt = self._context_builder.build_system_prompt()
        
        # 追加 runtime identity 信息
        identity_section = self._build_runtime_identity_section()
        if identity_section:
            base_prompt = f"{base_prompt}\n\n{identity_section}"
        
        logger.debug("Built system prompt via ContextBuilder (%d chars)", len(base_prompt))
        return base_prompt
    
    # 降级：默认 prompt
    logger.warning("ContextBuilder not available, using default system prompt")
    return "你是 xbot，一个智能助手。"

def _build_runtime_identity_section(self) -> str:
    """Build runtime identity section for system prompt.
    
    Includes model, provider, and backend information for transparency.
    """
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
```

**说明**:
- 参考 v0.3.35 的 `OptionsBuilder._build_system_prompt()` 实现
- 支持 `AgentConfig.system_prompt` 优先（不破坏旧行为）
- 包含 runtime identity 信息（model、provider 等）

---

#### 改动 5: 在适当位置调用 `_build_system_prompt()`

**文件**: `xbot/runtime/core/service.py`

**需要找到**: 调用 LLM 或构建 ClaudeAgentOptions 的位置

**可能的调用点**（需进一步确认）:
1. `_get_or_create_client()` 方法中
2. 构建 SDK options 的地方
3. `run()` 或 `execute()` 方法中

**改动示例**（假设在构建 options 时）:
```python
# 在某个构建 options 的方法中
system_prompt = self._build_system_prompt(agent_config)

options = ClaudeAgentOptions(
    # ... 其他参数
    system_prompt=system_prompt,
    # ...
)
```

---

## 三、行为说明

### 3.1 默认行为（load_bootstrap_files = true）

与 v0.3.35 完全一致：
```
System Prompt 组成:
├─ Identity（_get_identity）
│   ├─ 平台信息（macOS/Linux/Windows）
│   ├─ Runtime 环境
│   └─ 核心行为准则
├─ Bootstrap Files（_load_bootstrap_files）
│   ├─ AGENTS.md（如果存在）
│   ├─ SOUL.md（如果存在）
│   ├─ USER.md（如果存在）
│   └─ TOOLS.md（如果存在）
├─ Memory（memory.get_memory_context）
└─ Runtime Identity（_build_runtime_identity_section）
    ├─ Agent name
    ├─ Agent backend
    ├─ Configured model
    └─ Configured provider
```

### 3.2 关闭 Bootstrap 文件（load_bootstrap_files = false）

```
System Prompt 组成:
├─ Identity（_get_identity）
├─ Memory（memory.get_memory_context）
└─ Runtime Identity（_build_runtime_identity_section）
```

**不会加载**: AGENTS.md / SOUL.md / USER.md / TOOLS.md

**仍保留**: identity、memory、skills（如果有的话）、runtime identity

### 3.3 显式 system_prompt 优先

如果 `AgentConfig.system_prompt` 非空：
- **直接使用**显式 system_prompt
- **跳过** ContextBuilder 构建
- **保持**旧版本行为兼容

---

## 四、配置示例

### 4.1 默认配置（加载 bootstrap 文件）

```toml
[agents.defaults]
model = "claude-sonnet-4-5"
provider = "auto"
# loadBootstrapFiles 默认为 true，无需显式配置
```

### 4.2 关闭 bootstrap 文件加载

```toml
[agents.defaults]
model = "claude-sonnet-4-5"
provider = "auto"
loadBootstrapFiles = false  # 或 load_bootstrap_files = false
```

### 4.3 使用显式 system_prompt（最高优先级）

```toml
[agents.defaults]
model = "claude-sonnet-4-5"
system_prompt = "你是一个专业的代码助手。"  # 这会覆盖所有其他 system prompt 构建逻辑
```

---

## 五、测试策略

### 5.1 单元测试

1. **测试配置解析**
   - 验证 `load_bootstrap_files` 默认值为 `True`
   - 验证配置文件可以正确设置为 `False`
   - 验证 camelCase 和 snakeCase 都能正确解析

2. **测试 ContextBuilder**
   - `load_bootstrap_files=True` 时加载 4 个文件
   - `load_bootstrap_files=False` 时跳过文件加载
   - 文件不存在时不报错

3. **测试 AgentService._build_system_prompt()**
   - 有显式 system_prompt 时优先使用
   - 无显式 system_prompt 时使用 ContextBuilder
   - ContextBuilder 不可用时使用默认 prompt

### 5.2 集成测试

1. **端到端测试**
   - 创建包含 AGENTS.md 的 workspace
   - 启动 agent，验证 system prompt 包含 AGENTS.md 内容
   - 设置 `loadBootstrapFiles=false`，验证不包含

2. **回归测试**
   - 运行现有测试套件
   - 确保不破坏 v0.3.35 的行为

---

## 六、迁移路径

### 6.1 向后兼容性

- ✅ 默认 `load_bootstrap_files=True`，与 v0.3.35 行为一致
- ✅ 现有配置无需修改
- ✅ 显式 system_prompt 仍优先（不破坏旧行为）

### 6.2 用户迁移指南

**无需迁移**（默认行为兼容）

**可选优化**:
- 如果用户不需要 AGENTS.md 等文件，可设置 `loadBootstrapFiles=false` 减少 token 消耗
- 如果用户有自定义 system prompt，可继续使用 `system_prompt` 配置

---

## 七、风险评估

### 7.1 低风险

- ✅ 配置项有合理的默认值
- ✅ 不影响现有用户（向后兼容）
- ✅ 有降级路径（ContextBuilder 不可用时使用默认 prompt）

### 7.2 注意事项

- ⚠️ 需要确认 `_build_system_prompt()` 的调用点（需进一步分析代码）
- ⚠️ 需要测试与 Claude SDK 的集成是否正常工作
- ⚠️ 需要验证 system prompt 长度不超过模型限制

---

## 八、实施步骤

1. **第一步**: 添加配置项 `load_bootstrap_files` 到 schema.py
2. **第二步**: 修改 ContextBuilder 支持跳过 bootstrap 文件
3. **第三步**: AgentService 初始化时传递配置
4. **第四步**: 添加 `_build_system_prompt()` 和 `_build_runtime_identity_section()` 方法
5. **第五步**: 找到并修改调用点，连接 system prompt 链路
6. **第六步**: 编写单元测试
7. **第七步**: 运行集成测试验证
8. **第八步**: 文档更新（可选）

---

## 九、关键文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `xbot/platform/config/schema.py` | 新增字段 | AgentDefaults.load_bootstrap_files |
| `xbot/runtime/core/context/builder.py` | 修改 | 添加 load_bootstrap_files 参数和跳过逻辑 |
| `xbot/runtime/core/service.py` | 新增方法 | _build_system_prompt(), _build_runtime_identity_section() |
| `xbot/runtime/core/service.py` | 修改初始化 | 传递 load_bootstrap_files 配置 |
| `xbot/runtime/core/service.py` | 查找调用点 | 连接 system prompt 链路（需进一步分析） |

---

## 十、参考代码

### v0.3.35 关键代码片段

**OptionsBuilder._build_system_prompt()** (v0.3.35):
```python
def _build_system_prompt(self) -> str:
    """Build the system prompt."""
    base_prompt = "你是 xbot，一个智能助手。"
    if self._context_builder is not None:
        base_prompt = self._context_builder.build_system_prompt()
    identity_section = self._build_runtime_identity_section()
    if identity_section:
        base_prompt = f"{base_prompt}\n\n{identity_section}"
    return base_prompt
```

**ContextBuilder.build_system_prompt()** (v0.3.35 & 当前版本基本一致):
```python
def build_system_prompt(
    self,
    skill_names: list[str] | None = None,
    user_message: str = "",
    code_context: str = "",
    file_paths: list[str] | None = None,
) -> str:
    """Build the system prompt from identity, bootstrap files, memory, and skills."""
    parts = [self._get_identity()]
    
    bootstrap = self._load_bootstrap_files()
    if bootstrap:
        parts.append(bootstrap)
    
    memory = self.memory.get_memory_context()
    if memory:
        parts.append(f"# Memory\n\n{memory}")
    
    return "\n\n---\n\n".join(parts)
```

---

## 十一、待确认事项

1. **调用点确认**: 需要分析 AgentService 中在哪里构建 ClaudeAgentOptions 或调用 LLM
2. **AgentConfig 结构**: 确认 `AgentConfig.system_prompt` 字段是否存在
3. **Skills 加载**: 当前版本 ContextBuilder 中是否还有 skills 加载逻辑（v0.3.35 有 SkillsLoader）
4. **测试覆盖**: 确认现有测试用例是否需要更新

---

## 十二、总结

本方案通过 5 处关键改动，完整恢复了 v0.3.35 的 ContextBuilder → System Prompt 链路，同时新增了 `loadBootstrapFiles` 配置项提供灵活性。方案设计遵循向后兼容原则，默认行为与 v0.3.35 完全一致，不会影响现有用户。

**核心优势**:
- ✅ 恢复 v0.3.35 稳定行为
- ✅ 提供配置灵活性（可选择关闭 bootstrap 文件）
- ✅ 保持向后兼容（默认开启）
- ✅ 支持显式 system_prompt 优先（不破坏旧行为）
- ✅ 代码结构清晰，易于维护
