from __future__ import annotations

from pathlib import Path

import pytest

from xbot.memory.integration.api import read_workspace_memory_snapshot
from xbot.memory.integration.context_provider import MemoryContextProvider
from xbot.memory.integration.service import MemoryService
from xbot.memory.integration.turn_hooks import MemoryTurnHooks
from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.workers.extract_memories import ExtractMemoriesWorker


class _NoDreamer:
    async def maybe_run(self, session_key: str) -> None:
        _ = session_key


@pytest.mark.asyncio
async def test_memory_system_end_to_end_flow(tmp_path: Path) -> None:
    service = MemoryService(tmp_path)
    store = MemoryDirStore(tmp_path)
    provider = MemoryContextProvider(tmp_path, memory_store=store)

    remember_result = await service.remember(
        "Remember the latency dashboard is grafana/internal/d/api-latency.",
        "cli:direct",
    )
    assert "Saved memory topic" in remember_result

    fragments = provider.build_fragments("show me the latency dashboard", [])
    assert "Memory Index" in fragments.memory_index
    assert "latency dashboard" in fragments.relevant_memories.lower()

    async def extract_runner(session_key: str, messages: list[dict], direct_memory_write: bool) -> bool:
        _ = (session_key, direct_memory_write)
        store.create_memory(
            memory_type="project",
            title="Release freeze",
            description="Release freeze starts on 2026-04-05",
            body=messages[-1]["content"],
        )
        return True

    hooks = MemoryTurnHooks(
        tmp_path,
        extractor=ExtractMemoriesWorker(tmp_path, runner=extract_runner),
        dreamer=_NoDreamer(),
        extract_enabled=True,
        auto_dream_enabled=True,
    )
    await hooks.handle_turn_end(
        "cli:direct",
        messages=[{"role": "user", "content": "Release freeze starts on 2026-04-05"}],
        is_subagent=False,
        direct_memory_write=False,
    )

    snapshot = read_workspace_memory_snapshot(tmp_path)
    topic_names = {topic["name"] for topic in snapshot["topics"]}
    assert "Release freeze" in topic_names

    forget_result = await service.forget("latency dashboard", "cli:direct")
    assert "Deleted 1 memory topic" in forget_result
    assert "latency dashboard" not in service.list_memories().lower()
