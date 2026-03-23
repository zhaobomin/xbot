# XBot Skills Loading Strategy Design

> 基于 Claude Code 官方 Skill 设计理念
> Date: 2026-03-23

## 背景

### 当前问题

- **Token 消耗过高**: Skills 占用 ~28K tokens (65% of ~42K total)
- **全量加载**: 所有 active skills 的 SKILL.md 全部加载到 context
- **无法扩展**: 随着技能数量增加，token 消耗线性增长

### Claude Code 官方 Skill 设计理念

Claude Code 的 Skill 系统采用**三级延迟加载**模式：

```
Level 1: Description 始终可见 (~50 tokens/skill)
    ↓ (Skill 被触发时)
Level 2: SKILL.md 完整内容加载
    ↓ (Skill 需要额外资源时)
Level 3: 支持文件通过 read_file 工具加载
```

**核心理念**：
1. **触发优先**: 让模型知道"有什么技能可用"比"技能详细内容"更重要
2. **按需加载**: 只有被使用的技能才需要完整内容
3. **工具化**: 将技能内容作为工具可访问的资源，而非预加载

## 设计目标

1. **大幅降低基础 token 消耗**: Skills 部分从 ~28K 降至 ~2-3K
2. **保持功能完整**: 被触发的技能能获得完整上下文
3. **兼容现有 SDK**: Claude SDK 没有 Skills API，我们自行控制
4. **渐进式加载**: Description → Full Content → Supporting Files

## 详细设计

### 1. System Prompt 结构调整

#### 当前结构 (问题)

```
[Identity]
[Bootstrap]
[Memory]
[Skills Summary]        ← ~8K tokens
[Active Skills Full]    ← ~28K tokens (问题所在)
```

#### 新结构 (优化后)

```
[Identity]
[Bootstrap]
[Memory]
[Skills Catalog]        ← ~2-3K tokens (仅 description)
```

### 2. Skills Catalog 格式

Skills Catalog 只包含每个技能的元信息，让模型能够判断是否需要触发：

```markdown
## Available Skills

The following skills are available. Use the `Skill` tool to invoke them.

### Available Skills

| Skill | Description |
|-------|-------------|
| weather | Get current weather and forecasts (no API key required). |
| cron | Schedule reminders and recurring tasks. |
| memory | Two-layer memory system with grep-based recall. |
| github | Interact with GitHub using the `gh` CLI. |
| ... | ... |

When a user request matches a skill's purpose, invoke the Skill tool with the skill name.
```

**Token 估算**:
- 每个 skill ~50-80 tokens
- 20 个 skills = ~1-1.5K tokens
- 相比当前 ~28K，节省 ~90%+

### 3. 新增 `load_skill_content` 工具

当 Skill 被触发时，通过工具加载完整内容：

```python
# 在 Claude SDK 中新增工具
{
    "name": "load_skill_content",
    "description": "Load the full content of a skill. Use this when you need detailed instructions from a skill.",
    "input_schema": {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "The name of the skill to load"
            }
        },
        "required": ["skill_name"]
    }
}
```

### 4. 加载流程

```
用户请求
    ↓
System Prompt 包含 Skills Catalog (轻量)
    ↓
模型判断需要哪个 Skill
    ↓
调用 load_skill_content("skill_name")
    ↓
工具返回 SKILL.md 完整内容
    ↓
模型获得详细指令，执行任务
```

### 5. 代码改动

#### 5.1 `context.py` - 修改 `build_system_prompt()`

```python
def build_system_prompt(self) -> str:
    """Build the system prompt with lightweight skills catalog."""
    parts = [
        self._build_identity(),
        self._build_bootstrap(),
        self._build_memory(),
        self._build_skills_catalog(),  # 新方法：只生成 catalog
    ]
    return "\n\n".join(parts)

def _build_skills_catalog(self) -> str:
    """Build lightweight skills catalog (descriptions only)."""
    skills = self.skills.list_available_skills()
    
    lines = ["## Available Skills\n"]
    lines.append("The following skills extend your capabilities. Use the `Skill` tool to invoke them.\n")
    
    for skill in skills:
        desc = skill.get("description", "No description")
        available = skill.get("available", True)
        status = "✓" if available else "✗"
        lines.append(f"| {status} {skill['name']} | {desc} |")
    
    lines.append("\nUse `load_skill_content` for detailed instructions when needed.")
    
    return "\n".join(lines)
```

#### 5.2 `skills.py` - 新增 `list_available_skills()` 和 `load_skill_content()`

```python
def list_available_skills(self) -> list[dict]:
    """List all available skills with descriptions only (lightweight)."""
    skills = []
    for skill_dir in self.skills_dirs:
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists():
            metadata = self._parse_skill_metadata(skill_file)
            skills.append({
                "name": metadata.get("name", skill_dir.name),
                "description": metadata.get("description", ""),
                "available": self._check_availability(metadata),
                "location": str(skill_dir),
            })
    return skills

def load_skill_content(self, skill_name: str) -> str:
    """Load full SKILL.md content for a specific skill.
    
    This is called when the skill is actually invoked.
    """
    skill_path = self._find_skill(skill_name)
    if not skill_path:
        raise ValueError(f"Skill '{skill_name}' not found")
    
    return skill_path.read_text(encoding="utf-8")
```

#### 5.3 `claude_sdk_backend.py` - 添加工具

