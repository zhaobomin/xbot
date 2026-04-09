"""Tests for WebUI skill discovery in the current architecture."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from xbot.interfaces.webui.services import ServiceContainer


def _make_container(workspace_path: Path) -> ServiceContainer:
    config = SimpleNamespace(workspace_path=workspace_path)
    return ServiceContainer(
        config=config,
        bus=None,
        agent=None,
        conversation_store=None,
        cron=None,
        heartbeat=None,
    )


def test_list_skills_returns_empty_when_workspace_skills_missing(tmp_path: Path) -> None:
    container = _make_container(tmp_path)
    assert container.list_skills() == []


def test_list_skills_discovers_workspace_skill_markdown_files(tmp_path: Path) -> None:
    (tmp_path / "skills" / "demo-skill").mkdir(parents=True)
    (tmp_path / "skills" / "demo-skill" / "SKILL.md").write_text("# Demo", encoding="utf-8")

    container = _make_container(tmp_path)
    skills = container.list_skills()

    assert len(skills) == 1
    assert skills[0]["name"] == "demo-skill"
    assert skills[0]["source"] == "workspace"
    assert skills[0]["type"] == "skill"
    assert skills[0]["path"].endswith("skills/demo-skill/SKILL.md")


def test_list_skills_ignores_non_skill_files(tmp_path: Path) -> None:
    (tmp_path / "skills" / "a").mkdir(parents=True)
    (tmp_path / "skills" / "a" / "README.md").write_text("not a skill", encoding="utf-8")
    (tmp_path / "skills" / "b").mkdir(parents=True)
    (tmp_path / "skills" / "b" / "SKILL.md").write_text("# Skill B", encoding="utf-8")

    container = _make_container(tmp_path)
    skills = container.list_skills()

    assert [s["name"] for s in skills] == ["b"]
