"""Tests for cache-friendly prompt construction."""

from __future__ import annotations

from datetime import datetime as real_datetime
from importlib.resources import files as pkg_files
from pathlib import Path
import datetime as datetime_module

from xbot.agent.context.builder import ContextBuilder


class _FakeDatetime(real_datetime):
    current = real_datetime(2026, 2, 24, 13, 59)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def _create_memory_file(workspace: Path, name: str, desc: str) -> Path:
    """Create a minimal memory file with frontmatter."""
    mem_dir = workspace / "memory" / "project"
    mem_dir.mkdir(parents=True, exist_ok=True)
    path = mem_dir / f"{name}.md"
    path.write_text(
        f"---\ntype: project\ndescription: {desc}\n---\n\nBody of {name}.",
        encoding="utf-8",
    )
    return path


def _create_skill(workspace: Path, name: str, desc: str) -> None:
    """Create a minimal skill directory."""
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\ndescription: {desc}\n---\n\nSkill instructions for {name}.",
        encoding="utf-8",
    )


def test_bootstrap_files_are_backed_by_templates() -> None:
    template_dir = pkg_files("xbot") / "templates"

    for filename in ContextBuilder.BOOTSTRAP_FILES:
        assert (template_dir / filename).is_file(), f"missing bootstrap template: {filename}"


def test_system_prompt_stays_stable_when_clock_changes(tmp_path, monkeypatch) -> None:
    """System prompt should not change just because wall clock minute changes."""
    monkeypatch.setattr(datetime_module, "datetime", _FakeDatetime)

    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    _FakeDatetime.current = real_datetime(2026, 2, 24, 13, 59)
    prompt1 = builder.build_system_prompt()

    _FakeDatetime.current = real_datetime(2026, 2, 24, 14, 0)
    prompt2 = builder.build_system_prompt()

    assert prompt1 == prompt2


def test_runtime_context_is_separate_untrusted_user_message(tmp_path) -> None:
    """Runtime metadata should be merged with the user message."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    assert "## Current Session" not in messages[0]["content"]

    # Runtime context is now merged with user message into a single message
    assert messages[-1]["role"] == "user"
    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert ContextBuilder._RUNTIME_CONTEXT_TAG in user_content
    assert "Current Time:" in user_content
    assert "Channel: cli" in user_content
    assert "Chat ID: direct" in user_content
    assert "Return exactly: OK" in user_content


# --- Prompt cache ordering tests ---


def test_system_prompt_stable_prefix_after_memory_crud(tmp_path) -> None:
    """Stable prefix (identity+bootstrap+instructions+skills) must not change
    when memory index changes due to CRUD operations."""
    workspace = _make_workspace(tmp_path)
    _create_skill(workspace, "demo-skill", "A demo skill")
    builder = ContextBuilder(workspace)

    prompt_before = builder.build_system_prompt()

    # Create a memory file and rebuild the index
    _create_memory_file(workspace, "api-config", "API configuration notes")
    builder.memory.rebuild_index()

    prompt_after = builder.build_system_prompt()

    # Memory Index section should now appear
    assert "Memory Index" in prompt_after
    assert "Memory Index" not in prompt_before

    # Extract prefix up to the Memory Index section
    idx = prompt_after.index("# Memory Index")
    prefix_after = prompt_after[:idx]

    # Prefix must be identical (identity + bootstrap + instructions + skills)
    assert prefix_after == prompt_before.rstrip() + "\n\n---\n\n"


def test_system_prompt_no_relevant_memories(tmp_path) -> None:
    """System prompt must never contain 'Relevant Memories' section."""
    workspace = _make_workspace(tmp_path)
    _create_memory_file(workspace, "api-config", "API configuration notes")
    builder = ContextBuilder(workspace)
    builder.memory.rebuild_index()

    # Without user_message
    prompt = builder.build_system_prompt()
    assert "Relevant Memories" not in prompt

    # Even with user_message (should still not inject into system prompt)
    prompt_with_msg = builder.build_system_prompt(user_message="api config")
    assert "Relevant Memories" not in prompt_with_msg


def test_memory_index_after_skills_catalog(tmp_path) -> None:
    """Memory Index must appear AFTER Skills Catalog in system prompt
    to maximise Anthropic auto-cache prefix hits."""
    workspace = _make_workspace(tmp_path)
    _create_skill(workspace, "weather", "Get weather forecasts")
    _create_memory_file(workspace, "deploy-notes", "Deployment notes")
    builder = ContextBuilder(workspace)
    builder.memory.rebuild_index()

    prompt = builder.build_system_prompt()

    skills_pos = prompt.index("# Active Skills")
    memory_pos = prompt.index("# Memory Index")
    assert skills_pos < memory_pos, (
        "Skills Catalog must appear before Memory Index for cache-friendly ordering"
    )


def test_build_messages_system_prompt_cache_friendly(tmp_path) -> None:
    """build_messages() must produce identical system prompts regardless of
    current_message content, ensuring cache stability."""
    workspace = _make_workspace(tmp_path)
    _create_memory_file(workspace, "api-config", "API configuration notes")
    builder = ContextBuilder(workspace)
    builder.memory.rebuild_index()

    msgs1 = builder.build_messages(
        history=[],
        current_message="Show me the API config",
        channel="cli",
        chat_id="direct",
    )
    msgs2 = builder.build_messages(
        history=[],
        current_message="Deploy the application",
        channel="cli",
        chat_id="direct",
    )

    # System prompts must be identical even with different user messages
    assert msgs1[0]["content"] == msgs2[0]["content"]
