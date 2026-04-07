# XBot Skills & Plugins 原生集成设计

> 基于 Claude Code SDK 原生机制设计
> Date: 2026-04-07
> Status: Draft

## 1. 概述

### 1.1 背景

XBot 当前自行实现了 Skills 加载机制，与 Claude Code SDK 的原生机制不一致。本设计旨在：

1. 对齐 Claude Code SDK 的 Skills/Plugins 加载方式
2. 简化 XBot 代码，删除冗余的加载逻辑（约 500 行）
3. 迁移到 `.claude/skills/` 标准目录结构

### 1.2 Claude Code 原生机制

#### Skills 加载

Claude Code CLI 自动扫描以下目录的 Skills：

| 位置 | 路径 | 自动加载 |
|------|------|----------|
| Personal | `~/.claude/skills/<skill-name>/SKILL.md` | ✅ 是 |
| Project | `<project>/.claude/skills/<skill-name>/SKILL.md` | ✅ 是 |
| Additional | `<add-dir>/.claude/skills/<skill-name>/SKILL.md` | ✅ 是（例外） |

**关键发现**：`--add-dir` 参数主要用于文件访问权限，但 **Skills 是例外**——`.claude/skills/` 子目录会被自动加载并支持热更新。

#### Plugins 加载

Plugins **不会自动扫描**，必须显式指定：

```bash
# CLI 方式
claude --plugin-dir /path/to/plugin

# SDK 方式
ClaudeAgentOptions(
    plugins=[{"type": "local", "path": "/path/to/plugin"}]
)
```

#### 三级延迟加载

```
Level 1: Description 始终可见（轻量，~50 tokens/skill）
    ↓ (Skill 被触发时)
Level 2: SKILL.md 完整内容加载
    ↓ (Skill 需要额外资源时)
Level 3: 支持文件通过 read_file 工具加载
```

#### Hot-Reload（热更新）

> Skills created or modified in `~/.claude/skills` or `.claude/skills` are immediately available without restarting the session.

**关键特性**：
- CLI 内部实现文件监听（file watcher）
- 自动检测 skills 目录的变化
- 新增、修改、删除 skill 都会实时反映
- **不需要重建 client 或重启 CLI 进程**

---

## 2. 架构设计

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           XBot Runtime                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                        OptionsBuilder                                 │  │
│  │                                                                       │  │
│  │  build() ──────────────────────────────────────────────────────────┐  │  │
│  │    │                                                               │  │  │
│  │    ├── _build_add_dirs() ─────────────────────────────────────────┐│  │  │
│  │    │     │                                                        ││  │  │
│  │    │     ├── workspace/           (CLI 扫描 .claude/skills/)      ││  │  │
│  │    │     ├── workspace/skills/    (兼容旧目录)                    ││  │  │
│  │    │     └── ~/.claude/skills/    (用户级 skills)                ││  │  │
│  │    │                                                              ││  │  │
│  │    └── _build_plugins() ─────────────────────────────────────────┐│  │  │
│  │          │                                                       ││  │  │
│  │          ├── workspace/plugins/superpowers/                      ││  │  │
│  │          └── workspace/plugins/my-plugin/                        ││  │  │
│  │                                                                   ││  │  │
│  └───────────────────────────────────────────────────────────────────┘│  │  │
│                                                                        │  │  │
│                                        ▼                               │  │  │
│  ┌───────────────────────────────────────────────────────────────────────┐│  │
│  │                     ClaudeAgentOptions                                ││  │
│  │                                                                       ││  │
│  │  add_dirs: ["/workspace", "/workspace/skills", ...]                  ││  │
│  │  plugins: [{"type": "local", "path": "/workspace/plugins/superpowers"}]│ │
│  └───────────────────────────────────────────────────────────────────────┘│  │
│                                                                           │  │
└───────────────────────────────────────────────────────────────────────────┘  │
                                                                                │
                                    ▼                                           │
