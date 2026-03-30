from __future__ import annotations

import importlib
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from xbot.agent.capabilities.catalog import CapabilityCatalog
from xbot.agent.capabilities.skill_parsing import parse_skill_document
from xbot.agent.capabilities.skills_loader import SkillsLoader
from xbot.agent.state.context_mapping import SessionContext, SessionContextManager
from xbot.agent.state.machine import SessionPhase, SessionStateMachine
from xbot.agent.tools.base import Tool
from xbot.agent.tools.registry import ToolRegistry


def _write_skill(root: Path, name: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


class _SampleTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"


def test_parse_skill_document_preserves_python_types() -> None:
    parsed = parse_skill_document(
        "---\n"
        "description: Demo\n"
        "tool_exposable: true\n"
        "count: 2\n"
        "---\n"
        "Body"
    )

    assert parsed.description == "Demo"
    assert parsed.frontmatter["tool_exposable"] is True
    assert parsed.frontmatter["count"] == 2
    assert parsed.body == "Body"


def test_shared_skill_parsing_is_consistent_across_loader_and_catalog(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(
        builtin,
        "demo",
        "---\n"
        "description: Demo\n"
        "tool_exposable: true\n"
        "---\n"
        "### run\n"
        "Do it\n",
    )

    loader = SkillsLoader(tmp_path, builtin_skills_dir=builtin)
    catalog = CapabilityCatalog(tmp_path, builtin_skills_dir=builtin)

    meta = loader.get_skill_metadata("demo")
    assert meta is not None
    assert meta["tool_exposable"] is True
    assert loader._strip_frontmatter(loader.load_skill("demo") or "") == "### run\nDo it"
    assert catalog.skill_tool_names(include_unavailable=True) == {"demo_run"}


def test_skill_tool_names_uses_cache_until_file_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = tmp_path / "skills"
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    _write_skill(
        workspace_skills,
        "weather",
        "---\ndescription: Weather\ntool_exposable: true\n---\n### fetch\nGet weather\n",
    )
    catalog = CapabilityCatalog(tmp_path, builtin_skills_dir=builtin)
    # Warm loader metadata/path discovery so the counter only measures tool-name body parsing.
    assert len(catalog.list_skills(include_unavailable=True)) == 1

    read_calls = {"count": 0}
    original_read_text = Path.read_text

    def counted_read_text(self: Path, *args, **kwargs):
        read_calls["count"] += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    assert catalog.skill_tool_names(include_unavailable=True) == {"weather_fetch"}
    assert catalog.skill_tool_names(include_unavailable=True) == {"weather_fetch"}
    assert read_calls["count"] == 1

    skill_file = workspace_skills / "weather" / "SKILL.md"
    skill_file.write_text(
        "---\ndescription: Weather\ntool_exposable: true\n---\n### update\nUpdate weather\n",
        encoding="utf-8",
    )
    current_stat = skill_file.stat()
    os.utime(skill_file, (current_stat.st_atime, current_stat.st_mtime + 1))

    assert catalog.skill_tool_names(include_unavailable=True) == {"weather_update"}
    assert read_calls["count"] == 2


def test_sdk_import_without_dependency_does_not_warn(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    monkeypatch.delitem(sys.modules, "xbot.agent.capabilities.skill_to_mcp", raising=False)
    monkeypatch.delitem(sys.modules, "xbot.agent.capabilities.tool_adapter", raising=False)

    caplog.set_level(logging.WARNING)

    import xbot.agent.capabilities.skill_to_mcp as skill_to_mcp
    import xbot.agent.capabilities.tool_adapter as tool_adapter

    importlib.reload(skill_to_mcp)
    importlib.reload(tool_adapter)

    assert "claude-agent-sdk not installed" not in caplog.text


def test_tool_registry_read_operations_are_safe_under_concurrency() -> None:
    registry = ToolRegistry()
    errors: list[Exception] = []

    def writer() -> None:
        try:
            for i in range(200):
                registry.register(_SampleTool(f"tool_{i}"))
                if i % 3 == 0:
                    registry.unregister(f"tool_{i // 2}")
        except Exception as exc:  # pragma: no cover - failure path assertion
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(200):
                _ = registry.tool_names
                _ = registry.get_definitions()
                _ = registry.get("tool_1")
                _ = registry.has("tool_2")
                _ = len(registry)
        except Exception as exc:  # pragma: no cover - failure path assertion
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(writer), executor.submit(reader), executor.submit(reader), executor.submit(writer)]
        for future in futures:
            future.result()

    assert errors == []


def test_session_context_manager_is_consistent_under_concurrency() -> None:
    manager = SessionContextManager()

    def worker(idx: int) -> None:
        session_key = f"session:{idx}"
        sdk_id = f"sdk:{idx}"
        manager.set_context(session_key, sdk_id, SessionContext("telegram", str(idx)))
        manager.update_sdk_session_id(session_key, f"{sdk_id}:updated")
        assert manager.get_session_key_by_sdk_id(f"{sdk_id}:updated") == session_key

    with ThreadPoolExecutor(max_workers=8) as executor:
        for future in [executor.submit(worker, idx) for idx in range(50)]:
            future.result()

    assert manager.size() == 50
    for idx in range(50):
        assert manager.get_by_session_key(f"session:{idx}") is not None


def test_force_transition_delegates_to_transition() -> None:
    machine = SessionStateMachine()
    machine.transition = MagicMock(return_value=True)

    result = machine.force_transition("session:1", SessionPhase.RUNNING, reason="forced")

    assert result is True
    machine.transition.assert_called_once_with("session:1", SessionPhase.RUNNING, reason="forced", force=True)