```python
def _build_tools(self) -> list[dict]:
    """Build tools including skill loading."""
    tools = [
        # ... existing tools ...
        {
            "name": "load_skill_content",
            "description": "Load detailed instructions for a skill. Use when you've decided to use a skill and need its full guidance.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string"}
                },
                "required": ["skill_name"]
            }
        }
    ]
    return tools

async def _handle_tool_call(self, tool_name: str, arguments: dict) -> str:
    """Handle tool calls including skill loading."""
    if tool_name == "load_skill_content":
        return self._context_builder.skills.load_skill_content(
            arguments["skill_name"]
        )
    # ... existing handlers ...
```

### 6. Skill 触发机制

有两种触发方式：

#### 方式 A: 模型自动判断 (推荐)

Skills Catalog 中包含足够信息让模型判断：

```markdown
When the user asks about weather, forecasts, or temperature, use the "weather" skill.
When the user wants to schedule reminders or recurring tasks, use the "cron" skill.
```

#### 方式 B: 通过现有 Skill 工具

保持与 Claude Code 一致的 `Skill` 工具接口：

```json
{
    "name": "Skill",
    "description": "Execute a skill within the main conversation. When a skill matches the user's request, invoke it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "skill": {"type": "string", "description": "Skill name"},
            "args": {"type": "string", "description": "Optional arguments"}
        },
        "required": ["skill"]
    }
}
```

### 7. 支持文件加载 (Level 3)

SKILL.md 中引用的支持文件，通过 `read_file` 工具加载：

```markdown
## Supporting Files

- `skills/weather/weather_api.py` - API client implementation
- `skills/weather/templates/forecast.md` - Output template

Use the `read_file` tool to access these files when needed.
```

## Token 预算对比

| 组件 | 当前 | 优化后 | 节省 |
|------|------|--------|------|
| Skills Catalog | ~8K (summary) | ~2K | 6K |
| Active Skills Full | ~28K | 0 (延迟加载) | 28K |
| **基础 Total** | **~42K** | **~10K** | **~32K (76%)** |
| Skill Load (per use) | 0 | ~3-5K/skill | 按需 |

**效果**:
- 基础请求从 ~42K 降至 ~10K tokens
- 每次实际使用的技能额外消耗 ~3-5K
- 大多数请求只触发 0-1 个技能

## 实施计划

### Phase 1: 基础改造 (优先级: 高)

1. 修改 `SkillsLoader` 添加 `list_available_skills()` 方法
2. 修改 `build_system_prompt()` 使用轻量 Catalog
3. 添加 `load_skill_content` 工具
4. 测试基本功能

### Phase 2: 触发优化

1. 优化 SKILL.md 的 description 字段
2. 添加触发关键词提示
3. 调整工具描述提高触发准确率

### Phase 3: 支持文件

1. 定义 SKILL.md 中的文件引用格式
2. 在 Catalog 中标注有支持文件的技能
3. 实现文件加载提示

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 模型不自动触发 | 优化 description，添加触发示例 |
| 延迟加载影响响应速度 | 常用技能可预热加载 |
| 技能内容过长 | 单技能限制 10K tokens |
| 多技能触发 | 限制每次最多加载 3 个技能 |

## 与 Claude Code 一致性

| 特性 | Claude Code | XBot (设计后) |
|------|------------|---------------|
| Description 始终可见 | ✓ | ✓ |
| 按需加载完整内容 | ✓ | ✓ |
| 工具化访问 | ✓ (Skill tool) | ✓ (load_skill_content) |
| 支持文件延迟加载 | ✓ (read_file) | ✓ (read_file) |
| 元数据格式 | YAML frontmatter | YAML frontmatter |

## 总结

采用 Claude Code 官方的三级延迟加载模式：

1. **Level 1**: Skills Catalog 只包含 name + description (~50 tokens/skill)
2. **Level 2**: 通过 `load_skill_content` 工具加载完整 SKILL.md
3. **Level 3**: 通过 `read_file` 加载支持文件

预期收益：
- 基础 token 消耗降低 76%
- 保持完整功能
- 可扩展性强（添加新技能不增加基础开销）

---

## 实施结果 (2026-03-23)

### 实际 Token 节省效果

| 指标 | 旧方法 | 新方法 | 节省 |
|------|--------|--------|------|
| Skills 内容 tokens | 18,771 | 423 | **97.7%** |
| Skills 数量 | 20 | 20 | - |

### 实现的组件

1. **`LoadSkillContentTool`** (`xbot/agent/tools/skill_loader.py`)
   - 新增工具类，用于按需加载技能完整内容
   - 支持进度回调，在 CLI/Channel 显示加载状态

2. **`SkillsLoader.list_available_skills()`** (`xbot/agent/skills.py`)
   - 新增方法，返回轻量级技能列表
   - 只包含 name, description, available, requires

3. **`ContextBuilder._build_skills_catalog()`** (`xbot/agent/context.py`)
   - 修改 `build_system_prompt()` 使用轻量 Catalog
   - 不再预加载完整技能内容

4. **`ToolAdapter` 注册** (`xbot/agent/tool_adapter.py`)
   - 注册 `load_skill_content` 工具
   - 传入 skills_loader 和进度回调

5. **`_create_skill_progress_callback()`** (`xbot/agent/backends/claude_sdk_backend.py`)
   - 创建进度回调函数
   - 在 Channel 模式下发送加载通知

### 进度通知格式

当技能加载时，用户会在 CLI/Channel 看到：
- `📚 Loading skill: weather...`
- `✅ Skill loaded: weather`
- `❌ Skill not found: xxx`

### 后续优化建议

1. **触发优化**: 可以在 SKILL.md 的 description 中添加更多触发关键词
2. **常用技能预热**: 对 `memory` 等高频技能可以在启动时预加载
3. **缓存机制**: 同一 session 内缓存已加载的技能内容