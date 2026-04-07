# Skills & Plugins 原生集成实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对齐 Claude Code SDK 原生的 Skills/Plugins 加载机制，删除 XBot 自定义的 Triggers 功能。

**Architecture:** 通过 OptionsBuilder 构建 `add_dirs` 和 `plugins` 参数传给 SDK，由 CLI 自动扫描 `.claude/skills/` 目录加载 Skills，显式指定路径加载 Plugins。删除 skill_to_mcp.py（SDK 不转换 Skills 为 MCP）、skill_parsing.py（Triggers 解析）和 skills_loader.py 中的 Triggers 相关代码。

**Tech Stack:** Python 3.10+, Pydantic, Claude Code SDK

---

## 文件结构映射

| 文件 | 操作 | 说明 |
|------|------|------|
| `xbot/config/schema.py` | 修改 | 添加 SkillsConfig、PluginsConfig |
| `xbot/agent/backends/options_builder.py` | 修改 | 添加 `_build_add_dirs()`、`_build_plugins()` |
| `xbot/agent/capabilities/skill_to_mcp.py` | 删除 | SDK 不转换 Skills 为 MCP |
| `xbot/agent/capabilities/skill_parsing.py` | 删除 | Triggers 解析（非原生功能） |
| `xbot/agent/capabilities/skills_loader.py` | 修改 | 删除 Triggers 相关代码 |
| `tests/unit/config/test_schema_skills.py` | 创建 | 测试 SkillsConfig/PluginsConfig |
| `tests/unit/backends/test_options_builder_skills.py` | 创建 | 测试 add_dirs/plugins 构建 |

---

## Task 1: 添加 SkillsConfig 和 PluginsConfig 配置类

**Files:**
- Modify: `xbot/config/schema.py:119`
- Create: `tests/unit/config/test_schema_skills.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/config/test_schema_skills.py
"""Tests for SkillsConfig and PluginsConfig."""

import pytest
from xbot.config.schema import SkillsConfig, PluginsConfig, Config


class TestSkillsConfig:
    """Test SkillsConfig validation."""

    def test_skills_config_defaults(self):
        """Test default values for SkillsConfig."""
        config = SkillsConfig()
        assert config.enabled is True
        assert config.dirs == ["$workspace/.claude/skills"]
        assert config.additional_dirs == []

    def test_skills_config_custom_dirs(self):
        """Test custom dirs configuration."""
        config = SkillsConfig(
            dirs=["/custom/skills"],
            additional_dirs=["$workspace/skills", "$home/.claude/skills"]
        )
        assert config.dirs == ["/custom/skills"]
        assert len(config.additional_dirs) == 2

    def test_skills_config_disabled(self):
        """Test disabled skills."""
        config = SkillsConfig(enabled=False)
        assert config.enabled is False


class TestPluginsConfig:
    """Test PluginsConfig validation."""

    def test_plugins_config_defaults(self):
        """Test default values for PluginsConfig."""
        config = PluginsConfig()
        assert config.enabled is True
        assert config.dirs == ["$workspace/plugins"]
        assert config.enabled_plugins == []
        assert config.disabled_plugins == []

    def test_plugins_config_filtering(self):
        """Test enabled/disabled plugin filtering."""
        config = PluginsConfig(
            enabled_plugins=["superpowers"],
            disabled_plugins=["experimental"]
        )
        assert "superpowers" in config.enabled_plugins
        assert "experimental" in config.disabled_plugins


class TestConfigIntegration:
    """Test Config includes skills and plugins."""

    def test_config_has_skills_and_plugins(self):
        """Test Config includes skills and plugins fields."""
        config = Config()
        assert hasattr(config, "skills")
        assert hasattr(config, "plugins")
        assert isinstance(config.skills, SkillsConfig)
        assert isinstance(config.plugins, PluginsConfig)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/config/test_schema_skills.py -v`
Expected: FAIL with "ImportError: cannot import name 'SkillsConfig'"

- [ ] **Step 3: Write minimal implementation**

在 `xbot/config/schema.py` 的 `AgentsConfig` 类之后（约 line 119）添加：

```python
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
```

并在 `Config` 类中添加字段：

