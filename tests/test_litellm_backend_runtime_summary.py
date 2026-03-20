from __future__ import annotations

from types import SimpleNamespace

from xbot.agent.backends.litellm_backend import LiteLLMBackend
from xbot.config.schema import Config


def test_litellm_backend_tools_summary_includes_runtime_state(tmp_path) -> None:
    backend = LiteLLMBackend()
    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    backend._shared_resources = {
        "workspace": tmp_path,
        "config": config,
    }
    backend.agent_loop = SimpleNamespace(
        tools=[1, 2, 3],
        _mcp_connected=True,
    )

    summary = backend.get_tools_summary()

    assert "builtin_tools=" in summary
    assert "skill_tools=0" in summary
    assert "registered_tools=3" in summary
    assert "mcp_connected=True" in summary
