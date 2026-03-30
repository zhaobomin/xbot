from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from xbot_codex.codex.transport import CodexEvent
from xbot_codex.config import ServiceConfig
from xbot_codex.events import InboundMessage
from xbot_codex.runtime import CodexRuntime
from xbot_codex.session.store import SessionStore


class ErrorTransport:
    async def run_prompt(
        self,
        session_key: str,
        prompt: str,
        *,
        model: str | None,
        mode: str | None,
        profile: str | None,
        workdir: str,
    ) -> AsyncIterator[CodexEvent]:
        yield CodexEvent(type="error", content="codex failed")

    async def interrupt(self, session_key: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_runtime_surfaces_error_events_and_marks_session_error() -> None:
    runtime = CodexRuntime(
        config=ServiceConfig(),
        session_store=SessionStore(default_workdir_root="/tmp/xbot-codex"),
        transport=ErrorTransport(),
    )

    outbound = [
        m
        async for m in runtime.handle_message(
            InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="hello")
        )
    ]

    session = runtime.session_store.get("telegram:1")
    assert outbound[-1].metadata["event_type"] == "error"
    assert "codex failed" in outbound[-1].content
    assert session is not None
    assert session.process_state == "error"
    assert session.last_error == "codex failed"