```python
class Config(BaseSettings):
    """Root configuration for xbot."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)  # 新增
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)  # 新增
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/config/test_schema_skills.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add xbot/config/schema.py tests/unit/config/test_schema_skills.py
git commit -m "feat(config): add SkillsConfig and PluginsConfig for SDK native loading"
```

---

## Task 2: OptionsBuilder 添加 _build_add_dirs() 方法

**Files:**
- Modify: `xbot/agent/backends/options_builder.py:158`
- Create: `tests/unit/backends/test_options_builder_skills.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/backends/test_options_builder_skills.py
"""Tests for OptionsBuilder skills and plugins methods."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from xbot.agent.backends.options_builder import OptionsBuilder


class TestBuildAddDirs:
    """Test _build_add_dirs method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config = MagicMock()
        self.mock_config.skills.enabled = True
        self.mock_config.skills.dirs = ["$workspace/.claude/skills"]
        self.mock_config.skills.additional_dirs = []
        self.mock_config.agents.defaults.workspace = "/test/workspace"

        self.shared_resources = {"config": self.mock_config}

        self.builder = OptionsBuilder(
            shared_resources=self.shared_resources,
            sdk_config=MagicMock(),
            skill_converter=None,
            tool_adapter=None,
            sessions=None,
            context_builder=None,
            handoff_policy=None,
            capability_policy=None,
        )

    def test_build_add_dirs_returns_workspace(self):
        """Test that workspace root is included."""
        with patch.object(Path, "exists", return_value=True):
            dirs = self.builder._build_add_dirs()
            assert "/test/workspace" in dirs

    def test_build_add_dirs_includes_additional_dirs(self):
        """Test additional_dirs are included."""
        self.mock_config.skills.additional_dirs = ["$workspace/skills"]
        with patch.object(Path, "exists", return_value=True):
            dirs = self.builder._build_add_dirs()
            assert "/test/workspace/skills" in dirs

    def test_build_add_dirs_disabled_returns_empty(self):
        """Test disabled skills returns empty list."""
        self.mock_config.skills.enabled = False
        dirs = self.builder._build_add_dirs()
        assert dirs == []

    def test_build_add_dirs_skips_nonexistent(self):
        """Test nonexistent directories are skipped."""
        self.mock_config.skills.additional_dirs = ["$workspace/nonexistent"]
        with patch.object(Path, "exists", return_value=False):
            dirs = self.builder._build_add_dirs()
            assert "/test/workspace/nonexistent" not in dirs

    def test_expand_path_workspace_variable(self):
        """Test $workspace variable expansion."""
        result = self.builder._expand_path("$workspace/skills")
        assert result == "/test/workspace/skills"

    def test_expand_path_home_variable(self):
        """Test $home variable expansion."""
        with patch.object(Path, "home", return_value=Path("/home/user")):
            result = self.builder._expand_path("$home/.claude/skills")
            assert result == "/home/user/.claude/skills"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/backends/test_options_builder_skills.py -v`
Expected: FAIL with "AttributeError: 'OptionsBuilder' object has no attribute '_build_add_dirs'"

- [ ] **Step 3: Write minimal implementation**

在 `xbot/agent/backends/options_builder.py` 的 `OptionsBuilder` 类中添加方法（在 `_build_system_prompt` 方法之前）：

```python
def _build_add_dirs(self) -> list[str]:
    """构建 add_dirs 列表

    CLI 会自动扫描这些目录下的 .claude/skills/ 子目录。
    Skills 支持三级延迟加载和 Hot-Reload。
    """
    from pathlib import Path
    import os

    dirs = []
    config = self._shared_resources.get("config")

    if not config or not config.skills.enabled:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/backends/test_options_builder_skills.py::TestBuildAddDirs -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/backends/options_builder.py tests/unit/backends/test_options_builder_skills.py
git commit -m "feat(options): add _build_add_dirs for Skills directory loading"
```

---

## Task 3: OptionsBuilder 添加 _build_plugins() 方法

**Files:**
- Modify: `xbot/agent/backends/options_builder.py`
- Modify: `tests/unit/backends/test_options_builder_skills.py`

- [ ] **Step 1: Write the failing test**