┌─────────────────────────────────────────────────────────────────────────────┐ │
│                        Claude Code CLI (子进程)                              │ │
├─────────────────────────────────────────────────────────────────────────────┤ │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                     Skills Loading Pipeline                             ││
│  │                                                                         ││
│  │  1. 扫描 add_dirs 中的 .claude/skills/ 子目录                          ││
│  │  2. Level 1: 所有 skills 的 description 注入 context                   ││
│  │  3. 文件监听器启动 (Hot-Reload)                                        ││
│  │  4. Skill 被触发时 → Level 2: 加载完整 SKILL.md                        ││
│  │  5. 需要支持文件时 → Level 3: 通过 read_file 加载                      ││
│  │                                                                         ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                     Plugins Loading Pipeline                           ││
│  │                                                                         ││
│  │  1. 加载 plugin.json 配置                                              ││
│  │  2. 加载 skills/、hooks/、commands/                                    ││
│  │  3. Plugin skills 使用 namespace: "plugin-name:skill-name"             ││
│  │                                                                         ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘ │
```

### 2.2 目录结构对比

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     BEFORE: XBot 自定义结构                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  workspace/                                                                  │
│  ├── skills/                      # XBot 自定义位置                         │
│  │   └── my-skill/SKILL.md                                                 │
│  ├── .xbot/skills/               # XBot scoped 位置                         │
│  │   └── another-skill/SKILL.md                                             │
│  └── ~/.xbot/skills/             # XBot 个人位置                            │
│      └── personal-skill/SKILL.md                                            │
│                                                                              │
│  ❌ 与 Claude Code 标准不一致                                                │
│  ❌ 需要自己实现扫描和加载                                                   │
│  ❌ 每次请求重新扫描（效率低）                                               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

                              ═══════════════
                                 ▼ ▼ ▼ ▼
                              ═══════════════

┌─────────────────────────────────────────────────────────────────────────────┐
│                     AFTER: Claude Code 标准结构                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  workspace/                                                                  │
│  ├── .claude/                                                               │
│  │   └── skills/                 # ✅ SDK 标准位置（自动扫描）              │
│  │       ├── code-review/SKILL.md                                           │
│  │       └── deploy/SKILL.md                                                │
│  │                                                                          │
│  ├── skills/                     # 兼容旧目录（通过 add_dirs）              │
│  │   └── legacy-skill/SKILL.md                                             │
│  │                                                                          │
│  └── plugins/                                                                │
│      └── superpowers/                                                       │
│          ├── .claude-plugin/plugin.json                                     │
│          └── skills/brainstorming/SKILL.md                                  │
│                                                                              │
│  ~/.claude/skills/              # ✅ 用户级标准位置                          │
│      └── my-personal-skill/SKILL.md                                         │
│                                                                              │
│  ✅ 符合 Claude Code 标准                                                    │
│  ✅ CLI 自动扫描和加载                                                       │
│  ✅ 文件监听支持 Hot-Reload                                                  │
│  ✅ 三级延迟加载（节省 context）                                             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.3 数据流架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Skills Loading Flow                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Config (skills.dirs)                                                        │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ OptionsBuilder._build_add_dirs()                                    │    │
│  │                                                                      │    │
│  │  Input:                                                              │    │
│  │    - config.skills.dirs = ["$workspace/.claude/skills"]             │    │
│  │    - config.skills.additional_dirs = ["$workspace/skills"]          │    │
│  │                                                                      │    │
│  │  Output:                                                             │    │
│  │    add_dirs = [                                                      │    │
│  │      "/workspace",              # CLI 扫描 .claude/skills/          │    │
│  │      "/workspace/skills",       # 兼容旧目录                         │    │
│  │    ]                                                                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ ClaudeAgentOptions                                                   │    │
│  │                                                                      │    │
│  │  add_dirs: list[str]                                                │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Claude Code CLI (子进程)                                             │    │
│  │                                                                      │    │
│  │  1. 启动时扫描 add_dirs 中的 .claude/skills/                        │    │
│  │  2. 启动文件监听器                                                  │    │
│  │  3. 所有 skills 的 description 注入 context (Level 1)               │    │
│  │  4. Skill 触发时加载完整内容 (Level 2)                              │    │
│  │  5. 支持文件按需加载 (Level 3)                                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                        Plugins Loading Flow                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Config (plugins.dirs)                                                       │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ OptionsBuilder._build_plugins()                                      │    │
│  │                                                                      │    │
│  │  Input:                                                              │    │
│  │    - config.plugins.dirs = ["$workspace/plugins"]                   │    │
│  │    - config.plugins.enabled_plugins = ["superpowers"]              │    │
│  │                                                                      │    │
│  │  Process:                                                            │    │
│  │    1. 扫描 plugins/ 目录                                             │    │
│  │    2. 检查 .claude-plugin/plugin.json 存在                          │    │
│  │    3. 过滤 enabled/disabled 列表                                     │    │
│  │                                                                      │    │
│  │  Output:                                                             │    │
│  │    plugins = [                                                        │    │
│  │      {"type": "local", "path": "/workspace/plugins/superpowers"},   │    │
│  │    ]                                                                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ ClaudeAgentOptions                                                   │    │
│  │                                                                      │    │
│  │  plugins: list[dict]                                                │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Claude Code CLI (子进程)                                             │    │
│  │                                                                      │    │
│  │  1. 加载 plugin.json 配置                                           │    │
│  │  2. 加载 skills/、hooks/、commands/                                 │    │
│  │  3. Plugin skills 使用 namespace                                    │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Config Schema 设计

### 3.1 新增配置类

```python
# xbot/config/schema.py

