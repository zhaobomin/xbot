from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.agent.capabilities import CapabilityCatalog
from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
from xbot.agent.handoff_policy import HandoffDecision, HandoffPolicy
from xbot.agent.protocol import AgentContext, AgentResponse
from xbot.config.schema import Config
from xbot.config.schema import MCPServerConfig
from xbot.session.manager import SessionManager
from claude_agent_sdk.types import AgentDefinition as SDKAgentDefinition
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
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
        "xbot.agent.backends.claude_sdk_backend.MemoryConsolidator",
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
            self.disconnect = AsyncMock(return_value=None)

        async def connect(self):
            return None

        async def query(self, prompt: str, session_id: str) -> None:
            assert prompt == "hello"
            assert session_id == "cli:direct"

        async def receive_response(self):
            yield object()

    monkeypatch.setattr(
        "xbot.agent.backends.claude_sdk_backend.ClaudeSDKClient",
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
    assert "sdk_session_id" not in cleared.metadata


@pytest.mark.asyncio
async def test_claude_sdk_backend_reuses_connected_client_and_sdk_session_id(
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
        "xbot.agent.backends.claude_sdk_backend.MemoryConsolidator",
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
    created_clients: list["_FakeClient"] = []

    class _FakeClient:
        def __init__(self, options=None):
            self.options = options
            self.query_calls: list[tuple[str, str]] = []
            self.disconnect = AsyncMock(return_value=None)
            self._turn = 0
            created_clients.append(self)

        async def connect(self):
            return None

        async def query(self, prompt: str, session_id: str) -> None:
            self.query_calls.append((prompt, session_id))

        async def receive_response(self):
            self._turn += 1
            if self._turn == 1:
                yield AssistantMessage(content=[TextBlock(text="first")], model="claude")
                yield ResultMessage(
                    subtype="success",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id="sdk-session-1",
                    result="first",
                )
            else:
                yield AssistantMessage(content=[TextBlock(text="second")], model="claude")
                yield ResultMessage(
                    subtype="success",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=2,
                    session_id="sdk-session-1",
                    result="second",
                )

    monkeypatch.setattr(
        "xbot.agent.backends.claude_sdk_backend.ClaudeSDKClient",
        _FakeClient,
    )

    first = [
        response
        async for response in backend.process(
            AgentContext(
                session_key="telegram:42",
                prompt="hello",
                channel="telegram",
                chat_id="42",
            )
        )
    ]
    second = [
        response
        async for response in backend.process(
            AgentContext(
                session_key="telegram:42",
                prompt="follow up",
                channel="telegram",
                chat_id="42",
            )
        )
    ]

    assert [response.content for response in first if response.content] == ["first"]
    assert [response.content for response in second if response.content] == ["second"]
    assert len(created_clients) == 1
    assert created_clients[0].query_calls == [
        ("hello", "telegram:42"),
        ("follow up", "sdk-session-1"),
    ]
    assert sessions.get_or_create("telegram:42").metadata["sdk_session_id"] == "sdk-session-1"


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
            self.disconnect = AsyncMock(return_value=None)

        async def connect(self):
            return None

        async def query(self, prompt: str, session_id: str) -> None:
            return None

        async def receive_response(self):
            called["receive_response"] = True
            yield object()

        async def receive_messages(self):
            raise AssertionError("receive_messages() should not be used for single-query processing")

    monkeypatch.setattr(
        "xbot.agent.backends.claude_sdk_backend.ClaudeSDKClient",
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
        "xbot.agent.backends.claude_sdk_backend.MemoryConsolidator",
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


def test_claude_sdk_backend_build_options_uses_context_builder_system_prompt(tmp_path) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"

    backend._shared_resources = {
        "config": config,
        "workspace": str(tmp_path),
    }
    backend.sdk_config = config.agents.claude_sdk
    backend._skill_converter = None
    backend._tool_adapter = None
    backend._context_builder = SimpleNamespace(
        build_system_prompt=lambda *args, **kwargs: "FULL SYSTEM PROMPT"
    )

    options = backend._build_options()

    assert "FULL SYSTEM PROMPT" in options.system_prompt
    assert "## Runtime Identity" in options.system_prompt


def test_claude_sdk_backend_build_options_injects_runtime_identity_config(tmp_path) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.model = "glm-5"
    config.agents.defaults.provider = "aliyun_coding_plan"
    config.providers.aliyun_coding_plan.api_key = "test-key"

    backend._shared_resources = {
        "config": config,
        "workspace": str(tmp_path),
    }
    backend.sdk_config = config.agents.claude_sdk
    backend._skill_converter = None
    backend._tool_adapter = None
    backend._context_builder = SimpleNamespace(
        build_system_prompt=lambda *args, **kwargs: "FULL SYSTEM PROMPT"
    )

    options = backend._build_options()

    assert "FULL SYSTEM PROMPT" in options.system_prompt
    assert "## Runtime Identity" in options.system_prompt
    assert "- Agent name: `xbot`" in options.system_prompt
    assert "- Agent backend: `claude_sdk`" in options.system_prompt
    assert "- Configured model: `glm-5`" in options.system_prompt
    assert "- Configured provider: `aliyun_coding_plan`" in options.system_prompt
    assert (
        "When the user asks which model, provider, or agent is running, report the configured values above exactly."
        in options.system_prompt
    )


def test_claude_sdk_backend_build_options_appends_handoff_policy_to_system_prompt(tmp_path) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"
    config.agents.claude_sdk.agents = {
        "researcher": {
            "description": "Research specialist",
            "prompt": "Do research",
            "when": "the task requires focused research",
        }
    }

    backend._shared_resources = {
        "config": config,
        "workspace": str(tmp_path),
    }
    backend.sdk_config = config.agents.claude_sdk
    backend._skill_converter = None
    backend._tool_adapter = None
    backend._handoff_policy = HandoffPolicy(config.agents.claude_sdk.agents)
    backend._context_builder = SimpleNamespace(
        build_system_prompt=lambda *args, **kwargs: "FULL SYSTEM PROMPT"
    )

    options = backend._build_options()

    assert "FULL SYSTEM PROMPT" in options.system_prompt
    assert "## Delegation Policy" in options.system_prompt
    assert "`spawn` tool" in options.system_prompt
    assert "researcher" in options.system_prompt


def test_claude_sdk_backend_build_sdk_agents_applies_handoff_prompt_policy(tmp_path) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"
    config.agents.claude_sdk.agents = {
        "researcher": {
            "description": "Research specialist",
            "prompt": "Do research",
            "when": "the task requires focused research",
            "tools": ["shell", "web_search"],
            "model": "sonnet",
        }
    }

    backend._shared_resources = {
        "config": config,
        "workspace": str(tmp_path),
    }
    backend.sdk_config = config.agents.claude_sdk
    backend._handoff_policy = HandoffPolicy(config.agents.claude_sdk.agents)

    agents = backend._build_sdk_agents()

    assert agents is not None
    researcher = agents["researcher"]
    assert isinstance(researcher, SDKAgentDefinition)
    assert researcher.tools == ["exec", "web_search"]
    assert "Use when: the task requires focused research" in researcher.description
    assert "You are a specialist agent invoked by the main xbot agent." in researcher.prompt


def test_claude_sdk_backend_build_sdk_agents_drops_unavailable_tools(tmp_path) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"
    config.agents.claude_sdk.agents = {
        "researcher": {
            "description": "Research specialist",
            "prompt": "Do research",
            "tools": ["shell", "missing_tool"],
        }
    }

    backend._shared_resources = {
        "config": config,
        "workspace": str(tmp_path),
    }
    backend.sdk_config = config.agents.claude_sdk
    backend._capabilities = CapabilityCatalog(tmp_path)
    from xbot.agent.capability_policy import CapabilityPolicy

    backend._capability_policy = CapabilityPolicy(backend._capabilities)
    backend._handoff_policy = HandoffPolicy(config.agents.claude_sdk.agents)

    agents = backend._build_sdk_agents()

    assert agents is not None
    researcher = agents["researcher"]
    assert researcher.tools == ["exec"]
    assert "Dropped unavailable tools: missing_tool" in researcher.description


@pytest.mark.asyncio
async def test_claude_sdk_backend_emits_handoff_activation_trace(tmp_path, monkeypatch) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"
    config.agents.claude_sdk.agents = {
        "researcher": {
            "description": "Research specialist",
            "prompt": "Do research",
        }
    }

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

    class _FakeClient:
        def __init__(self, options=None):
            self.disconnect = AsyncMock(return_value=None)

        async def connect(self):
            return None

        async def query(self, prompt: str, session_id: str) -> None:
            return None

        async def receive_response(self):
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="sdk-session-1",
                result="done",
            )

    monkeypatch.setattr(
        "xbot.agent.backends.claude_sdk_backend.ClaudeSDKClient",
        _FakeClient,
    )

    responses = [
        response
        async for response in backend.process(
            AgentContext(
                session_key="telegram:42",
                prompt="hello",
                channel="telegram",
                chat_id="42",
            )
        )
    ]

    assert responses[0].progress_texts == [
        "Running: delegation policy active (researcher)",
        "Running: handoff policy decided main - no specialist agent strongly matched request",
    ]


def test_claude_sdk_backend_tools_summary_includes_handoff_agents(tmp_path) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"
    config.agents.claude_sdk.agents = {
        "researcher": {
            "description": "Research specialist",
            "prompt": "Do research",
        }
    }

    backend._shared_resources = {"config": config, "workspace": str(tmp_path)}
    backend.sdk_config = config.agents.claude_sdk
    backend._capabilities = CapabilityCatalog(tmp_path)

    summary = backend.get_tools_summary()

    assert "builtin_tools=" in summary
    assert "skill_tools=" in summary
    assert "handoff_agents=researcher" in summary
    assert "connected_sessions=0" in summary
    assert "local_tools=0" in summary


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


def test_claude_sdk_backend_classifies_unknown_tool_as_mcp_when_external_servers_configured(tmp_path) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.tools.mcp_servers = {
        "github": MCPServerConfig(url="https://example.com/mcp")
    }
    backend._shared_resources = {"config": config}
    backend._capabilities = CapabilityCatalog(tmp_path)

    assert backend._classify_tool_name("github_search") == "mcp"


def test_claude_sdk_backend_keeps_unknown_tool_as_tool_without_external_mcp(tmp_path) -> None:
    backend = ClaudeSDKBackend()
    backend._shared_resources = {"config": Config()}
    backend._capabilities = CapabilityCatalog(tmp_path)

    assert backend._classify_tool_name("future_builtin") == "tool"


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


def test_claude_sdk_backend_convert_message_adds_handoff_trace_for_agent_task() -> None:
    backend = ClaudeSDKBackend()
    backend._handoff_policy = HandoffPolicy(
        {"researcher": {"description": "Research specialist", "prompt": "Do research"}}
    )

    started = backend._convert_message(
        TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="task-1",
            description="researcher is handling the task",
            uuid="u1",
            session_id="s1",
            task_type="subagent",
        )
    )

    assert started is not None
    assert started.progress_texts == [
        "Running: researcher is handling the task",
        "Handoff: researcher is handling the task",
    ]


def test_handoff_policy_decide_native_handoff() -> None:
    policy = HandoffPolicy(
        {
            "researcher": {
                "description": "Research specialist",
                "prompt": "Do research",
                "when": "use for research and analysis",
            }
        }
    )

    decision = policy.decide("Please research today's AI launches")

    assert decision == HandoffDecision(
        mode="native_handoff",
        reason="specialist agent matched request",
        candidate_agents=("researcher",),
    )


def test_handoff_policy_decide_background() -> None:
    policy = HandoffPolicy({"researcher": {"description": "Research specialist", "prompt": "Do research"}})

    decision = policy.decide("Please do this in background and report later")

    assert decision.mode == "background"


@pytest.mark.asyncio
async def test_claude_sdk_backend_falls_back_to_main_agent_when_primary_run_fails(tmp_path, monkeypatch) -> None:
    backend = ClaudeSDKBackend()
    config = Config()
    config.agents.type = "claude_sdk"
    config.agents.defaults.workspace = str(tmp_path)
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.api_key = "test-key"
    config.agents.claude_sdk.agents = {
        "researcher": {
            "description": "Research specialist",
            "prompt": "Do research",
            "when": "use for research",
        }
    }

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

    created_clients: list[tuple[object, bool]] = []

    class _FakeClient:
        def __init__(self, options=None):
            self.options = options
            self.disconnect = AsyncMock(return_value=None)
            created_clients.append((self, options.agents is not None))

        async def connect(self):
            return None

        async def query(self, prompt: str, session_id: str) -> None:
            if self.options.agents is not None:
                raise RuntimeError("primary failed")
            self.prompt = prompt

        async def receive_response(self):
            yield AssistantMessage(content=[TextBlock(text="fallback ok")], model="claude")
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="sdk-session-1",
                result="fallback ok",
            )

    monkeypatch.setattr(
        "xbot.agent.backends.claude_sdk_backend.ClaudeSDKClient",
        _FakeClient,
    )

    responses = [
        response
        async for response in backend.process(
            AgentContext(
                session_key="telegram:42",
                prompt="Please research this issue",
                channel="telegram",
                chat_id="42",
            )
        )
    ]

    progress = [text for response in responses for text in response.progress_texts]
    contents = [response.content for response in responses if response.content]

    assert any(text.startswith("Running: handoff policy decided native_handoff") for text in progress)
    assert any(text.startswith("Handoff: fallback to main agent") for text in progress)
    assert contents == ["fallback ok"]
    assert created_clients[0][1] is True
    assert created_clients[1][1] is False