在 `tests/unit/backends/test_options_builder_skills.py` 中添加：

```python
class TestBuildPlugins:
    """Test _build_plugins method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config = MagicMock()
        self.mock_config.plugins.enabled = True
        self.mock_config.plugins.dirs = ["$workspace/plugins"]
        self.mock_config.plugins.enabled_plugins = []
        self.mock_config.plugins.disabled_plugins = []
        self.mock_config.agents.defaults.workspace = "/test/workspace"

        self.shared_resources = {"config": self.mock_config}

        self.builder = OptionsBuilder(
            shared_resources=self.shared_resources,
            sdk_config=MagicMock(),
            skill_converter=None,
            tool_adapter=None,
            sessions=None,
            context_builder=None,
            handoff_policy=None,
            capability_policy=None,
        )

    def test_build_plugins_empty_when_no_plugins_dir(self):
        """Test empty list when plugins directory doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            plugins = self.builder._build_plugins()
            assert plugins == []

    def test_build_plugins_disabled_returns_empty(self):
        """Test disabled plugins returns empty list."""
        self.mock_config.plugins.enabled = False
        plugins = self.builder._build_plugins()
        assert plugins == []

    def test_is_valid_plugin_checks_plugin_json(self):
        """Test _is_valid_plugin checks for plugin.json."""
        with patch.object(Path, "exists") as mock_exists:
            mock_exists.side_effect = lambda p: str(p).endswith("plugin.json")
            result = self.builder._is_valid_plugin(Path("/test/plugins/superpowers"))
            assert result is True

    def test_should_load_plugin_enabled_list(self):
        """Test plugin filtering by enabled_plugins."""
        self.mock_config.plugins.enabled_plugins = ["superpowers"]
        assert self.builder._should_load_plugin("superpowers", self.mock_config) is True
        assert self.builder._should_load_plugin("other", self.mock_config) is False

    def test_should_load_plugin_disabled_list(self):
        """Test plugin filtering by disabled_plugins."""
        self.mock_config.plugins.disabled_plugins = ["experimental"]
        assert self.builder._should_load_plugin("experimental", self.mock_config) is False
        assert self.builder._should_load_plugin("superpowers", self.mock_config) is True

    def test_should_load_plugin_no_filtering(self):
        """Test plugin loading without filtering lists."""
        assert self.builder._should_load_plugin("any-plugin", self.mock_config) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/backends/test_options_builder_skills.py::TestBuildPlugins -v`
Expected: FAIL with "AttributeError: 'OptionsBuilder' object has no attribute '_build_plugins'"

- [ ] **Step 3: Write minimal implementation**

在 `xbot/agent/backends/options_builder.py` 中添加方法（在 `_build_add_dirs` 之后）：

```python
def _build_plugins(self) -> list[dict]:
    """构建 plugins 列表

    扫描配置的插件目录，过滤启用的插件。
    """
    from pathlib import Path

    plugins = []
    config = self._shared_resources.get("config")

    if not config or not config.plugins.enabled:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/backends/test_options_builder_skills.py::TestBuildPlugins -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/backends/options_builder.py tests/unit/backends/test_options_builder_skills.py
git commit -m "feat(options): add _build_plugins for Plugin loading"
```

---

## Task 4: OptionsBuilder.build() 添加 add_dirs 和 plugins 参数

**Files:**
- Modify: `xbot/agent/backends/options_builder.py:158`
- Modify: `tests/unit/backends/test_options_builder_skills.py`

- [ ] **Step 1: Write the failing test**

在 `tests/unit/backends/test_options_builder_skills.py` 中添加：

