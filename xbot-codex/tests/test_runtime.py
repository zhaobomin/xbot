from collections.abc import AsyncIterator

import pytest

from xbot_codex.codex.transport import CodexEvent
from xbot_codex.config import CodexConfig, RuntimeConfig, ServiceConfig
from xbot_codex.events import InboundMessage
from xbot_codex.runtime import CodexRuntime
from xbot_codex.session.store import SessionStore


class StubTransport:
    def __init__(self, events: list[CodexEvent] | None = None):
        self.events = events or []
        self.interrupted: list[str] = []
        self.started: list[tuple[str, str]] = []

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
        self.started.append((session_key, prompt))
        for event in self.events:
            yield event

    async def interrupt(self, session_key: str) -> bool:
        self.interrupted.append(session_key)
        return True


def make_runtime(transport: StubTransport | None = None) -> CodexRuntime:
    config = ServiceConfig(
        codex=CodexConfig(binary_path="codex", workdir_root="/tmp/xbot-codex"),
        runtime=RuntimeConfig(),
    )
    return CodexRuntime(
        config=config,
        session_store=SessionStore(default_workdir_root="/tmp/xbot-codex"),
        transport=transport or StubTransport(),
    )


def test_runtime_uses_isolated_codex_home_by_default() -> None:
    config = ServiceConfig(
        codex=CodexConfig(binary_path="codex", workdir_root="/tmp/xbot-codex"),
        runtime=RuntimeConfig(),
    )
    runtime = CodexRuntime(
        config=config,
        session_store=SessionStore(default_workdir_root="/tmp/xbot-codex"),
    )

    assert runtime.transport.env["HOME"] == "/tmp/xbot-codex/codex-home"
    assert runtime.transport.env["CODEX_HOME"] == "/tmp/xbot-codex/codex-home"


def test_runtime_passes_proxy_env_to_transport() -> None:
    config = ServiceConfig(
        codex=CodexConfig(
            binary_path="codex",
            workdir_root="/tmp/xbot-codex",
            proxy="http://127.0.0.1:7890",
        ),
        runtime=RuntimeConfig(),
    )
    runtime = CodexRuntime(
        config=config,
        session_store=SessionStore(default_workdir_root="/tmp/xbot-codex"),
    )

    assert runtime.transport.env["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert runtime.transport.env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert runtime.transport.env["ALL_PROXY"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_runtime_handles_help_command_without_transport() -> None:
    runtime = make_runtime()
    msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="!help")

    outbound = [m async for m in runtime.handle_message(msg)]

    assert len(outbound) == 1
    assert "!new" in outbound[0].content
    assert outbound[0].channel == "telegram"


@pytest.mark.asyncio
async def test_runtime_stop_command_interrupts_active_session() -> None:
    transport = StubTransport()
    runtime = make_runtime(transport)
    runtime.session_store.get_or_create("telegram", "1").process_state = "running"

    outbound = [
        m
        async for m in runtime.handle_message(
            InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="!stop")
        )
    ]

    assert transport.interrupted == ["telegram:1"]
    assert "Stopped" in outbound[0].content


@pytest.mark.asyncio
async def test_runtime_maps_codex_stream_to_outbound_messages() -> None:
    transport = StubTransport(
        [
            CodexEvent(type="message.delta", delta="hel"),
            CodexEvent(type="message.delta", delta="lo"),
            CodexEvent(type="message.final", content="hello"),
        ]
    )
    runtime = make_runtime(transport)

    outbound = [
        m
        async for m in runtime.handle_message(
            InboundMessage(
                channel="feishu",
                sender_id="u1",
                chat_id="chat-1",
                content="hello",
                metadata={"message_id": "mid-1", "chat_type": "p2p"},
            )
        )
    ]

    assert [m.metadata.get("event_type") for m in outbound] == ["message.final"]
    assert outbound[0].content == "hello"
    assert outbound[0].metadata["message_id"] == "mid-1"
    assert outbound[0].metadata["chat_type"] == "p2p"
    assert transport.started == [("feishu:chat-1", "hello")]


@pytest.mark.asyncio
async def test_runtime_flushes_aggregated_delta_when_no_final_event() -> None:
    transport = StubTransport(
        [
            CodexEvent(type="message.delta", delta="hel"),
            CodexEvent(type="message.delta", delta="lo"),
        ]
    )
    runtime = make_runtime(transport)

    outbound = [
        m
        async for m in runtime.handle_message(
            InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="hello")
        )
    ]

    assert len(outbound) == 1
    assert outbound[0].metadata.get("event_type") == "message.final"
    assert outbound[0].content == "hello"


@pytest.mark.asyncio
async def test_runtime_model_command_updates_session_model() -> None:
    runtime = make_runtime()

    outbound = [
        m
        async for m in runtime.handle_message(
            InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="!model gpt-5-codex")
        )
    ]

    session = runtime.session_store.get("telegram:1")
    assert session is not None
    assert session.codex_model == "gpt-5-codex"
    assert "gpt-5-codex" in outbound[0].content
