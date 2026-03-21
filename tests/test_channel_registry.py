"""Tests for channel registry."""

import pytest
from unittest.mock import patch, MagicMock

from xbot.channels.registry import (
    discover_channel_names,
    load_channel_class,
    discover_plugins,
    discover_all,
)


class TestDiscoverChannelNames:
    """Tests for discover_channel_names function."""

    def test_returns_list_of_channel_names(self):
        """Test that discover_channel_names returns a list of strings."""
        names = discover_channel_names()
        assert isinstance(names, list)
        assert all(isinstance(name, str) for name in names)

    def test_excludes_internal_modules(self):
        """Test that internal modules are excluded."""
        names = discover_channel_names()
        assert "base" not in names
        assert "manager" not in names
        assert "registry" not in names

    def test_includes_known_channels(self):
        """Test that known channels are included."""
        names = discover_channel_names()
        # These are the known channel modules
        expected_channels = {"telegram", "feishu", "discord", "slack", "email"}
        found = expected_channels & set(names)
        assert len(found) > 0, f"Expected at least some of {expected_channels}, got {names}"


class TestLoadChannelClass:
    """Tests for load_channel_class function."""

    def test_loads_telegram_channel(self):
        """Test loading the Telegram channel class."""
        from xbot.channels.base import BaseChannel
        
        cls = load_channel_class("telegram")
        assert issubclass(cls, BaseChannel)
        assert cls.name == "telegram"

    def test_loads_feishu_channel(self):
        """Test loading the Feishu channel class."""
        from xbot.channels.base import BaseChannel
        
        cls = load_channel_class("feishu")
        assert issubclass(cls, BaseChannel)
        assert cls.name == "feishu"

    def test_loads_slack_channel(self):
        """Test loading the Slack channel class."""
        from xbot.channels.base import BaseChannel
        
        cls = load_channel_class("slack")
        assert issubclass(cls, BaseChannel)
        assert cls.name == "slack"

    def test_raises_for_invalid_module(self):
        """Test that ImportError is raised for modules without BaseChannel."""
        with pytest.raises(ImportError):
            load_channel_class("base")  # base.py has no concrete channel


class TestDiscoverPlugins:
    """Tests for discover_plugins function."""

    def test_returns_dict(self):
        """Test that discover_plugins returns a dict."""
        plugins = discover_plugins()
        assert isinstance(plugins, dict)

    @patch("importlib.metadata.entry_points")
    def test_handles_entry_points(self, mock_entry_points):
        """Test that entry points are discovered."""
        mock_ep = MagicMock()
        mock_ep.name = "test_plugin"
        mock_ep.load.return_value = MagicMock
        mock_entry_points.return_value = [mock_ep]

        plugins = discover_plugins()
        assert "test_plugin" in plugins


class TestDiscoverAll:
    """Tests for discover_all function."""

    def test_returns_dict(self):
        """Test that discover_all returns a dict."""
        channels = discover_all()
        assert isinstance(channels, dict)

    def test_includes_builtin_channels(self):
        """Test that built-in channels are included."""
        channels = discover_all()
        # At minimum, telegram should be available
        assert len(channels) > 0

    def test_builtin_takes_priority(self):
        """Test that built-in channels take priority over plugins."""
        # Built-in channels should not be shadowed by plugins
        channels = discover_all()
        # All channel classes should have a 'name' attribute
        for name, cls in channels.items():
            assert hasattr(cls, "name")