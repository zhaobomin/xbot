"""Tests for skills loader."""

from pathlib import Path
from unittest.mock import patch

import pytest

from xbot.agent.capabilities.skills_loader import SkillsLoader


class TestSkillsLoader:
    """Tests for SkillsLoader."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create a test workspace."""
        return tmp_path

    @pytest.fixture
    def builtin_skills(self, tmp_path: Path) -> Path:
        """Create a test builtin skills directory."""
        builtin = tmp_path / "builtin_skills"
        builtin.mkdir()
        return builtin

    @pytest.fixture
    def loader(self, workspace: Path, builtin_skills: Path) -> SkillsLoader:
        """Create a skills loader."""
        return SkillsLoader(workspace, builtin_skills_dir=builtin_skills)

    def test_init(self, loader: SkillsLoader, workspace: Path, builtin_skills: Path) -> None:
        """Test initialization."""
        assert loader.workspace == workspace
        assert loader.builtin_skills == builtin_skills

    def test_list_skills_empty(self, loader: SkillsLoader) -> None:
        """Test listing skills when none exist."""
        skills = loader.list_skills()
        assert skills == []

    def test_list_skills_builtin(self, builtin_skills: Path, workspace: Path) -> None:
        """Test listing builtin skills."""
        # Create a builtin skill
        skill_dir = builtin_skills / "test_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: Test skill\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        skills = loader.list_skills(filter_unavailable=False)

        assert len(skills) == 1
        assert skills[0]["name"] == "test_skill"
        assert skills[0]["source"] == "builtin"

    def test_list_skills_workspace(self, workspace: Path, builtin_skills: Path) -> None:
        """Test listing workspace skills (higher priority)."""
        # Create workspace skill
        ws_skills = workspace / "skills"
        ws_skills.mkdir()
        skill_dir = ws_skills / "my_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: My skill\n---\nContent")

        # Create builtin skill with same name (should be overridden)
        builtin_dir = builtin_skills / "my_skill"
        builtin_dir.mkdir()
        (builtin_dir / "SKILL.md").write_text("---\ndescription: Builtin\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        skills = loader.list_skills(filter_unavailable=False)

        # Workspace skill should take priority
        assert len(skills) == 1
        assert skills[0]["source"] == "workspace"

    def test_load_skill_not_found(self, loader: SkillsLoader) -> None:
        """Test loading non-existent skill."""
        result = loader.load_skill("nonexistent")
        assert result is None

    def test_load_skill_builtin(self, builtin_skills: Path, workspace: Path) -> None:
        """Test loading builtin skill."""
        skill_dir = builtin_skills / "test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("Test content")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        result = loader.load_skill("test")

        assert result == "Test content"

    def test_load_skill_workspace_override(self, workspace: Path, builtin_skills: Path) -> None:
        """Test that workspace skill overrides builtin."""
        # Create workspace skill
        ws_skills = workspace / "skills"
        ws_skills.mkdir()
        skill_dir = ws_skills / "test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("Workspace version")

        # Create builtin skill
        builtin_dir = builtin_skills / "test"
        builtin_dir.mkdir()
        (builtin_dir / "SKILL.md").write_text("Builtin version")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        result = loader.load_skill("test")

        assert result == "Workspace version"

    def test_load_skills_for_context(self, builtin_skills: Path, workspace: Path) -> None:
        """Test loading skills for context."""
        skill_dir = builtin_skills / "test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("Test content")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        result = loader.load_skills_for_context(["test"])

        assert "Skill: test" in result
        assert "Test content" in result

    def test_load_skills_for_context_empty(self, loader: SkillsLoader) -> None:
        """Test loading empty skill list."""
        result = loader.load_skills_for_context([])
        assert result == ""

    def test_build_skills_summary_empty(self, loader: SkillsLoader) -> None:
        """Test building summary when no skills."""
        result = loader.build_skills_summary()
        assert result == ""

    def test_build_skills_summary(self, builtin_skills: Path, workspace: Path) -> None:
        """Test building skills summary."""
        skill_dir = builtin_skills / "weather"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: Get weather\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        result = loader.build_skills_summary()

        assert "<skills>" in result
        assert "</skills>" in result
        assert "weather" in result
        assert "Get weather" in result

    def test_strip_frontmatter(self, loader: SkillsLoader) -> None:
        """Test stripping frontmatter."""
        content = "---\ntitle: Test\n---\nActual content"
        result = loader._strip_frontmatter(content)
        assert "Actual content" in result
        assert "---" not in result

    def test_strip_frontmatter_no_frontmatter(self, loader: SkillsLoader) -> None:
        """Test content without frontmatter."""
        content = "Just content"
        result = loader._strip_frontmatter(content)
        assert result == "Just content"

    def test_get_skill_metadata(self, builtin_skills: Path, workspace: Path) -> None:
        """Test getting skill metadata."""
        skill_dir = builtin_skills / "test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: Test\ntool_exposable: true\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        meta = loader.get_skill_metadata("test")

        assert meta is not None
        assert meta.get("description") == "Test"
        assert meta.get("tool_exposable") is True

    def test_get_skill_metadata_not_found(self, loader: SkillsLoader) -> None:
        """Test getting metadata for non-existent skill."""
        result = loader.get_skill_metadata("nonexistent")
        assert result is None

    def test_is_tool_exposable_true(self, builtin_skills: Path, workspace: Path) -> None:
        """Test tool_exposable skill."""
        skill_dir = builtin_skills / "test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ntool_exposable: true\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        assert loader.is_tool_exposable("test") is True

    def test_is_tool_exposable_false(self, builtin_skills: Path, workspace: Path) -> None:
        """Test non-tool_exposable skill."""
        skill_dir = builtin_skills / "test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ntool_exposable: false\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        assert loader.is_tool_exposable("test") is False

    def test_is_tool_exposable_missing(self, builtin_skills: Path, workspace: Path) -> None:
        """Test skill without tool_exposable metadata."""
        skill_dir = builtin_skills / "test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("No metadata")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        assert loader.is_tool_exposable("test") is False

    def test_get_always_skills(self, builtin_skills: Path, workspace: Path) -> None:
        """Test getting always skills."""
        skill_dir = builtin_skills / "always_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text('---\ndescription: Always\nmetadata: {"xbot": {"always": true}}\n---\nContent')

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        always = loader.get_always_skills()

        assert "always_skill" in always

    def test_check_requirements_bins(self, loader: SkillsLoader) -> None:
        """Test checking binary requirements."""
        # Test with a binary that likely exists
        meta = {"requires": {"bins": ["ls"]}}
        assert loader._check_requirements(meta) is True

        # Test with a binary that likely doesn't exist
        meta = {"requires": {"bins": ["nonexistent_binary_12345"]}}
        assert loader._check_requirements(meta) is False

    def test_check_requirements_env(self, loader: SkillsLoader) -> None:
        """Test checking environment variable requirements."""
        import os

        # Test with an env var that likely exists
        meta = {"requires": {"env": ["PATH"]}}
        assert loader._check_requirements(meta) is True

        # Test with an env var that likely doesn't exist
        meta = {"requires": {"env": ["NONEXISTENT_VAR_12345"]}}
        assert loader._check_requirements(meta) is False

    def test_parse_xbot_metadata(self, loader: SkillsLoader) -> None:
        """Test parsing xbot metadata."""
        raw = '{"xbot": {"version": "1.0", "always": true}}'
        result = loader._parse_xbot_metadata(raw)
        assert result.get("version") == "1.0"
        assert result.get("always") is True

    def test_parse_xbot_metadata_openclaw_fallback(self, loader: SkillsLoader) -> None:
        """Test parsing openclaw metadata fallback."""
        raw = '{"openclaw": {"version": "2.0"}}'
        result = loader._parse_xbot_metadata(raw)
        assert result.get("version") == "2.0"

    def test_parse_xbot_metadata_invalid(self, loader: SkillsLoader) -> None:
        """Test parsing invalid metadata."""
        result = loader._parse_xbot_metadata("not json")
        assert result == {}

    def test_get_missing_requirements(self, loader: SkillsLoader) -> None:
        """Test getting missing requirements description."""
        meta = {"requires": {"bins": ["nonexistent_123"], "env": ["MISSING_VAR"]}}
        result = loader._get_missing_requirements(meta)

        assert "CLI: nonexistent_123" in result
        assert "ENV: MISSING_VAR" in result


class TestSkillTriggering:
    """Tests for skill triggering functionality."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create a test workspace."""
        return tmp_path

    @pytest.fixture
    def builtin_skills(self, tmp_path: Path) -> Path:
        """Create a test builtin skills directory."""
        builtin = tmp_path / "builtin_skills"
        builtin.mkdir()
        return builtin

    @pytest.fixture
    def loader(self, workspace: Path, builtin_skills: Path) -> SkillsLoader:
        """Create a skills loader."""
        return SkillsLoader(workspace, builtin_skills_dir=builtin_skills)

    def test_get_skill_triggers_no_metadata(self, loader: SkillsLoader) -> None:
        """Test get_skill_triggers returns empty triggers when no skill exists."""
        triggers = loader.get_skill_triggers("nonexistent")
        assert triggers.triggers == []
        assert triggers.excludes == []

    def test_get_skill_triggers_with_triggers(self, builtin_skills: Path, workspace: Path) -> None:
        """Test get_skill_triggers parses trigger definitions."""
        skill_dir = builtin_skills / "review"
        skill_dir.mkdir()
        skill_content = """---
name: review
description: Review code
triggers:
  - when: user_requests
    patterns: ["review", "simplify"]
---

Review the code.
"""
        (skill_dir / "SKILL.md").write_text(skill_content)

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        triggers = loader.get_skill_triggers("review")

        assert len(triggers.triggers) == 1
        assert triggers.triggers[0].kind == "user_requests"
        assert "review" in triggers.triggers[0].patterns
        assert "simplify" in triggers.triggers[0].patterns

    def test_get_skill_triggers_with_excludes(self, builtin_skills: Path, workspace: Path) -> None:
        """Test get_skill_triggers parses exclude definitions."""
        skill_dir = builtin_skills / "review"
        skill_dir.mkdir()
        skill_content = """---
name: review
triggers:
  - when: user_requests
    patterns: ["review"]
excludes:
  - when: user_requests
    patterns: ["skip review", "no review"]
---

Review the code.
"""
        (skill_dir / "SKILL.md").write_text(skill_content)

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        triggers = loader.get_skill_triggers("review")

        assert len(triggers.triggers) == 1
        assert len(triggers.excludes) == 1
        assert triggers.excludes[0].kind == "user_requests"
        assert "skip review" in triggers.excludes[0].patterns

    def test_get_triggered_skills_user_requests(self, builtin_skills: Path, workspace: Path) -> None:
        """Test triggering skills based on user message."""
        skill_dir = builtin_skills / "weather"
        skill_dir.mkdir()
        skill_content = """---
name: weather
triggers:
  - when: user_requests
    patterns: ["weather", "forecast"]
---

Weather skill.
"""
        (skill_dir / "SKILL.md").write_text(skill_content)

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)

        # Should trigger
        triggered = loader.get_triggered_skills(user_message="What's the weather today?")
        assert "weather" in triggered

        # Should trigger
        triggered = loader.get_triggered_skills(user_message="Give me a forecast")
        assert "weather" in triggered

        # Should not trigger
        triggered = loader.get_triggered_skills(user_message="Hello world")
        assert "weather" not in triggered

    def test_get_triggered_skills_code_contains(self, builtin_skills: Path, workspace: Path) -> None:
        """Test triggering skills based on code context."""
        skill_dir = builtin_skills / "claude-api"
        skill_dir.mkdir()
        skill_content = """---
name: claude-api
triggers:
  - when: code_contains
    patterns: ["anthropic", "claude_agent_sdk"]
---

Claude API skill.
"""
        (skill_dir / "SKILL.md").write_text(skill_content)

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)

        # Should trigger when code contains pattern
        triggered = loader.get_triggered_skills(code_context="import anthropic")
        assert "claude-api" in triggered

        triggered = loader.get_triggered_skills(code_context="from claude_agent_sdk import...")
        assert "claude-api" in triggered

        # Should not trigger
        triggered = loader.get_triggered_skills(code_context="import openai")
        assert "claude-api" not in triggered

    def test_get_triggered_skills_file_pattern(self, builtin_skills: Path, workspace: Path) -> None:
        """Test triggering skills based on file paths."""
        skill_dir = builtin_skills / "python-helper"
        skill_dir.mkdir()
        skill_content = """---
name: python-helper
triggers:
  - when: file_pattern
    patterns: [".py", "requirements.txt"]
---

Python helper skill.
"""
        (skill_dir / "SKILL.md").write_text(skill_content)

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)

        # Should trigger when file matches pattern
        triggered = loader.get_triggered_skills(file_paths=["/home/user/main.py"])
        assert "python-helper" in triggered

        triggered = loader.get_triggered_skills(file_paths=["requirements.txt"])
        assert "python-helper" in triggered

        # Should not trigger
        triggered = loader.get_triggered_skills(file_paths=["main.js"])
        assert "python-helper" not in triggered

    def test_get_triggered_skills_exclusion(self, builtin_skills: Path, workspace: Path) -> None:
        """Test that exclusions prevent triggering."""
        skill_dir = builtin_skills / "review"
        skill_dir.mkdir()
        skill_content = """---
name: review
triggers:
  - when: user_requests
    patterns: ["review", "simplify"]
excludes:
  - when: user_requests
    patterns: ["skip review", "no review"]
---

Review skill.
"""
        (skill_dir / "SKILL.md").write_text(skill_content)

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)

        # Should trigger
        triggered = loader.get_triggered_skills(user_message="Please review this code")
        assert "review" in triggered

        # Should be excluded
        triggered = loader.get_triggered_skills(user_message="Please review this code, but skip review for tests")
        assert "review" not in triggered

        triggered = loader.get_triggered_skills(user_message="simplify this, no review needed")
        assert "review" not in triggered

    def test_get_triggered_skills_multiple_triggers(self, builtin_skills: Path, workspace: Path) -> None:
        """Test multiple trigger conditions (OR logic)."""
        skill_dir = builtin_skills / "multi-trigger"
        skill_dir.mkdir()
        skill_content = """---
name: multi-trigger
triggers:
  - when: user_requests
    patterns: ["help"]
  - when: code_contains
    patterns: ["TODO"]
---

Multi-trigger skill.
"""
        (skill_dir / "SKILL.md").write_text(skill_content)

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)

        # Should trigger from user message
        triggered = loader.get_triggered_skills(user_message="I need help")
        assert "multi-trigger" in triggered

        # Should trigger from code context
        triggered = loader.get_triggered_skills(code_context="# TODO: fix this")
        assert "multi-trigger" in triggered

        # Should trigger when both match
        triggered = loader.get_triggered_skills(user_message="help me", code_context="# TODO")
        assert "multi-trigger" in triggered

    def test_check_trigger_user_requests(self, loader: SkillsLoader) -> None:
        """Test _check_trigger for user_requests kind."""
        from xbot.agent.capabilities.skills_loader import TriggerCondition

        condition = TriggerCondition(kind="user_requests", patterns=["test", "example"])
        assert loader._check_trigger(condition, "This is a test", "", None) is True
        assert loader._check_trigger(condition, "Show me an example", "", None) is True
        assert loader._check_trigger(condition, "No match here", "", None) is False

    def test_check_trigger_code_contains(self, loader: SkillsLoader) -> None:
        """Test _check_trigger for code_contains kind."""
        from xbot.agent.capabilities.skills_loader import TriggerCondition

        condition = TriggerCondition(kind="code_contains", patterns=["import os", "from sys"])
        assert loader._check_trigger(condition, "", "import os", None) is True
        assert loader._check_trigger(condition, "", "from sys import path", None) is True
        assert loader._check_trigger(condition, "", "no match", None) is False

    def test_check_trigger_file_pattern(self, loader: SkillsLoader) -> None:
        """Test _check_trigger for file_pattern kind."""
        from xbot.agent.capabilities.skills_loader import TriggerCondition

        condition = TriggerCondition(kind="file_pattern", patterns=[".py", ".ts"])
        assert loader._check_trigger(condition, "", "", ["main.py"]) is True
        assert loader._check_trigger(condition, "", "", ["src/app.ts"]) is True
        assert loader._check_trigger(condition, "", "", None) is False
        assert loader._check_trigger(condition, "", "", ["main.js"]) is False

    def test_match_patterns_case_insensitive(self, loader: SkillsLoader) -> None:
        """Test that pattern matching is case-insensitive."""
        assert loader._match_patterns(["REVIEW"], "Please review this") is True
        assert loader._match_patterns(["review"], "Please REVIEW this") is True
        assert loader._match_patterns(["Review"], "please review this") is True

    def test_parse_trigger_list_invalid_input(self, loader: SkillsLoader) -> None:
        """Test _parse_trigger_list handles invalid input."""
        assert loader._parse_trigger_list(None) == []
        assert loader._parse_trigger_list("not a list") == []
        assert loader._parse_trigger_list([{"no_when": "value"}]) == []  # Missing 'when'
        assert loader._parse_trigger_list([{"when": "user_requests"}]) == []  # Missing 'patterns'

    def test_parse_trigger_list_string_patterns(self, loader: SkillsLoader) -> None:
        """Test _parse_trigger_list handles string patterns."""
        result = loader._parse_trigger_list([{"when": "user_requests", "patterns": "single"}])
        assert len(result) == 1
        assert result[0].patterns == ["single"]

    def test_get_full_metadata_no_yaml(self, builtin_skills: Path, workspace: Path) -> None:
        """Test _get_full_metadata without PyYAML falls back to simple parsing."""
        skill_dir = builtin_skills / "test"
        skill_dir.mkdir()
        skill_content = """---
name: test
description: Simple
---

Content.
"""
        (skill_dir / "SKILL.md").write_text(skill_content)

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        meta = loader._get_full_metadata("test")

        assert meta.get("name") == "test"
        assert meta.get("description") == "Simple"

    def test_get_full_metadata_no_frontmatter(self, builtin_skills: Path, workspace: Path) -> None:
        """Test _get_full_metadata with content without frontmatter."""
        skill_dir = builtin_skills / "test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("No frontmatter here")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        meta = loader._get_full_metadata("test")

        assert meta == {}

    def test_parse_yaml_simple_basic(self, loader: SkillsLoader) -> None:
        """Test _parse_yaml_simple with basic YAML."""
        yaml_content = """name: test
description: A test skill
version: 1.0"""
        result = loader._parse_yaml_simple(yaml_content)

        assert result.get("name") == "test"
        assert result.get("description") == "A test skill"
        # Version is parsed as float
        assert result.get("version") == 1.0

    def test_parse_yaml_simple_inline_list(self, loader: SkillsLoader) -> None:
        """Test _parse_yaml_simple with inline list values."""
        yaml_content = """name: test
patterns: [pattern1, pattern2]"""
        result = loader._parse_yaml_simple(yaml_content)

        assert result.get("name") == "test"
        assert "patterns" in result
        assert len(result["patterns"]) == 2
