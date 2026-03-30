"""Tests for capability_policy module."""

from unittest.mock import MagicMock

import pytest

from xbot.agent.capabilities.policy import CapabilityPolicy, CapabilityResolution
from xbot.agent.capabilities.catalog import CapabilityCatalog


class TestCapabilityResolution:
    """Tests for CapabilityResolution dataclass."""

    def test_create(self) -> None:
        """Test creating a resolution."""
        resolution = CapabilityResolution(allowed=["exec", "read"], dropped=["unknown"])
        assert resolution.allowed == ["exec", "read"]
        assert resolution.dropped == ["unknown"]

    def test_empty(self) -> None:
        """Test empty resolution."""
        resolution = CapabilityResolution(allowed=[], dropped=[])
        assert resolution.allowed == []
        assert resolution.dropped == []


class TestCapabilityPolicy:
    """Tests for CapabilityPolicy."""

    @pytest.fixture
    def mock_catalog(self) -> CapabilityCatalog:
        """Create a mock capability catalog."""
        catalog = MagicMock(spec=CapabilityCatalog)
        catalog.builtin_tool_names.return_value = {"exec", "read", "write", "web_search"}
        catalog.skill_tool_names.return_value = {"weather", "cron"}
        return catalog

    def test_init(self, mock_catalog: CapabilityCatalog) -> None:
        """Test policy initialization."""
        policy = CapabilityPolicy(mock_catalog)
        assert policy.catalog == mock_catalog
        assert policy.mcp_servers == {}

    def test_init_with_mcp_servers(self, mock_catalog: CapabilityCatalog) -> None:
        """Test policy with MCP servers."""
        mcp = {"filesystem": {"command": "mcp-filesystem"}}
        policy = CapabilityPolicy(mock_catalog, mcp_servers=mcp)
        assert policy.mcp_servers == mcp

    def test_available_tool_names_non_sdk(self, mock_catalog: CapabilityCatalog) -> None:
        """Test available tools for a non-SDK backend."""
        policy = CapabilityPolicy(mock_catalog)
        names = policy.available_tool_names("custom")
        assert "exec" in names
        assert "read" in names
        # Skills are not included for non-SDK backends
        assert "weather" not in names

    def test_available_tool_names_claude_sdk(self, mock_catalog: CapabilityCatalog) -> None:
        """Test available tools for claude_sdk backend."""
        policy = CapabilityPolicy(mock_catalog)
        names = policy.available_tool_names("claude_sdk")
        assert "exec" in names
        assert "weather" in names  # Skills included for claude_sdk

    def test_resolve_agent_tools_all_allowed(self, mock_catalog: CapabilityCatalog) -> None:
        """Test resolving all valid tools."""
        policy = CapabilityPolicy(mock_catalog)
        result = policy.resolve_agent_tools(["exec", "read"], backend="custom")
        assert "exec" in result.allowed
        assert "read" in result.allowed
        assert result.dropped == []

    def test_resolve_agent_tools_drops_unknown(self, mock_catalog: CapabilityCatalog) -> None:
        """Test that unknown tools are dropped."""
        policy = CapabilityPolicy(mock_catalog)
        result = policy.resolve_agent_tools(
            ["exec", "unknown_tool", "read"], backend="custom"
        )
        assert "exec" in result.allowed
        assert "read" in result.allowed
        assert "unknown_tool" in result.dropped

    def test_resolve_agent_tools_allows_mcp_tools(self, mock_catalog: CapabilityCatalog) -> None:
        """Test that MCP tools are allowed."""
        policy = CapabilityPolicy(mock_catalog, mcp_servers={"fs": {}})
        result = policy.resolve_agent_tools(["exec", "mcp_fs_read"], backend="custom")
        assert "exec" in result.allowed
        assert "mcp_fs_read" in result.allowed

    def test_resolve_agent_tools_empty_list(self, mock_catalog: CapabilityCatalog) -> None:
        """Test resolving empty tool list."""
        policy = CapabilityPolicy(mock_catalog)
        result = policy.resolve_agent_tools([], backend="custom")
        assert result.allowed == []
        assert result.dropped == []

    def test_resolve_agent_tools_none(self, mock_catalog: CapabilityCatalog) -> None:
        """Test resolving None tool list."""
        policy = CapabilityPolicy(mock_catalog)
        result = policy.resolve_agent_tools(None, backend="custom")
        assert result.allowed == []
        assert result.dropped == []

    def test_build_backend_trace_non_sdk(self, mock_catalog: CapabilityCatalog) -> None:
        """Test building trace for a non-SDK backend."""
        policy = CapabilityPolicy(mock_catalog)
        trace = policy.build_backend_trace("custom")
        assert "builtin_tools=4" in trace
        assert "skill_tools=0" in trace
        assert "mcp_servers=0" in trace

    def test_build_backend_trace_claude_sdk(self, mock_catalog: CapabilityCatalog) -> None:
        """Test building trace for claude_sdk."""
        policy = CapabilityPolicy(mock_catalog)
        trace = policy.build_backend_trace("claude_sdk")
        assert "builtin_tools=4" in trace
        assert "skill_tools=2" in trace  # Skills counted for claude_sdk

    def test_build_backend_trace_with_mcp(self, mock_catalog: CapabilityCatalog) -> None:
        """Test building trace with MCP servers."""
        policy = CapabilityPolicy(mock_catalog, mcp_servers={"fs": {}, "git": {}})
        trace = policy.build_backend_trace("custom")
        assert "mcp_servers=2" in trace
