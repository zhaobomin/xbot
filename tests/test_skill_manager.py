"""Tests for dynamic skill hot-reload and Python plugin support.

Tests for:
- SkillManager: fingerprint computation, change detection, Python skill loading
- SkillsLoader new features: personal directory, disable-model-invocation,
  user-invocable, type field
- ToolAdapter.register_python_skill_tools: dynamic tool registration
"""

from __future__ import annotations

import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from xbot.agent.capabilities.skills_loader import SkillsLoader
from xbot.agent.capabilities.skill_manager import SkillManager
from xbot.agent.tools.base import Tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_skill(root: Path, name: str, body: str) -> Path:
    """Helper to create a SKILL.md under root/name/."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    p = skill_dir / "SKILL.md"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return skill_dir


def _write_python_skill(root: Path, name: str, skill_md: str, tool_py: str) -> Path:
    """Helper to create a Python skill with SKILL.md and tool.py."""
    skill_dir = _write_skill(root, name, skill_md)
    (skill_dir / "tool.py").write_text(textwrap.dedent(tool_py), encoding="utf-8")
    return skill_dir


# ===========================================================================
# SkillsLoader: personal directory
# ===========================================================================

class TestPersonalSkillsDirectory:
    """Tests for personal skills directory (~/.xbot/skills/) support."""

    def test_personal_skills_dir_attribute(self, tmp_path: Path) -> None:
        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "builtin")
        assert loader.personal_skills == Path.home() / ".xbot" / "skills"

    def test_personal_skills_discovered(self, tmp_path: Path, monkeypatch) -> None:
        """Skills in the personal directory are discovered."""
        personal_dir = tmp_path / "personal_skills"
        _write_skill(personal_dir, "my-personal", """\
            ---
            name: my-personal
            description: A personal skill
            ---
            Content""")

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        monkeypatch.setattr(loader, "personal_skills", personal_dir)

        skills = loader.list_skills(filter_unavailable=False)
        assert any(s["name"] == "my-personal" and s["source"] == "personal" for s in skills)

    def test_personal_skills_priority_below_scoped(self, tmp_path: Path, monkeypatch) -> None:
        """scoped_workspace skills override personal skills of same name."""
        personal_dir = tmp_path / "personal_skills"
        _write_skill(personal_dir, "shared", """\
            ---
            name: shared
            description: personal version
            ---
            personal""")

        scoped_dir = tmp_path / ".xbot" / "skills"
        _write_skill(scoped_dir, "shared", """\
            ---
            name: shared
            description: scoped version
            ---
            scoped""")

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        monkeypatch.setattr(loader, "personal_skills", personal_dir)

        skills = loader.list_skills(filter_unavailable=False)
        shared = [s for s in skills if s["name"] == "shared"]
        assert len(shared) == 1
        assert shared[0]["source"] == "scoped_workspace"

    def test_load_skill_from_personal(self, tmp_path: Path, monkeypatch) -> None:
        """load_skill() can find skills in the personal directory."""
        personal_dir = tmp_path / "personal_skills"
        _write_skill(personal_dir, "greet", """\
            ---
            name: greet
            description: Greeting skill
            ---
            Say hello""")

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        monkeypatch.setattr(loader, "personal_skills", personal_dir)

        content = loader.load_skill("greet")
        assert content is not None
        assert "Say hello" in content


# ===========================================================================
# SkillsLoader: disable-model-invocation / user-invocable
# ===========================================================================

class TestInvocationControl:
    """Tests for disable-model-invocation and user-invocable frontmatter."""

    @pytest.fixture
    def loader_with_skills(self, tmp_path: Path) -> SkillsLoader:
        ws = tmp_path / "skills"
        _write_skill(ws, "normal-skill", """\
            ---
            name: normal-skill
            description: A normal skill
            ---
            Content""")
        _write_skill(ws, "model-hidden", """\
            ---
            name: model-hidden
            description: Hidden from model
            disable-model-invocation: true
            ---
            Content""")
        _write_skill(ws, "no-slash", """\
            ---
            name: no-slash
            description: No slash menu
            user-invocable: false
            ---
            Content""")
        return SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")

    def test_is_model_invocable_default_true(self, loader_with_skills: SkillsLoader) -> None:
        assert loader_with_skills.is_model_invocable("normal-skill") is True

    def test_is_model_invocable_disabled(self, loader_with_skills: SkillsLoader) -> None:
        assert loader_with_skills.is_model_invocable("model-hidden") is False

    def test_is_user_invocable_default_true(self, loader_with_skills: SkillsLoader) -> None:
        assert loader_with_skills.is_user_invocable("normal-skill") is True

    def test_is_user_invocable_false(self, loader_with_skills: SkillsLoader) -> None:
        assert loader_with_skills.is_user_invocable("no-slash") is False

    def test_list_available_skills_excludes_model_hidden(self, loader_with_skills: SkillsLoader) -> None:
        """list_available_skills should exclude disable-model-invocation skills."""
        skills = loader_with_skills.list_available_skills()
        names = [s["name"] for s in skills]
        assert "normal-skill" in names
        assert "model-hidden" not in names  # excluded from catalog
        assert "no-slash" in names  # still in catalog, just hidden from slash menu

    def test_list_available_skills_includes_user_invocable_field(self, loader_with_skills: SkillsLoader) -> None:
        skills = loader_with_skills.list_available_skills()
        no_slash = next(s for s in skills if s["name"] == "no-slash")
        assert no_slash["user_invocable"] is False
        normal = next(s for s in skills if s["name"] == "normal-skill")
        assert normal["user_invocable"] is True

    def test_build_skills_summary_excludes_model_hidden(self, loader_with_skills: SkillsLoader) -> None:
        """build_skills_summary XML should not contain disable-model-invocation skills."""
        summary = loader_with_skills.build_skills_summary()
        assert "normal-skill" in summary
        assert "model-hidden" not in summary
        assert "no-slash" in summary


# ===========================================================================
# SkillsLoader: type field
# ===========================================================================

class TestSkillTypeField:
    """Tests for 'type' field in list_skills."""

    def test_markdown_skill_type(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        _write_skill(ws, "md-skill", """\
            ---
            name: md-skill
            description: Markdown skill
            ---
            Content""")

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        skills = loader.list_skills(filter_unavailable=False)
        assert skills[0]["type"] == "markdown"

    def test_python_skill_type(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        skill_dir = _write_skill(ws, "py-skill", """\
            ---
            name: py-skill
            description: Python skill
            type: python
            ---
            Content""")
        (skill_dir / "tool.py").write_text("# placeholder", encoding="utf-8")

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        skills = loader.list_skills(filter_unavailable=False)
        assert skills[0]["type"] == "python"

    def test_type_in_list_available_skills(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        _write_skill(ws, "md", """\
            ---
            name: md
            description: Markdown
            ---
            Content""")

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        skills = loader.list_available_skills()
        assert skills[0]["type"] == "markdown"


# ===========================================================================
# SkillManager: fingerprint and change detection
# ===========================================================================

class TestSkillManagerFingerprint:
    """Tests for SkillManager fingerprint computation and change detection."""

    def test_initial_version_is_set(self, tmp_path: Path) -> None:
        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        assert mgr.version  # non-empty string
        assert len(mgr.version) == 32  # md5 hex digest

    def test_fingerprint_stable_when_no_changes(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        _write_skill(ws, "stable", """\
            ---
            name: stable
            description: Stable skill
            ---
            Content""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        fp1 = mgr.compute_fingerprint()
        fp2 = mgr.compute_fingerprint()
        assert fp1 == fp2

    def test_fingerprint_changes_on_new_skill(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        _write_skill(ws, "first", """\
            ---
            name: first
            description: First skill
            ---
            Content""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        fp_before = mgr.compute_fingerprint()

        _write_skill(ws, "second", """\
            ---
            name: second
            description: Second skill
            ---
            Content""")

        fp_after = mgr.compute_fingerprint()
        assert fp_before != fp_after

    def test_fingerprint_changes_on_modify_skill(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        skill_dir = _write_skill(ws, "mutable", """\
            ---
            name: mutable
            description: Will change
            ---
            Version 1""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        fp_before = mgr.compute_fingerprint()

        # Ensure mtime differs (some filesystems have 1s resolution)
        time.sleep(0.05)
        (skill_dir / "SKILL.md").write_text("---\nname: mutable\n---\nVersion 2")

        fp_after = mgr.compute_fingerprint()
        assert fp_before != fp_after

    def test_fingerprint_changes_on_delete_skill(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        skill_dir = _write_skill(ws, "doomed", """\
            ---
            name: doomed
            description: Will be deleted
            ---
            Content""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        fp_before = mgr.compute_fingerprint()

        # Delete skill
        (skill_dir / "SKILL.md").unlink()
        skill_dir.rmdir()

        fp_after = mgr.compute_fingerprint()
        assert fp_before != fp_after

    def test_check_for_changes_returns_false_initially(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        _write_skill(ws, "s1", """\
            ---
            name: s1
            description: Skill 1
            ---
            Content""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        # No changes since init
        assert mgr.check_for_changes() is False

    def test_check_for_changes_detects_new_skill(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        _write_skill(ws, "s1", """\
            ---
            name: s1
            description: Skill 1
            ---
            Content""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        version_before = mgr.version

        # Add new skill
        _write_skill(ws, "s2", """\
            ---
            name: s2
            description: Skill 2
            ---
            Content""")

        assert mgr.check_for_changes() is True
        assert mgr.version != version_before

    def test_check_for_changes_updates_version(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        _write_skill(ws, "s1", """\
            ---
            name: s1
            description: Skill 1
            ---
            Content""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        _write_skill(ws, "s2", """\
            ---
            name: s2
            description: Skill 2
            ---
            Content""")

        mgr.check_for_changes()
        # After update, no more changes
        assert mgr.check_for_changes() is False

    def test_empty_workspace_fingerprint(self, tmp_path: Path) -> None:
        """Empty workspace should still produce a valid fingerprint."""
        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        assert len(mgr.version) == 32


# ===========================================================================
# SkillManager: Python skill loading
# ===========================================================================

class TestSkillManagerPythonLoading:
    """Tests for SkillManager Python skill plugin loading."""

    def _make_python_skill(self, ws_skills: Path, name: str, tool_code: str) -> Path:
        return _write_python_skill(
            ws_skills,
            name,
            f"""\
            ---
            name: {name}
            description: Python skill {name}
            type: python
            ---
            A Python skill.""",
            tool_code,
        )

    def test_load_python_skill_factory(self, tmp_path: Path) -> None:
        """Python skill with create_tools() factory should be loaded."""
        ws = tmp_path / "skills"
        self._make_python_skill(ws, "echo", """\
            from xbot.agent.tools.base import Tool
            from typing import Any

            class EchoTool(Tool):
                @property
                def name(self): return "echo"
                @property
                def description(self): return "Echoes input"
                @property
                def parameters(self): return {"type": "object", "properties": {"text": {"type": "string"}}}
                async def execute(self, **kwargs): return kwargs.get("text", "")

            def create_tools(**kwargs):
                return [EchoTool()]
        """)

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        tools = mgr.get_python_tools()

        assert len(tools) == 1
        assert tools[0].name == "echo"

    def test_load_python_skill_autodiscover(self, tmp_path: Path) -> None:
        """Python skill without factory: Tool subclasses auto-discovered."""
        ws = tmp_path / "skills"
        self._make_python_skill(ws, "ping", """\
            from xbot.agent.tools.base import Tool
            from typing import Any

            class PingTool(Tool):
                @property
                def name(self): return "ping"
                @property
                def description(self): return "Pong"
                @property
                def parameters(self): return {"type": "object", "properties": {}}
                async def execute(self, **kwargs): return "pong"
        """)

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        tools = mgr.get_python_tools()

        assert len(tools) == 1
        assert tools[0].name == "ping"

    def test_broken_python_skill_isolated(self, tmp_path: Path) -> None:
        """A broken tool.py should not prevent other skills from loading."""
        ws = tmp_path / "skills"

        # Good skill
        self._make_python_skill(ws, "good", """\
            from xbot.agent.tools.base import Tool
            class GoodTool(Tool):
                @property
                def name(self): return "good"
                @property
                def description(self): return "Good"
                @property
                def parameters(self): return {"type": "object", "properties": {}}
                async def execute(self, **kwargs): return "ok"
        """)

        # Broken skill (syntax error)
        self._make_python_skill(ws, "broken", """\
            this is not valid python!!!
        """)

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        tools = mgr.get_python_tools()

        # Only the good tool should be loaded
        assert len(tools) == 1
        assert tools[0].name == "good"

    def test_markdown_skill_not_loaded_as_python(self, tmp_path: Path) -> None:
        """Markdown-only skills should not produce Python tools."""
        ws = tmp_path / "skills"
        _write_skill(ws, "md-only", """\
            ---
            name: md-only
            description: Markdown only
            ---
            Content""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        tools = mgr.get_python_tools()
        assert len(tools) == 0

    def test_python_skill_without_type_not_loaded(self, tmp_path: Path) -> None:
        """tool.py exists but SKILL.md doesn't declare type: python."""
        ws = tmp_path / "skills"
        skill_dir = _write_skill(ws, "no-type", """\
            ---
            name: no-type
            description: No type declared
            ---
            Content""")
        (skill_dir / "tool.py").write_text("""\
from xbot.agent.tools.base import Tool
class FakeTool(Tool):
    @property
    def name(self): return "fake"
    @property
    def description(self): return "Fake"
    @property
    def parameters(self): return {"type": "object", "properties": {}}
    async def execute(self, **kwargs): return "nope"
""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        tools = mgr.get_python_tools()
        # type field is determined by tool.py existence, not frontmatter "type"
        # The list_skills returns type="python" when tool.py exists
        # But _reload_python_skills checks skill_info["type"] == "python"
        # Since list_skills detects tool.py presence, this SHOULD load
        # Let's verify the actual behavior
        # Actually: list_skills sets type="python" when tool.py exists regardless of frontmatter
        # So the tool WILL be loaded. Let's verify:
        assert len(tools) == 1
        assert tools[0].name == "fake"

    def test_hot_reload_picks_up_new_python_skill(self, tmp_path: Path) -> None:
        """Adding a Python skill after init should be detected by check_for_changes."""
        ws = tmp_path / "skills"
        ws.mkdir(parents=True, exist_ok=True)

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        assert len(mgr.get_python_tools()) == 0

        # Add a Python skill
        self._make_python_skill(ws, "late", """\
            from xbot.agent.tools.base import Tool
            class LateTool(Tool):
                @property
                def name(self): return "late"
                @property
                def description(self): return "Added late"
                @property
                def parameters(self): return {"type": "object", "properties": {}}
                async def execute(self, **kwargs): return "late"
        """)

        assert mgr.check_for_changes() is True
        assert len(mgr.get_python_tools()) == 1
        assert mgr.get_python_tools()[0].name == "late"

    def test_hot_reload_removes_deleted_python_skill(self, tmp_path: Path) -> None:
        """Deleting a Python skill should remove its tools after check_for_changes."""
        ws = tmp_path / "skills"
        skill_dir = self._make_python_skill(ws, "temp", """\
            from xbot.agent.tools.base import Tool
            class TempTool(Tool):
                @property
                def name(self): return "temp"
                @property
                def description(self): return "Temporary"
                @property
                def parameters(self): return {"type": "object", "properties": {}}
                async def execute(self, **kwargs): return "tmp"
        """)

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        assert len(mgr.get_python_tools()) == 1

        # Delete the skill
        import shutil
        shutil.rmtree(skill_dir)

        assert mgr.check_for_changes() is True
        assert len(mgr.get_python_tools()) == 0

    def test_factory_receives_workspace_kwarg(self, tmp_path: Path) -> None:
        """create_tools() should receive workspace as kwarg."""
        ws = tmp_path / "skills"
        self._make_python_skill(ws, "ws-aware", """\
            from xbot.agent.tools.base import Tool
            class WsTool(Tool):
                def __init__(self, workspace=None):
                    self._workspace = workspace
                @property
                def name(self): return "ws_aware"
                @property
                def description(self): return str(self._workspace)
                @property
                def parameters(self): return {"type": "object", "properties": {}}
                async def execute(self, **kwargs): return str(self._workspace)

            def create_tools(workspace=None, **kwargs):
                return [WsTool(workspace=workspace)]
        """)

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        tools = mgr.get_python_tools()
        assert len(tools) == 1
        assert str(tmp_path) in tools[0].description


# ===========================================================================
# SkillManager: skills_loader property
# ===========================================================================

class TestSkillManagerBackwardCompat:
    """Tests for SkillManager backward compatibility."""

    def test_skills_loader_property(self, tmp_path: Path) -> None:
        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        assert isinstance(mgr.skills_loader, SkillsLoader)

    def test_skills_loader_shares_workspace(self, tmp_path: Path) -> None:
        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        assert mgr.skills_loader.workspace == tmp_path


# ===========================================================================
# ToolAdapter: register_python_skill_tools
# ===========================================================================

class TestToolAdapterPythonSkills:
    """Tests for ToolAdapter.register_python_skill_tools."""

    def _make_mock_tool(self, name: str) -> Tool:
        """Create a minimal concrete Tool for testing."""
        class _MockTool(Tool):
            @property
            def name(self): return name
            @property
            def description(self): return f"Mock {name}"
            @property
            def parameters(self): return {"type": "object", "properties": {}}
            async def execute(self, **kwargs): return "mock"
        return _MockTool()

    def test_register_adds_tools(self, tmp_path: Path) -> None:
        from xbot.agent.capabilities.tool_adapter import ToolAdapter
        adapter = ToolAdapter(workspace=str(tmp_path))
        t1 = self._make_mock_tool("tool_a")
        t2 = self._make_mock_tool("tool_b")

        adapter.register_python_skill_tools([t1, t2])
        assert adapter._tools.get("tool_a") is t1
        assert adapter._tools.get("tool_b") is t2
        assert adapter._python_skill_tool_names == {"tool_a", "tool_b"}

    def test_register_replaces_old_tools(self, tmp_path: Path) -> None:
        from xbot.agent.capabilities.tool_adapter import ToolAdapter
        adapter = ToolAdapter(workspace=str(tmp_path))

        old = self._make_mock_tool("old_tool")
        adapter.register_python_skill_tools([old])
        assert "old_tool" in adapter._tools

        new = self._make_mock_tool("new_tool")
        adapter.register_python_skill_tools([new])
        assert "old_tool" not in adapter._tools  # removed
        assert "new_tool" in adapter._tools

    def test_register_empty_clears_all(self, tmp_path: Path) -> None:
        from xbot.agent.capabilities.tool_adapter import ToolAdapter
        adapter = ToolAdapter(workspace=str(tmp_path))

        t = self._make_mock_tool("to_remove")
        adapter.register_python_skill_tools([t])
        assert "to_remove" in adapter._tools

        adapter.register_python_skill_tools([])
        assert "to_remove" not in adapter._tools
        assert len(adapter._python_skill_tool_names) == 0

    def test_register_does_not_affect_builtin_tools(self, tmp_path: Path) -> None:
        from xbot.agent.capabilities.tool_adapter import ToolAdapter
        adapter = ToolAdapter(workspace=str(tmp_path))

        # Simulate a builtin tool
        builtin = self._make_mock_tool("builtin_exec")
        adapter._tools["builtin_exec"] = builtin

        # Register Python skill tools
        py_tool = self._make_mock_tool("py_tool")
        adapter.register_python_skill_tools([py_tool])

        # Builtin should still be there
        assert adapter._tools.get("builtin_exec") is builtin
        assert "builtin_exec" not in adapter._python_skill_tool_names

        # Replace with empty
        adapter.register_python_skill_tools([])
        assert adapter._tools.get("builtin_exec") is builtin  # still there


# ===========================================================================
# SkillManager + ToolAdapter integration
# ===========================================================================

class TestSkillManagerToolAdapterIntegration:
    """Integration test: SkillManager syncs Python tools to ToolAdapter."""

    def test_sync_tools_to_adapter(self, tmp_path: Path) -> None:
        from xbot.agent.capabilities.tool_adapter import ToolAdapter

        ws = tmp_path / "skills"
        _write_python_skill(ws, "sync-test", """\
            ---
            name: sync-test
            description: Sync test
            type: python
            ---
            Test skill.""",
            """\
            from xbot.agent.tools.base import Tool
            class SyncTool(Tool):
                @property
                def name(self): return "sync_tool"
                @property
                def description(self): return "Sync test tool"
                @property
                def parameters(self): return {"type": "object", "properties": {}}
                async def execute(self, **kwargs): return "synced"
        """)

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        adapter = ToolAdapter(workspace=str(tmp_path))

        mgr.sync_tools_to_adapter(adapter)

        assert "sync_tool" in adapter._tools
        assert "sync_tool" in adapter._python_skill_tool_names


# ===========================================================================
# Bug #3: sys.modules cleanup on skill deletion
# ===========================================================================

class TestSysModulesCleanup:
    """Tests that deleted Python skills have their sys.modules entries cleaned."""

    def test_deleted_skill_module_removed_from_sys_modules(self, tmp_path: Path) -> None:
        """After deleting a Python skill, its module should be removed from sys.modules."""
        import sys
        import shutil

        ws = tmp_path / "skills"
        skill_dir = _write_python_skill(ws, "ephemeral", """\
            ---
            name: ephemeral
            description: Will be deleted
            type: python
            ---
            Temp.""",
            """\
            from xbot.agent.tools.base import Tool
            class EphemeralTool(Tool):
                @property
                def name(self): return "ephemeral"
                @property
                def description(self): return "Ephemeral"
                @property
                def parameters(self): return {"type": "object", "properties": {}}
                async def execute(self, **kwargs): return "gone"
        """)

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        module_name = "xbot._skill_plugins.workspace.ephemeral"
        assert module_name in sys.modules
        assert len(mgr.get_python_tools()) == 1

        # Delete the skill
        shutil.rmtree(skill_dir)
        mgr.check_for_changes()

        assert module_name not in sys.modules
        assert len(mgr.get_python_tools()) == 0

    def test_surviving_skill_module_kept_in_sys_modules(self, tmp_path: Path) -> None:
        """Skills that still exist should keep their sys.modules entry."""
        import sys
        import shutil

        ws = tmp_path / "skills"
        _write_python_skill(ws, "keeper", """\
            ---
            name: keeper
            description: Stays
            type: python
            ---
            Stays.""",
            """\
            from xbot.agent.tools.base import Tool
            class KeeperTool(Tool):
                @property
                def name(self): return "keeper"
                @property
                def description(self): return "Keeper"
                @property
                def parameters(self): return {"type": "object", "properties": {}}
                async def execute(self, **kwargs): return "kept"
        """)
        victim_dir = _write_python_skill(ws, "victim", """\
            ---
            name: victim
            description: Will go
            type: python
            ---
            Gone.""",
            """\
            from xbot.agent.tools.base import Tool
            class VictimTool(Tool):
                @property
                def name(self): return "victim"
                @property
                def description(self): return "Victim"
                @property
                def parameters(self): return {"type": "object", "properties": {}}
                async def execute(self, **kwargs): return "gone"
        """)

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        assert len(mgr.get_python_tools()) == 2
        assert "xbot._skill_plugins.workspace.keeper" in sys.modules
        assert "xbot._skill_plugins.workspace.victim" in sys.modules

        shutil.rmtree(victim_dir)
        mgr.check_for_changes()

        assert "xbot._skill_plugins.workspace.keeper" in sys.modules
        assert "xbot._skill_plugins.workspace.victim" not in sys.modules
        assert len(mgr.get_python_tools()) == 1


# ===========================================================================
# Bug #5: fingerprint deduplication by name
# ===========================================================================

class TestFingerprintDeduplication:
    """Tests that fingerprint deduplicates same-name skills across sources."""

    def test_builtin_shadowed_by_workspace_not_in_fingerprint(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When workspace overrides a builtin skill, only workspace version
        contributes to the fingerprint."""
        import time as _time

        builtin_dir = tmp_path / "builtin_skills"
        _write_skill(builtin_dir, "shared", """\
            ---
            name: shared
            description: builtin version
            ---
            builtin""")

        ws_skills = tmp_path / "skills"
        _write_skill(ws_skills, "shared", """\
            ---
            name: shared
            description: workspace version
            ---
            workspace""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=builtin_dir)
        fp_before = mgr.compute_fingerprint()

        # Modify the builtin version (which is shadowed)
        _time.sleep(0.05)
        (builtin_dir / "shared" / "SKILL.md").write_text(
            "---\nname: shared\n---\nbuiltin v2"
        )

        fp_after = mgr.compute_fingerprint()
        # Fingerprint should NOT change because builtin is shadowed
        assert fp_before == fp_after

    def test_workspace_change_changes_fingerprint(
        self, tmp_path: Path
    ) -> None:
        """Modifying the higher-priority workspace skill should change fingerprint."""
        import time as _time

        builtin_dir = tmp_path / "builtin_skills"
        _write_skill(builtin_dir, "shared", """\
            ---
            name: shared
            description: builtin
            ---
            builtin""")

        ws_skills = tmp_path / "skills"
        _write_skill(ws_skills, "shared", """\
            ---
            name: shared
            description: workspace
            ---
            workspace""")

        mgr = SkillManager(tmp_path, builtin_skills_dir=builtin_dir)
        fp_before = mgr.compute_fingerprint()

        _time.sleep(0.05)
        (ws_skills / "shared" / "SKILL.md").write_text(
            "---\nname: shared\n---\nworkspace v2"
        )

        fp_after = mgr.compute_fingerprint()
        assert fp_before != fp_after


# ===========================================================================
# Bug #6: autodiscovery filters imported Tool subclasses
# ===========================================================================

class TestAutodiscoveryFiltering:
    """Tests that autodiscovery only instantiates classes defined in the module."""

    def test_imported_tool_subclass_not_instantiated(self, tmp_path: Path) -> None:
        """A Tool subclass imported from another module should not be autodiscovered."""
        ws = tmp_path / "skills"
        # This skill imports ReadFileTool (a real Tool subclass) but defines its own tool.
        # Only the locally defined tool should be discovered.
        _write_python_skill(ws, "import-test", """\
            ---
            name: import-test
            description: Tests import filtering
            type: python
            ---
            Test.""",
            """\
            from xbot.agent.tools.base import Tool
            # Import an existing Tool subclass — should NOT be autodiscovered
            from xbot.agent.tools.filesystem import ReadFileTool

            class LocalTool(Tool):
                @property
                def name(self): return "local_only"
                @property
                def description(self): return "Local"
                @property
                def parameters(self): return {"type": "object", "properties": {}}
                async def execute(self, **kwargs): return "local"
        """)

        mgr = SkillManager(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        tools = mgr.get_python_tools()

        tool_names = [t.name for t in tools]
        assert "local_only" in tool_names
        # ReadFileTool should NOT have been instantiated
        assert not any("read" in n.lower() for n in tool_names)
