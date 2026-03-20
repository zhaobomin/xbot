from __future__ import annotations

from pathlib import Path


def test_make_agent_runtime_uses_router_runtime(monkeypatch, tmp_path: Path) -> None:
    from nanobot.cli import commands
    from nanobot.config.schema import Config

    captured = {}

    class _FakeRuntime:
        def __init__(self, *, config, shared_resources):
            captured["config"] = config
            captured["shared_resources"] = shared_resources

    monkeypatch.setattr(commands, "AgentRuntime", _FakeRuntime)

    config = Config()

    runtime = commands._make_agent_runtime(
        config=config,
        bus="BUS",
        provider="PROVIDER",
        workspace=tmp_path,
        cron_service="CRON",
        session_manager="SESSIONS",
    )

    assert runtime is not None
    assert captured["config"] is config
    assert captured["shared_resources"]["bus"] == "BUS"
    assert captured["shared_resources"]["provider"] == "PROVIDER"
    assert captured["shared_resources"]["workspace"] == tmp_path
    assert captured["shared_resources"]["cron_service"] == "CRON"
    assert captured["shared_resources"]["session_manager"] == "SESSIONS"
