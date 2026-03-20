from __future__ import annotations

from types import SimpleNamespace

import pytest

from xbot.agent.backends.litellm_backend import LiteLLMBackend
from xbot.agent.protocol import AgentContext


@pytest.mark.asyncio
async def test_litellm_backend_streams_progress_and_tool_hints(monkeypatch) -> None:
    backend = LiteLLMBackend()

    async def _process_message(msg, session_key=None, on_progress=None):
        assert session_key == "cli:direct"
        assert on_progress is not None
        await on_progress("Thinking: planning")
        await on_progress('Tool: read_file("README.md")', tool_hint=True)
        return SimpleNamespace(content="done")

    backend.agent_loop = SimpleNamespace(_process_message=_process_message)

    responses = [
        response
        async for response in backend.process(
            AgentContext(
                session_key="cli:direct",
                prompt="hello",
                channel="cli",
                chat_id="direct",
            )
        )
    ]

    assert responses[0].progress_texts == ["Thinking: planning"]
    assert responses[1].tool_hint_text == 'Tool: read_file("README.md")'
    assert responses[2].content == "done"
