"""Tests for OptionsBuilder skills and plugins methods."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from xbot.agent.backends.options_builder import OptionsBuilder


class TestBuildAddDirs:
    """Test _build_add_dirs method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config = MagicMock()
        self.mock_config.skills.enabled = True
        self.mock_config.skills.dirs = ["$workspace/.claude/skills"]
        self.mock_config.skills.additional_dirs = []
        self.mock_config.agents.defaults.workspace = "/test/workspace"

        self.shared_resource = {"config": self.mock_config}

        self.builder = OptionsBuilder(
            shared_resources=self.shared_resource,
            sdk_config=MagicMock(),
            skill_converter=None,
            tool_adapter=None,
            sessions=None,
            context_builder=None,
            handoff_policy=None,
            capability_policy=None,
        )

    def test_build_add_dirs_returns_workspace(self):
        """Test that workspace root is included."""
        with patch.object(Path, "exists", return_value=True):
            dirs = self.builder._build_add_dirs()
            assert "/test/workspace" in dirs

    def test_build_add_dirs_includes_additional_dirs(self):
        """Test additional_dirs are included."""
        self.mock_config.skills.additional_dirs = ["$workspace/skills"]
        with patch.object(Path, "exists", return_value=True):
            dirs = self.builder._build_add_dirs()
            assert "/test/workspace/skills" in dirs

    def test_build_add_dirs_disabled_returns_empty(self):
        """Test disabled skills returns empty list."""
        self.mock_config.skills.enabled = False
        dirs = self.builder._build_add_dirs()
        assert dirs == []

    def test_build_add_dirs_skips_nonexistent(self):
        """Test nonexistent directories are skipped."""
        self.mock_config.skills.additional_dirs = ["$workspace/nonexistent"]
        with patch.object(Path, "exists", return_value=False):
            dirs = self.builder._build_add_dirs()
            assert "/test/workspace/nonexistent" not in dirs

    def test_expand_path_workspace_variable(self):
        """Test $workspace variable expansion."""
        result = self.builder._expand_path("$workspace/skills")
        assert result == "/test/workspace/skills"

    def test_expand_path_home_variable(self):
        """Test $home variable expansion."""
        with patch.object(Path, "home", return_value=Path("/home/user")):
            result = self.builder._expand_path("$home/.claude/skills")
            assert result == "/home/user/.claude/skills"


class TestBuildPlugins:
    """Test _build_plugins method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config = MagicMock()
        self.mock_config.plugins.enabled = True
        self.mock_config.plugins.dirs = ["$workspace/plugins"]
        self.mock_config.plugins.enabled_plugins = []
        self.mock_config.plugins.disabled_plugins = []
        self.mock_config.agents.defaults.workspace = "/test/workspace"

        self.shared_resource = {"config": self.mock_config}

        self.builder = OptionsBuilder(
            shared_resources=self.shared_resource,
            sdk_config=MagicMock(),
            skill_converter=None,
            tool_adapter=None,
            sessions=None,
            context_builder=None,
            handoff_policy=None,
            capability_policy=None,
        )

    def test_build_plugins_empty_when_no_plugins_dir(self):
        """Test empty list when plugins directory doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            plugins = self.builder._build_plugins()
            assert plugins == []

    def test_build_plugins_disabled_returns_empty(self):
        """Test disabled plugins returns empty list."""
        self.mock_config.plugins.enabled = False
        plugins = self.builder._build_plugins()
        assert plugins == []

    def test_is_valid_plugin_checks_plugin_json(self):
        """Test _is_valid_plugin checks for plugin.json."""
        # Create a mock path that returns True for the plugin.json path
        mock_path = MagicMock()
        mock_path.__truediv__ = lambda self, other: MagicMock(
            __truediv__=lambda self, other2: MagicMock(
                exists=MagicMock(return_value=str(other) == ".claude-plugin" and str(other2) == "plugin.json")
            )
        )
        result = self.builder._is_valid_plugin(mock_path)
        assert result is True

    def test_should_load_plugin_enabled_list(self):
        """Test plugin filtering by enabled_plugins."""
        self.mock_config.plugins.enabled_plugins = ["superpowers"]
        assert self.builder._should_load_plugin("superpowers", self.mock_config) is True
        assert self.builder._should_load_plugin("other", self.mock_config) is False

    def test_should_load_plugin_disabled_list(self):
        """Test plugin filtering by disabled_plugins."""
        self.mock_config.plugins.disabled_plugins = ["experimental"]
        assert self.builder._should_load_plugin("experimental", self.mock_config) is False
        assert self.builder._should_load_plugin("superpowers", self.mock_config) is True

    def test_should_load_plugin_no_filtering(self):
        """Test plugin loading without filtering lists."""
        assert self.builder._should_load_plugin("any-plugin", self.mock_config) is True


