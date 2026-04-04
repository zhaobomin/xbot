from __future__ import annotations

from pathlib import Path

import pytest

from xbot.memory.integration.service import MemoryService
from xbot.memory.memdir.store import MemoryDirStore
from xbot.providers.base import LLMResponse, ToolCallRequest


class _NoAuxBackend:
    pass


class _MaliciousAuxBackend:
    async def call_for_auxiliary(self, **kwargs):
        _ = kwargs
        return LLMResponse(
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
                                "path": "/tmp/not-allowed.md",
                            }
                        ]
                    },
                )
            ],
        )


@pytest.mark.asyncio
async def test_memory_service_remember_creates_memory_without_aux_backend(tmp_path: Path) -> None:
    service = MemoryService(tmp_path, backend=_NoAuxBackend())

    result = await service.remember("Remember that the release freeze starts on 2026-04-05.", "cli:direct")

    assert "Saved memory topic" in result
    store = MemoryDirStore(tmp_path)
    index = store.load_index_for_prompt()
    assert "release freeze starts on 2026-04-05" in index.lower()


@pytest.mark.asyncio
async def test_memory_service_forget_deletes_matching_memory(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    path = store.create_memory(
        memory_type="project",
        title="Release freeze",
        description="Release freeze starts on 2026-04-05",
        body="Freeze starts on 2026-04-05.",
    )
    service = MemoryService(tmp_path, backend=_NoAuxBackend())

    result = await service.forget("release freeze", "cli:direct")

    assert "Deleted 1 memory topic" in result
    assert not path.exists()


def test_memory_service_read_by_topic_name(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    store.create_memory(
        memory_type="feedback",
        title="Use rg",
        description="Prefer rg for searches",
        body="Use rg instead of grep.",
    )
    service = MemoryService(tmp_path)

    result = service.read_memory("Use rg")

    assert "# Use rg" in result
    assert "Prefer rg for searches" in result


def test_memory_service_list_and_search_use_topic_headers(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    store.create_memory(
        memory_type="reference",
        title="Latency dashboard",
        description="Grafana dashboard for API latency",
        body="grafana/internal/d/api-latency",
    )
    service = MemoryService(tmp_path)

    listed = service.list_memories()
    searched = service.search_memories("latency")

    assert "Latency dashboard [reference]" in listed
    assert "Latency dashboard [reference]" in searched


def test_memory_service_read_rejects_non_memory_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("nope", encoding="utf-8")
    service = MemoryService(tmp_path)

    result = service.read_memory(str(outside))

    assert "No memory found" in result


@pytest.mark.asyncio
async def test_memory_service_remember_rejects_auxiliary_ops_outside_memory_dir(tmp_path: Path) -> None:
    service = MemoryService(tmp_path, backend=_MaliciousAuxBackend())

    with pytest.raises(ValueError):
        await service.remember("remember this", "cli:direct")
