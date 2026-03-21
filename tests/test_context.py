"""Tests for context builder."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xbot.agent.context import ContextBuilder


class TestContextBuilder:
    """Tests for ContextBuilder."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create a test workspace."""
        return tmp_path

    @pytest.fixture
    def builder(self, workspace: Path) -> ContextBuilder:
        """Create a context builder."""
        return ContextBuilder(workspace, use_reme=False)

    def test_init(self, builder: ContextBuilder, workspace: Path) -> None:
        """Test initialization."""
        assert builder.workspace == workspace
        assert builder.using_reme is False

    def test_bootstrap_files_constant(self) -> None:
        """Test that bootstrap files are defined."""
        assert "AGENTS.md" in ContextBuilder.BOOTSTRAP_FILES
        assert "SOUL.md" in ContextBuilder.BOOTSTRAP_FILES
        assert "USER.md" in ContextBuilder.BOOTSTRAP_FILES

    def test_build_runtime_context(self) -> None:
        """Test building runtime context."""
        ctx = ContextBuilder._build_runtime_context("telegram", "12345")
        assert "Current Time:" in ctx
        assert "Channel: telegram" in ctx
        assert "Chat ID: 12345" in ctx

    def test_build_runtime_context_no_channel(self) -> None:
        """Test building runtime context without channel."""
        ctx = ContextBuilder._build_runtime_context(None, None)
        assert "Current Time:" in ctx
        assert "Channel:" not in ctx

    def test_get_identity(self, builder: ContextBuilder) -> None:
        """Test getting identity section."""
        identity = builder._get_identity()
        assert "xbot" in identity
        assert "Workspace" in identity

    def test_load_bootstrap_files_empty(self, builder: ContextBuilder) -> None:
        """Test loading bootstrap files when none exist."""
        result = builder._load_bootstrap_files()
        assert result == ""

    def test_load_bootstrap_files_with_content(self, workspace: Path) -> None:
        """Test loading bootstrap files with content."""
        # Create a bootstrap file
        agents_file = workspace / "AGENTS.md"
        agents_file.write_text("# Agent Instructions\n\nBe helpful.")

        builder = ContextBuilder(workspace, use_reme=False)
        result = builder._load_bootstrap_files()

        assert "AGENTS.md" in result
        assert "Agent Instructions" in result

    def test_load_bootstrap_files_multiple(self, workspace: Path) -> None:
        """Test loading multiple bootstrap files."""
        (workspace / "AGENTS.md").write_text("Agent content")
        (workspace / "SOUL.md").write_text("Soul content")

        builder = ContextBuilder(workspace, use_reme=False)
        result = builder._load_bootstrap_files()

        assert "AGENTS.md" in result
        assert "SOUL.md" in result
        assert "Agent content" in result
        assert "Soul content" in result

    def test_build_system_prompt_basic(self, builder: ContextBuilder) -> None:
        """Test building basic system prompt."""
        prompt = builder.build_system_prompt()
        assert "xbot" in prompt
        assert "Workspace" in prompt

    def test_build_user_content_text_only(self, builder: ContextBuilder) -> None:
        """Test building user content with text only."""
        result = builder._build_user_content("Hello world", None)
        assert result == "Hello world"

    def test_build_user_content_no_media(self, builder: ContextBuilder) -> None:
        """Test building user content with empty media list."""
        result = builder._build_user_content("Test", [])
        assert result == "Test"

    def test_build_messages_basic(self, builder: ContextBuilder) -> None:
        """Test building basic messages."""
        messages = builder.build_messages(
            history=[],
            current_message="Hello",
            channel="telegram",
            chat_id="123",
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Hello" in messages[1]["content"]

    def test_build_messages_with_history(self, builder: ContextBuilder) -> None:
        """Test building messages with history."""
        history = [{"role": "user", "content": "Previous message"}]
        messages = builder.build_messages(
            history=history,
            current_message="New message",
        )

        assert len(messages) == 3
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Previous message"

    def test_add_tool_result(self, builder: ContextBuilder) -> None:
        """Test adding tool result."""
        messages = [{"role": "user", "content": "test"}]
        result = builder.add_tool_result(
            messages,
            tool_call_id="123",
            tool_name="exec",
            result="output",
        )

        assert len(result) == 2
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "123"

    def test_add_assistant_message(self, builder: ContextBuilder) -> None:
        """Test adding assistant message."""
        messages = [{"role": "user", "content": "test"}]
        result = builder.add_assistant_message(
            messages,
            content="Response",
        )

        assert len(result) == 2
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Response"

    def test_add_assistant_message_with_tool_calls(self, builder: ContextBuilder) -> None:
        """Test adding assistant message with tool calls."""
        messages = []
        tool_calls = [{"id": "1", "function": {"name": "exec", "arguments": "{}"}}]
        result = builder.add_assistant_message(
            messages,
            content=None,
            tool_calls=tool_calls,
        )

        assert result[0]["role"] == "assistant"
        assert result[0]["tool_calls"] == tool_calls


class TestContextBuilderWithReMe:
    """Tests for ContextBuilder with ReMe."""

    def test_reme_not_available_fallback(self, tmp_path: Path) -> None:
        """Test fallback when ReMe is not available."""
        with patch("xbot.agent.context._REME_AVAILABLE", False):
            builder = ContextBuilder(tmp_path, use_reme=True)
            assert builder.using_reme is False

    def test_reme_disabled(self, tmp_path: Path) -> None:
        """Test with ReMe explicitly disabled."""
        builder = ContextBuilder(tmp_path, use_reme=False)
        assert builder.using_reme is False