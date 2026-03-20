from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
from nanobot.agent.protocol import AgentContext, AgentResponse
from nanobot.config.schema import Config
from nanobot.session.manager import SessionManager
from claude_agent_sdk.types import AgentDefinition as SDKAgentDefinition
from claude_agent_sdk.types import (
    AssistantMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)


@pytest.mark.asyncio
async def test_claude_sdk_backend_persists_session_history_and_reset_clears_it(
    tmp_path,
    monkeypatch,
) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"

    sessions = SessionManager(tmp_path)

    class _FakeMemoryConsolidator:
        def __init__(self, *args, **kwargs) -> None:
            self.archive_messages = AsyncMock(return_value=True)
            self.maybe_consolidate_by_tokens = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "nanobot.agent.backends.claude_sdk_backend.MemoryConsolidator",
        _FakeMemoryConsolidator,
    )

    await backend.initialize(
        config.agents,
        {
            "config": config,
            "workspace": tmp_path,
            "session_manager": sessions,
            "provider": MagicMock(),
            "bus": MagicMock(),
            "tools_config": config.tools,
        },
    )

    backend._tool_adapter = None
    backend._build_options = MagicMock(return_value=object())  # type: ignore[method-assign]

    class _FakeClient:
        def __init__(self, options=None):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def query(self, prompt: str, session_id: str) -> None:
            assert prompt == "hello"
            assert session_id == "cli:direct"

        async def receive_response(self):
            yield object()

    monkeypatch.setattr(
        "nanobot.agent.backends.claude_sdk_backend.ClaudeSDKClient",
        _FakeClient,
    )
    backend._convert_message = MagicMock(return_value=AgentResponse(content="world"))  # type: ignore[method-assign]

    results = [
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

    assert [response.content for response in results] == ["world"]

    session = sessions.get_or_create("cli:direct")
    assert [message["role"] for message in session.messages] == ["user", "assistant"]
    assert session.messages[0]["content"] == "hello"
    assert session.messages[1]["content"] == "world"

    await backend.reset_session("cli:direct")

    cleared = sessions.get_or_create("cli:direct")
    assert cleared.messages == []


@pytest.mark.asyncio
async def test_claude_sdk_backend_uses_receive_response_for_single_query(
    tmp_path,
    monkeypatch,
) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"

    await backend.initialize(
        config.agents,
        {
            "config": config,
            "workspace": tmp_path,
            "session_manager": SessionManager(tmp_path),
            "provider": MagicMock(),
            "bus": MagicMock(),
            "tools_config": config.tools,
        },
    )

    backend._tool_adapter = None
    backend._build_options = MagicMock(return_value=object())  # type: ignore[method-assign]

    called = {"receive_response": False}

    class _FakeClient:
        def __init__(self, options=None):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def query(self, prompt: str, session_id: str) -> None:
            return None

        async def receive_response(self):
            called["receive_response"] = True
            yield object()

        async def receive_messages(self):
            raise AssertionError("receive_messages() should not be used for single-query processing")

    monkeypatch.setattr(
        "nanobot.agent.backends.claude_sdk_backend.ClaudeSDKClient",
        _FakeClient,
    )
    backend._convert_message = MagicMock(return_value=AgentResponse(content="ok"))  # type: ignore[method-assign]

    _ = [
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

    assert called["receive_response"] is True


@pytest.mark.asyncio
async def test_claude_sdk_backend_cancel_session_delegates_to_spawn_manager(tmp_path, monkeypatch) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"

    class _FakeMemoryConsolidator:
        def __init__(self, *args, **kwargs) -> None:
            self.archive_messages = AsyncMock(return_value=True)
            self.maybe_consolidate_by_tokens = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "nanobot.agent.backends.claude_sdk_backend.MemoryConsolidator",
        _FakeMemoryConsolidator,
    )

    cancel_mock = AsyncMock(return_value=3)
    spawn_manager = SimpleNamespace(cancel_by_session=cancel_mock)
    tool_adapter = SimpleNamespace(get_tool=lambda name: SimpleNamespace(_manager=spawn_manager) if name == "spawn" else None)

    await backend.initialize(
        config.agents,
        {
            "config": config,
            "workspace": tmp_path,
            "session_manager": SessionManager(tmp_path),
            "provider": MagicMock(),
            "bus": MagicMock(),
            "tools_config": config.tools,
        },
    )
    backend._tool_adapter = tool_adapter

    cancelled = await backend.cancel_session("cli:direct")

    assert cancelled == 3
    cancel_mock.assert_awaited_once_with("cli:direct")


def test_claude_sdk_backend_build_options_converts_native_agents(tmp_path) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"
    config.agents.claude_sdk.agents = {
        "researcher": {
            "description": "Research tasks",
            "prompt": "Investigate the topic",
            "tools": ["exec", "web_search"],
            "model": "sonnet",
        }
    }

    backend._shared_resources = {
        "config": config,
        "workspace": str(tmp_path),
    }
    backend.sdk_config = config.agents.claude_sdk
    backend._skill_converter = None
    backend._tool_adapter = None

    options = backend._build_options()

    assert options.agents is not None
    researcher = options.agents["researcher"]
    assert isinstance(researcher, SDKAgentDefinition)
    assert researcher.tools == ["exec", "web_search"]
    assert researcher.model == "sonnet"


def test_claude_sdk_backend_build_options_uses_env_for_auth_not_extra_args(tmp_path) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"
    config.providers.anthropic.api_base = "https://api.anthropic.com/v1/messages"

    backend._shared_resources = {
        "config": config,
        "workspace": str(tmp_path),
    }
    backend.sdk_config = config.agents.claude_sdk
    backend._skill_converter = None
    backend._tool_adapter = None

    options = backend._build_options()

    assert options.env["ANTHROPIC_API_KEY"] == "test-key"
    assert options.env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert options.extra_args == {}


def test_claude_sdk_backend_convert_message_preserves_thinking_text_and_tool_calls() -> None:
    backend = ClaudeSDKBackend()

    message = AssistantMessage(
        content=[
            ThinkingBlock(thinking="first inspect the repo", signature="sig"),
            TextBlock(text="done"),
            ToolUseBlock(id="tool-1", name="read_file", input={"path": "README.md"}),
        ],
        model="claude-opus",
    )

    response = backend._convert_message(message)

    assert response is not None
    assert response.progress_texts == ["Thinking: first inspect the repo"]
    assert response.content == "done"
    assert response.tool_calls == [
        {"id": "tool-1", "name": "read_file", "input": {"path": "README.md"}, "kind": "tool"}
    ]


def test_claude_sdk_backend_convert_message_maps_task_lifecycle_to_progress() -> None:
    backend = ClaudeSDKBackend()

    started = backend._convert_message(
        TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="task-1",
            description="Planning execution",
            uuid="u1",
            session_id="s1",
        )
    )
    progress = backend._convert_message(
        TaskProgressMessage(
            subtype="task_progress",
            data={},
            task_id="task-1",
            description="Reading files",
            usage={"total_tokens": 1, "tool_uses": 1, "duration_ms": 1},
            uuid="u2",
            session_id="s1",
            last_tool_name="read_file",
        )
    )
    finished = backend._convert_message(
        TaskNotificationMessage(
            subtype="task_notification",
            data={},
            task_id="task-1",
            status="completed",
            output_file="",
            summary="Finished execution",
            uuid="u3",
            session_id="s1",
        )
    )

    assert started is not None
    assert started.progress_texts == ["Running: Planning execution"]

    assert progress is not None
    assert progress.progress_texts == ["Running: Reading files"]
    assert progress.tool_calls == [{"name": "read_file", "input": {}, "kind": "tool"}]

    assert finished is not None
    assert finished.progress_texts == ["Running: Finished execution"]
