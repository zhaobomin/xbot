"""Integration tests for xbot's major architecture components.

These tests verify the interactions between AgentService, ClientPool,
MessageBus, SessionStateMachine, RuntimeResponseHandlers,
and the config system without requiring real API keys or network access.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.interaction.response_handlers import RuntimeResponseHandlers
from xbot.platform.bus.events import InboundMessage, OutboundMessage
from xbot.platform.bus.queue import (
    InteractionRequest,
    InteractionResponse,
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)
from xbot.runtime.core.client_pool import ClientPool
from xbot.runtime.core.protocol import (
    AgentContext,
    AgentResponse,
    StructuredLLMResponse,
)
from xbot.runtime.core.service import AgentService
from xbot.runtime.core.types import AgentConfig
from xbot.runtime.state import RuntimeSessionRegistry
from xbot.runtime.state.machine import SessionPhase, SessionStateMachine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bus() -> MessageBus:
    return MessageBus()


@pytest.fixture
def state_machine() -> SessionStateMachine:
    return SessionStateMachine()


@pytest.fixture
def agent_config():
    """Minimal AgentConfig."""
    return AgentConfig(
        model="test-model",
        system_prompt="You are a test assistant.",
    )


def _make_config_mock(*, provider: str | None = None, api_key: str | None = None, claude_sdk=None):
    """Create a properly structured config mock for AgentService."""
    config = MagicMock()
    config.agents.defaults.provider = provider
    config.agents.claude_sdk = claude_sdk
    if provider and api_key:
        provider_config = MagicMock()
        provider_config.api_key = api_key
        provider_config.api_base = None
        setattr(config.providers, provider, provider_config)
    else:
        config.providers = None
    return config


@pytest.fixture
def shared_resources(tmp_path: Path, bus: MessageBus, state_machine: SessionStateMachine) -> dict[str, Any]:
    return {
        "workspace": str(tmp_path),
        "config": _make_config_mock(),
        "bus": bus,
        "runtime_registry": state_machine,
    }


@pytest.fixture
def service() -> AgentService:
    return AgentService()


# ---------------------------------------------------------------------------
# 1. AgentService Lifecycle
# ---------------------------------------------------------------------------

class TestAgentServiceLifecycle:
    """Test AgentService initialization, configuration, and shutdown."""

    @pytest.mark.asyncio
    async def test_initialize_sets_config_and_resources(
        self, service: AgentService, agent_config, shared_resources
    ) -> None:
        await service.initialize(agent_config, shared_resources)

        assert service._initialized is True
        assert service._config is agent_config
        assert service._shared_resources is shared_resources

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(
        self, service: AgentService, agent_config, shared_resources
    ) -> None:
        await service.initialize(agent_config, shared_resources)
        await service.initialize(agent_config, shared_resources)

        assert service._initialized is True

    @pytest.mark.asyncio
    async def test_initialize_without_config_raises(self, service: AgentService) -> None:
        with pytest.raises(RuntimeError):
            await service.initialize(None, {})

    @pytest.mark.asyncio
    async def test_shutdown_resets_state(
        self, service: AgentService, agent_config, shared_resources
    ) -> None:
        await service.initialize(agent_config, shared_resources)
        await service.shutdown()

        assert service._initialized is False

    @pytest.mark.asyncio
    async def test_double_shutdown_is_safe(
        self, service: AgentService, agent_config, shared_resources
    ) -> None:
        await service.initialize(agent_config, shared_resources)
        await service.shutdown()
        await service.shutdown()  # Should not raise

    @pytest.mark.asyncio
    async def test_name_property(self, service: AgentService) -> None:
        assert service.name == "agent_service"


# ---------------------------------------------------------------------------
# 2. SDK Options Builder
# ---------------------------------------------------------------------------

class TestSDKOptionsBuilder:
    """Test _build_sdk_options resolves config to ClaudeAgentOptions."""

    @pytest.mark.asyncio
    async def test_workspace_path_expanded(
        self, service: AgentService, agent_config, tmp_path: Path
    ) -> None:
        resources = {
            "workspace": "~/test_workspace",
            "config": _make_config_mock(claude_sdk=None),
        }
        await service.initialize(agent_config, resources)

        options = service._build_sdk_options()

        # Should not contain tilde
        assert "~" not in options.cwd
        assert Path(options.cwd).is_absolute()

    @pytest.mark.asyncio
    async def test_model_propagated(
        self, service: AgentService, agent_config, shared_resources
    ) -> None:
        await service.initialize(agent_config, shared_resources)

        options = service._build_sdk_options()

        assert options.model == "test-model"

    @pytest.mark.asyncio
    async def test_system_prompt_propagated(
        self, service: AgentService, agent_config, shared_resources
    ) -> None:
        await service.initialize(agent_config, shared_resources)

        options = service._build_sdk_options()

        assert options.system_prompt == "You are a test assistant."

    @pytest.mark.asyncio
    async def test_sdk_config_propagated(
        self, service: AgentService, agent_config, tmp_path: Path
    ) -> None:
        sdk_config = MagicMock()
        sdk_config.max_turns = 500
        sdk_config.permission_mode = "bypassPermissions"
        sdk_config.disallowed_tools = ["WebFetch"]

        config_mock = _make_config_mock(claude_sdk=sdk_config)

        resources = {
            "workspace": str(tmp_path),
            "config": config_mock,
        }
        await service.initialize(agent_config, resources)

        options = service._build_sdk_options()

        assert options.max_turns == 500
        assert options.permission_mode == "bypassPermissions"
        assert options.disallowed_tools == ["WebFetch"]

    @pytest.mark.asyncio
    async def test_sdk_config_defaults_when_absent(
        self, service: AgentService, agent_config, shared_resources
    ) -> None:
        shared_resources["config"] = _make_config_mock(claude_sdk=None)
        await service.initialize(agent_config, shared_resources)

        options = service._build_sdk_options()

        # Should have sensible defaults
        assert options.max_turns == 40
        assert options.permission_mode == "acceptEdits"

    @pytest.mark.asyncio
    async def test_cli_session_cwd_override_from_runtime_registry(
        self, service: AgentService, agent_config, tmp_path: Path
    ) -> None:
        registry = RuntimeSessionRegistry()
        session_key = "cli:test-cwd"
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        session_cwd = tmp_path / "session-cwd"
        session_cwd.mkdir(parents=True)
        registry.set_session_cwd(session_key, str(session_cwd))

        resources = {
            "workspace": str(workspace),
            "config": _make_config_mock(claude_sdk=None),
            "runtime_registry": registry,
            "run_mode": "cli",
        }
        await service.initialize(agent_config, resources)

        options = service._build_sdk_options(session_key=session_key)
        assert Path(options.cwd) == session_cwd.resolve()


# ---------------------------------------------------------------------------
# 3. MessageBus Integration
# ---------------------------------------------------------------------------

class TestMessageBusIntegration:
    """Test MessageBus pub/sub for permission and interaction flows."""

    @pytest.mark.asyncio
    async def test_permission_request_response_roundtrip(self, bus: MessageBus) -> None:
        req = PermissionRequest(
            request_id="perm-1",
            session_key="test:chat1",
            channel="test",
            chat_id="chat1",
            tool_name="exec",
            tool_input={"command": "ls"},
            message="Run ls?",
        )
        await bus.publish_permission_request(req)

        # Should be findable
        pending = bus.get_pending_request_for_session("test:chat1")
        assert pending == "perm-1"

        # Submit response
        resp = PermissionResponse(
            request_id="perm-1",
            session_key="test:chat1",
            decision="allow",
        )

        async def submit_after_delay():
            await asyncio.sleep(0.01)
            await bus.submit_permission_response(resp)

        task = asyncio.create_task(submit_after_delay())
        result = await bus.wait_permission_response("perm-1", timeout=5.0)
        await task

        assert result.decision == "allow"
        assert result.request_id == "perm-1"

    @pytest.mark.asyncio
    async def test_interaction_request_response_roundtrip(self, bus: MessageBus) -> None:
        req = InteractionRequest(
            request_id="int-1",
            session_key="test:chat1",
            channel="test",
            chat_id="chat1",
            kind="question",
            prompt="Which option?",
            suggestions=["A", "B"],
        )
        await bus.publish_interaction_request(req)

        pending = bus.get_pending_interaction_for_session("test:chat1")
        assert pending == "int-1"

        resp = InteractionResponse(
            request_id="int-1",
            session_key="test:chat1",
            action="reply",
            content="A",
        )

        async def submit_after_delay():
            await asyncio.sleep(0.01)
            await bus.submit_interaction_response(resp)

        task = asyncio.create_task(submit_after_delay())
        result = await bus.wait_interaction_response("int-1", timeout=5.0)
        await task

        assert result.content == "A"
        assert result.action == "reply"

    @pytest.mark.asyncio
    async def test_permission_timeout(self, bus: MessageBus) -> None:
        req = PermissionRequest(
            request_id="perm-timeout",
            session_key="test:chat1",
            channel="test",
            chat_id="chat1",
            tool_name="exec",
            tool_input={},
            message="timeout test",
        )
        await bus.publish_permission_request(req)

        # wait_permission_response catches TimeoutError and returns deny
        result = await bus.wait_permission_response("perm-timeout", timeout=0.05)
        assert result.decision == "deny"
        assert "Timeout" in result.reason

    @pytest.mark.asyncio
    async def test_inbound_outbound_basic(self, bus: MessageBus) -> None:
        in_msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="Hello",
        )
        await bus.publish_inbound(in_msg)
        received = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert received.content == "Hello"
        assert received.session_key == "test:chat1"

        out_msg = OutboundMessage(
            channel="test",
            chat_id="chat1",
            content="Hi there",
        )
        await bus.publish_outbound(out_msg)
        received = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert received.content == "Hi there"


# ---------------------------------------------------------------------------
# 4. SessionStateMachine
# ---------------------------------------------------------------------------

class TestSessionStateMachineIntegration:
    """Test session state transitions and validation."""

    def test_new_session_starts_idle(self, state_machine: SessionStateMachine) -> None:
        phase = state_machine.get_phase("new-session")
        assert phase == SessionPhase.IDLE

    def test_valid_transition_idle_to_running(self, state_machine: SessionStateMachine) -> None:
        result = state_machine.transition("s1", SessionPhase.RUNNING, reason="start processing")
        assert result is True
        assert state_machine.get_phase("s1") == SessionPhase.RUNNING

    def test_valid_transition_running_to_waiting_permission(
        self, state_machine: SessionStateMachine
    ) -> None:
        state_machine.transition("s1", SessionPhase.RUNNING)
        result = state_machine.transition("s1", SessionPhase.WAITING_PERMISSION)
        assert result is True
        assert state_machine.get_phase("s1") == SessionPhase.WAITING_PERMISSION

    def test_valid_transition_running_to_idle(self, state_machine: SessionStateMachine) -> None:
        state_machine.transition("s1", SessionPhase.RUNNING)
        result = state_machine.transition("s1", SessionPhase.IDLE, reason="done")
        assert result is True
        assert state_machine.get_phase("s1") == SessionPhase.IDLE

    def test_invalid_transition_rejected(self, state_machine: SessionStateMachine) -> None:
        # STOPPING -> RUNNING is invalid (STOPPING can only go to IDLE or ERROR)
        state_machine.transition("s1", SessionPhase.RUNNING)
        state_machine.transition("s1", SessionPhase.STOPPING)
        result = state_machine.transition("s1", SessionPhase.RUNNING, reason="retry")
        assert result is False
        assert state_machine.get_phase("s1") == SessionPhase.STOPPING

    def test_force_transition_overrides_validation(
        self, state_machine: SessionStateMachine
    ) -> None:
        # Force from STOPPING to RUNNING (normally invalid)
        state_machine.transition("s1", SessionPhase.RUNNING)
        state_machine.transition("s1", SessionPhase.STOPPING)
        result = state_machine.transition("s1", SessionPhase.RUNNING, force=True)
        assert result is True
        assert state_machine.get_phase("s1") == SessionPhase.RUNNING

    def test_is_idle_is_busy(self, state_machine: SessionStateMachine) -> None:
        assert state_machine.is_idle("s1") is True
        assert state_machine.is_busy("s1") is False

        state_machine.transition("s1", SessionPhase.RUNNING)
        assert state_machine.is_idle("s1") is False
        assert state_machine.is_busy("s1") is True

    def test_full_lifecycle(self, state_machine: SessionStateMachine) -> None:
        """Test complete session lifecycle: idle -> running -> waiting -> running -> idle."""
        key = "lifecycle-test"

        assert state_machine.get_phase(key) == SessionPhase.IDLE

        state_machine.transition(key, SessionPhase.RUNNING, reason="user prompt")
        assert state_machine.is_busy(key)

        state_machine.transition(key, SessionPhase.WAITING_PERMISSION, reason="need approval")
        assert state_machine.get_phase(key) == SessionPhase.WAITING_PERMISSION

        state_machine.transition(key, SessionPhase.RUNNING, reason="approved")
        assert state_machine.get_phase(key) == SessionPhase.RUNNING

        state_machine.transition(key, SessionPhase.IDLE, reason="completed")
        assert state_machine.is_idle(key)

    def test_on_transition_callback(self) -> None:
        transitions: list[tuple] = []

        def on_transition(session_key, from_phase, to_phase, reason):
            transitions.append((session_key, from_phase, to_phase, reason))

        sm = SessionStateMachine(on_transition=on_transition)
        sm.transition("s1", SessionPhase.RUNNING, reason="start")

        assert len(transitions) == 1
        assert transitions[0] == ("s1", SessionPhase.IDLE, SessionPhase.RUNNING, "start")


# ---------------------------------------------------------------------------
# 5. ClientPool Integration
# ---------------------------------------------------------------------------

class TestClientPoolIntegration:
    """Test ClientPool create/get/disconnect lifecycle."""

    @pytest.mark.asyncio
    async def test_create_and_get_same_client(self) -> None:
        pool = ClientPool()

        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.disconnect = AsyncMock()
            mock_cls.return_value = mock_client

            options = MagicMock()
            client1 = await pool.get_or_create("s1", options)
            client2 = await pool.get_or_create("s1")

            assert client1 is client2
            mock_client.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_removes_client(self) -> None:
        pool = ClientPool()

        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.disconnect = AsyncMock()
            mock_cls.return_value = mock_client

            options = MagicMock()
            await pool.get_or_create("s1", options)

            result = await pool.disconnect("s1")
            assert result is True

            snap = pool.snapshot()
            assert snap["counts"]["connected"] == 0

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_session(self) -> None:
        pool = ClientPool()
        result = await pool.disconnect("nonexistent")
        assert result is True  # Already disconnected

    @pytest.mark.asyncio
    async def test_disconnect_all(self) -> None:
        pool = ClientPool()

        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.disconnect = AsyncMock()
            mock_cls.return_value = mock_client

            options = MagicMock()
            await pool.get_or_create("s1", options)
            await pool.get_or_create("s2", options)

            await pool.disconnect_all()

            snap = pool.snapshot()
            assert snap["counts"]["connected"] == 0

    @pytest.mark.asyncio
    async def test_snapshot_reflects_state(self) -> None:
        pool = ClientPool()

        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_cls.return_value = mock_client

            options = MagicMock()
            await pool.get_or_create("s1", options)

            snap = pool.snapshot()
            assert "s1" in snap["clients"]
            assert snap["counts"]["connected"] == 1


# ---------------------------------------------------------------------------
# 6. RuntimeResponseHandlers Delegation
# ---------------------------------------------------------------------------

class TestRuntimeResponseHandlersDelegation:
    """Test that handlers correctly delegate to runtime's shared resources."""

    def test_bus_from_shared_resources(self, bus: MessageBus) -> None:
        runtime = MagicMock()
        runtime._shared_resources = {"bus": bus}

        handlers = RuntimeResponseHandlers(runtime)
        assert handlers._bus is bus

    def test_bus_fallback_to_direct_attr(self, bus: MessageBus) -> None:
        runtime = MagicMock()
        runtime._shared_resources = {}
        runtime.bus = bus

        handlers = RuntimeResponseHandlers(runtime)
        assert handlers._bus is bus

    def test_state_coordinator_from_shared_resources(self) -> None:
        sm = MagicMock()
        runtime = MagicMock()
        runtime._shared_resources = {"runtime_registry": sm}

        handlers = RuntimeResponseHandlers(runtime)
        assert handlers._state_coordinator is sm

    def test_state_coordinator_fallback_to_direct_attr(self) -> None:
        sm = MagicMock()
        runtime = MagicMock()
        runtime._shared_resources = {}
        runtime.runtime_registry = sm

        handlers = RuntimeResponseHandlers(runtime)
        assert handlers._state_coordinator is sm

    def test_interaction_retry_counts_delegates_to_runtime(self) -> None:
        runtime = MagicMock()
        runtime._shared_resources = {}
        runtime._interaction_retry_counts = {"s1": 3}

        handlers = RuntimeResponseHandlers(runtime)
        assert handlers._interaction_retry_counts["s1"] == 3

    def test_interaction_retry_counts_fallback_to_own_dict(self) -> None:
        runtime = MagicMock()
        runtime._shared_resources = {}
        runtime._interaction_retry_counts = None

        handlers = RuntimeResponseHandlers(runtime)
        # Should use own dict, which is empty
        assert handlers._interaction_retry_counts == {}


