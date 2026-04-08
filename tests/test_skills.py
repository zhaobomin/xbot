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

    def test_strip_frontmatter(self, loader: SkillsLoader) -> None:
        """Test stripping frontmatter."""
        content = "---\ntitle: Test\n---\nActual content"
        result = loader.strip_frontmatter(content)
        assert "Actual content" in result
        assert "---" not in result

    def test_strip_frontmatter_no_frontmatter(self, loader: SkillsLoader) -> None:
        """Test content without frontmatter."""
        content = "Just content"
        result = loader.strip_frontmatter(content)
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

    def test_get_skill_metadata_always(self, builtin_skills: Path, workspace: Path) -> None:
        """Test getting skill metadata with always flag."""
        skill_dir = builtin_skills / "always_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text('---\ndescription: Always\nmetadata: {"xbot": {"always": true}}\n---\nContent')

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        meta = loader.get_skill_metadata("always_skill")

        assert meta is not None
        assert meta.get("description") == "Always"

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

    def test_list_available_skills(self, builtin_skills: Path, workspace: Path) -> None:
        """Test listing available skills with metadata."""
        skill_dir = builtin_skills / "weather"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: Get weather\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        available = loader.list_available_skills()

        assert len(available) >= 1
        names = [s["name"] for s in available]
        assert "weather" in names

    def test_is_model_invocable_default(self, builtin_skills: Path, workspace: Path) -> None:
        """Test is_model_invocable defaults to True."""
        skill_dir = builtin_skills / "test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: Test\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        assert loader.is_model_invocable("test") is True

    def test_is_model_invocable_disabled(self, builtin_skills: Path, workspace: Path) -> None:
        """Test is_model_invocable when disabled."""
        skill_dir = builtin_skills / "hidden"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndisable-model-invocation: true\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        assert loader.is_model_invocable("hidden") is False

    def test_is_user_invocable_default(self, builtin_skills: Path, workspace: Path) -> None:
        """Test is_user_invocable defaults to True."""
        skill_dir = builtin_skills / "test"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: Test\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        assert loader.is_user_invocable("test") is True

    def test_is_user_invocable_hidden(self, builtin_skills: Path, workspace: Path) -> None:
        """Test is_user_invocable when hidden from menu."""
        skill_dir = builtin_skills / "auto"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nuser-invocable: false\n---\nContent")

        loader = SkillsLoader(workspace, builtin_skills_dir=builtin_skills)
        assert loader.is_user_invocable("auto") is False


