from __future__ import annotations

from pathlib import Path

from xbot.platform.utils.helpers import load_init_pack, sync_workspace_skill_pack


def test_default_init_pack_includes_weather() -> None:
    pack = load_init_pack("default")
    skills = pack.get("skills", [])
    assert isinstance(skills, list)
    assert "weather" in skills


def test_sync_workspace_skill_pack_copies_weather(tmp_path: Path) -> None:
    added = sync_workspace_skill_pack(tmp_path, "default")

    weather_skill = tmp_path / ".claude" / "skills" / "weather" / "SKILL.md"
    assert weather_skill.exists()
    # Added list may be empty if already exists, but on fresh tmp_path it should include weather
    assert any(item.startswith(".claude/skills/weather") for item in added)