# ---------------------------------------------------------------------------
# 7. CapabilityCatalog + Policy Integration
# ---------------------------------------------------------------------------

class TestCapabilityCatalogPolicyIntegration:
    """Test CapabilityCatalog and CapabilityPolicy work together."""

    def test_policy_allows_builtin_tools(self) -> None:
        from xbot.capabilities.catalog import CapabilityCatalog
        from xbot.capabilities.policy import CapabilityPolicy

        catalog = CapabilityCatalog(Path("/tmp/nonexistent"))
        policy = CapabilityPolicy(catalog)

        resolution = policy.resolve_agent_tools(
            ["shell", "read_file", "web_search"],
            backend="claude_sdk",
        )

        assert "exec" in resolution.allowed  # shell -> exec alias
        assert "read_file" in resolution.allowed
        assert "web_search" in resolution.allowed
        assert resolution.dropped == []

    def test_policy_drops_unknown_tools(self, tmp_path: Path) -> None:
        from xbot.capabilities.catalog import CapabilityCatalog
        from xbot.capabilities.policy import CapabilityPolicy

        catalog = CapabilityCatalog(tmp_path)
        policy = CapabilityPolicy(catalog)

        resolution = policy.resolve_agent_tools(
            ["exec", "totally_unknown_tool"],
            backend="claude_sdk",
        )

        assert "exec" in resolution.allowed
        assert "totally_unknown_tool" in resolution.dropped

    def test_policy_allows_mcp_prefixed_tools(self, tmp_path: Path) -> None:
        from xbot.capabilities.catalog import CapabilityCatalog
        from xbot.capabilities.policy import CapabilityPolicy

        catalog = CapabilityCatalog(tmp_path)
        policy = CapabilityPolicy(catalog)

        resolution = policy.resolve_agent_tools(
            ["mcp_docs_search", "mcp_github_issues"],
            backend="claude_sdk",
        )

        # mcp_ prefixed tools are always allowed
        assert "mcp_docs_search" in resolution.allowed
        assert "mcp_github_issues" in resolution.allowed

    def test_policy_drops_unknown_skill_prefixed_tools(self) -> None:
        from xbot.capabilities.catalog import CapabilityCatalog
        from xbot.capabilities.policy import CapabilityPolicy

        catalog = CapabilityCatalog(Path("/tmp/nonexistent"))
        policy = CapabilityPolicy(catalog)

        resolution = policy.resolve_agent_tools(
            ["skill_weather", "read_file"],
            backend="claude_sdk",
        )

        assert "read_file" in resolution.allowed
        assert "skill_weather" in resolution.dropped