```python
class TestOptionsBuilderIntegration:
    """Test OptionsBuilder.build() includes add_dirs and plugins."""

    def test_build_includes_add_dirs(self):
        """Test that build() passes add_dirs to ClaudeAgentOptions."""
        mock_config = MagicMock()
        mock_config.skills.enabled = True
        mock_config.skills.dirs = ["$workspace/.claude/skills"]
        mock_config.skills.additional_dirs = []
        mock_config.plugins.enabled = False
        mock_config.agents.defaults.workspace = "/test/workspace"
        mock_config.agents.defaults.model = "claude-sonnet-4-5"

        shared_resources = {"config": mock_config}

        builder = OptionsBuilder(
            shared_resources=shared_resources,
            sdk_config=MagicMock(max_turns=40, permission_mode="acceptEdits", hooks=None, disallowed_tools=["WebFetch"]),
            skill_converter=None,
            tool_adapter=None,
            sessions=None,
            context_builder=None,
            handoff_policy=None,
            capability_policy=None,
        )

        with patch.object(Path, "exists", return_value=True):
            with patch("xbot.agent.backends.options_builder.ClaudeAgentOptions") as mock_opts:
                builder.build()
                call_kwargs = mock_opts.call_args[1]
                assert "add_dirs" in call_kwargs
                assert "/test/workspace" in call_kwargs["add_dirs"]

    def test_build_includes_plugins(self):
        """Test that build() passes plugins to ClaudeAgentOptions."""
        mock_config = MagicMock()
        mock_config.skills.enabled = False
        mock_config.plugins.enabled = True
        mock_config.plugins.dirs = ["$workspace/plugins"]
        mock_config.plugins.enabled_plugins = []
        mock_config.plugins.disabled_plugins = []
        mock_config.agents.defaults.workspace = "/test/workspace"
        mock_config.agents.defaults.model = "claude-sonnet-4-5"

        shared_resources = {"config": mock_config}

        builder = OptionsBuilder(
            shared_resources=shared_resources,
            sdk_config=MagicMock(max_turns=40, permission_mode="acceptEdits", hooks=None, disallowed_tools=["WebFetch"]),
            skill_converter=None,
            tool_adapter=None,
            sessions=None,
            context_builder=None,
            handoff_policy=None,
            capability_policy=None,
        )

        # Create a mock plugin directory structure
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "iterdir") as mock_iterdir:
                mock_plugin = MagicMock()
                mock_plugin.is_dir.return_value = True
                mock_plugin.name = "superpowers"
                mock_plugin.__str__ = lambda self: "/test/workspace/plugins/superpowers"
                mock_iterdir.return_value = [mock_plugin]

                with patch.object(builder, "_is_valid_plugin", return_value=True):
                    with patch("xbot.agent.backends.options_builder.ClaudeAgentOptions") as mock_opts:
                        builder.build()
                        call_kwargs = mock_opts.call_args[1]
                        assert "plugins" in call_kwargs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/backends/test_options_builder_skills.py::TestOptionsBuilderIntegration -v`
Expected: FAIL with "KeyError: 'add_dirs'" or AssertionError

- [ ] **Step 3: Write minimal implementation**

修改 `OptionsBuilder.build()` 方法（约 line 158），在 `ClaudeAgentOptions` 构造中添加参数：

```python
return ClaudeAgentOptions(
    cwd=self._shared_resources.get("workspace", defaults.workspace),
    model=model,
    max_turns=self._sdk_config.max_turns,
    permission_mode=self._sdk_config.permission_mode,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/backends/test_options_builder_skills.py::TestOptionsBuilderIntegration -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/backends/options_builder.py tests/unit/backends/test_options_builder_skills.py
git commit -m "feat(options): pass add_dirs and plugins to ClaudeAgentOptions"
```

---

## Task 5: 删除 skill_to_mcp.py

**Files:**
- Delete: `xbot/agent/capabilities/skill_to_mcp.py` (213 lines)
- Verify: No imports of this module

- [ ] **Step 1: Check for imports of skill_to_mcp**

Run: `grep -r "skill_to_mcp" xbot/ --include="*.py"`
Expected: Find only the file itself, no imports

- [ ] **Step 2: Delete the file**

```bash
git rm xbot/agent/capabilities/skill_to_mcp.py
```

- [ ] **Step 3: Run tests to verify nothing breaks**

