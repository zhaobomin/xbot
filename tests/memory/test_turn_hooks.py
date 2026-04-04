import asyncio
from pathlib import Path

import pytest

from xbot.memory.integration.turn_hooks import MemoryTurnHooks


class _FakeExtractor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def request_run(
        self,
        session_key: str,
        *,
        messages: list[dict] | None = None,
        direct_memory_write: bool = False,
    ) -> None:
        _ = messages
        self.calls.append((session_key, direct_memory_write))


class _FakeDreamer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def maybe_run(self, session_key: str) -> None:
        self.calls.append(session_key)


@pytest.mark.asyncio
async def test_turn_hooks_trigger_extract_and_auto_dream_for_main_thread(tmp_path: Path) -> None:
    extractor = _FakeExtractor()
    dreamer = _FakeDreamer()
    hooks = MemoryTurnHooks(
        tmp_path,
        extractor=extractor,
        dreamer=dreamer,
        extract_enabled=True,
        auto_dream_enabled=True,
    )

    await hooks.handle_turn_end("telegram:1", is_subagent=False, direct_memory_write=False)
    await asyncio.sleep(0)

    assert extractor.calls == [("telegram:1", False)]
    assert dreamer.calls == ["telegram:1"]


@pytest.mark.asyncio
async def test_turn_hooks_skip_extract_for_subagent(tmp_path: Path) -> None:
    extractor = _FakeExtractor()
    dreamer = _FakeDreamer()
    hooks = MemoryTurnHooks(
        tmp_path,
        extractor=extractor,
        dreamer=dreamer,
        extract_enabled=True,
        auto_dream_enabled=True,
    )

    await hooks.handle_turn_end("telegram:1", is_subagent=True, direct_memory_write=False)
    await asyncio.sleep(0)

    assert extractor.calls == []
    assert dreamer.calls == ["telegram:1"]
