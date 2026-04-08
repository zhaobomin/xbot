"""Tests for tool adapter."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.capabilities.tool_adapter import ToolAdapter


class TestToolAdapter:
    """Tests for ToolAdapter."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create a test workspace."""
        return tmp_path

    @pytest.fixture
    def adapter(self, workspace: Path) -> ToolAdapter:
        """Create a tool adapter."""
        return ToolAdapter(str(workspace))

    def test_init(self, adapter: ToolAdapter, workspace: Path) -> None:
        """Test initialization."""
        assert adapter.workspace == workspace
        assert adapter.tools_config is None
        assert adapter.shared_resources == {}

    def test_init_with_config(self, workspace: Path) -> None:
        """Test initialization with config."""
        config = MagicMock()
        resources = {"bus": MagicMock()}
        adapter = ToolAdapter(str(workspace), tools_config=config, shared_resources=resources)

        assert adapter.tools_config == config
        assert adapter.shared_resources == resources

    def test_set_tool_context(self, adapter: ToolAdapter) -> None:
        """Test setting tool context."""
        session_key = "telegram:123:topic:99"
        adapter.set_tool_context(
            channel="telegram",
            chat_id="123",
            session_key=session_key,
            message_id="456",
        )

        # Context is stored under session_key
        assert adapter._tool_context[session_key]["channel"] == "telegram"
        assert adapter._tool_context[session_key]["chat_id"] == "123"
        assert adapter._tool_context[session_key]["session_key"] == session_key
        assert adapter._tool_context[session_key]["message_id"] == "456"

    def test_get_tool_empty(self, adapter: ToolAdapter) -> None:
        """Test getting tool when not registered."""
        result = adapter.get_tool("nonexistent")
        assert result is None

    def test_get_tool_canonical_name(self, adapter: ToolAdapter) -> None:
        """Test that get_tool normalizes tool name."""
        # Should handle both mcp_ prefixed and non-prefixed names
        result = adapter.get_tool("mcp_nonexistent")
        assert result is None

    def test_get_alias(self, adapter: ToolAdapter) -> None:
        """Test get() alias."""
        assert adapter.get("nonexistent") is None

    def test_create_mcp_server_no_sdk(self, workspace: Path) -> None:
        """Test MCP server creation when SDK not available."""
        with patch("xbot.capabilities.tool_adapter.SDK_AVAILABLE", False):
            adapter = ToolAdapter(str(workspace))
            result = adapter.create_mcp_server()
            assert result == {}


class TestToolAdapterRegistration:
    """Tests for tool registration."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create a test workspace."""
        return tmp_path

    def test_register_tools_creates_instances(self, workspace: Path) -> None:
        """Test that _register_xbot_tools creates tool instances."""
        adapter = ToolAdapter(str(workspace))
        adapter._register_xbot_tools()

        # SDK-native tools are intentionally excluded from xbot MCP adapter.
        assert "read_file" not in adapter._tools
        assert "write_file" not in adapter._tools
        assert "exec" not in adapter._tools
        assert "web_search" in adapter._tools
        assert "web_fetch" in adapter._tools
        assert "memory" in adapter._tools

    def test_register_tools_with_message(self, workspace: Path) -> None:
        """Test tool registration with message bus."""
        mock_bus = MagicMock()
        adapter = ToolAdapter(
            str(workspace),
            shared_resources={"bus": mock_bus},
        )
        adapter._register_xbot_tools()

        assert "message" in adapter._tools

    def test_register_tools_with_cron(self, workspace: Path) -> None:
        """Test tool registration with cron service."""
        mock_cron = MagicMock()
        adapter = ToolAdapter(
            str(workspace),
            shared_resources={"cron_service": mock_cron},
        )
        adapter._register_xbot_tools()

        assert "cron" in adapter._tools

    def test_get_tool_after_registration(self, workspace: Path) -> None:
        """Test getting tool after registration."""
        adapter = ToolAdapter(str(workspace))
        adapter._register_xbot_tools()

        tool = adapter.get_tool("web_search")
        assert tool is not None
        assert hasattr(tool, "name")
        assert tool.name == "web_search"

    def test_workspace_restriction_does_not_register_sdk_native_tools(self, workspace: Path) -> None:
        """SDK-native filesystem/shell tools must stay out of xbot adapter."""
        config = MagicMock()
        config.restrict_to_workspace = True
        adapter = ToolAdapter(str(workspace), tools_config=config)
        adapter._register_xbot_tools()
        assert "read_file" not in adapter._tools
        assert "exec" not in adapter._tools

    @pytest.mark.asyncio
    async def test_message_tool_uses_per_session_context(self, workspace: Path) -> None:
        bus = MagicMock()
        bus.publish_outbound = AsyncMock()
        adapter = ToolAdapter(str(workspace), shared_resources={"bus": bus})
        adapter._register_xbot_tools()
        tool = adapter.get_tool("message")

        barrier = asyncio.Event()

        async def _send(session_key: str, chat_id: str) -> None:
            adapter.set_tool_context(
                channel="telegram",
                chat_id=chat_id,
                session_key=session_key,
            )
            barrier.set()
            await barrier.wait()
            await tool.execute(content=f"hello-{chat_id}")

        await asyncio.gather(
            _send("telegram:1", "chat-1"),
            _send("telegram:2", "chat-2"),
        )

        sent = [call.args[0] for call in bus.publish_outbound.await_args_list]
        assert {(msg.channel, msg.chat_id, msg.content) for msg in sent} == {
            ("telegram", "chat-1", "hello-chat-1"),
            ("telegram", "chat-2", "hello-chat-2"),
        }

    @pytest.mark.asyncio
    async def test_cron_tool_uses_per_session_context(self, workspace: Path) -> None:
        cron_service = MagicMock()
        cron_service.add_job.side_effect = lambda **kwargs: SimpleNamespace(
            id=f"job-{kwargs['to']}",
            name=kwargs["name"],
        )
        adapter = ToolAdapter(str(workspace), shared_resources={"cron_service": cron_service})
        adapter._register_xbot_tools()
        tool = adapter.get_tool("cron")

        barrier = asyncio.Event()

        async def _schedule(session_key: str, chat_id: str) -> None:
            adapter.set_tool_context(
                channel="telegram",
                chat_id=chat_id,
                session_key=session_key,
            )
            barrier.set()
            await barrier.wait()
            await tool.execute(action="add", message=f"ping-{chat_id}", every_seconds=60)

        await asyncio.gather(
            _schedule("telegram:1", "chat-1"),
            _schedule("telegram:2", "chat-2"),
        )

        calls = cron_service.add_job.call_args_list
        assert {(call.kwargs["channel"], call.kwargs["to"], call.kwargs["message"]) for call in calls} == {
            ("telegram", "chat-1", "ping-chat-1"),
            ("telegram", "chat-2", "ping-chat-2"),
        }