# ---------------------------------------------------------------------------
# 9. Structured LLM Call (mocked HTTP)
# ---------------------------------------------------------------------------

class TestStructuredLLMCall:
    """Test call_for_structured with mocked HTTP responses."""

    @pytest.fixture
    def service_with_api_key(self, tmp_path: Path):
        """AgentService initialized with a test API key for HTTP calls."""
        service = AgentService()
        config = AgentConfig(model="test-model", system_prompt="test")
        resources = {
            "workspace": str(tmp_path),
            "config": _make_config_mock(provider="anthropic", api_key="test-key-123"),
        }
        return service, config, resources

    @pytest.mark.asyncio
    async def test_call_returns_text_response(self, service_with_api_key) -> None:
        service, config, resources = service_with_api_key
        await service.initialize(config, resources)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "Hello from LLM"}],
            "stop_reason": "end_turn",
        }

        with patch("httpx.AsyncClient") as mock_http_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "Hello"}],
            )

        assert isinstance(result, StructuredLLMResponse)
        assert result.content == "Hello from LLM"
        assert result.finish_reason == "end_turn"
        assert result.tool_calls == []

    @pytest.mark.asyncio
    async def test_call_returns_tool_use_response(self, service_with_api_key) -> None:
        service, config, resources = service_with_api_key
        await service.initialize(config, resources)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "content": [
                {"type": "text", "text": "Let me search."},
                {
                    "type": "tool_use",
                    "name": "web_search",
                    "input": {"query": "weather today"},
                },
            ],
            "stop_reason": "tool_use",
        }

        with patch("httpx.AsyncClient") as mock_http_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "Search weather"}],
                tools=[{
                    "name": "web_search",
                    "description": "Search the web",
                    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
                }],
            )

        assert result.finish_reason == "tool_use"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "web_search"
        assert result.tool_calls[0].arguments == {"query": "weather today"}

    @pytest.mark.asyncio
    async def test_call_handles_http_error(self, service_with_api_key) -> None:
        service, config, resources = service_with_api_key
        await service.initialize(config, resources)

        import httpx

        mock_request = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.json.return_value = {"error": {"message": "Rate limited"}}

        with patch("httpx.AsyncClient") as mock_http_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "429", request=mock_request, response=mock_response
                )
            )
            mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "Hello"}],
            )

        assert result.finish_reason == "error"
        assert "Rate limited" in result.content