Run: `pytest tests/ -v --ignore=tests/unit/agent/capabilities/`
Expected: PASS (no tests should import skill_to_mcp)

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: delete skill_to_mcp.py - SDK doesn't convert Skills to MCP"
```

---

## Task 6: 删除 skill_parsing.py

**Files:**
- Delete: `xbot/agent/capabilities/skill_parsing.py` (116 lines)
- Modify: `xbot/agent/capabilities/skills_loader.py:11` - Remove import

- [ ] **Step 1: Check for imports of skill_parsing**

Run: `grep -r "skill_parsing" xbot/ --include="*.py"`
Expected: Found in `skills_loader.py` line 11

- [ ] **Step 2: Remove import from skills_loader.py**

在 `xbot/agent/capabilities/skills_loader.py` 中删除 import：

```python
# 删除这行:
from xbot.agent.capabilities.skill_parsing import parse_skill_document, strip_frontmatter
```

并添加替代实现（内联简单的 frontmatter 解析）：

```python
import re

def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from markdown content."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return content

def _parse_frontmatter(content: str) -> dict | None:
    """Simple YAML frontmatter parser for basic key: value pairs."""
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    yaml_content = parts[1].strip()

    result = {}
    for line in yaml_content.split("\n"):
        if ":" in line and not line.strip().startswith("#"):
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            # Remove quotes
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            result[key] = value
    return result
```

- [ ] **Step 3: Update skills_loader.py to use internal functions**

替换 `parse_skill_document` 为 `_parse_frontmatter`：

```python
# 原来的代码:
parsed = parse_skill_document(content)
return parsed.frontmatter or None

# 改为:
return _parse_frontmatter(content)
```

- [ ] **Step 4: Delete the file**

```bash
git rm xbot/agent/capabilities/skill_parsing.py
```

- [ ] **Step 5: Run tests to verify nothing breaks**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add xbot/agent/capabilities/skills_loader.py
git rm xbot/agent/capabilities/skill_parsing.py
git commit -m "refactor: delete skill_parsing.py, inline simple frontmatter parsing"
```

---

## Task 7: 删除 SkillsLoader 中的 Triggers 相关代码

**Files:**
- Modify: `xbot/agent/capabilities/skills_loader.py`

- [ ] **Step 1: Identify code to delete**

需要删除以下内容（约 200 行）：

1. `TriggerCondition` dataclass (lines 20-31)
2. `SkillTriggers` dataclass (lines 34-43)
3. `__init__` 中的 cache 相关属性 (lines 61-63)
4. `get_skill_triggers()` 方法 (lines 318-373)
5. `invalidate_triggers_cache()` 方法 (lines 375-386)
6. `_parse_trigger_list()` 方法 (lines 388-410)
7. `_get_full_metadata()` 方法 (lines 413-424)
8. `_parse_yaml_simple()` 方法 (lines 426-505)
9. `_parse_yaml_value()` 方法 (lines 508-532)
10. `_parse_inline_list()` 方法 (lines 534-540)
11. `get_triggered_skills()` 方法 (lines 542-579)
12. `_check_trigger()` 方法 (lines 582-601)
13. `_match_patterns()` 方法 (lines 604-610)

- [ ] **Step 2: Delete the classes and methods**

删除上述列出的所有代码。

- [ ] **Step 3: Run tests to verify nothing breaks**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add xbot/agent/capabilities/skills_loader.py
git commit -m "refactor: remove Triggers functionality from SkillsLoader (non-SDK-native)"
```

---

## Task 8: 验证完整功能

**Files:**
- Test: Integration test for Skills/Plugins loading

- [ ] **Step 1: Run all unit tests**

Run: `pytest tests/unit/ -v`
Expected: PASS

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/integration/ -v`
Expected: PASS

- [ ] **Step 3: Manual verification - Skills loading**

创建测试 Skill 目录结构并验证加载：

```bash
mkdir -p /test/workspace/.claude/skills/test-skill
echo '---
name: test-skill
description: "Test skill for verification"
---
Test skill content' > /test/workspace/.claude/skills/test-skill/SKILL.md
```

- [ ] **Step 4: Manual verification - Plugins loading**

创建测试 Plugin 目录结构并验证加载：

```bash
mkdir -p /test/workspace/plugins/test-plugin/.claude-plugin
echo '{"name": "test-plugin", "version": "1.0"}' > /test/workspace/plugins/test-plugin/.claude-plugin/plugin.json
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: verify Skills & Plugins native integration complete"
```

---

## 变更历史

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2026-04-07 | v1.0 | 初始实现计划 |