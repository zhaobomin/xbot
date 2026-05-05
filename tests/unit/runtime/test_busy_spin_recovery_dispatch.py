from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import pytest

from xbot.platform.bus.events import InboundMessage
from xbot.platform.bus.queue import MessageBus
from xbot.runtime.core.protocol import AgentContext, AgentResponse
from xbot.runtime.core.service import AgentService
from xbot.runtime.state import RuntimeSessionRegistry


def _build_service(tmp_path) -> AgentService:
    service = AgentService()
    service._shared_resources = {
        "runtime_registry": RuntimeSessionRegistry(),
        "workspace": str(tmp_path),
        "run_mode": "cli",
    }
    service._commands_loader = None
    service._initialized = True
    return service


def _build_message() -> InboundMessage:
    return InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="c1",
        content="hello",
        timestamp=datetime.now(),
        metadata={},
    )


async def _drain_outbound(bus: MessageBus) -> list[str]:
    items: list[str] = []
    while not bus.outbound.empty():
        msg = await bus.consume_outbound()
        items.append(msg.content)
    return items


@pytest.mark.asyncio
async def test_dispatch_recovers_once_then_succeeds(tmp_path) -> None:
    service = _build_service(tmp_path)
    bus = MessageBus()
    msg = _build_message()

    process_calls = {"count": 0}
    recovery_calls = {"count": 0}

    async def fake_process(context: AgentContext) -> AsyncIterator[AgentResponse]:
        _ = context
        process_calls["count"] += 1
        if process_calls["count"] == 1:
            yield AgentResponse(
                content=(
                    "Error: [AgentService] SDK stream ended before idle boundary "
                    "for feishu:c1 after 8 messages"
                ),
                finish_reason="error",
            )
            return
        yield AgentResponse(content="ok", event_type="result")

    async def fake_recovery(session_key: str, *, reason: str) -> bool:
        _ = session_key
        _ = reason
        recovery_calls["count"] += 1
        return True

    service.process = fake_process  # type: ignore[method-assign]
    service._attempt_broken_session_recovery = fake_recovery  # type: ignore[method-assign]

    await service._dispatch(msg, bus)

    outbound = await _drain_outbound(bus)
    assert process_calls["count"] == 2
    assert recovery_calls["count"] == 1
    assert outbound == ["ok"]


@pytest.mark.asyncio
async def test_dispatch_stream_error_does_not_retry_forever(tmp_path) -> None:
    service = _build_service(tmp_path)
    bus = MessageBus()
    msg = _build_message()

    process_calls = {"count": 0}
    recovery_calls = {"count": 0}

    async def fake_process(context: AgentContext) -> AsyncIterator[AgentResponse]:
        _ = context
        process_calls["count"] += 1
        yield AgentResponse(
            content=(
                "Error: [AgentService] SDK stream ended before idle boundary "
                "before idle boundary for feishu:c1 after 8 messages"
            ),
            finish_reason="error",
        )

    async def fake_recovery(session_key: str, *, reason: str) -> bool:
        _ = session_key
        _ = reason
        recovery_calls["count"] += 1
        return True

    service.process = fake_process  # type: ignore[method-assign]
    service._attempt_broken_session_recovery = fake_recovery  # type: ignore[method-assign]

    await service._dispatch(msg, bus)

    outbound = await _drain_outbound(bus)
    # First failure + one auto retry, then stop.
    assert process_calls["count"] == 2
    assert recovery_calls["count"] == 1
    assert len(outbound) == 1
    assert "stream ended before idle boundary" in outbound[0]


@pytest.mark.asyncio
async def test_dispatch_no_retry_when_recovery_fails(tmp_path) -> None:
    service = _build_service(tmp_path)
    bus = MessageBus()
    msg = _build_message()

    process_calls = {"count": 0}
    recovery_calls = {"count": 0}

    async def fake_process(context: AgentContext) -> AsyncIterator[AgentResponse]:
        _ = context
        process_calls["count"] += 1
        yield AgentResponse(
            content="Error: Missing idle boundary for feishu:c1",
            finish_reason="error",
        )

    async def fake_recovery(session_key: str, *, reason: str) -> bool:
        _ = session_key
        _ = reason
        recovery_calls["count"] += 1
        return False

    service.process = fake_process  # type: ignore[method-assign]
    service._attempt_broken_session_recovery = fake_recovery  # type: ignore[method-assign]

    await service._dispatch(msg, bus)

    outbound = await _drain_outbound(bus)
    assert process_calls["count"] == 1
    assert recovery_calls["count"] == 1
    assert len(outbound) == 1
    assert "Missing idle boundary" in outbound[0]