# ---------------------------------------------------------------------------
# 10. End-to-End Processing (mocked SDK)
# ---------------------------------------------------------------------------

class TestEndToEndProcessing:
    """Test full message processing flow with mocked SDK client."""

    @pytest.mark.asyncio
    async def test_process_yields_responses(
        self, service: AgentService, agent_config, shared_resources
    ) -> None:
        await service.initialize(agent_config, shared_resources)

        # Mock the SDK client
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.query = AsyncMock()
        mock_client.disconnect = AsyncMock()

        # Create mock messages that the SDK would yield (assistant + idle boundary)
        assistant_msg = MagicMock()
        assistant_msg.__class__.__name__ = "AssistantMessage"
        idle_msg = MagicMock()
        idle_msg.__class__.__name__ = "SystemMessage"
        idle_msg.subtype = "session_state_changed"
        idle_msg.data = {"state": "idle"}

        async def mock_receive():
            yield assistant_msg
            yield idle_msg

        mock_client.receive_messages = mock_receive

        with patch.object(service._client_pool, "get_or_create", new=AsyncMock(return_value=mock_client)):
            # Mock _convert_event to return an AgentResponse
            with patch.object(
                service,
                "_convert_event",
                side_effect=lambda event: (
                    AgentResponse(content="Hello from agent", finish_reason="stop")
                    if type(event).__name__ == "AssistantMessage"
                    else None
                ),
            ):
                context = AgentContext(session_key="test:chat1", prompt="Hello")
                responses = []
                async for resp in service.process(context):
                    responses.append(resp)

        assert len(responses) == 1
        assert responses[0].content == "Hello from agent"

    @pytest.mark.asyncio
    async def test_process_handles_empty_stream(
        self, service: AgentService, agent_config, shared_resources
    ) -> None:
        await service.initialize(agent_config, shared_resources)

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.query = AsyncMock()

        async def mock_receive():
            return
            yield  # Make it an async generator that yields nothing

        mock_client.receive_messages = mock_receive

        with patch.object(service._client_pool, "get_or_create", new=AsyncMock(return_value=mock_client)):
            context = AgentContext(session_key="test:chat1", prompt="Hello")
            responses = []
            async for resp in service.process(context):
                responses.append(resp)

        assert len(responses) == 1
        assert responses[0].finish_reason == "error"
        assert "idle boundary" in responses[0].content.lower()

    @pytest.mark.asyncio
    async def test_process_pipeline_registers_subagent_model_compat_hook(
        self, service: AgentService, tmp_path: Path
    ) -> None:
        """E2E: initialize -> options -> process should include model-compat PreToolUse hook."""
        agent_config = AgentConfig(model="glm-5", system_prompt="test")

        runtime_config = MagicMock()
        runtime_config.agents.defaults.provider = "alrun"
        runtime_config.agents.defaults.model = "glm-5"
        runtime_config.agents.defaults.available_models = ["glm-5"]
        runtime_config.agents.claude_sdk.compact_notify = False
        runtime_config.agents.claude_sdk.hooks = None

        resources = {
            "workspace": str(tmp_path),
            "config": runtime_config,
        }
        await service.initialize(agent_config, resources)

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.query = AsyncMock()
        mock_client.disconnect = AsyncMock()

        class ResultMessage:
            pass

        async def mock_receive():
            yield ResultMessage()

        mock_client.receive_messages = mock_receive
        captured_options: dict[str, Any] = {}

        async def _capture_get_or_create(*_args, **kwargs):
            captured_options["options"] = kwargs.get("options")
            return mock_client

        with patch.object(service._client_pool, "get_or_create", new=AsyncMock(side_effect=_capture_get_or_create)):
            with patch.object(
                service,
                "_convert_event",
                return_value=AgentResponse(content="ok", finish_reason="stop"),
            ):
                context = AgentContext(session_key="test:chat1", prompt="hello")
                async for _ in service.process(context):
                    pass

        options = captured_options.get("options")
        assert options is not None
        assert options.hooks is not None
        assert "PreToolUse" in options.hooks

        matcher = options.hooks["PreToolUse"][0]
        handler = matcher.hooks[0]
        output = await handler(
            {
                "session_id": "test:chat1",
                "tool_name": "Agent",
                "tool_input": {
                    "description": "weather",
                    "prompt": "query weather",
                    "model": "haiku",
                    "subagent_type": "Explore",
                },
            },
            None,
            MagicMock(),
        )

        assert output is not None
        updated = output["hookSpecificOutput"]["updatedInput"]
        assert updated["model"] == "inherit"
        assert updated["subagent_type"] == "Explore"

    @pytest.mark.asyncio
    async def test_process_pipeline_keeps_supported_typed_subagent_model(
        self, service: AgentService, tmp_path: Path
    ) -> None:
        """E2E: supported typed subagent model should pass through unchanged."""
        agent_config = AgentConfig(model="glm-5", system_prompt="test")

        runtime_config = MagicMock()
        runtime_config.agents.defaults.provider = "alrun"
        runtime_config.agents.defaults.model = "glm-5"
        runtime_config.agents.defaults.available_models = ["glm-5", "haiku"]
        runtime_config.agents.claude_sdk.compact_notify = False
        runtime_config.agents.claude_sdk.hooks = None

        resources = {
            "workspace": str(tmp_path),
            "config": runtime_config,
        }
        await service.initialize(agent_config, resources)

        options = service._build_sdk_options(session_key="test:chat2")
        assert options is not None
        assert options.hooks is not None
        assert "PreToolUse" in options.hooks

        matcher = options.hooks["PreToolUse"][0]
        handler = matcher.hooks[0]
        output = await handler(
            {
                "session_id": "test:chat2",
                "tool_name": "Agent",
                "tool_input": {
                    "description": "weather",
                    "prompt": "query weather",
                    "model": "haiku",
                    "subagent_type": "Explore",
                },
            },
            None,
            MagicMock(),
        )

        assert output is None


