"""Tests for agent router."""

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.agent.protocol import AgentBackend, AgentContext, AgentResponse
from xbot.agent.router import AgentRouter, register_default_backends
from xbot.config.schema import AgentsConfig


class MockBackend(AgentBackend):
    """Mock backend for testing."""

    def __init__(self) -> None:
        self._name = "mock"
        self._initialized = False
        self._shutdown_called = False

    @property
    def name(self) -> str:
        return self._name

    async def initialize(
        self, config: AgentsConfig, shared_resources: dict[str, Any]
    ) -> None:
        self._initialized = True

    async def shutdown(self) -> None:
        self._shutdown_called = True

    async def process(self, context: AgentContext) -> AsyncIterator[AgentResponse]:
        yield AgentResponse(content="Mock response")


class TestAgentRouter:
    """Tests for AgentRouter."""

    @pytest.fixture
    def config(self) -> AgentsConfig:
        """Create a test config."""
        return AgentsConfig()  # Uses default claude_sdk

    @pytest.fixture
    def shared_resources(self) -> dict[str, Any]:
        """Create shared resources."""
        return {"workspace": "/tmp/test"}

    @pytest.fixture
    def router_with_mock(self, config: AgentsConfig, shared_resources: dict[str, Any]) -> AgentRouter:
        """Create a router with mock backend registered."""
        original_backends = AgentRouter._backends.copy()
        AgentRouter._backends["claude_sdk"] = MockBackend
        router = AgentRouter(config, shared_resources)
        router._original_backends = original_backends
        return router

    def teardown_method(self, method: Any) -> None:
        """Restore backends after each test."""
        AgentRouter._backends = {}
        register_default_backends()

    def test_init(self, router_with_mock: AgentRouter) -> None:
        """Test router initialization."""
        assert router_with_mock.backend_type == "claude_sdk"
        assert router_with_mock._backend is None
        assert router_with_mock._initialized is False

    def test_backend_raises_before_init(self, router_with_mock: AgentRouter) -> None:
        """Test that accessing backend before init raises."""
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = router_with_mock.backend

    @pytest.mark.asyncio
    async def test_initialize(self, router_with_mock: AgentRouter) -> None:
        """Test backend initialization."""
        await router_with_mock.initialize()
        assert router_with_mock._initialized is True
        assert router_with_mock._backend is not None
        assert router_with_mock._backend._initialized is True

    @pytest.mark.asyncio
    async def test_initialize_twice(self, router_with_mock: AgentRouter) -> None:
        """Test that double initialization is safe."""
        await router_with_mock.initialize()
        await router_with_mock.initialize()  # Should not raise
        assert router_with_mock._initialized is True

    @pytest.mark.asyncio
    async def test_initialize_unknown_backend(self) -> None:
        """Test that unknown backend raises error."""
        AgentRouter._backends = {}
        config = AgentsConfig()  # Uses default claude_sdk
        router = AgentRouter(config, {})
        with pytest.raises(ValueError, match="Unknown agent backend"):
            await router.initialize()

    @pytest.mark.asyncio
    async def test_process(self, router_with_mock: AgentRouter) -> None:
        """Test message processing."""
        context = AgentContext(
            session_key="test",
            prompt="Hello",
            history=[],
        )
        responses = []
        async for response in router_with_mock.process(context):
            responses.append(response)

        assert len(responses) == 1
        assert responses[0].content == "Mock response"

    @pytest.mark.asyncio
    async def test_shutdown(self, router_with_mock: AgentRouter) -> None:
        """Test router shutdown."""
        await router_with_mock.initialize()
        backend = router_with_mock._backend
        await router_with_mock.shutdown()

        assert router_with_mock._backend is None
        assert router_with_mock._initialized is False
        assert backend._shutdown_called is True

    @pytest.mark.asyncio
    async def test_shutdown_without_init(self, router_with_mock: AgentRouter) -> None:
        """Test shutdown without initialization is safe."""
        await router_with_mock.shutdown()  # Should not raise

    def test_register_backend(self) -> None:
        """Test registering a backend."""
        AgentRouter.register_backend("test_backend", MockBackend)
        assert "test_backend" in AgentRouter._backends

    def test_get_available_backends(self, router_with_mock: AgentRouter) -> None:
        """Test getting available backends."""
        backends = AgentRouter.get_available_backends()
        assert "claude_sdk" in backends


class TestRegisterDefaultBackends:
    """Tests for register_default_backends function."""

    def teardown_method(self, method: Any) -> None:
        """Restore backends after each test."""
        AgentRouter._backends = {}
        register_default_backends()

    def test_registers_claude_sdk(self) -> None:
        """Test that Claude SDK backend is registered."""
        AgentRouter._backends = {}

        register_default_backends()
        assert "claude_sdk" in AgentRouter._backends