from __future__ import annotations

from pathlib import Path

from nanobot.agent.capabilities import CapabilityCatalog, canonical_tool_name
from nanobot.agent.skills import SkillsLoader


def _write_skill(root: Path, name: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_skills_loader_uses_workspace_then_dot_nanobot_then_builtin(tmp_path) -> None:
    workspace_skills = tmp_path / "skills"
    dot_nanobot_skills = tmp_path / ".nanobot" / "skills"
    builtin_skills = tmp_path / "builtin"

    _write_skill(workspace_skills, "shared", "---\ndescription: workspace\n---\nworkspace")
    _write_skill(dot_nanobot_skills, "legacy", "---\ndescription: legacy\n---\nlegacy")
    _write_skill(builtin_skills, "shared", "---\ndescription: builtin shared\n---\nbuiltin")
    _write_skill(builtin_skills, "builtin_only", "---\ndescription: builtin only\n---\nbuiltin only")

    loader = SkillsLoader(tmp_path, builtin_skills_dir=builtin_skills)

    skills = loader.list_skills(filter_unavailable=False)
    names = [skill["name"] for skill in skills]

    assert names == ["shared", "legacy", "builtin_only"]
    assert loader.load_skill("shared") is not None
    assert "workspace" in loader.load_skill("shared")
    assert "legacy" in loader.load_skill("legacy")
    assert "builtin only" in loader.load_skill("builtin_only")


def test_canonical_tool_name_maps_shell_alias_to_exec() -> None:
    assert canonical_tool_name("shell") == "exec"
    assert canonical_tool_name("exec") == "exec"
    assert canonical_tool_name("web_search") == "web_search"


def test_capability_catalog_normalizes_agent_tool_names() -> None:
    normalized = CapabilityCatalog.normalize_tool_names(["shell", "web_search", "exec"])
    assert normalized == ["exec", "web_search"]