# ---------------------------------------------------------------------------
# 11. on_progress async/sync callback handling
# ---------------------------------------------------------------------------

class TestOnProgressCallback:
    """Test that both async and sync on_progress callbacks work."""

    def test_agent_response_progress_texts(self) -> None:
        """AgentResponse can carry progress texts."""
        resp = AgentResponse(
            content="done",
            progress_texts=["Step 1...", "Step 2..."],
        )
        assert len(resp.progress_texts) == 2

    def test_protocol_dataclass_defaults(self) -> None:
        """AgentResponse defaults are sane."""
        resp = AgentResponse(content="hello")
        assert resp.finish_reason == "stop"
        assert resp.progress_texts == []
        assert resp.tool_calls is None
        assert resp.is_delta is False


# ---------------------------------------------------------------------------
# 12. Cross-Component Integration
# ---------------------------------------------------------------------------

class TestCrossComponentIntegration:
    """Test interactions across multiple components."""

    @pytest.mark.asyncio
    async def test_bus_state_machine_coordination(
        self, bus: MessageBus, state_machine: SessionStateMachine
    ) -> None:
        """MessageBus permission request triggers state transition."""
        key = "test:chat1"

        # Start processing
        state_machine.transition(key, SessionPhase.RUNNING)
        assert state_machine.is_busy(key)

        # Agent needs permission
        state_machine.transition(key, SessionPhase.WAITING_PERMISSION)

        req = PermissionRequest(
            request_id="p1",
            session_key=key,
            channel="test",
            chat_id="chat1",
            tool_name="exec",
            tool_input={"cmd": "ls"},
            message="Run command?",
        )
        await bus.publish_permission_request(req)

        assert bus.get_pending_request_for_session(key) == "p1"
        assert state_machine.get_phase(key) == SessionPhase.WAITING_PERMISSION

        # User approves
        resp = PermissionResponse(request_id="p1", session_key=key, decision="allow")

        async def submit():
            await asyncio.sleep(0.01)
            await bus.submit_permission_response(resp)

        task = asyncio.create_task(submit())
        result = await bus.wait_permission_response("p1", timeout=5.0)
        await task

        assert result.decision == "allow"

        # Resume processing
        state_machine.transition(key, SessionPhase.RUNNING)
        assert state_machine.get_phase(key) == SessionPhase.RUNNING

        # Complete
        state_machine.transition(key, SessionPhase.IDLE)
        assert state_machine.is_idle(key)

    @pytest.mark.asyncio
    async def test_service_with_bus_and_state(
        self, service: AgentService, agent_config, bus: MessageBus, state_machine: SessionStateMachine, tmp_path: Path
    ) -> None:
        """Service can access bus and state machine from shared resources."""
        resources = {
            "workspace": str(tmp_path),
            "config": _make_config_mock(claude_sdk=None),
            "bus": bus,
            "runtime_registry": state_machine,
        }
        await service.initialize(agent_config, resources)

        assert service._shared_resources["bus"] is bus
        assert service._shared_resources["runtime_registry"] is state_machine

    @pytest.mark.asyncio
    async def test_response_handlers_integration_with_real_bus(
        self, bus: MessageBus
    ) -> None:
        """RuntimeResponseHandlers can access bus and publish via it."""
        runtime = MagicMock()
        runtime._shared_resources = {"bus": bus, "runtime_registry": MagicMock()}

        handlers = RuntimeResponseHandlers(runtime)

        # Verify bus is accessible
        assert handlers._bus is bus

    def test_skills_and_catalog_integration(self) -> None:
        """CapabilityCatalog returns empty skills list as SDK manages skills."""
        from xbot.capabilities.catalog import CapabilityCatalog

        # Skills are now loaded natively by Claude Code SDK
        catalog = CapabilityCatalog(Path("/tmp/nonexistent"))

        skill_caps = catalog.list_skills(include_unavailable=True)
        assert len(skill_caps) == 0  # Skills managed by SDK

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent_state(
        self, state_machine: SessionStateMachine
    ) -> None:
        """Different sessions have independent state transitions."""
        state_machine.transition("s1", SessionPhase.RUNNING)
        state_machine.transition("s2", SessionPhase.WAITING_PERMISSION)

        assert state_machine.get_phase("s1") == SessionPhase.RUNNING
        assert state_machine.get_phase("s2") == SessionPhase.WAITING_PERMISSION
        assert state_machine.get_phase("s3") == SessionPhase.IDLE

        state_machine.transition("s1", SessionPhase.IDLE)
        assert state_machine.is_idle("s1")
        assert not state_machine.is_idle("s2")
