from __future__ import annotations

from pathlib import Path

from xbot.config.schema import MCPServerConfig
from xbot.agent.capabilities.catalog import CapabilityCatalog, canonical_tool_name
from xbot.agent.capabilities.policy import CapabilityPolicy
from xbot.agent.capabilities.skills_loader import SkillsLoader
from xbot.agent.capabilities.skill_to_mcp import SkillToMCPConverter


def _write_skill(root: Path, name: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_skills_loader_uses_workspace_then_dot_xbot_then_builtin(tmp_path) -> None:
    workspace_skills = tmp_path / "skills"
    dot_xbot_skills = tmp_path / ".xbot" / "skills"
    builtin_skills = tmp_path / "builtin"

    _write_skill(workspace_skills, "shared", "---\ndescription: workspace\n---\nworkspace")
    _write_skill(dot_xbot_skills, "scoped", "---\ndescription: scoped\n---\nscoped")
    _write_skill(builtin_skills, "shared", "---\ndescription: builtin shared\n---\nbuiltin")
    _write_skill(builtin_skills, "builtin_only", "---\ndescription: builtin only\n---\nbuiltin only")

    loader = SkillsLoader(tmp_path, builtin_skills_dir=builtin_skills)

    skills = loader.list_skills(filter_unavailable=False)
    names = [skill["name"] for skill in skills]

    assert names == ["shared", "scoped", "builtin_only"]
    assert loader.load_skill("shared") is not None
    assert "workspace" in loader.load_skill("shared")
    assert "scoped" in loader.load_skill("scoped")
    assert "builtin only" in loader.load_skill("builtin_only")


def test_canonical_tool_name_maps_shell_alias_to_exec() -> None:
    assert canonical_tool_name("shell") == "exec"
    assert canonical_tool_name("exec") == "exec"
    assert canonical_tool_name("web_search") == "web_search"


def test_capability_catalog_normalizes_agent_tool_names() -> None:
    normalized = CapabilityCatalog.normalize_tool_names(["shell", "web_search", "exec"])
    assert normalized == ["exec", "web_search"]


def test_capability_policy_filters_unavailable_agent_tools(tmp_path) -> None:
    workspace_skills = tmp_path / "skills"
    _write_skill(
        workspace_skills,
        "weather",
        "---\ndescription: weather\ntool_exposable: true\n---\nweather body",
    )

    catalog = CapabilityCatalog(tmp_path)
    policy = CapabilityPolicy(catalog)
    resolution = policy.resolve_agent_tools(
        ["shell", "skill_weather", "missing_tool"],
        backend="claude_sdk",
    )

    assert resolution.allowed == ["exec", "skill_weather"]
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


def test_capability_catalog_build_summary_includes_skills_and_mcp(tmp_path) -> None:
    workspace_skills = tmp_path / "skills"
    _write_skill(
        workspace_skills,
        "weather",
        "---\ndescription: weather\ntool_exposable: true\n---\nweather body",
    )

    catalog = CapabilityCatalog(tmp_path)
    summary = catalog.build_summary(
        mcp_servers={"docs": MCPServerConfig(url="https://example.com/mcp")}
    )

    assert "builtin_tools=" in summary
    assert "skills=1" in summary
    assert "tool_exposable_skills=1" in summary
    assert "mcp_servers=1" in summary
    assert "skill_weather" in summary
    assert "docs[streamableHttp]" in summary


def test_capability_catalog_only_exposes_tool_exposable_skills_as_tools(tmp_path) -> None:
    workspace_skills = tmp_path / "skills"
    _write_skill(
        workspace_skills,
        "weather",
        "---\ndescription: weather\ntool_exposable: true\n---\nweather body",
    )
    _write_skill(
        workspace_skills,
        "writing",
        "---\ndescription: writing\n---\nwriting body",
    )

    catalog = CapabilityCatalog(tmp_path)

    assert catalog.skill_tool_names(include_unavailable=True) == {"skill_weather"}
    assert catalog.classify_tool_name("skill_weather") == "skill"
    assert catalog.classify_tool_name("read_file") == "tool"
    assert catalog.classify_tool_name("mcp_docs_search") == "mcp"
    assert catalog.classify_tool_name("github_search", assume_unknown_mcp=True) == "mcp"


def test_skill_to_mcp_converter_only_converts_tool_exposable_skills(tmp_path, monkeypatch) -> None:
    workspace_skills = tmp_path / "skills"
    _write_skill(
        workspace_skills,
        "weather",
        "---\ndescription: weather\ntool_exposable: true\n---\nweather body",
    )
    _write_skill(
        workspace_skills,
        "writing",
        "---\ndescription: writing\n---\nwriting body",
    )

    monkeypatch.setattr("xbot.agent.capabilities.skill_to_mcp.SDK_AVAILABLE", True)
    monkeypatch.setattr(
        "xbot.agent.capabilities.skill_to_mcp.create_sdk_mcp_server",
        lambda **kwargs: kwargs,
    )

    converter = SkillToMCPConverter(str(tmp_path))
    servers = converter.convert_all_skills()

    assert set(servers) == {"skills"}
    assert len(servers["skills"]["tools"]) == 1
