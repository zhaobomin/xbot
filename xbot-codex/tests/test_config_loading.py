from __future__ import annotations

from pathlib import Path

from xbot_codex.config import ServiceConfig, load_config


def test_load_config_reads_toml_and_applies_env_overrides(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[service]
name = "custom-codex"

[channels.telegram]
enabled = true
token = "from-file"

[codex]
binary_path = "codex"
default_model = "gpt-5-codex"
"""
    )
    monkeypatch.setenv("XBOT_CODEX_TELEGRAM_TOKEN", "from-env")
    monkeypatch.setenv("XBOT_CODEX_CODEX_HOME", "/tmp/codex-home")
    monkeypatch.setenv("XBOT_CODEX_HTTP_PROXY", "http://127.0.0.1:7890")

    config = load_config(config_path)

    assert isinstance(config, ServiceConfig)
    assert config.service_name == "custom-codex"
    assert config.channels.telegram.enabled is True
    assert config.channels.telegram.token == "from-env"
    assert config.codex.default_model == "gpt-5-codex"
    assert config.codex.home == "/tmp/codex-home"
    assert config.codex.proxy == "http://127.0.0.1:7890"
