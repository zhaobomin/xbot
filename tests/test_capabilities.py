from __future__ import annotations

from pathlib import Path

from xbot.capabilities.catalog import CapabilityCatalog, canonical_tool_name
from xbot.capabilities.policy import CapabilityPolicy
from xbot.platform.config.schema import MCPServerConfig


def test_canonical_tool_name_maps_shell_alias_to_exec() -> None:
    assert canonical_tool_name("shell") == "exec"
    assert canonical_tool_name("exec") == "exec"
    assert canonical_tool_name("web_search") == "web_search"


def test_capability_catalog_normalizes_agent_tool_names() -> None:
    normalized = CapabilityCatalog.normalize_tool_names(["shell", "web_search", "exec"])
    assert normalized == ["exec", "web_search"]


def test_capability_policy_filters_unavailable_agent_tools() -> None:
    # Skills are now loaded natively by Claude Code SDK
    # Test only validates that builtin tools work
    catalog = CapabilityCatalog(Path("/tmp/nonexistent"))
    policy = CapabilityPolicy(catalog)
    resolution = policy.resolve_agent_tools(
        ["shell", "missing_tool"],
        backend="claude_sdk",
    )

    assert resolution.allowed == ["exec"]
    assert resolution.dropped == ["missing_tool"]


def test_capability_catalog_lists_builtin_tools() -> None:
    builtin_names = {cap.name for cap in CapabilityCatalog.list_builtin_tools()}
    assert "exec" in builtin_names
    assert "web_search" in builtin_names
    assert "shell" not in builtin_names


def test_capability_catalog_lists_external_mcp_servers() -> None:
    servers = CapabilityCatalog.list_external_mcp_servers(
        {
            "docs": MCPServerConfig(url="https://example.com/mcp"),
            "local": MCPServerConfig(command="npx", args=["foo"], enabled_tools=["bar"]),
        }
    )

    assert [(server.name, server.transport, server.enabled_tools) for server in servers] == [
        ("docs", "streamableHttp", ("*",)),
        ("local", "stdio", ("bar",)),
    ]


def test_capability_catalog_build_summary_includes_mcp(tmp_path) -> None:
    # Skills are now loaded natively by Claude Code SDK
    catalog = CapabilityCatalog(tmp_path)
    summary = catalog.build_summary(
        mcp_servers={"docs": MCPServerConfig(url="https://example.com/mcp")}
    )

    assert "builtin_tools=" in summary
    assert "mcp_servers=1" in summary
    assert "docs[streamableHttp]" in summary


def test_capability_catalog_tool_classification() -> None:
    # Skills are now loaded natively by Claude Code SDK
    catalog = CapabilityCatalog(Path("/tmp/nonexistent"))

    assert catalog.classify_tool_name("read_file") == "tool"
    assert catalog.classify_tool_name("mcp_docs_search") == "mcp"
    assert catalog.classify_tool_name("github_search", assume_unknown_mcp=True) == "mcp"
