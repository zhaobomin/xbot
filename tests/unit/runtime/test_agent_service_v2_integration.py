from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from xbot.platform.bus.events import InboundMessage, OutboundMessage
from xbot.platform.bus.queue import InteractionRequest, MessageBus, PermissionRequest
from xbot.runtime.core.protocol import AgentContext, AgentResponse
from xbot.runtime.core.service import AgentService
from xbot.runtime.state import RuntimeSessionRegistry, SessionPhase


class SystemMessage:
    """Fake SDK SystemMessage for process-loop tests."""

    def __init__(
        self,
        *,
        state: str = "idle",
        subtype: str = "session_state_changed",
        data: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> None:
        self.subtype = subtype
        self.data = data if data is not None else {"state": state}
        self.session_id = session_id


class FakeClient:
    def __init__(self, *, events: list[Any], query_error: BaseException | None = None) -> None:
        self._events = list(events)
        self._query_error = query_error

    async def query(self, prompt: str) -> None:
        _ = prompt
        if self._query_error is not None:
            raise self._query_error

    def receive_messages(self):
        async def _gen():
            for item in self._events:
                if isinstance(item, BaseException):
                    raise item
                yield item

        return _gen()

    async def get_server_info(self) -> dict[str, Any]:
        return {}


@dataclass
class _StoredSession:
    key: str
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[tuple[str, str]] = field(default_factory=list)

    def add_message(self, role: str, content: str) -> None:
        self.messages.append((role, content))


class _ConversationStore:
    def __init__(self, *, fail_save: bool = False) -> None:
        self.fail_save = fail_save
        self.saved = 0
        self.sessions: dict[str, _StoredSession] = {}

    def get_or_create(self, key: str) -> _StoredSession:
        if key not in self.sessions:
            self.sessions[key] = _StoredSession(key=key)
        return self.sessions[key]

    def save(self, session: _StoredSession) -> None:
        if self.fail_save:
            raise RuntimeError("save failed")
        self.saved += 1
        self.sessions[session.key] = session


def _make_service(tmp_path) -> tuple[AgentService, RuntimeSessionRegistry]:
    registry = RuntimeSessionRegistry()
    service = AgentService()
    service._initialized = True
    service._shared_resources = {
        "runtime_registry": registry,
        "workspace": str(tmp_path),
        "run_mode": "cli",
    }
    return service, registry


def _make_context(session_key: str = "feishu:c1") -> AgentContext:
    return AgentContext(
        session_key=session_key,
        prompt="hello",
        channel="feishu",
        chat_id="c1",
        media=[],
    )


def _make_message(session_key: str = "feishu:c1") -> InboundMessage:
    return InboundMessage(
        channel="feishu",
        sender_id="u1",
        chat_id="c1",
        content="hello",
        timestamp=datetime.now(),
        metadata={},
        session_key_override=session_key,
    )


async def _drain_outbound(bus: MessageBus) -> list[OutboundMessage]:
    items: list[OutboundMessage] = []
    while not bus.outbound.empty():
        items.append(await bus.consume_outbound())
    return items


@pytest.mark.asyncio
async def test_process_happy_path_to_idle_and_sync_sdk_session(tmp_path) -> None:
    service, registry = _make_service(tmp_path)
    context = _make_context("feishu:c-ok")
    client = FakeClient(events=[SystemMessage(state="idle", session_id="sdk-ok")])

    async def _get_client(session_key: str):
        _ = session_key
        return client

    async def _refresh(session_key: str, c: Any) -> None:
        _ = session_key
        _ = c

    service._get_or_create_client = _get_client  # type: ignore[method-assign]
    service._refresh_session_commands_from_client = _refresh  # type: ignore[method-assign]

    responses = [r async for r in service.process(context)]
    assert responses == []
    assert registry.get_phase("feishu:c-ok") == SessionPhase.IDLE
    assert registry.resolve_sdk_session_id("feishu:c-ok") == "sdk-ok"


@pytest.mark.asyncio
async def test_process_client_acquire_failure_sets_broken(tmp_path) -> None:
    service, registry = _make_service(tmp_path)
    context = _make_context("feishu:c-acq")

    async def _get_client(session_key: str):
        _ = session_key
        raise RuntimeError("connect failed")

    service._get_or_create_client = _get_client  # type: ignore[method-assign]

    responses = [r async for r in service.process(context)]
    assert len(responses) == 1
    assert responses[0].finish_reason == "error"
    assert "connect failed" in responses[0].content
    assert registry.get_phase("feishu:c-acq") == SessionPhase.BROKEN


@pytest.mark.asyncio
async def test_process_query_failure_sets_broken(tmp_path) -> None:
    service, registry = _make_service(tmp_path)
    context = _make_context("feishu:c-query")
    client = FakeClient(events=[], query_error=RuntimeError("query failed"))

    async def _get_client(session_key: str):
        _ = session_key
        return client

    async def _refresh(session_key: str, c: Any) -> None:
        _ = session_key
        _ = c

    service._get_or_create_client = _get_client  # type: ignore[method-assign]
    service._refresh_session_commands_from_client = _refresh  # type: ignore[method-assign]

    responses = [r async for r in service.process(context)]
    assert len(responses) == 1
    assert responses[0].finish_reason == "error"
    assert "query failed" in responses[0].content
    assert registry.get_phase("feishu:c-query") == SessionPhase.BROKEN


@pytest.mark.asyncio
async def test_process_stream_end_before_idle_sets_releasing(tmp_path) -> None:
    service, registry = _make_service(tmp_path)
    context = _make_context("feishu:c-end")
    client = FakeClient(events=[])

    async def _get_client(session_key: str):
        _ = session_key
        return client

    async def _refresh(session_key: str, c: Any) -> None:
        _ = session_key
        _ = c

    service._get_or_create_client = _get_client  # type: ignore[method-assign]
    service._refresh_session_commands_from_client = _refresh  # type: ignore[method-assign]

    responses = [r async for r in service.process(context)]
    assert len(responses) == 1
    assert "stream ended before idle boundary" in responses[0].content.lower()
    assert registry.get_phase("feishu:c-end") == SessionPhase.RELEASING_CLIENT


@pytest.mark.asyncio
async def test_process_pending_permission_goes_waiting_permission(tmp_path) -> None:
    """When a permission request is pending when the stream ends with idle boundary,
    the session should transition to WAITING_PERMISSION."""
    service, registry = _make_service(tmp_path)
    bus = MessageBus()
    service._shared_resources["bus"] = bus
    context = _make_context("feishu:c-pending-perm")
    client = FakeClient(events=[SystemMessage(state="idle")])

    req = PermissionRequest(
        request_id="perm-1",
        session_key="feishu:c-pending-perm",
        channel="feishu",
        chat_id="c1",
        tool_name="Bash",
        tool_input={},
        message="allow?",
    )
    await bus.publish_permission_request(req)

    async def _get_client(session_key: str):
        _ = session_key
        return client

    async def _refresh(session_key: str, c: Any) -> None:
        _ = session_key
        _ = c

    service._get_or_create_client = _get_client  # type: ignore[method-assign]
    service._refresh_session_commands_from_client = _refresh  # type: ignore[method-assign]

    responses = [r async for r in service.process(context)]
    assert responses == []
    assert registry.get_phase("feishu:c-pending-perm") == SessionPhase.WAITING_PERMISSION


@pytest.mark.asyncio
async def test_process_pending_interaction_goes_waiting_interaction(tmp_path) -> None:
    """When an interaction request is pending when the stream ends with idle boundary,
    the session should transition to WAITING_INTERACTION."""
    service, registry = _make_service(tmp_path)
    bus = MessageBus()
    service._shared_resources["bus"] = bus
    context = _make_context("feishu:c-pending-int")
    client = FakeClient(events=[SystemMessage(state="idle")])

    req = InteractionRequest(
        request_id="int-1",
        session_key="feishu:c-pending-int",
        channel="feishu",
        chat_id="c1",
        kind="question",
        prompt="continue?",
    )
    await bus.publish_interaction_request(req)

    async def _get_client(session_key: str):
        _ = session_key
        return client

    async def _refresh(session_key: str, c: Any) -> None:
        _ = session_key
        _ = c

    service._get_or_create_client = _get_client  # type: ignore[method-assign]
    service._refresh_session_commands_from_client = _refresh  # type: ignore[method-assign]

    responses = [r async for r in service.process(context)]
    assert responses == []
    assert registry.get_phase("feishu:c-pending-int") == SessionPhase.WAITING_INTERACTION


@pytest.mark.asyncio
async def test_process_cancelled_error_interrupts_state(tmp_path) -> None:
    service, registry = _make_service(tmp_path)
    context = _make_context("feishu:c-cancel")
    client = FakeClient(events=[], query_error=asyncio.CancelledError())

    async def _get_client(session_key: str):
        _ = session_key
        return client

    async def _refresh(session_key: str, c: Any) -> None:
        _ = session_key
        _ = c

    service._get_or_create_client = _get_client  # type: ignore[method-assign]
    service._refresh_session_commands_from_client = _refresh  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        _ = [r async for r in service.process(context)]

    assert registry.get_phase("feishu:c-cancel") == SessionPhase.RELEASING_CLIENT


@pytest.mark.asyncio
async def test_dispatch_nonrecoverable_error_no_recovery(tmp_path) -> None:
    service, _ = _make_service(tmp_path)
    bus = MessageBus()
    msg = _make_message("feishu:c-dispatch-nr")
    recover_calls = {"count": 0}

    async def fake_process(context: AgentContext) -> AsyncIterator[AgentResponse]:
        _ = context
        yield AgentResponse(content="Error: query failed", finish_reason="error")

    async def fake_recover(session_key: str, *, reason: str) -> bool:
        _ = session_key
        _ = reason
        recover_calls["count"] += 1
        return True

    service.process = fake_process  # type: ignore[method-assign]
    service._attempt_broken_session_recovery = fake_recover  # type: ignore[method-assign]

    await service._dispatch(msg, bus)
    outbound = await _drain_outbound(bus)
    assert recover_calls["count"] == 0
    assert len(outbound) == 1
    assert "query failed" in outbound[0].content


@pytest.mark.asyncio
async def test_dispatch_usage_summary_and_result_are_published(tmp_path) -> None:
    service, _ = _make_service(tmp_path)
    bus = MessageBus()
    msg = _make_message("feishu:c-dispatch-usage")

    async def fake_process(context: AgentContext) -> AsyncIterator[AgentResponse]:
        _ = context
        yield AgentResponse(
            content="done",
            event_type="result",
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    service.process = fake_process  # type: ignore[method-assign]

    await service._dispatch(msg, bus)
    outbound = await _drain_outbound(bus)
    contents = [m.content for m in outbound]
    assert "done" in contents
    assert any("Usage: input 10 tokens, output 5 tokens" in c for c in contents)


@pytest.mark.asyncio
async def test_dispatch_persists_user_and_assistant_messages(tmp_path) -> None:
    service, _ = _make_service(tmp_path)
    bus = MessageBus()
    msg = _make_message("feishu:c-dispatch-store")
    store = _ConversationStore()
    service._shared_resources["conversation_store"] = store

    async def fake_process(context: AgentContext) -> AsyncIterator[AgentResponse]:
        _ = context
        yield AgentResponse(content="assistant answer", event_type="result")

    service.process = fake_process  # type: ignore[method-assign]
    await service._dispatch(msg, bus)

    session = store.get_or_create("im:feishu:c-dispatch-store")
    assert ("user", "hello") in session.messages
    assert ("assistant", "assistant answer") in session.messages
    assert store.saved == 1


@pytest.mark.asyncio
async def test_dispatch_store_save_failure_does_not_break_response(tmp_path) -> None:
    service, _ = _make_service(tmp_path)
    bus = MessageBus()
    msg = _make_message("feishu:c-dispatch-store-fail")
    service._shared_resources["conversation_store"] = _ConversationStore(fail_save=True)

    async def fake_process(context: AgentContext) -> AsyncIterator[AgentResponse]:
        _ = context
        yield AgentResponse(content="assistant answer", event_type="result")

    service.process = fake_process  # type: ignore[method-assign]
    await service._dispatch(msg, bus)

    outbound = await _drain_outbound(bus)
    assert any(m.content == "assistant answer" for m in outbound)


@pytest.mark.asyncio
async def test_dispatch_process_exception_returns_failure_message(tmp_path) -> None:
    service, _ = _make_service(tmp_path)
    bus = MessageBus()
    msg = _make_message("feishu:c-dispatch-ex")

    async def fake_process(context: AgentContext) -> AsyncIterator[AgentResponse]:
        _ = context
        if False:
            yield AgentResponse(content="")
        raise RuntimeError("boom")

    service.process = fake_process  # type: ignore[method-assign]
    await service._dispatch(msg, bus)

    outbound = await _drain_outbound(bus)
    assert len(outbound) == 1
    assert "处理出错: boom" in outbound[0].content


@pytest.mark.asyncio
async def test_interrupt_session_calls_worker_interrupt_and_keeps_worker(tmp_path) -> None:
    service, registry = _make_service(tmp_path)
    session_key = "feishu:c-interrupt"
    mock_client = type("Client", (), {})()
    interrupt_called = False

    async def _interrupt() -> None:
        nonlocal interrupt_called
        interrupt_called = True

    mock_client.interrupt = _interrupt
    worker = service._create_detached_session_worker(
        session_key=session_key,
        client=mock_client,
        channel="feishu",
        chat_id="c1",
    )
    await worker.input_queue.put({"type": "user", "message": {"role": "user", "content": "queued"}})
    service._session_workers[session_key] = worker

    result = await service.interrupt_session(session_key)
    assert result["interrupted"] is True
    assert result["queued_cleared"] == 1
    assert interrupt_called is True
    assert service._session_workers[session_key] is worker
    assert registry.get_phase(session_key) == SessionPhase.IDLE


@pytest.mark.asyncio
async def test_process_direct_recoverable_error_auto_recovers_once(tmp_path) -> None:
    service, _ = _make_service(tmp_path)
    process_calls = {"count": 0}
    recover_calls = {"count": 0}

    async def fake_process(context: AgentContext) -> AsyncIterator[AgentResponse]:
        _ = context
        process_calls["count"] += 1
        if process_calls["count"] == 1:
            yield AgentResponse(
                content=(
                    "Error: SDK stream ended before idle boundary "
                    "for feishu:c1"
                ),
                finish_reason="error",
            )
            return
        yield AgentResponse(content="done", event_type="result")

    async def fake_recovery(session_key: str, *, reason: str) -> bool:
        _ = session_key
        _ = reason
        recover_calls["count"] += 1
        return True

    service.process = fake_process  # type: ignore[method-assign]
    service._attempt_broken_session_recovery = fake_recovery  # type: ignore[method-assign]

    result = await service.process_direct("hello", session_key="feishu:c1")
    assert result == "done"
    assert process_calls["count"] == 2
    assert recover_calls["count"] == 1
