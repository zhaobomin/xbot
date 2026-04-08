"""Tests for skills loader behavior in current architecture."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from xbot.agent.capabilities.skills_loader import SkillsLoader


def _write_skill(root: Path, name: str, body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(textwrap.dedent(body), encoding="utf-8")
    return skill_dir


class TestPersonalSkillsDirectory:
    def test_personal_skills_dir_attribute(self, tmp_path: Path) -> None:
        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "builtin")
        assert loader.personal_skills == Path.home() / ".xbot" / "skills"

    def test_personal_skills_discovered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        personal_dir = tmp_path / "personal_skills"
        _write_skill(
            personal_dir,
            "my-personal",
            """\
            ---
            name: my-personal
            description: A personal skill
            ---
            Content
            """,
        )

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        monkeypatch.setattr(loader, "personal_skills", personal_dir)

        skills = loader.list_skills(filter_unavailable=False)
        assert any(s["name"] == "my-personal" and s["source"] == "personal" for s in skills)

    def test_priority_workspace_over_scoped_personal_builtin(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ws_dir = tmp_path / "skills"
        scoped_dir = tmp_path / ".xbot" / "skills"
        personal_dir = tmp_path / "personal"
        builtin_dir = tmp_path / "builtin"

        for root, desc in [
            (ws_dir, "workspace version"),
            (scoped_dir, "scoped version"),
            (personal_dir, "personal version"),
            (builtin_dir, "builtin version"),
        ]:
            _write_skill(
                root,
                "shared",
                f"""\
                ---
                name: shared
                description: {desc}
                ---
                {desc}
                """,
            )

        loader = SkillsLoader(tmp_path, builtin_skills_dir=builtin_dir)
        monkeypatch.setattr(loader, "personal_skills", personal_dir)

        skills = [s for s in loader.list_skills(filter_unavailable=False) if s["name"] == "shared"]
        assert len(skills) == 1
        assert skills[0]["source"] == "workspace"

    def test_load_skill_from_personal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        personal_dir = tmp_path / "personal_skills"
        _write_skill(
            personal_dir,
            "greet",
            """\
            ---
            name: greet
            description: Greeting skill
            ---
            Say hello
            """,
        )

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        monkeypatch.setattr(loader, "personal_skills", personal_dir)

        content = loader.load_skill("greet")
        assert content is not None
        assert "Say hello" in content


class TestInvocationControl:
    @pytest.fixture
    def loader_with_skills(self, tmp_path: Path) -> SkillsLoader:
        ws = tmp_path / "skills"
        _write_skill(
            ws,
            "normal-skill",
            """\
            ---
            name: normal-skill
            description: A normal skill
            ---
            Content
            """,
        )
        _write_skill(
            ws,
            "model-hidden",
            """\
            ---
            name: model-hidden
            description: Hidden from model
            disable-model-invocation: true
            ---
            Content
            """,
        )
        _write_skill(
            ws,
            "no-slash",
            """\
            ---
            name: no-slash
            description: No slash menu
            user-invocable: false
            ---
            Content
            """,
        )
        return SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")

    def test_model_user_invocation_flags(self, loader_with_skills: SkillsLoader) -> None:
        assert loader_with_skills.is_model_invocable("normal-skill") is True
        assert loader_with_skills.is_model_invocable("model-hidden") is False
        assert loader_with_skills.is_user_invocable("normal-skill") is True
        assert loader_with_skills.is_user_invocable("no-slash") is False

    def test_list_available_skills_filters_model_hidden(self, loader_with_skills: SkillsLoader) -> None:
        skills = loader_with_skills.list_available_skills()
        names = [s["name"] for s in skills]
        assert "normal-skill" in names
        assert "model-hidden" not in names
        assert "no-slash" in names

    def test_list_available_skills_exposes_user_invocable(self, loader_with_skills: SkillsLoader) -> None:
        skills = loader_with_skills.list_available_skills()
        no_slash = next(s for s in skills if s["name"] == "no-slash")
        normal = next(s for s in skills if s["name"] == "normal-skill")
        assert no_slash["user_invocable"] is False
        assert normal["user_invocable"] is True


class TestSkillTypeAndMetadata:
    def test_markdown_and_python_type_detection(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        _write_skill(
            ws,
            "md-skill",
            """\
            ---
            name: md-skill
            description: Markdown skill
            ---
            Content
            """,
        )
        py_dir = _write_skill(
            ws,
            "py-skill",
            """\
            ---
            name: py-skill
            description: Python skill
            ---
            Content
            """,
        )
        (py_dir / "tool.py").write_text("# plugin", encoding="utf-8")

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        skills = {s["name"]: s for s in loader.list_skills(filter_unavailable=False)}
        assert skills["md-skill"]["type"] == "markdown"
        assert skills["py-skill"]["type"] == "python"

    def test_tool_exposable_frontmatter(self, tmp_path: Path) -> None:
        ws = tmp_path / "skills"
        _write_skill(
            ws,
            "tooly",
            """\
            ---
            name: tooly
            description: Tool exposable
            tool_exposable: true
            ---
            Content
            """,
        )

        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no_builtin")
        assert loader.is_tool_exposable("tooly") is True
        assert loader.is_tool_exposable("missing") is False