@dataclass
class SkillsConfig(Base):
    """Skills 配置
    
    Skills 通过 SDK 的 add_dirs 参数加载。
    CLI 会自动扫描 .claude/skills/ 子目录。
    """
    enabled: bool = True
    dirs: list[str] = field(default_factory=lambda: ["$workspace/.claude/skills"])
    additional_dirs: list[str] = field(default_factory=list)


@dataclass
class PluginsConfig(Base):
    """Plugins 配置
    
    Plugins 需要显式指定，CLI 不会自动扫描。
    """
    enabled: bool = True
    dirs: list[str] = field(default_factory=lambda: ["$workspace/plugins"])
    enabled_plugins: list[str] = field(default_factory=list)
    disabled_plugins: list[str] = field(default_factory=list)


@dataclass
class XbotConfig(Base):
    """XBot 完整配置"""
    agents: AgentsConfig
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
```

### 3.2 配置示例

**基础配置**：
```json
{
  "skills": {
    "enabled": true,
    "dirs": ["$workspace/.claude/skills"],
    "additional_dirs": []
  },
  "plugins": {
    "enabled": true,
    "dirs": ["$workspace/plugins"]
  }
}
```

**加载 Superpowers**：
```json
{
  "plugins": {
    "dirs": ["$workspace/plugins"],
    "enabled_plugins": ["superpowers"]
  }
}
```

**多个 Skills 目录**：
```json
{
  "skills": {
    "dirs": ["$workspace/.claude/skills"],
    "additional_dirs": [
      "$workspace/skills",
      "$home/.claude/skills"
    ]
  }
}
```

**禁用特定 Plugin**：
```json
{
  "plugins": {
    "dirs": ["$workspace/plugins"],
    "disabled_plugins": ["experimental-plugin"]
  }
}
```

### 3.3 变量替换

| 变量 | 替换为 | 示例 |
|------|--------|------|
| `$workspace` | `agents.defaults.workspace` | `$workspace/skills` |
| `$home` | 用户主目录 | `$home/.claude/skills` |
| `$project` | 当前项目目录 | `$project/.claude/skills` |

---

## 4. OptionsBuilder 修改

### 4.1 新增方法

```python
# xbot/agent/backends/options_builder.py

