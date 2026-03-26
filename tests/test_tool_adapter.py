"""Tests for tool adapter."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xbot.agent.tool_adapter import ToolAdapter


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
        with patch("xbot.agent.tool_adapter.SDK_AVAILABLE", False):
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

        # Check that basic tools are registered
        assert "read_file" in adapter._tools
        assert "write_file" in adapter._tools
        assert "exec" in adapter._tools
        assert "web_search" in adapter._tools

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

        tool = adapter.get_tool("read_file")
        assert tool is not None
        assert hasattr(tool, "name")
        assert tool.name == "read_file"

    def test_workspace_restriction(self, workspace: Path) -> None:
        """Test workspace restriction is applied."""
        config = MagicMock()
        config.restrict_to_workspace = True

        adapter = ToolAdapter(str(workspace), tools_config=config)
        adapter._register_xbot_tools()

        # Tools should be created with workspace restriction
        assert adapter._tools["read_file"]._allowed_dir == workspace
