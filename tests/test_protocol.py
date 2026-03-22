"""Tests for agent protocol definitions."""

import pytest

from xbot.agent.protocol import AgentBackend, AgentContext, AgentResponse


class TestAgentResponse:
    """Tests for AgentResponse dataclass."""

    def test_default_values(self) -> None:
        """Test default values are set correctly."""
        response = AgentResponse(content="Hello")
        assert response.content == "Hello"
        assert response.progress_texts == []
        assert response.tool_calls is None
        assert response.tool_hint_text == ""
        assert response.finish_reason == "stop"
        assert response.usage is None
        assert response.raw_message is None
        assert response.is_delta is False
        assert response.delta_content == ""

    def test_custom_values(self) -> None:
        """Test custom values are set correctly."""
        response = AgentResponse(
            content="Result",
            progress_texts=["Thinking...", "Processing..."],
            tool_calls=[{"name": "test", "args": {}}],
            tool_hint_text="Tool: test",
            finish_reason="tool_use",
            usage={"total_tokens": 100},
        )
        assert response.content == "Result"
        assert len(response.progress_texts) == 2
        assert response.tool_calls is not None
        assert response.finish_reason == "tool_use"
        assert response.usage["total_tokens"] == 100

    def test_delta_response(self) -> None:
        """Test delta/streaming response."""
        response = AgentResponse(
            content="",
            is_delta=True,
            delta_content="Hello",
        )
        assert response.is_delta is True
        assert response.delta_content == "Hello"


class TestAgentContext:
    """Tests for AgentContext dataclass."""

    def test_default_values(self) -> None:
        """Test default values are set correctly."""
        context = AgentContext(session_key="test:123", prompt="Hello")
        assert context.session_key == "test:123"
        assert context.prompt == "Hello"
        assert context.history == []
        assert context.media is None
        assert context.channel == ""
        assert context.chat_id == ""
        assert context.metadata == {}

    def test_custom_values(self) -> None:
        """Test custom values are set correctly."""
        context = AgentContext(
            session_key="telegram:7743853836",
            prompt="What's the weather?",
            history=[{"role": "user", "content": "Hi"}],
            channel="telegram",
            chat_id="7743853836",
            metadata={"source": "test"},
        )
        assert context.session_key == "telegram:7743853836"
        assert len(context.history) == 1
        assert context.channel == "telegram"
        assert context.metadata["source"] == "test"


class TestAgentBackend:
    """Tests for AgentBackend abstract class."""

    def test_is_abstract(self) -> None:
        """Test that AgentBackend cannot be instantiated directly."""
        with pytest.raises(TypeError):
            AgentBackend()

    def test_default_implementations(self) -> None:
        """Test default method implementations."""

        class MockBackend(AgentBackend):
            @property
            def name(self) -> str:
                return "mock"

            async def initialize(self, config, shared_resources):
                pass

            async def process(self, context):
                yield AgentResponse(content="test")

            async def shutdown(self):
                pass

        backend = MockBackend()
        assert backend.name == "mock"

        # Test default implementations
        import asyncio
        assert asyncio.run(backend.execute_tool("test", {})) is None
        asyncio.run(backend.reset_session("test"))
        assert asyncio.run(backend.cancel_session("test")) == 0
        assert backend.get_tools_summary() == ""

    def test_interrupt_session_default(self) -> None:
        """Test default interrupt_session returns False."""
        import asyncio

        class MockBackend(AgentBackend):
            @property
            def name(self) -> str:
                return "mock"

            async def initialize(self, config, shared_resources):
                pass

            async def process(self, context):
                yield AgentResponse(content="test")

            async def shutdown(self):
                pass

        backend = MockBackend()
        result = asyncio.run(backend.interrupt_session("test_session"))
        assert result is False

    def test_compact_session_default(self) -> None:
        """Test default compact_session returns not supported message."""
        import asyncio

        class MockBackend(AgentBackend):
            @property
            def name(self) -> str:
                return "mock"

            async def initialize(self, config, shared_resources):
                pass

            async def process(self, context):
                yield AgentResponse(content="test")

            async def shutdown(self):
                pass

        backend = MockBackend()
        result = asyncio.run(backend.compact_session("test_session"))
        assert result["messages_consolidated"] == 0
        assert result["success"] is True
        assert "not supported" in result["message"].lower()