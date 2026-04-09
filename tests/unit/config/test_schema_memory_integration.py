"""Tests for Claude SDK memory integration schema."""

from xbot.platform.config.schema import Config


def test_claude_sdk_memory_integration_defaults() -> None:
    config = Config()
    mi = config.agents.claude_sdk.memory_integration

    assert mi.mode == "auto"
    assert mi.setting_sources.cli == ["user", "project", "local"]
    assert mi.setting_sources.gateway == ["user", "project", "local"]
    assert mi.sdk_settings.auto_memory_enabled is None
    assert mi.sdk_settings.auto_memory_directory is None
    assert mi.sdk_settings.claude_md_excludes == []


def test_claude_sdk_memory_integration_accepts_camel_case() -> None:
    config = Config.model_validate(
        {
            "agents": {
                "claudeSdk": {
                    "memoryIntegration": {
                        "mode": "on",
                        "settingSources": {
                            "cli": ["user", "local"],
                            "gateway": ["project", "local"],
                        },
                        "sdkSettings": {
                            "autoMemoryEnabled": True,
                            "autoMemoryDirectory": "/tmp/claude-memory",
                            "claudeMdExcludes": ["**/node_modules/**"],
                        },
                    },
                    "systemPromptStrategy": {
                        "preset": "claude_code",
                        "appendXbotPrompt": False,
                    },
                }
            }
        }
    )

    mi = config.agents.claude_sdk.memory_integration
    assert mi.mode == "on"
    assert mi.setting_sources.cli == ["user", "local"]
    assert mi.setting_sources.gateway == ["project", "local"]
    assert mi.sdk_settings.auto_memory_enabled is True
    assert mi.sdk_settings.auto_memory_directory == "/tmp/claude-memory"
    assert mi.sdk_settings.claude_md_excludes == ["**/node_modules/**"]
    assert config.agents.claude_sdk.system_prompt_strategy.preset == "claude_code"
    assert config.agents.claude_sdk.system_prompt_strategy.append_xbot_prompt is False