class TestOptionsBuilderIntegration:
    """Test OptionsBuilder.build() includes add_dirs and plugins."""

    def test_build_includes_add_dirs(self):
        """Test that build() passes add_dirs to ClaudeAgentOptions."""
        mock_config = MagicMock()
        mock_config.skills.enabled = True
        mock_config.skills.dirs = ["$workspace/.claude/skills"]
        mock_config.skills.additional_dirs = []
        mock_config.plugins.enabled = False
        mock_config.agents.defaults.workspace = "/test/workspace"
        mock_config.agents.defaults.model = "claude-sonnet-4-5"
        mock_config.agents.defaults.provider = "anthropic"
        # Mock providers for _get_provider_config
        mock_providers = MagicMock()
        mock_anthropic = MagicMock()
        mock_anthropic.api_key = MagicMock(get_secret_value=MagicMock(return_value="test-key"))
        mock_anthropic.api_base = None
        mock_providers.anthropic = mock_anthropic
        mock_config.providers = mock_providers

        shared_resource = {"config": mock_config}

        builder = OptionsBuilder(
            shared_resources=shared_resource,
            sdk_config=MagicMock(
                max_turns=40,
                permission_mode="acceptEdits",
                hooks=None,
                disallowed_tools=["WebFetch"],
                env={},
                extra_args={},
                agents=None,
            ),
            skill_converter=None,
            tool_adapter=None,
            sessions=None,
            context_builder=None,
            handoff_policy=None,
            capability_policy=None,
            permission_handler=None,
        )

        with patch.object(Path, "exists", return_value=True):
            with patch("claude_agent_sdk.ClaudeAgentOptions") as mock_opts:
                mock_opts.return_value = MagicMock()
                builder.build()
                call_kwargs = mock_opts.call_args[1]
                assert "add_dirs" in call_kwargs
                assert "/test/workspace" in call_kwargs["add_dirs"]

    def test_build_includes_plugins(self):
        """Test that build() passes plugins to ClaudeAgentOptions."""
        mock_config = MagicMock()
        mock_config.skills.enabled = False
        mock_config.plugins.enabled = True
        mock_config.plugins.dirs = ["$workspace/plugins"]
        mock_config.plugins.enabled_plugins = []
        mock_config.plugins.disabled_plugins = []
        mock_config.agents.defaults.workspace = "/test/workspace"
        mock_config.agents.defaults.model = "claude-sonnet-4-5"
        mock_config.agents.defaults.provider = "anthropic"
        # Mock providers for _get_provider_config
        mock_providers = MagicMock()
        mock_anthropic = MagicMock()
        mock_anthropic.api_key = MagicMock(get_secret_value=MagicMock(return_value="test-key"))
        mock_anthropic.api_base = None
        mock_providers.anthropic = mock_anthropic
        mock_config.providers = mock_providers

        shared_resource = {"config": mock_config}

        builder = OptionsBuilder(
            shared_resources=shared_resource,
            sdk_config=MagicMock(
                max_turns=40,
                permission_mode="acceptEdits",
                hooks=None,
                disallowed_tools=["WebFetch"],
                env={},
                extra_args={},
                agents=None,
            ),
            skill_converter=None,
            tool_adapter=None,
            sessions=None,
            context_builder=None,
            handoff_policy=None,
            capability_policy=None,
            permission_handler=None,
        )

        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "iterdir") as mock_iterdir:
                mock_plugin = MagicMock()
                mock_plugin.is_dir.return_value = True
                mock_plugin.name = "superpowers"
                mock_plugin.__str__ = lambda self: "/test/workspace/plugins/superpowers"
                mock_iterdir.return_value = [mock_plugin]

                with patch.object(builder, "_is_valid_plugin", return_value=True):
                    with patch("claude_agent_sdk.ClaudeAgentOptions") as mock_opts:
                        mock_opts.return_value = MagicMock()
                        builder.build()
                        call_kwargs = mock_opts.call_args[1]
                        assert "plugins" in call_kwargs