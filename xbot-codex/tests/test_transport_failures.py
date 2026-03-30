from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from xbot_codex.codex.transport import CodexEvent, CodexTransport


@pytest.mark.asyncio
async def test_run_prompt_returns_error_event_when_binary_missing(tmp_path: Path) -> None:
    transport = CodexTransport(binary_path=str(tmp_path / "missing-codex"))

    events = [
        event
        async for event in transport.run_prompt(
            "telegram:1",
            "hello",
            model=None,
            mode=None,
            profile=None,
            workdir=str(tmp_path / "wd"),
        )
    ]

    assert events[-1].type == "error"
    assert "missing-codex" in events[-1].content


@pytest.mark.asyncio
async def test_read_events_emits_error_event_for_nonzero_exit() -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = FakeStream([b'{"type":"message.delta","delta":"hi"}\n'])
            self.returncode = 9

        async def wait(self) -> int:
            return self.returncode

    class FakeStream:
        def __init__(self, lines: list[bytes]):
            self._lines = list(lines)

        async def readline(self) -> bytes:
            await asyncio.sleep(0)
            return self._lines.pop(0) if self._lines else b""

    transport = CodexTransport(binary_path="codex")
    events = [event async for event in transport.read_events(FakeProcess())]

    assert events[-1] == CodexEvent(type="error", content="Codex exited with status 9")
