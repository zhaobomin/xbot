"""Tests for Skills Lazy Loading functionality.

Tests for:
- LoadSkillContentTool: on-demand skill content loading
- SkillsLoader.list_available_skills: lightweight metadata retrieval
- ContextBuilder._build_skills_catalog: skills catalog generation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.agent.skills import SkillsLoader
from xbot.agent.tools.skill_loader import LoadSkillContentTool


def _write_skill(root: Path, name: str, body: str) -> None:
    """Helper to create a skill file."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


class TestListAvailableSkills:
    """Tests for SkillsLoader.list_available_skills()."""

    def test_returns_lightweight_metadata_only(self, tmp_path: Path) -> None:
        """Should return only name, description, available, source."""
        workspace_skills = tmp_path / "skills"
        _write_skill(
            workspace_skills,
            "weather",
            """---
name: weather
description: Get weather forecasts
---
# Weather Skill
This is a very long skill content that should NOT be included in the metadata.
It has multiple lines and detailed instructions.
""",
        )

        # Pass empty builtin dir to avoid loading system skills
        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtins")
        skills = loader.list_available_skills()

        assert len(skills) == 1
        skill = skills[0]

        # Should have these keys
        assert skill["name"] == "weather"
        assert skill["description"] == "Get weather forecasts"
        assert skill["available"] is True
        assert skill["source"] == "workspace"

        # Should NOT have full content
        assert "This is a very long skill content" not in str(skill)

    def test_includes_missing_requirements_for_unavailable_skills(self, tmp_path: Path, monkeypatch) -> None:
        """Should include 'requires' field for unavailable skills."""
        workspace_skills = tmp_path / "skills"
        _write_skill(
            workspace_skills,
            "github",
            '''---
name: github
description: GitHub operations
metadata: {"xbot": {"requires": {"env": ["FAKE_GITHUB_TOKEN"]}}}
---
# GitHub Skill
''',
        )

        # Ensure the env var is not set
        monkeypatch.delenv("FAKE_GITHUB_TOKEN", raising=False)

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtins")
        skills = loader.list_available_skills()

        assert len(skills) == 1
        skill = skills[0]
        assert skill["available"] is False
        assert "requires" in skill
        assert "FAKE_GITHUB_TOKEN" in skill["requires"]

    def test_respects_skill_priority_workspace_first(self, tmp_path: Path) -> None:
        """Workspace skills should take priority over builtin."""
        workspace_skills = tmp_path / "skills"
        builtin_skills = tmp_path / "builtin"

        _write_skill(
            workspace_skills,
            "shared",
            "---\nname: shared\ndescription: workspace version\n---\nworkspace",
        )
        _write_skill(
            builtin_skills,
            "shared",
            "---\nname: shared\ndescription: builtin version\n---\nbuiltin",
        )
        _write_skill(
            builtin_skills,
            "builtin_only",
            "---\nname: builtin_only\ndescription: builtin only\n---\nbuiltin",
        )

        loader = SkillsLoader(tmp_path, builtin_skills_dir=builtin_skills)
        skills = loader.list_available_skills()

        names = [s["name"] for s in skills]
        assert names == ["shared", "builtin_only"]

        # Should use workspace version description
        shared = next(s for s in skills if s["name"] == "shared")
        assert shared["description"] == "workspace version"
        assert shared["source"] == "workspace"

        builtin = next(s for s in skills if s["name"] == "builtin_only")
        assert builtin["source"] == "builtin"

    def test_returns_empty_list_when_no_skills(self, tmp_path: Path) -> None:
        """Should return empty list when no skills exist."""
        # Pass empty builtin dir to avoid loading system skills
        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtins")
        skills = loader.list_available_skills()
        assert skills == []


