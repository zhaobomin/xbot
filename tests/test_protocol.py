"""Tests for agent protocol definitions."""


from xbot.agent.protocol import AgentContext, AgentResponse


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
