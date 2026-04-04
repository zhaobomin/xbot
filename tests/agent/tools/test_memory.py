"""Tests for Claude-style MemoryTool."""

import asyncio
from pathlib import Path

import pytest

from xbot.agent.tools.memory import MemoryTool
from xbot.memory.memdir.store import MemoryDirStore


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    return tmp_path


class TestMemoryTool:
    def test_list_memories(self, temp_workspace: Path) -> None:
        store = MemoryDirStore(temp_workspace)
        store.create_memory(
            memory_type="user",
            title="Senior backend engineer",
            description="User is experienced in backend systems",
            body="User is experienced in backend systems.",
        )
        tool = MemoryTool(workspace=temp_workspace, memory_store=store)

        result = asyncio.run(tool.execute(action="list"))

        assert "Senior backend engineer" in result
        assert "user" in result

    def test_read_memory_topic(self, temp_workspace: Path) -> None:
        store = MemoryDirStore(temp_workspace)
        path = store.create_memory(
            memory_type="feedback",
            title="Use rg",
            description="Prefer rg for search",
            body="Use rg.\n\n**Why:** Faster.\n**How to apply:** Use rg first.",
        )
        tool = MemoryTool(workspace=temp_workspace, memory_store=store)

        result = asyncio.run(tool.execute(action="read", path=str(path)))

        assert "Prefer rg for search" in result
        assert "**Why:** Faster." in result

    def test_search_memories(self, temp_workspace: Path) -> None:
        store = MemoryDirStore(temp_workspace)
        store.create_memory(
            memory_type="reference",
            title="Latency dashboard",
            description="Grafana latency dashboard for request path work",
            body="grafana/internal/d/api-latency",
        )
        tool = MemoryTool(workspace=temp_workspace, memory_store=store)

        result = asyncio.run(tool.execute(action="search", query="latency dashboard"))

        assert "Latency dashboard" in result
        assert "Grafana latency dashboard" in result

    def test_write_topic(self, temp_workspace: Path) -> None:
        tool = MemoryTool(workspace=temp_workspace)

        result = asyncio.run(
            tool.execute(
                action="write_topic",
                memory_type="project",
                title="Release freeze",
                description="Release freeze starts 2026-04-05",
                content="Release freeze starts 2026-04-05.\n\n**Why:** Release cut.\n**How to apply:** Avoid risky merges.",
            )
        )

        assert "saved" in result.lower()
        index = (temp_workspace / "memory" / "MEMORY.md").read_text(encoding="utf-8")
        assert "Release freeze" in index

    def test_update_topic(self, temp_workspace: Path) -> None:
        store = MemoryDirStore(temp_workspace)
        path = store.create_memory(
            memory_type="project",
            title="Release freeze",
            description="Release freeze starts 2026-04-05",
            body="old",
        )
        tool = MemoryTool(workspace=temp_workspace, memory_store=store)

        result = asyncio.run(
            tool.execute(
                action="update_topic",
                path=str(path),
                content="new body",
                description="Updated release freeze",
            )
        )

        assert "updated" in result.lower()
        assert "new body" in path.read_text(encoding="utf-8")

    def test_delete_topic(self, temp_workspace: Path) -> None:
        store = MemoryDirStore(temp_workspace)
        path = store.create_memory(
            memory_type="reference",
            title="Old dashboard",
            description="old dashboard",
            body="body",
        )
        tool = MemoryTool(workspace=temp_workspace, memory_store=store)

        result = asyncio.run(tool.execute(action="delete_topic", path=str(path)))

        assert "deleted" in result.lower()
        assert not path.exists()
