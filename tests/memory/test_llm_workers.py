from pathlib import Path

import pytest

from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.workers.auto_dream import execute_auto_dream
from xbot.memory.workers.extract_memories import execute_extract_memories
from xbot.memory.workers.operations import apply_memory_operations
from xbot.providers.base import LLMResponse, ToolCallRequest


class _FakeBackend:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def call_for_auxiliary(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.mark.asyncio
async def test_execute_extract_memories_applies_create_update_delete_ops(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    existing = store.create_memory(
        memory_type="feedback",
        title="Use rg",
        description="Prefer rg",
        body="old body",
    )
    response = LLMResponse(
        content=None,
        finish_reason="tool_calls",
        tool_calls=[
            ToolCallRequest(
                id="1",
                name="persist_memories",
                arguments={
                    "operations": [
                        {
                            "action": "update",
                            "path": str(existing),
                            "description": "Prefer rg for search",
                            "content": "new body",
                        },
                        {
                            "action": "create",
                            "memory_type": "project",
                            "title": "Release freeze",
                            "description": "Freeze starts 2026-04-05",
                            "content": "Freeze starts 2026-04-05",
                        },
                    ]
                },
            )
        ],
    )
    backend = _FakeBackend(response)

    ok = await execute_extract_memories(
        backend,
        workspace=tmp_path,
        session_key="telegram:1",
        messages=[{"role": "user", "content": "remember the release freeze starts 2026-04-05"}],
    )

    assert ok is True
    assert "new body" in existing.read_text(encoding="utf-8")
    index = store.load_index_for_prompt()
    assert "Release freeze" in index
    assert backend.calls[0]["tool_choice"]["function"]["name"] == "persist_memories"


@pytest.mark.asyncio
async def test_execute_auto_dream_applies_operations(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    existing = store.create_memory(
        memory_type="reference",
        title="Old dashboard",
        description="old dashboard",
        body="body",
    )
    response = LLMResponse(
        content=None,
        finish_reason="tool_calls",
        tool_calls=[
            ToolCallRequest(
                id="1",
                name="persist_memories",
                arguments={
                    "operations": [
                        {
                            "action": "delete",
                            "path": str(existing),
                        },
                        {
                            "action": "create",
                            "memory_type": "reference",
                            "title": "Latency dashboard",
                            "description": "Grafana latency dashboard for request path work",
                            "content": "grafana/internal/d/api-latency",
                        },
                    ]
                },
            )
        ],
    )
    backend = _FakeBackend(response)

    ok = await execute_auto_dream(
        backend,
        workspace=tmp_path,
        session_key="telegram:1",
    )

    assert ok is True
    assert not existing.exists()
    assert "Latency dashboard" in store.load_index_for_prompt()


def test_apply_memory_operations_rejects_paths_outside_memory_dir(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError):
        apply_memory_operations(
            store,
            [{"action": "delete", "path": str(outside)}],
        )
