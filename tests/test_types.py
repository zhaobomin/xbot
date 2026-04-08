"""Tests for agent types."""

from xbot.agent.types import AgentConfig, SessionConfig


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default values are set correctly."""
        config = AgentConfig(
            model="claude-sonnet-4-6",
            system_prompt="You are a helpful assistant.",
        )
        assert config.model == "claude-sonnet-4-6"
        assert config.system_prompt == "You are a helpful assistant."
        assert config.tools == []
        assert config.mcp_servers == {}
        assert config.agents is None

    def test_custom_values(self) -> None:
        """Test custom values are set correctly."""
        config = AgentConfig(
            model="claude-opus-4-6",
            system_prompt="Custom prompt",
            tools=[{"name": "test_tool"}],
            mcp_servers={"server1": {"url": "http://localhost"}},
            agents=[{"name": "researcher", "description": "Research agent"}],
        )
        assert config.model == "claude-opus-4-6"
        assert len(config.tools) == 1
        assert "server1" in config.mcp_servers
        assert config.agents is not None
        assert len(config.agents) == 1


class TestSessionConfig:
    """Tests for SessionConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default values are set correctly."""
        config = SessionConfig(workspace="/tmp/workspace")
        assert config.workspace == "/tmp/workspace"
        assert config.permissions == {}

    def test_custom_permissions(self) -> None:
        """Test custom permissions."""
        config = SessionConfig(
            workspace="/workspace",
            permissions={"read": True, "write": False},
        )
        assert config.permissions["read"] is True
        assert config.permissions["write"] is False
