"""Tests for SkillsConfig and PluginsConfig."""

from xbot.platform.config.schema import Config, PluginsConfig, SkillsConfig


class TestSkillsConfig:
    """Test SkillsConfig validation."""

    def test_skills_config_defaults(self):
        """Test default values for SkillsConfig."""
        config = SkillsConfig()
        assert config.enabled is True
        assert config.dirs == ["$workspace/.claude/skills"]
        assert config.additional_dirs == []

    def test_skills_config_custom_dirs(self):
        """Test custom dirs configuration."""
        config = SkillsConfig(
            dirs=["/custom/skills"],
            additional_dirs=["$workspace/skills", "$home/.claude/skills"]
        )
        assert config.dirs == ["/custom/skills"]
        assert len(config.additional_dirs) == 2

    def test_skills_config_disabled(self):
        """Test disabled skills."""
        config = SkillsConfig(enabled=False)
        assert config.enabled is False


class TestPluginsConfig:
    """Test PluginsConfig validation."""

    def test_plugins_config_defaults(self):
        """Test default values for PluginsConfig."""
        config = PluginsConfig()
        assert config.enabled is True
        assert config.dirs == ["$workspace/plugins"]
        assert config.enabled_plugins == []
        assert config.disabled_plugins == []

    def test_plugins_config_filtering(self):
        """Test enabled/disabled plugin filtering."""
        config = PluginsConfig(
            enabled_plugins=["superpowers"],
            disabled_plugins=["experimental"]
        )
        assert "superpowers" in config.enabled_plugins
        assert "experimental" in config.disabled_plugins


class TestConfigIntegration:
    """Test Config includes skills and plugins."""

    def test_config_has_skills_and_plugins(self):
        """Test Config includes skills and plugins fields."""
        config = Config()
        assert hasattr(config, "skills")
        assert hasattr(config, "plugins")
        assert isinstance(config.skills, SkillsConfig)
        assert isinstance(config.plugins, PluginsConfig)