class OptionsBuilder:
    def build(self, session_key: str | None = None) -> ClaudeAgentOptions:
        from claude_agent_sdk import ClaudeAgentOptions

        # ... 现有代码 ...

        return ClaudeAgentOptions(
            # ... 现有参数 ...
            
            # Skills: 通过 add_dirs 加载（CLI 自动扫描 .claude/skills/）
            add_dirs=self._build_add_dirs(),
            
            # Plugins: 显式加载
            plugins=self._build_plugins(),
        )

    def _build_add_dirs(self) -> list[str]:
        """构建 add_dirs 列表
        
        CLI 会自动扫描这些目录下的 .claude/skills/ 子目录。
        Skills 支持三级延迟加载和 Hot-Reload。
        """
        dirs = []
        config = self._shared_resources.get("config")
        
        if not config.skills.enabled:
            return []
        
        workspace = Path(config.agents.defaults.workspace)
        
        # 1. workspace 根目录（CLI 自动扫描 .claude/skills/）
        dirs.append(str(workspace))
        
        # 2. 额外的 skills 目录（非标准位置，如兼容旧目录）
        for dir_path in config.skills.additional_dirs:
            expanded = self._expand_path(dir_path)
            if Path(expanded).exists():
                dirs.append(expanded)
        
        return dirs

    def _build_plugins(self) -> list[dict]:
        """构建 plugins 列表
        
        扫描配置的插件目录，过滤启用的插件。
        """
        plugins = []
        config = self._shared_resources.get("config")
        
        if not config.plugins.enabled:
            return []
        
        for plugin_dir in config.plugins.dirs:
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
        enabled = config.plugins.enabled_plugins
        disabled = config.plugins.disabled_plugins
        
        # 如果有启用列表，只加载列表中的
        if enabled and name not in enabled:
            return False
        
        # 检查是否在禁用列表中
        if name in disabled:
            return False
        
        return True

    def _expand_path(self, path: str) -> str:
        """展开路径变量
        
        支持 $workspace, $home, $project 变量。
        """
        config = self._shared_resources.get("config")
        workspace = config.agents.defaults.workspace
        
        result = path
        result = result.replace("$workspace", workspace)
        result = result.replace("$home", str(Path.home()))
        result = result.replace("$project", os.getcwd())
        
        return result
```

---

## 5. 可删除的代码

### 5.1 完全删除

| 文件/组件 | 行数 | 原因 |
|-----------|------|------|
| `xbot/agent/capabilities/skill_to_mcp.py` | ~150 | SDK 原生不转换 Skills 为 MCP |
| `xbot/agent/capabilities/skill_parsing.py` | ~60 | Triggers 解析（非原生功能） |
| `SkillsLoader.get_skill_triggers()` | ~150 | Triggers 功能删除 |
| `SkillsLoader.get_triggered_skills()` | ~100 | Triggers 功能删除 |
| `SkillsLoader._parse_trigger_list()` | ~50 | Triggers 功能删除 |
| `SkillsLoader._check_trigger()` | ~80 | Triggers 功能删除 |
| `TriggerCondition` dataclass | ~20 | Triggers 功能删除 |
| `SkillTriggers` dataclass | ~15 | Triggers 功能删除 |

**总计删除**：约 625 行代码

### 5.2 可保留的代码（可选）

| 组件 | 保留原因 |
|------|----------|
| `SkillsLoader.list_skills()` | 可用于 UI 展示 Skills 列表 |
| `SkillsLoader.load_skill()` | 可用于外部读取 Skill 内容 |

### 5.3 代码删除详细说明

#### skill_to_mcp.py - 完全删除

```python
# 文件: xbot/agent/capabilities/skill_to_mcp.py
# 状态: 删除整个文件

# 原因:
# 1. SDK 原生不转换 Skills 为 MCP tools
# 2. Skills 通过 description 匹配 + 直接加载 SKILL.md 工作
# 3. 没有其他代码依赖此模块
```

#### skill_parsing.py - 删除 Triggers 部分

```python
# 文件: xbot/agent/capabilities/skill_parsing.py
# 状态: 删除整个文件