class TestLoadSkillContentTool:
    """Tests for LoadSkillContentTool."""

    def test_tool_properties(self, tmp_path: Path) -> None:
        """Test tool name and description."""
        loader = SkillsLoader(tmp_path)
        tool = LoadSkillContentTool(skills_loader=loader)

        assert tool.name == "load_skill_content"
        assert "skill" in tool.description.lower()
        assert "skill_name" in tool.parameters["properties"]

    @pytest.mark.asyncio
    async def test_load_existing_skill(self, tmp_path: Path) -> None:
        """Should load and return full skill content."""
        workspace_skills = tmp_path / "skills"
        _write_skill(
            workspace_skills,
            "weather",
            """---
name: weather
description: Get weather
---
# Weather Skill

Detailed instructions here.
""",
        )

        loader = SkillsLoader(tmp_path)
        tool = LoadSkillContentTool(skills_loader=loader)

        result = await tool.execute(skill_name="weather")

        assert "# Skill: weather" in result
        assert "Detailed instructions here" in result
        # Frontmatter should be stripped
        assert "---" not in result

    @pytest.mark.asyncio
    async def test_load_nonexistent_skill(self, tmp_path: Path) -> None:
        """Should return error message for non-existent skill."""
        loader = SkillsLoader(tmp_path)
        tool = LoadSkillContentTool(skills_loader=loader)

        result = await tool.execute(skill_name="nonexistent")

        assert "not found" in result.lower()
        assert "nonexistent" in result

    @pytest.mark.asyncio
    async def test_progress_callback_loading_status(self, tmp_path: Path) -> None:
        """Should call progress callback with loading status."""
        workspace_skills = tmp_path / "skills"
        _write_skill(
            workspace_skills,
            "test",
            "---\nname: test\ndescription: test\n---\ncontent",
        )

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtins")
        callback = AsyncMock()
        tool = LoadSkillContentTool(skills_loader=loader, progress_callback=callback)

        await tool.execute(skill_name="test")

        # Should be called with loading then loaded
        assert callback.call_count == 2
        statuses = [call[0][1] for call in callback.call_args_list]
        assert "loading" in statuses
        assert "loaded" in statuses

    @pytest.mark.asyncio
    async def test_progress_callback_not_found_status(self, tmp_path: Path) -> None:
        """Should call progress callback with not_found status."""
        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtins")
        callback = AsyncMock()
        tool = LoadSkillContentTool(skills_loader=loader, progress_callback=callback)

        await tool.execute(skill_name="missing")

        assert callback.call_count == 2
        statuses = [call[0][1] for call in callback.call_args_list]
        assert "loading" in statuses
        assert "not_found" in statuses

    def test_set_progress_callback(self, tmp_path: Path) -> None:
        """Should allow updating progress callback."""
        loader = SkillsLoader(tmp_path)
        tool = LoadSkillContentTool(skills_loader=loader)

        assert tool._progress_callback is None

        callback = AsyncMock()
        tool.set_progress_callback(callback)

        assert tool._progress_callback is callback


class TestSkillsCatalogFormat:
    """Tests for the Skills Catalog XML format."""

    def test_catalog_xml_format(self, tmp_path: Path) -> None:
        """Should generate valid XML format for skills catalog."""
        workspace_skills = tmp_path / "skills"
        _write_skill(
            workspace_skills,
            "weather",
            "---\nname: weather\ndescription: Get weather\n---\ncontent",
        )
        _write_skill(
            workspace_skills,
            "cron",
            "---\nname: cron\ndescription: Schedule tasks\n---\ncontent",
        )

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtins")
        skills = loader.list_available_skills()

        # Build catalog manually to test format
        lines = ["<skills>"]
        for skill in skills:
            lines.append(f'  <skill available="true">')
            lines.append(f"    <name>{skill['name']}</name>")
            lines.append(f"    <description>{skill['description']}</description>")
            lines.append("  </skill>")
        lines.append("</skills>")
        catalog = "\n".join(lines)

        assert "<skills>" in catalog
        assert "</skills>" in catalog
        assert 'available="true"' in catalog
        assert "<name>weather</name>" in catalog
        assert "<name>cron</name>" in catalog

    def test_catalog_includes_unavailable_skills(self, tmp_path: Path, monkeypatch) -> None:
        """Should include unavailable skills with requires field."""
        workspace_skills = tmp_path / "skills"
        _write_skill(
            workspace_skills,
            "github",
            '''---
name: github
description: GitHub operations
metadata: {"xbot": {"requires": {"env": ["FAKE_GITHUB_TOKEN"]}}}
---
content
''',
        )

        # Ensure the env var is not set
        monkeypatch.delenv("FAKE_GITHUB_TOKEN", raising=False)

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtins")
        skills = loader.list_available_skills()

        assert len(skills) == 1
        skill = skills[0]
        assert skill["available"] is False
        assert skill["requires"] == "ENV: FAKE_GITHUB_TOKEN"


class TestTokenSavings:
    """Tests to verify token savings from lazy loading."""

    def test_catalog_vs_full_content_tokens(self, tmp_path: Path) -> None:
        """Verify that catalog uses significantly fewer tokens than full content."""
        workspace_skills = tmp_path / "skills"

        # Create multiple skills with substantial content
        for i in range(5):
            _write_skill(
                workspace_skills,
                f"skill_{i}",
                f"""---
name: skill_{i}
description: Description for skill {i}
---
# Skill {i}

This is a detailed skill with many lines of content.
Each line adds to the token count.

## Section 1
Content here...

## Section 2
More content here...

## Section 3
Even more content here...

## Examples
- Example 1
- Example 2
- Example 3

## Best Practices
1. Practice 1
2. Practice 2
3. Practice 3
""",
            )

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtins")

        # Get lightweight catalog
        catalog_skills = loader.list_available_skills()
        catalog_text = " ".join(
            f"{s['name']} {s['description']}" for s in catalog_skills
        )
        catalog_tokens = len(catalog_text.split())

        # Get full content
        all_skills = loader.list_skills(filter_unavailable=True)
        full_content = loader.load_skills_for_context([s["name"] for s in all_skills])
        full_tokens = len(full_content.split())

        # Catalog should be significantly smaller
        assert catalog_tokens < full_tokens / 5  # At least 80% reduction
        print(f"\nToken comparison: catalog={catalog_tokens}, full={full_tokens}, "
              f"reduction={100 * (1 - catalog_tokens / full_tokens):.1f}%")