# 原因:
# 1. Triggers 是 XBot 特有功能，非 SDK 原生
# 2. SDK 通过 description 字段匹配触发
# 3. frontmatter 解析可由 CLI 内部处理
```

#### skills_loader.py - 删除 Triggers 相关代码

```python
# 文件: xbot/agent/capabilities/skills_loader.py
# 状态: 删除以下内容

# 删除:
# - class TriggerCondition
# - class SkillTriggers
# - def get_skill_triggers()
# - def invalidate_triggers_cache()
# - def _parse_trigger_list()
# - def get_triggered_skills()
# - def _check_trigger()
# - self._triggers_cache
# - self._triggers_cache_mtime
```

---

## 6. 迁移指南

### Phase 1: 添加新配置（1 天）

1. 添加 `SkillsConfig` 和 `PluginsConfig` 到 `xbot/config/schema.py`
2. 修改 `OptionsBuilder` 添加 `_build_add_dirs()` 和 `_build_plugins()`
3. 运行测试验证配置加载正确

### Phase 2: 验证（1 天）

1. 创建测试 Skills 到 `.claude/skills/`
2. 测试 Skills 是否正确加载
3. 测试 Plugins 是否正确加载
4. 测试 Hot-Reload 是否工作

### Phase 3: 清理代码（1 天）

1. 删除 `skill_to_mcp.py`
2. 删除 `skill_parsing.py`
3. 删除 `SkillsLoader` 中的 Triggers 相关代码
4. 更新 `ContextBuilder` 移除对旧 SkillsLoader 方法的调用
5. 运行完整测试

### Phase 4: 迁移目录（可选）

将现有 skills 迁移到标准位置：

```bash
# 迁移命令
mkdir -p workspace/.claude/skills
mv workspace/skills/* workspace/.claude/skills/

# 更新配置（兼容旧位置）
# config.json:
{
  "skills": {
    "additional_dirs": ["$workspace/skills"]
  }
}
```

---

## 7. 注意事项

### 7.1 Skills 优先级

当同名 Skill 存在于多个位置时，优先级为：

```
Enterprise > Personal (~/.claude/skills/) > Project (.claude/skills/) > Plugin skills
```

Plugin skills 使用命名空间 `plugin-name:skill-name`，不会与其他 Skill 冲突。

### 7.2 Hot-Reload 支持

**Skills**：
- ✅ 支持 Hot-Reload
- CLI 内置文件监听
- 修改 SKILL.md 后立即生效
- 不需要重建 client

**Plugins**：
- ❌ 不支持 Hot-Reload
- 需要重启会话才能更新

### 7.3 add_dirs 限制

`add_dirs` 参数主要用于文件访问权限，但 **Skills 是例外**：

| 配置 | 是否从 add_dirs 加载 |
|------|---------------------|
| `.claude/skills/` | ✅ 是（例外） |
| `.claude/agents/` | ❌ 否 |
| `.claude/commands/` | ❌ 否 |
| `.claude/hooks/` | ❌ 否 |
| `CLAUDE.md` | ❌ 否（需设置环境变量） |

---

## 8. 测试计划

### 8.1 单元测试

- `test_build_add_dirs()` - 验证目录列表构建正确
- `test_build_plugins()` - 验证插件列表构建正确
- `test_plugin_filtering()` - 验证 enabled/disabled 过滤
- `test_path_expansion()` - 验证变量替换

### 8.2 集成测试

- Skills 从 `.claude/skills/` 正确加载
- Skills 从 `add_dirs` 兼容目录加载
- Plugins 正确加载
- Plugin skills 使用正确命名空间

### 8.3 手动测试

- 修改 SKILL.md 后 Hot-Reload 生效
- 多个 Skills 目录优先级正确
- Plugin 启用/禁用正常工作

---

## 变更历史

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2026-04-07 | v1.0 | 初始设计文档 |