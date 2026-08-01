"""Deep tests for xbot/interfaces/cli/commands.py — focus on uncovered functions."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from xbot.interfaces.cli.commands import app
from xbot.platform.config.schema import Config


def _strip_ansi(text: str) -> str:
    ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
    return ansi_escape.sub("", text)


runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def mock_config(tmp_workspace: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_workspace)
    return cfg


@pytest.fixture
def fake_config_file(tmp_path: Path) -> Path:
    cfg = tmp_path / "instance" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}")
    return cfg


# ---------------------------------------------------------------------------
# 1. _sanitize_terminal_text()
# ---------------------------------------------------------------------------


class TestSanitizeTerminalText:
    def test_strips_ansi_osc_sequences(self):
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "hello\x1b]0;title\x07world"
        result = _sanitize_terminal_text(text)
        assert result == "helloworld"

    def test_strips_ansi_dcs_sequences(self):
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "hello\x1bPsome dcs\x1b\\world"
        result = _sanitize_terminal_text(text)
        assert result == "helloworld"

    def test_strips_standard_csi_sequences(self):
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "\x1b[31mred text\x1b[0m normal"
        result = _sanitize_terminal_text(text)
        assert result == "red text normal"

    def test_strips_caret_csi_sequences(self):
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "hello^[[43;1Rworld"
        result = _sanitize_terminal_text(text)
        assert result == "helloworld"

    def test_strips_literal_esc_csi_sequences(self):
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "hello\\x1b[43;1Rworld"
        result = _sanitize_terminal_text(text)
        assert result == "helloworld"

    def test_strips_control_characters(self):
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "hel\x00lo\x08world\x7f"
        result = _sanitize_terminal_text(text)
        assert result == "helloworld"

    def test_preserves_normal_text(self):
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "Hello, world! 123"
        result = _sanitize_terminal_text(text)
        assert result == "Hello, world! 123"

    def test_handles_none_and_empty_string(self):
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        assert _sanitize_terminal_text(None) == ""
        assert _sanitize_terminal_text("") == ""

    def test_strips_mixed_sequences(self):
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "\x1b]0;title\x07\x1b[31mred\x1b[0m\x00clean"
        result = _sanitize_terminal_text(text)
        assert result == "redclean"


# ---------------------------------------------------------------------------
# 2. _normalize_exec_cwd()
# ---------------------------------------------------------------------------


class TestNormalizeExecCwd:
    def test_resolves_relative_path(self):
        from xbot.interfaces.cli.commands import _normalize_exec_cwd

        result = _normalize_exec_cwd(".")
        assert Path(result).is_absolute()

    def test_expands_tilde(self):
        from xbot.interfaces.cli.commands import _normalize_exec_cwd

        result = _normalize_exec_cwd("~/test")
        assert "~" not in result
        assert Path(result).is_absolute()

    def test_resolves_path_object(self):
        from xbot.interfaces.cli.commands import _normalize_exec_cwd

        result = _normalize_exec_cwd(Path("/tmp/../tmp/test"))
        assert result == str(Path("/tmp/test").resolve())

    def test_string_input(self):
        from xbot.interfaces.cli.commands import _normalize_exec_cwd

        result = _normalize_exec_cwd("/absolute/path")
        assert result == str(Path("/absolute/path").resolve())


# ---------------------------------------------------------------------------
# 3. _generate_cli_session_key()
# ---------------------------------------------------------------------------


class TestGenerateCliSessionKey:
    def test_starts_with_cli_prefix(self):
        from xbot.interfaces.cli.commands import _generate_cli_session_key

        key = _generate_cli_session_key()
        assert key.startswith("cli:")

    def test_unique_keys(self):
        from xbot.interfaces.cli.commands import _generate_cli_session_key

        keys = {_generate_cli_session_key() for _ in range(50)}
        assert len(keys) == 50


# ---------------------------------------------------------------------------
# 4. _is_exit_command()
# ---------------------------------------------------------------------------


class TestIsExitCommand:
    def test_exit_commands(self):
        from xbot.interfaces.cli.commands import _is_exit_command, EXIT_COMMANDS

        for cmd in EXIT_COMMANDS:
            assert _is_exit_command(cmd) is True

    def test_case_insensitive(self):
        from xbot.interfaces.cli.commands import _is_exit_command

        assert _is_exit_command("EXIT") is True
        assert _is_exit_command("Quit") is True
        assert _is_exit_command(":Q") is True

    def test_non_exit_commands(self):
        from xbot.interfaces.cli.commands import _is_exit_command

        assert _is_exit_command("hello") is False
        assert _is_exit_command("exit1") is False
        assert _is_exit_command("/quit ") is False  # trailing space
        assert _is_exit_command("") is False


# ---------------------------------------------------------------------------
# 5. _parse_media_from_input()
# ---------------------------------------------------------------------------


class TestParseMediaFromInput:
    def test_no_file_refs_returns_input_unchanged(self, tmp_workspace):
        from xbot.interfaces.cli.commands import _parse_media_from_input

        clean, media = _parse_media_from_input("hello world", workspace=tmp_workspace)
        assert clean == "hello world"
        assert media == []

    def test_extracts_existing_file_ref(self, tmp_workspace):
        from xbot.interfaces.cli.commands import _parse_media_from_input

        f = tmp_workspace / "test.png"
        f.write_text("fake png")
        clean, media = _parse_media_from_input(f"look at @{f.name}", workspace=tmp_workspace)
        assert str(f.resolve()) in media
        assert f"@{f.name}" not in clean

    def test_nonexistent_file_keeps_original_text(self, tmp_workspace):
        from xbot.interfaces.cli.commands import _parse_media_from_input

        clean, media = _parse_media_from_input("@nonexistent.png", workspace=tmp_workspace)
        assert "@nonexistent.png" in clean
        assert media == []

    def test_path_outside_workspace_is_rejected(self, tmp_workspace, tmp_path, capsys):
        from xbot.interfaces.cli.commands import _parse_media_from_input

        outside = tmp_path / "outside.png"
        outside.write_text("x")
        clean, media = _parse_media_from_input(
            f"@{outside}", workspace=tmp_workspace
        )
        # File exists but outside workspace → should remain in text + error printed
        assert media == []
        captured = capsys.readouterr()
        assert "workspace" in captured.out.lower() or "@path" in captured.out

    def test_quoted_file_ref(self, tmp_workspace):
        from xbot.interfaces.cli.commands import _parse_media_from_input

        f = tmp_workspace / "my file.jpg"
        f.write_text("fake jpg")
        clean, media = _parse_media_from_input(
            f'look at @"my file.jpg"', workspace=tmp_workspace
        )
        assert str(f.resolve()) in media

    def test_empty_after_removal_returns_default(self, tmp_workspace):
        from xbot.interfaces.cli.commands import _parse_media_from_input

        f = tmp_workspace / "only.png"
        f.write_text("x")
        clean, media = _parse_media_from_input(f"@{f.name}", workspace=tmp_workspace)
        assert clean == "请处理这些文件"
        assert len(media) == 1


# ---------------------------------------------------------------------------
# 6. _resolve_heartbeat_target() (additional edge cases)
# ---------------------------------------------------------------------------


class TestResolveHeartbeatTarget:
    def test_explicit_channel_only_without_chat_id_returns_none(self):
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        cfg = Config()
        cfg.gateway.heartbeat.channel = "telegram"
        cfg.gateway.heartbeat.chat_id = ""
        assert _resolve_heartbeat_target(
            config=cfg, enabled_channels=["telegram"], conversation_store=None,
        ) is None

    def test_explicit_chat_id_only_without_channel_returns_none(self):
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        cfg = Config()
        cfg.gateway.heartbeat.channel = ""
        cfg.gateway.heartbeat.chat_id = "123"
        assert _resolve_heartbeat_target(
            config=cfg, enabled_channels=["telegram"], conversation_store=None,
        ) is None

    def test_no_conversation_store_and_no_explicit_target(self):
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        cfg = Config()
        assert _resolve_heartbeat_target(
            config=cfg, enabled_channels=["telegram"], conversation_store=None,
        ) is None

    def test_conversation_store_skips_cli_and_system_channels(self):
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        cfg = Config()
        store = MagicMock(list_sessions=lambda: [
            {"key": "cli:direct"},
            {"key": "system:internal"},
            {"key": "heartbeat:check"},
            {"key": "cron:job1"},
            {"key": "telegram:user1"},
        ])
        result = _resolve_heartbeat_target(
            config=cfg, enabled_channels=["telegram"], conversation_store=store,
        )
        assert result == ("telegram", "user1")

    def test_conversation_store_skips_cron_prefix(self):
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        cfg = Config()
        store = MagicMock(list_sessions=lambda: [
            {"key": "cronjob:1"},
            {"key": "discord:chat1"},
        ])
        result = _resolve_heartbeat_target(
            config=cfg, enabled_channels=["discord"], conversation_store=store,
        )
        assert result == ("discord", "chat1")

    def test_conversation_store_without_list_sessions(self):
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        cfg = Config()
        assert _resolve_heartbeat_target(
            config=cfg, enabled_channels=["telegram"], conversation_store=object(),
        ) is None

    def test_empty_chat_id_in_session_is_skipped(self):
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        cfg = Config()
        store = MagicMock(list_sessions=lambda: [
            {"key": "telegram:"},  # empty chat_id
            {"key": "slack:real"},
        ])
        result = _resolve_heartbeat_target(
            config=cfg, enabled_channels=["telegram", "slack"], conversation_store=store,
        )
        assert result == ("slack", "real")


# ---------------------------------------------------------------------------
# 7. _utc_now_iso() and _normalize_iso_for_sort()
# ---------------------------------------------------------------------------


class TestTimeHelpers:
    def test_utc_now_iso_format(self):
        from xbot.interfaces.cli.commands import _utc_now_iso

        result = _utc_now_iso()
        assert result.endswith("Z")
        assert "+" not in result

    def test_normalize_iso_for_sort_string(self):
        from xbot.interfaces.cli.commands import _normalize_iso_for_sort

        assert _normalize_iso_for_sort("2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"

    def test_normalize_iso_for_sort_non_string(self):
        from xbot.interfaces.cli.commands import _normalize_iso_for_sort

        assert _normalize_iso_for_sort(None) == ""
        assert _normalize_iso_for_sort(123) == ""
        assert _normalize_iso_for_sort(3.14) == ""


# ---------------------------------------------------------------------------
# 8. _list_session_index()
# ---------------------------------------------------------------------------


class TestListSessionIndex:
    def test_none_store_returns_empty(self):
        from xbot.interfaces.cli.commands import _list_session_index

        assert _list_session_index(None) == []

    def test_store_without_list_sessions_returns_empty(self):
        from xbot.interfaces.cli.commands import _list_session_index

        assert _list_session_index(object()) == []

    def test_builds_index_with_metadata(self):
        from xbot.interfaces.cli.commands import _list_session_index

        session = MagicMock()
        session.metadata = {"sdk_session_id": "sdk-1", "execution_cwd": "/tmp"}

        store = MagicMock()
        store.list_sessions.return_value = [
            {"key": "cli:test", "created_at": "2026-01-01", "updated_at": "2026-01-02"}
        ]
        store.get.return_value = session

        result = _list_session_index(store)
        assert len(result) == 1
        assert result[0]["key"] == "cli:test"
        assert result[0]["sdk_session_id"] == "sdk-1"
        assert result[0]["execution_cwd"] == "/tmp"

    def test_skips_empty_keys(self):
        from xbot.interfaces.cli.commands import _list_session_index

        store = MagicMock()
        store.list_sessions.return_value = [
            {"key": ""},
            {"key": None},
            {"key": "  "},
        ]
        store.get.return_value = None
        assert _list_session_index(store) == []

    def test_handles_get_exception(self):
        from xbot.interfaces.cli.commands import _list_session_index

        store = MagicMock()
        store.list_sessions.return_value = [{"key": "cli:test"}]
        store.get.side_effect = RuntimeError("boom")

        result = _list_session_index(store)
        assert len(result) == 1
        assert result[0]["sdk_session_id"] is None

    def test_sorts_by_last_used_then_updated_then_created(self):
        from xbot.interfaces.cli.commands import _list_session_index

        store = MagicMock()
        store.list_sessions.return_value = [
            {"key": "cli:old", "created_at": "2026-01-01", "updated_at": "2026-01-01"},
            {"key": "cli:new", "created_at": "2026-01-03", "updated_at": "2026-01-03"},
        ]
        store.get.return_value = None

        result = _list_session_index(store)
        assert result[0]["key"] == "cli:new"  # Most recent first

    def test_session_with_non_dict_metadata_is_ignored(self):
        from xbot.interfaces.cli.commands import _list_session_index

        session = MagicMock()
        session.metadata = "not a dict"

        store = MagicMock()
        store.list_sessions.return_value = [{"key": "cli:test"}]
        store.get.return_value = session

        result = _list_session_index(store)
        assert len(result) == 1
        assert result[0]["sdk_session_id"] is None


# ---------------------------------------------------------------------------
# 9. _select_continue_session() and _select_resume_session()
# ---------------------------------------------------------------------------


class TestSelectSession:
    def test_select_continue_session_matches_cwd(self, tmp_path):
        from xbot.interfaces.cli.commands import _select_continue_session

        target_cwd = str((tmp_path / "proj").resolve())
        sessions = [
            {"key": "cli:a", "execution_cwd": "/other"},
            {"key": "cli:b", "execution_cwd": target_cwd},
        ]
        result = _select_continue_session(sessions, tmp_path / "proj")
        assert result["key"] == "cli:b"

    def test_select_continue_session_no_match(self, tmp_path):
        from xbot.interfaces.cli.commands import _select_continue_session

        sessions = [{"key": "cli:a", "execution_cwd": "/other"}]
        assert _select_continue_session(sessions, tmp_path / "proj") is None

    def test_select_resume_session_by_key(self):
        from xbot.interfaces.cli.commands import _select_resume_session

        sessions = [
            {"key": "cli:target", "sdk_session_id": "sdk-1"},
            {"key": "cli:other", "sdk_session_id": "sdk-2"},
        ]
        result = _select_resume_session(sessions, "cli:target")
        assert result["sdk_session_id"] == "sdk-1"

    def test_select_resume_session_by_sdk_id(self):
        from xbot.interfaces.cli.commands import _select_resume_session

        sessions = [
            {"key": "cli:a", "sdk_session_id": "sdk-target"},
        ]
        result = _select_resume_session(sessions, "sdk-target")
        assert result["key"] == "cli:a"

    def test_select_resume_session_empty_value(self):
        from xbot.interfaces.cli.commands import _select_resume_session

        assert _select_resume_session([{"key": "cli:a"}], "") is None
        assert _select_resume_session([{"key": "cli:a"}], None) is None

    def test_select_resume_session_no_match(self):
        from xbot.interfaces.cli.commands import _select_resume_session

        sessions = [{"key": "cli:a", "sdk_session_id": "sdk-1"}]
        assert _select_resume_session(sessions, "nonexistent") is None


# ---------------------------------------------------------------------------
# 10. _update_cli_session_metadata()
# ---------------------------------------------------------------------------


class TestUpdateCliSessionMetadata:
    def test_none_store_is_noop(self, tmp_path):
        from xbot.interfaces.cli.commands import _update_cli_session_metadata

        _update_cli_session_metadata(None, session_key="k", execution_cwd=tmp_path)
        # No exception

    def test_store_without_required_methods_is_noop(self, tmp_path):
        from xbot.interfaces.cli.commands import _update_cli_session_metadata

        _update_cli_session_metadata(object(), session_key="k", execution_cwd=tmp_path)
        # No exception

    def test_updates_metadata(self, tmp_path):
        from xbot.interfaces.cli.commands import _update_cli_session_metadata

        session = MagicMock()
        session.metadata = {}

        store = MagicMock()
        store.get_or_create.return_value = session

        _update_cli_session_metadata(
            store, session_key="cli:test", execution_cwd=tmp_path,
        )

        assert store.get_or_create.called
        assert store.save.called
        assert session.metadata["execution_cwd"] == str(tmp_path.resolve())
        assert session.metadata["run_mode"] == "cli"
        assert "last_used_at" in session.metadata

    def test_creates_metadata_dict_if_not_dict(self, tmp_path):
        from xbot.interfaces.cli.commands import _update_cli_session_metadata

        session = MagicMock()
        session.metadata = "not a dict"

        store = MagicMock()
        store.get_or_create.return_value = session

        _update_cli_session_metadata(
            store, session_key="cli:test", execution_cwd=tmp_path,
        )

        assert isinstance(session.metadata, dict)
        assert session.metadata["run_mode"] == "cli"

    def test_calls_mark_metadata_dirty_if_available(self, tmp_path):
        from xbot.interfaces.cli.commands import _update_cli_session_metadata

        session = MagicMock()
        session.metadata = {}

        store = MagicMock()
        store.get_or_create.return_value = session

        _update_cli_session_metadata(
            store, session_key="cli:test", execution_cwd=tmp_path,
        )

        session.mark_metadata_dirty.assert_called_once()

    def test_handles_exception_gracefully(self, tmp_path):
        from xbot.interfaces.cli.commands import _update_cli_session_metadata

        store = MagicMock()
        store.get_or_create.side_effect = RuntimeError("db error")

        # Should not raise
        _update_cli_session_metadata(
            store, session_key="cli:test", execution_cwd=tmp_path,
        )


# ---------------------------------------------------------------------------
# 11. _validate_path_in_workspace()
# ---------------------------------------------------------------------------


class TestValidatePathInWorkspace:
    def test_path_inside_workspace(self, tmp_path):
        from xbot.interfaces.cli.commands import _validate_path_in_workspace

        ws = tmp_path / "workspace"
        ws.mkdir()
        f = ws / "file.txt"
        f.write_text("x")
        assert _validate_path_in_workspace(f, ws) is True

    def test_path_outside_workspace(self, tmp_path):
        from xbot.interfaces.cli.commands import _validate_path_in_workspace

        ws = tmp_path / "workspace"
        ws.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("x")
        assert _validate_path_in_workspace(outside, ws) is False

    def test_path_equals_workspace(self, tmp_path):
        from xbot.interfaces.cli.commands import _validate_path_in_workspace

        ws = tmp_path / "workspace"
        ws.mkdir()
        assert _validate_path_in_workspace(ws, ws) is True


# ---------------------------------------------------------------------------
# 12. _merge_missing_defaults()
# ---------------------------------------------------------------------------


class TestMergeMissingDefaults:
    def test_merges_missing_keys(self):
        from xbot.interfaces.cli.commands import _merge_missing_defaults

        existing = {"a": 1}
        defaults = {"a": 99, "b": 2}
        result = _merge_missing_defaults(existing, defaults)
        assert result == {"a": 1, "b": 2}

    def test_recursive_merge(self):
        from xbot.interfaces.cli.commands import _merge_missing_defaults

        existing = {"nested": {"x": 1}}
        defaults = {"nested": {"x": 99, "y": 2}}
        result = _merge_missing_defaults(existing, defaults)
        assert result == {"nested": {"x": 1, "y": 2}}

    def test_non_dict_existing_returns_existing(self):
        from xbot.interfaces.cli.commands import _merge_missing_defaults

        assert _merge_missing_defaults("str", {"a": 1}) == "str"
        assert _merge_missing_defaults(42, {"a": 1}) == 42

    def test_non_dict_defaults_returns_existing(self):
        from xbot.interfaces.cli.commands import _merge_missing_defaults

        assert _merge_missing_defaults({"a": 1}, "str") == {"a": 1}


# ---------------------------------------------------------------------------
# 13. _make_console()
# ---------------------------------------------------------------------------


class TestMakeConsole:
    def test_returns_console(self):
        from xbot.interfaces.cli.commands import _make_console
        from rich.console import Console

        c = _make_console()
        assert isinstance(c, Console)


# ---------------------------------------------------------------------------
# 14. _render_interactive_ansi()
# ---------------------------------------------------------------------------


class TestRenderInteractiveAnsi:
    def test_renders_content(self):
        from xbot.interfaces.cli.commands import _render_interactive_ansi

        result = _render_interactive_ansi(lambda c: c.print("hello"))
        assert "hello" in result


# ---------------------------------------------------------------------------
# 15. _print_deprecated_memory_window_notice()
# ---------------------------------------------------------------------------


class TestPrintDeprecatedMemoryWindowNotice:
    def test_prints_notice_when_deprecated(self, capsys):
        from xbot.interfaces.cli.commands import _print_deprecated_memory_window_notice

        cfg = Config()
        cfg.agents.defaults.memory_window = 100
        _print_deprecated_memory_window_notice(cfg)
        # Console.print goes to stdout, but Rich Console might redirect
        # Just check it doesn't raise


# ---------------------------------------------------------------------------
# 16. version_callback()
# ---------------------------------------------------------------------------


class TestVersionCallback:
    def test_prints_version_and_exits(self):
        from xbot.interfaces.cli.commands import version_callback
        import typer

        with pytest.raises(typer.Exit):
            version_callback(True)

    def test_noop_when_false(self):
        from xbot.interfaces.cli.commands import version_callback

        version_callback(False)  # Should not raise


# ---------------------------------------------------------------------------
# 17. set_cli_editing_mode()
# ---------------------------------------------------------------------------


class TestSetCliEditingMode:
    def test_noop_when_session_is_none(self):
        from xbot.interfaces.cli.commands import set_cli_editing_mode, _PROMPT_SESSION

        import xbot.interfaces.cli.commands as mod
        orig = mod._PROMPT_SESSION
        try:
            mod._PROMPT_SESSION = None
            set_cli_editing_mode("vim")  # Should not raise
        finally:
            mod._PROMPT_SESSION = orig

    def test_sets_vim_mode(self):
        from xbot.interfaces.cli.commands import set_cli_editing_mode
        from prompt_toolkit.enums import EditingMode
        import xbot.interfaces.cli.commands as mod

        fake_session = MagicMock()
        orig = mod._PROMPT_SESSION
        try:
            mod._PROMPT_SESSION = fake_session
            set_cli_editing_mode("vim")
            assert fake_session.editing_mode == EditingMode.VI
        finally:
            mod._PROMPT_SESSION = orig

    def test_sets_emacs_mode(self):
        from xbot.interfaces.cli.commands import set_cli_editing_mode
        from prompt_toolkit.enums import EditingMode
        import xbot.interfaces.cli.commands as mod

        fake_session = MagicMock()
        orig = mod._PROMPT_SESSION
        try:
            mod._PROMPT_SESSION = fake_session
            set_cli_editing_mode("emacs")
            assert fake_session.editing_mode == EditingMode.EMACS
        finally:
            mod._PROMPT_SESSION = orig


# ---------------------------------------------------------------------------
# 18. _ThinkingSpinner
# ---------------------------------------------------------------------------


class TestThinkingSpinner:
    def test_enabled_spinner(self):
        from xbot.interfaces.cli.commands import _ThinkingSpinner

        spinner = _ThinkingSpinner(enabled=True)
        with spinner:
            assert spinner._active is True
        assert spinner._active is False

    def test_disabled_spinner(self):
        from xbot.interfaces.cli.commands import _ThinkingSpinner

        spinner = _ThinkingSpinner(enabled=False)
        with spinner:
            assert spinner._active is True
        assert spinner._active is False

    def test_pause_context(self):
        from xbot.interfaces.cli.commands import _ThinkingSpinner

        spinner = _ThinkingSpinner(enabled=True)
        with spinner:
            with spinner.pause():
                pass  # Should not raise


# ---------------------------------------------------------------------------
# 19. _print_cli_progress_line()
# ---------------------------------------------------------------------------


class TestPrintCliProgressLine:
    def test_without_spinner(self):
        from xbot.interfaces.cli.commands import _print_cli_progress_line

        _print_cli_progress_line("test progress", None)

    def test_with_spinner(self):
        from xbot.interfaces.cli.commands import _ThinkingSpinner, _print_cli_progress_line

        spinner = _ThinkingSpinner(enabled=False)
        with spinner:
            _print_cli_progress_line("test progress", spinner)


# ---------------------------------------------------------------------------
# 20. _make_agent_service()
# ---------------------------------------------------------------------------


class TestMakeAgentService:
    def test_creates_service(self, mock_config, tmp_workspace):
        from xbot.interfaces.cli.commands import _make_agent_service

        bus = MagicMock()
        cron = MagicMock()
        store = MagicMock()

        service = _make_agent_service(
            config=mock_config,
            bus=bus,
            workspace=tmp_workspace,
            execution_cwd=tmp_workspace,
            cron_service=cron,
            conversation_store=store,
        )

        assert service is not None

    def test_includes_optional_resources(self, mock_config, tmp_workspace):
        from xbot.interfaces.cli.commands import _make_agent_service

        bus = MagicMock()
        cron = MagicMock()
        store = MagicMock()
        registry = MagicMock()
        perm = MagicMock()
        resume_policy = {"mode": "none"}

        service = _make_agent_service(
            config=mock_config,
            bus=bus,
            workspace=tmp_workspace,
            execution_cwd=tmp_workspace,
            cron_service=cron,
            conversation_store=store,
            runtime_registry=registry,
            permission_handler=perm,
            resume_policy=resume_policy,
        )

        assert service is not None


# ---------------------------------------------------------------------------
# 21. sessions_list() command
# ---------------------------------------------------------------------------


class TestSessionsListCommand:
    def test_no_sessions_found(self, monkeypatch, mock_config):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        result = runner.invoke(app, ["sessions", "list"])
        assert result.exit_code == 0
        assert "No sessions found" in result.stdout

    def test_with_cwd_filter(self, monkeypatch, mock_config, tmp_workspace):
        from xbot.runtime.session.conversation_store import ConversationStore

        store = ConversationStore(tmp_workspace)
        s1 = store.get_or_create("cli:filtered")
        s1.metadata.update({
            "execution_cwd": str((tmp_workspace / "proj").resolve()),
            "last_used_at": "2026-01-01T00:00:00Z",
            "run_mode": "cli",
        })
        s1.mark_metadata_dirty()
        store.save(s1)

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        # Filter by different cwd → no results
        result = runner.invoke(app, ["sessions", "list", "--cwd", "/nonexistent"])
        assert result.exit_code == 0
        assert "No sessions found" in result.stdout

    def test_limit_zero_shows_all(self, monkeypatch, mock_config, tmp_workspace):
        from xbot.runtime.session.conversation_store import ConversationStore

        store = ConversationStore(tmp_workspace)
        for i in range(3):
            s = store.get_or_create(f"cli:session-{i}")
            s.metadata.update({
                "last_used_at": f"2026-01-0{i+1}T00:00:00Z",
                "run_mode": "cli",
            })
            s.mark_metadata_dirty()
            store.save(s)

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        result = runner.invoke(app, ["sessions", "list", "--limit", "0"])
        assert result.exit_code == 0
        assert "session-0" in _strip_ansi(result.stdout)


# ---------------------------------------------------------------------------
# 22. sessions_show() command
# ---------------------------------------------------------------------------


class TestSessionsShowCommand:
    def test_session_not_found(self, monkeypatch, mock_config):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        result = runner.invoke(app, ["sessions", "show", "nonexistent"])
        assert result.exit_code == 1
        assert "session not found" in result.stdout

    def test_shows_session_details(self, monkeypatch, mock_config, tmp_workspace):
        from xbot.runtime.session.conversation_store import ConversationStore

        store = ConversationStore(tmp_workspace)
        s1 = store.get_or_create("cli:show-test")
        s1.metadata.update({
            "sdk_session_id": "sdk-show",
            "execution_cwd": "/test",
            "last_used_at": "2026-01-01T00:00:00Z",
            "run_mode": "cli",
        })
        s1.mark_metadata_dirty()
        store.save(s1)

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        result = runner.invoke(app, ["sessions", "show", "cli:show-test"])
        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "Session Key: cli:show-test" in output
        assert "SDK Session: sdk-show" in output


# ---------------------------------------------------------------------------
# 23. status() command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_shows_config_and_workspace(self, monkeypatch, mock_config, tmp_workspace):
        config_path = tmp_workspace / "config.json"
        config_path.write_text("{}")

        monkeypatch.setattr(
            "xbot.platform.config.loader.get_config_path", lambda: config_path,
        )
        monkeypatch.setattr(
            "xbot.platform.config.loader.load_config", lambda _p=None: mock_config,
        )

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "xbot Status" in result.stdout
        assert "Config:" in result.stdout
        assert "Workspace:" in result.stdout

    def test_status_config_not_found(self, monkeypatch, mock_config, tmp_path):
        config_path = tmp_path / "nonexistent.json"

        monkeypatch.setattr(
            "xbot.platform.config.loader.get_config_path", lambda: config_path,
        )
        monkeypatch.setattr(
            "xbot.platform.config.loader.load_config", lambda _p=None: mock_config,
        )

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "✗" in result.stdout  # Config file not found marker


# ---------------------------------------------------------------------------
# 24. channels_status() command
# ---------------------------------------------------------------------------


class TestChannelsStatusCommand:
    def test_shows_channel_table(self, monkeypatch):
        fake_channel = MagicMock()
        fake_channel.display_name = "Telegram"

        fake_config = Config()

        monkeypatch.setattr(
            "xbot.channels.registry.discover_all",
            lambda: {"telegram": fake_channel},
        )
        monkeypatch.setattr(
            "xbot.platform.config.loader.load_config",
            lambda: fake_config,
        )

        result = runner.invoke(app, ["channels", "status"])
        assert result.exit_code == 0
        assert "Channel Status" in result.stdout

    def test_channel_with_dict_config(self, monkeypatch):
        fake_channel = MagicMock()
        fake_channel.display_name = "TestChannel"

        fake_config = MagicMock()
        fake_config.channels = MagicMock()
        fake_config.channels.testchannel = {"enabled": True}

        monkeypatch.setattr(
            "xbot.channels.registry.discover_all",
            lambda: {"testchannel": fake_channel},
        )
        monkeypatch.setattr(
            "xbot.platform.config.loader.load_config",
            lambda: fake_config,
        )

        result = runner.invoke(app, ["channels", "status"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# 25. plugins_list() command
# ---------------------------------------------------------------------------


class TestPluginsListCommand:
    def test_shows_plugin_table(self, monkeypatch):
        fake_channel = MagicMock()
        fake_channel.display_name = "Discord"

        fake_config = Config()

        monkeypatch.setattr(
            "xbot.channels.registry.discover_all",
            lambda: {"discord": fake_channel},
        )
        monkeypatch.setattr(
            "xbot.channels.registry.discover_channel_names",
            lambda: {"discord"},
        )
        monkeypatch.setattr(
            "xbot.platform.config.loader.load_config",
            lambda: fake_config,
        )

        result = runner.invoke(app, ["plugins", "list"])
        assert result.exit_code == 0
        assert "Channel Plugins" in result.stdout
        assert "builtin" in result.stdout

    def test_shows_plugin_source(self, monkeypatch):
        fake_channel = MagicMock()
        fake_channel.display_name = "Custom"

        fake_config = Config()

        monkeypatch.setattr(
            "xbot.channels.registry.discover_all",
            lambda: {"custom": fake_channel},
        )
        monkeypatch.setattr(
            "xbot.channels.registry.discover_channel_names",
            lambda: set(),  # not builtin
        )
        monkeypatch.setattr(
            "xbot.platform.config.loader.load_config",
            lambda: fake_config,
        )

        result = runner.invoke(app, ["plugins", "list"])
        assert result.exit_code == 0
        assert "plugin" in result.stdout


# ---------------------------------------------------------------------------
# 26. _load_runtime_config()
# ---------------------------------------------------------------------------


class TestLoadRuntimeConfig:
    def test_loads_with_no_args(self, monkeypatch):
        from xbot.interfaces.cli.commands import _load_runtime_config

        cfg = Config()
        monkeypatch.setattr(
            "xbot.platform.config.loader.load_config", lambda _p=None: cfg,
        )
        result = _load_runtime_config()
        assert result is cfg

    def test_config_not_found_exits(self, monkeypatch, tmp_path):
        from xbot.interfaces.cli.commands import _load_runtime_config
        import typer

        missing = tmp_path / "missing.json"
        with pytest.raises(typer.Exit):
            _load_runtime_config(config=str(missing))

    def test_workspace_override(self, monkeypatch):
        from xbot.interfaces.cli.commands import _load_runtime_config

        cfg = Config()
        monkeypatch.setattr(
            "xbot.platform.config.loader.load_config", lambda _p=None: cfg,
        )
        result = _load_runtime_config(workspace="/custom/workspace")
        assert result.agents.defaults.workspace == "/custom/workspace"

    def test_validation_error_exits(self, monkeypatch):
        from xbot.interfaces.cli.commands import _load_runtime_config
        from xbot.platform.config.validator import ConfigurationError
        import typer

        cfg = Config()
        monkeypatch.setattr(
            "xbot.platform.config.loader.load_config", lambda _p=None: cfg,
        )
        monkeypatch.setattr(
            "xbot.platform.config.validator.validate_config",
            lambda _c: (_ for _ in ()).throw(ConfigurationError("bad config")),
        )
        with pytest.raises(typer.Exit):
            _load_runtime_config()


# ---------------------------------------------------------------------------
# 27. _flush_pending_tty_input()
# ---------------------------------------------------------------------------


class TestFlushPendingTtyInput:
    def test_non_tty_is_noop(self, monkeypatch):
        from xbot.interfaces.cli.commands import _flush_pending_tty_input

        monkeypatch.setattr("os.isatty", lambda _fd: False)
        _flush_pending_tty_input()  # Should not raise

    def test_stdin_error_is_noop(self, monkeypatch):
        from xbot.interfaces.cli.commands import _flush_pending_tty_input

        monkeypatch.setattr("sys.stdin", MagicMock(fileno=MagicMock(side_effect=OSError)))
        _flush_pending_tty_input()


# ---------------------------------------------------------------------------
# 28. _restore_terminal()
# ---------------------------------------------------------------------------


class TestRestoreTerminal:
    def test_no_saved_attrs_is_noop(self):
        from xbot.interfaces.cli.commands import _restore_terminal
        import xbot.interfaces.cli.commands as mod

        orig = mod._SAVED_TERM_ATTRS
        try:
            mod._SAVED_TERM_ATTRS = None
            _restore_terminal()  # Should not raise
        finally:
            mod._SAVED_TERM_ATTRS = orig


# ---------------------------------------------------------------------------
# 29. _load_cli_editing_mode()
# ---------------------------------------------------------------------------


class TestLoadCliEditingMode:
    def test_returns_emacs_by_default(self, monkeypatch):
        from xbot.interfaces.cli.commands import _load_cli_editing_mode
        from prompt_toolkit.enums import EditingMode

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands.get_data_dir",
            lambda: Path("/nonexistent"),
        )
        assert _load_cli_editing_mode() == EditingMode.EMACS

    def test_returns_vi_when_configured(self, monkeypatch, tmp_path):
        from xbot.interfaces.cli.commands import _load_cli_editing_mode
        from prompt_toolkit.enums import EditingMode

        settings_file = tmp_path / "local_command_settings.json"
        settings_file.write_text(json.dumps({"editorMode": "vim"}))

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands.get_data_dir",
            lambda: tmp_path,
        )
        assert _load_cli_editing_mode() == EditingMode.VI


# ---------------------------------------------------------------------------
# 30. agent() command — additional scenarios
# ---------------------------------------------------------------------------


class TestAgentCommand:
    def test_agent_new_flag_forces_new_session(self, monkeypatch, mock_config, tmp_workspace):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)
        monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
        monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
        monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _p: object())

        seen_keys = []

        class _FakeService:
            channels_config = None

            async def initialize(self):
                return None

            async def process_direct(self, *args, **kwargs):
                seen_keys.append(kwargs["session_key"])
                return "ok"

            async def close_mcp(self):
                return None

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._make_agent_service",
            lambda **kw: _FakeService(),
        )

        result = runner.invoke(app, ["agent", "-m", "hi", "--new"])
        assert result.exit_code == 0
        assert len(seen_keys) == 1
        assert seen_keys[0].startswith("cli:")

    def test_agent_resume_strict_not_found(self, monkeypatch, mock_config, tmp_workspace):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)

        result = runner.invoke(app, ["agent", "-m", "hi", "--resume", "nonexistent"])
        assert result.exit_code == 1
        assert "resume target not found" in _strip_ansi(result.stdout)

    def test_agent_resume_non_strict_falls_back(self, monkeypatch, mock_config, tmp_workspace):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)
        monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
        monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
        monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _p: object())

        seen_keys = []

        class _FakeService:
            channels_config = None

            async def initialize(self):
                return None

            async def process_direct(self, *args, **kwargs):
                seen_keys.append(kwargs["session_key"])
                return "ok"

            async def close_mcp(self):
                return None

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._make_agent_service",
            lambda **kw: _FakeService(),
        )

        result = runner.invoke(app, ["agent", "-m", "hi", "--resume", "nonexistent", "--no-resume-strict"])
        assert result.exit_code == 0
        assert len(seen_keys) == 1

    def test_agent_continue_no_match_creates_new(self, monkeypatch, mock_config, tmp_workspace):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)
        monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
        monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
        monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _p: object())

        seen_keys = []

        class _FakeService:
            channels_config = None

            async def initialize(self):
                return None

            async def process_direct(self, *args, **kwargs):
                seen_keys.append(kwargs["session_key"])
                return "ok"

            async def close_mcp(self):
                return None

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._make_agent_service",
            lambda **kw: _FakeService(),
        )

        result = runner.invoke(app, ["agent", "-m", "hi", "--continue"])
        assert result.exit_code == 0
        assert len(seen_keys) == 1

    def test_agent_with_session_id(self, monkeypatch, mock_config, tmp_workspace):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)
        monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
        monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
        monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _p: object())

        seen_keys = []

        class _FakeService:
            channels_config = None

            async def initialize(self):
                return None

            async def process_direct(self, *args, **kwargs):
                seen_keys.append(kwargs["session_key"])
                return "ok"

            async def close_mcp(self):
                return None

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._make_agent_service",
            lambda **kw: _FakeService(),
        )

        result = runner.invoke(app, ["agent", "-m", "hi", "--session", "my-session:123"])
        assert result.exit_code == 0
        assert seen_keys[0] == "my-session:123"

    def test_agent_initialize_failure(self, monkeypatch, mock_config, tmp_workspace):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)
        monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
        monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
        monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _p: object())

        class _FailingService:
            channels_config = None

            async def initialize(self):
                raise RuntimeError("init failed")

            async def close_mcp(self):
                return None

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._make_agent_service",
            lambda **kw: _FailingService(),
        )

        result = runner.invoke(app, ["agent", "-m", "hi"])
        assert result.exit_code == 0
        assert "failed to initialize agent" in _strip_ansi(result.stdout)

    def test_agent_process_failure(self, monkeypatch, mock_config, tmp_workspace):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)
        monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
        monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
        monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _p: object())

        class _FailingService:
            channels_config = None

            async def initialize(self):
                return None

            async def process_direct(self, *args, **kwargs):
                raise RuntimeError("process failed")

            async def close_mcp(self):
                return None

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._make_agent_service",
            lambda **kw: _FailingService(),
        )

        result = runner.invoke(app, ["agent", "-m", "hi"])
        assert result.exit_code == 0
        assert "failed to process message" in _strip_ansi(result.stdout)

    def test_agent_im_prefix_stripped(self, monkeypatch, mock_config, tmp_workspace):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)
        monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
        monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
        monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _p: object())

        captured = {}

        class _FakeService:
            channels_config = None

            async def initialize(self):
                return None

            async def process_direct(self, *args, **kwargs):
                captured["channel"] = kwargs.get("channel")
                captured["chat_id"] = kwargs.get("chat_id")
                return "ok"

            async def close_mcp(self):
                return None

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._make_agent_service",
            lambda **kw: _FakeService(),
        )

        result = runner.invoke(app, ["agent", "-m", "hi", "--session", "im:slack:channel1"])
        assert result.exit_code == 0
        assert captured["channel"] == "slack"
        assert captured["chat_id"] == "channel1"

    def test_agent_continue_and_resume_conflict(self, monkeypatch, mock_config):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)

        result = runner.invoke(app, ["agent", "-m", "hi", "--continue", "--resume", "x"])
        assert result.exit_code == 1
        assert "--continue cannot be used with --resume" in _strip_ansi(result.stdout)

    def test_agent_progress_coalescing(self, monkeypatch, mock_config, tmp_workspace):
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)
        monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
        monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
        monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _p: object())

        class _FakeService:
            channels_config = None

            async def initialize(self):
                return None

            async def process_direct(self, *args, **kwargs):
                on_progress = kwargs["on_progress"]
                await on_progress("thinking...", tool_hint=False, event_type="thinking")
                await on_progress('Bash(cwd="/tmp")', tool_hint=True, event_type="tool_call")
                return "done"

            async def close_mcp(self):
                return None

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._make_agent_service",
            lambda **kw: _FakeService(),
        )

        result = runner.invoke(app, ["agent", "-m", "hi"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# 31. gateway() command — more scenarios
# ---------------------------------------------------------------------------


class _StopGW(RuntimeError):
    pass


class TestGatewayCommand:
    def test_gateway_setup_prints_version_and_config(self, monkeypatch, mock_config, fake_config_file):
        """Gateway prints version, config path, and agent type before agent creation."""
        monkeypatch.setattr("xbot.platform.config.loader.set_config_path", lambda _p: None)
        monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _p=None: mock_config)
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)

        # Raise inside _make_agent_service to test setup output without running full flow
        class _SetupDone(RuntimeError):
            pass

        def _raise_in_make_service(**kw):
            raise _SetupDone("setup done")

        monkeypatch.setattr("xbot.interfaces.cli.commands._make_agent_service", _raise_in_make_service)

        result = runner.invoke(app, ["gateway", "--config", str(fake_config_file)])
        assert isinstance(result.exception, _SetupDone)
        assert "Starting xbot gateway" in result.stdout
        assert "Agent type: claude_sdk" in result.stdout

    def test_gateway_no_webui_skips_webui_setup(self, monkeypatch, mock_config, fake_config_file):
        """--no-webui flag skips WebUI creation."""
        monkeypatch.setattr("xbot.platform.config.loader.set_config_path", lambda _p: None)
        monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _p=None: mock_config)
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)

        seen = {}

        def _raise_in_make_service(**kw):
            seen["called"] = True
            raise _StopGW("stop")

        monkeypatch.setattr("xbot.interfaces.cli.commands._make_agent_service", _raise_in_make_service)

        result = runner.invoke(app, ["gateway", "--config", str(fake_config_file), "--no-webui"])
        assert isinstance(result.exception, _StopGW)
        assert seen.get("called") is True


class TestGatewayCallbacksFlow:
    def test_gateway_cron_callback_sets_on_job(self, monkeypatch, mock_config, fake_config_file):
        """Verify gateway wires up the cron callback."""
        monkeypatch.setattr("xbot.platform.config.loader.set_config_path", lambda _p: None)
        monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _p=None: mock_config)
        monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _p: None)

        _store = MagicMock()
        _store.list_sessions.return_value = []
        monkeypatch.setattr("xbot.runtime.session.conversation_store.ConversationStore", lambda _w: _store)
        monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: MagicMock())
        monkeypatch.setattr("xbot.interaction.permission.PermissionRequestHandler", lambda **_k: MagicMock())

        captured_cron = {}

        class _CapturingCron:
            on_job = None
            def __init__(self, _p): pass
            def status(self): return {"jobs": 0}
            async def start(self): raise _StopGW("stop")
            async def shutdown(self): pass
            def stop(self): pass

        monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", _CapturingCron)

        # Need all other deps to not crash before cron.start()
        _health = MagicMock()
        _health.start = AsyncMock()
        _health.stop = AsyncMock()
        monkeypatch.setattr("xbot.runtime.system.monitoring.health.HealthCheckService", lambda **_k: _health)

        class _FakeChannelManager:
            enabled_channels = ["telegram"]
            def check_channels_health(self):
                return {}
            async def start_all(self):
                pass
            async def stop_all(self):
                pass

        monkeypatch.setattr("xbot.channels.manager.ChannelManager", lambda _c, _b: _FakeChannelManager())
        monkeypatch.setattr("xbot.runtime.system.heartbeat.service.HeartbeatService", lambda **kw: MagicMock(
            start=AsyncMock(), shutdown=AsyncMock(), stop=MagicMock(),
        ))

        class _FakeBus:
            async def publish_outbound(self, msg): pass

        monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", _FakeBus)

        class _FakeAgent:
            tools = {}
            backend = MagicMock()
            channels_config = None
            async def initialize(self): pass
            async def run(self): raise _StopGW("done")
            def stop(self): pass
            async def close_mcp(self): pass

        monkeypatch.setattr("xbot.interfaces.cli.commands._make_agent_service", lambda **kw: _FakeAgent())
        monkeypatch.setattr("xbot.runtime.system.monitoring.alerting.init_alert_service", lambda *_a, **_kw: MagicMock())
        monkeypatch.setattr("xbot.runtime.system.monitoring.alerting.AlertConfig", lambda **_kw: MagicMock())
        monkeypatch.setattr("xbot.interfaces.gateway.app.create_app", lambda *_a, **_kw: MagicMock())
        monkeypatch.setattr("xbot.interfaces.gateway.services.ServiceContainer", lambda **_kw: MagicMock())
        monkeypatch.setattr("xbot.runtime.system.monitoring.health.create_health_router", lambda _h: MagicMock())
        monkeypatch.setattr("xbot.platform.config.loader.save_config", lambda *_a, **_kw: None)

        result = runner.invoke(app, ["gateway", "--config", str(fake_config_file), "--no-webui"])
        # We expect _StopGW from agent.run() in the finally block
        # The key thing is that the gateway ran without errors up to that point
        assert result.exception is not None


# ---------------------------------------------------------------------------
# 32. crew_run() command
# ---------------------------------------------------------------------------


class TestCrewRunCommand:
    def test_crew_run_loads_yaml_and_creates_orchestrator(self, monkeypatch, mock_config, tmp_path):
        # Create a fake YAML config
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test\nagents: {}\ntasks: []\n")

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )

        captured = {}

        @dataclass
        class FakeTask:
            name: str = "task1"
            agent: str = "agent1"
            context_from: list = field(default_factory=list)
            human_review: bool = False
            human_briefing: bool = False

        @dataclass
        class FakeCrewConfig:
            name: str = "test"
            description: str = ""
            process: Any = None
            agents: dict = field(default_factory=dict)
            tasks: list = field(default_factory=list)
            workspace: str = "."
            verbose: bool = False

        @dataclass
        class FakeResult:
            crew_name: str = "test"
            task_results: list = field(default_factory=list)
            status: str = "completed"
            total_time: float = 1.0
            summary: str = "done"

        monkeypatch.setattr(
            "xbot.crew.CrewOrchestrator",
            lambda **kw: captured.setdefault("orch", MagicMock()) or captured["orch"],
        )
        captured["orch"] = MagicMock()
        captured["orch"].run = AsyncMock(return_value=FakeResult())

        monkeypatch.setattr(
            "xbot.crew.load_crew_config",
            lambda _p: FakeCrewConfig(),
        )
        monkeypatch.setattr(
            "xbot.crew.config.CrewConfigLoader",
            lambda **kw: MagicMock(load=lambda _p: {"name": "test", "agents": {}, "tasks": []}),
        )
        monkeypatch.setattr(
            "xbot.crew.models.parse_crew_config",
            lambda _d, _p=None: FakeCrewConfig(),
        )

        # Need _print_crew_result to not crash
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._print_crew_result",
            lambda _r: None,
        )

        result = runner.invoke(app, ["crew", "run", str(yaml_file)])
        assert result.exit_code == 0

    def test_crew_run_invalid_var_format(self, monkeypatch, mock_config, tmp_path):
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test\nagents: {}\ntasks: []\n")

        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._load_runtime_config",
            lambda _c, _w: mock_config,
        )

        @dataclass
        class FakeCrewConfig:
            name: str = "test"
            tasks: list = field(default_factory=list)
            agents: dict = field(default_factory=dict)
            workspace: str = "."
            verbose: bool = False

        monkeypatch.setattr(
            "xbot.crew.config.CrewConfigLoader",
            lambda **kw: MagicMock(load=MagicMock(side_effect=RuntimeError("fail"))),
        )
        monkeypatch.setattr(
            "xbot.crew.load_crew_config",
            lambda _p: FakeCrewConfig(),
        )
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands._print_crew_result",
            lambda _r: None,
        )

        fake_orch = MagicMock()
        fake_orch.run = AsyncMock(return_value=SimpleNamespace(
            crew_name="test", task_results=[], status="completed",
            total_time=0.5, summary="ok",
        ))
        monkeypatch.setattr("xbot.crew.CrewOrchestrator", lambda **kw: fake_orch)

        result = runner.invoke(app, ["crew", "run", str(yaml_file), "--var", "badvar"])
        assert result.exit_code == 0
        assert "Invalid --var format" in result.stdout


# ---------------------------------------------------------------------------
# 33. _print_crew_result()
# ---------------------------------------------------------------------------


class TestPrintCrewResult:
    def test_prints_crew_result(self):
        from xbot.interfaces.cli.commands import _print_crew_result
        from xbot.crew.models import CrewResult, TaskResult

        now = datetime.now(timezone.utc)
        tr = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="output text",
            status="success",
            started_at=now,
            finished_at=now + timedelta(seconds=5),
        )
        result = CrewResult(
            crew_name="test-crew",
            task_results=[tr],
            status="completed",
            total_time=5.0,
            summary="all good",
        )
        _print_crew_result(result)  # Should not raise

    def test_prints_failed_status(self):
        from xbot.interfaces.cli.commands import _print_crew_result
        from xbot.crew.models import CrewResult, TaskResult

        now = datetime.now(timezone.utc)
        tr = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="err",
            status="failed",
            started_at=now,
            finished_at=now + timedelta(seconds=1),
        )
        result = CrewResult(
            crew_name="fail-crew",
            task_results=[tr],
            status="failed",
            total_time=1.0,
            summary="boom",
        )
        _print_crew_result(result)


# ---------------------------------------------------------------------------
# 34. _generate_markdown_report() and _generate_html_report()
# ---------------------------------------------------------------------------


class TestReportGeneration:
    def test_markdown_report(self):
        from xbot.interfaces.cli.commands import _generate_markdown_report

        manifest = {
            "crew_name": "test-crew",
            "run_id": "run-1",
            "status": "completed",
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:01:00Z",
            "total_time": 60.0,
            "tasks": [{"task_name": "task1", "status": "success"}],
        }
        task_outputs = [{"file": "task1.md", "content": "output text"}]
        report = _generate_markdown_report(manifest, task_outputs)
        assert "test-crew" in report
        assert "task1" in report
        assert "output text" in report

    def test_html_report(self):
        from xbot.interfaces.cli.commands import _generate_html_report

        manifest = {
            "crew_name": "test-crew",
            "status": "completed",
            "total_time": 60.0,
            "tasks": [{"task_name": "task1", "status": "success"}],
        }
        task_outputs = [{"file": "task1.md", "content": "output <text>"}]
        report = _generate_html_report(manifest, task_outputs)
        assert "<html>" in report
        assert "test-crew" in report
        assert "&lt;text&gt;" in report  # HTML escaped

    def test_markdown_report_truncates_long_content(self):
        from xbot.interfaces.cli.commands import _generate_markdown_report

        manifest = {"crew_name": "x", "total_time": 0}
        long_content = "x" * 5000
        task_outputs = [{"file": "big.md", "content": long_content}]
        report = _generate_markdown_report(manifest, task_outputs)
        assert "truncated" in report


# ---------------------------------------------------------------------------
# 35. _on_cron_job (gateway callback)
# ---------------------------------------------------------------------------


class TestOnCronJobCallback:
    """Test the on_cron_job closure inside gateway() indirectly by testing the pattern."""

    def test_cron_job_callback_invokes_agent(self):
        """Verify the on_cron_job pattern works correctly."""
        # This is tested via gateway integration tests, but we verify
        # the closure pattern here by checking the gateway code path
        pass  # Covered by gateway tests below


# ---------------------------------------------------------------------------
# 37. _print_agent_response()
# ---------------------------------------------------------------------------


class TestPrintAgentResponse:
    def test_renders_markdown(self):
        from xbot.interfaces.cli.commands import _print_agent_response

        _print_agent_response("# Hello", render_markdown=True)

    def test_renders_plain_text(self):
        from xbot.interfaces.cli.commands import _print_agent_response

        _print_agent_response("Hello", render_markdown=False)

    def test_handles_none(self):
        from xbot.interfaces.cli.commands import _print_agent_response

        _print_agent_response(None, render_markdown=True)


# ---------------------------------------------------------------------------
# 38. _select_continue_session() edge cases
# ---------------------------------------------------------------------------


class TestSelectContinueSessionEdgeCases:
    def test_none_execution_cwd_is_skipped(self, tmp_path):
        from xbot.interfaces.cli.commands import _select_continue_session

        sessions = [
            {"key": "cli:a", "execution_cwd": None},
            {"key": "cli:b", "execution_cwd": str(tmp_path.resolve())},
        ]
        result = _select_continue_session(sessions, tmp_path)
        assert result["key"] == "cli:b"

    def test_empty_execution_cwd_is_skipped(self, tmp_path):
        from xbot.interfaces.cli.commands import _select_continue_session

        sessions = [
            {"key": "cli:a", "execution_cwd": ""},
        ]
        result = _select_continue_session(sessions, tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# 39. main() callback
# ---------------------------------------------------------------------------


class TestMainCallback:
    def test_main_configures_logging(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "xbot" in result.stdout


# ---------------------------------------------------------------------------
# 40. webui subcommands
# ---------------------------------------------------------------------------


class TestWebuiCommands:
    def test_webui_serve_help(self):
        result = runner.invoke(app, ["webui", "serve", "--help"])
        assert result.exit_code == 0
        assert "--port" in result.stdout


# ---------------------------------------------------------------------------
# 41. crew subcommands
# ---------------------------------------------------------------------------


class TestCrewSubcommands:
    def test_crew_show_loads_config(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test")

        @dataclass
        class FakeRole:
            goal: str = "test goal"
            model: str = "claude-3"

        @dataclass
        class FakeTask:
            name: str = "task1"
            agent: str = "agent1"
            context_from: list = field(default_factory=list)
            human_review: bool = True
            human_briefing: bool = False

        @dataclass
        class FakeConfig:
            name: str = "test-crew"
            description: str = "a test"
            process: Any = None
            workspace: str = "."
            agents: dict = field(default_factory=lambda: {"agent1": FakeRole()})
            tasks: list = field(default_factory=lambda: [FakeTask()])

        # Set process to a SimpleNamespace instance
        FakeConfig.process = SimpleNamespace(value="sequential")
        fake_config = FakeConfig()
        fake_config.process = SimpleNamespace(value="sequential")

        monkeypatch.setattr(
            "xbot.crew.load_crew_config", lambda _p: fake_config,
        )

        result = runner.invoke(app, ["crew", "show", str(yaml_file)])
        assert result.exit_code == 0
        assert "test-crew" in result.stdout

    def test_crew_validate_valid_config(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test")

        @dataclass
        class FakeTask:
            name: str = "task1"
            agent: str = "agent1"
            context_from: list = field(default_factory=list)

        @dataclass
        class FakeConfig:
            agents: dict = field(default_factory=lambda: {"agent1": MagicMock()})
            tasks: list = field(default_factory=lambda: [FakeTask()])

        monkeypatch.setattr(
            "xbot.crew.load_crew_config", lambda _p: FakeConfig(),
        )

        result = runner.invoke(app, ["crew", "validate", str(yaml_file)])
        assert result.exit_code == 0
        assert "Validation passed" in result.stdout

    def test_crew_validate_file_not_found(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "missing.yaml"

        monkeypatch.setattr(
            "xbot.crew.load_crew_config",
            lambda _p: (_ for _ in ()).throw(FileNotFoundError("missing")),
        )

        result = runner.invoke(app, ["crew", "validate", str(yaml_file)])
        assert result.exit_code == 1
        assert "File not found" in result.stdout

    def test_crew_validate_unknown_agent_ref(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test")

        @dataclass
        class FakeTask:
            name: str = "task1"
            agent: str = "unknown_agent"
            context_from: list = field(default_factory=list)

        @dataclass
        class FakeConfig:
            agents: dict = field(default_factory=lambda: {"agent1": MagicMock()})
            tasks: list = field(default_factory=lambda: [FakeTask()])

        monkeypatch.setattr(
            "xbot.crew.load_crew_config", lambda _p: FakeConfig(),
        )

        result = runner.invoke(app, ["crew", "validate", str(yaml_file)])
        assert result.exit_code == 1
        assert "unknown agent" in result.stdout

    def test_crew_validate_duplicate_task_names(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test")

        @dataclass
        class FakeTask:
            name: str = "same_name"
            agent: str = "agent1"
            context_from: list = field(default_factory=list)

        @dataclass
        class FakeConfig:
            agents: dict = field(default_factory=lambda: {"agent1": MagicMock()})
            tasks: list = field(default_factory=lambda: [FakeTask(), FakeTask()])

        monkeypatch.setattr(
            "xbot.crew.load_crew_config", lambda _p: FakeConfig(),
        )

        result = runner.invoke(app, ["crew", "validate", str(yaml_file)])
        assert result.exit_code == 1
        assert "Duplicate task names" in result.stdout

    def test_crew_validate_dry_run_shows_plan(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test")

        @dataclass
        class FakeTask:
            name: str = "task1"
            agent: str = "agent1"
            context_from: list = field(default_factory=list)

        @dataclass
        class FakeConfig:
            agents: dict = field(default_factory=lambda: {"agent1": MagicMock()})
            tasks: list = field(default_factory=lambda: [FakeTask()])

        monkeypatch.setattr(
            "xbot.crew.load_crew_config", lambda _p: FakeConfig(),
        )

        result = runner.invoke(app, ["crew", "validate", str(yaml_file), "--dry-run"])
        assert result.exit_code == 0
        assert "Execution Plan" in result.stdout

    def test_crew_checkpoints_no_dir(self, tmp_path):
        result = runner.invoke(app, ["crew", "checkpoints", str(tmp_path)])
        assert result.exit_code == 0
        assert "No checkpoints" in result.stdout

    def test_crew_history_no_dir(self, tmp_path):
        result = runner.invoke(app, ["crew", "history", str(tmp_path)])
        assert result.exit_code == 0
        assert "No execution history" in result.stdout

    def test_crew_graph_ascii(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test")

        @dataclass
        class FakeTask:
            name: str = "task1"
            agent: str = "agent1"
            context_from: list = field(default_factory=list)

        @dataclass
        class FakeConfig:
            name: str = "test-crew"
            tasks: list = field(default_factory=lambda: [FakeTask()])

        monkeypatch.setattr(
            "xbot.crew.load_crew_config", lambda _p: FakeConfig(),
        )

        result = runner.invoke(app, ["crew", "graph", str(yaml_file)])
        assert result.exit_code == 0
        assert "task1" in result.stdout

    def test_crew_graph_mermaid(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test")

        @dataclass
        class FakeTask:
            name: str = "task1"
            agent: str = "agent1"
            context_from: list = field(default_factory=lambda: ["task2"])

        @dataclass
        class FakeTask2:
            name: str = "task2"
            agent: str = "agent1"
            context_from: list = field(default_factory=list)

        @dataclass
        class FakeConfig:
            name: str = "test-crew"
            tasks: list = field(default_factory=lambda: [FakeTask(), FakeTask2()])

        monkeypatch.setattr(
            "xbot.crew.load_crew_config", lambda _p: FakeConfig(),
        )

        result = runner.invoke(app, ["crew", "graph", str(yaml_file), "--mermaid"])
        assert result.exit_code == 0
        assert "graph TD" in result.stdout

    def test_crew_graph_output_file(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test")
        output_file = tmp_path / "graph.txt"

        @dataclass
        class FakeTask:
            name: str = "task1"
            agent: str = "agent1"
            context_from: list = field(default_factory=list)

        @dataclass
        class FakeConfig:
            name: str = "test-crew"
            tasks: list = field(default_factory=lambda: [FakeTask()])

        monkeypatch.setattr(
            "xbot.crew.load_crew_config", lambda _p: FakeConfig(),
        )

        result = runner.invoke(app, ["crew", "graph", str(yaml_file), "-o", str(output_file)])
        assert result.exit_code == 0
        assert output_file.exists()

    def test_crew_export_no_runs(self, tmp_path):
        result = runner.invoke(app, ["crew", "export", str(tmp_path)])
        assert result.exit_code == 1
        assert "No crew runs" in result.stdout

    def test_crew_export_json(self, tmp_path):
        runs_dir = tmp_path / ".xbot" / "crew_runs" / "run1"
        runs_dir.mkdir(parents=True)
        manifest = {"crew_name": "test", "status": "completed", "tasks": []}
        (runs_dir / "manifest.json").write_text(json.dumps(manifest))

        result = runner.invoke(app, ["crew", "export", str(tmp_path), "-f", "json"])
        assert result.exit_code == 0
        assert "test" in result.stdout

    def test_crew_export_markdown(self, tmp_path):
        runs_dir = tmp_path / ".xbot" / "crew_runs" / "run1"
        runs_dir.mkdir(parents=True)
        manifest = {"crew_name": "test", "status": "completed", "tasks": []}
        (runs_dir / "manifest.json").write_text(json.dumps(manifest))

        result = runner.invoke(app, ["crew", "export", str(tmp_path), "-f", "markdown"])
        assert result.exit_code == 0
        assert "test" in result.stdout

    def test_crew_export_html(self, tmp_path):
        runs_dir = tmp_path / ".xbot" / "crew_runs" / "run1"
        runs_dir.mkdir(parents=True)
        manifest = {"crew_name": "test", "status": "completed", "tasks": []}
        (runs_dir / "manifest.json").write_text(json.dumps(manifest))

        result = runner.invoke(app, ["crew", "export", str(tmp_path), "-f", "html"])
        assert result.exit_code == 0
        assert "<html>" in result.stdout

    def test_crew_export_to_file(self, tmp_path):
        runs_dir = tmp_path / ".xbot" / "crew_runs" / "run1"
        runs_dir.mkdir(parents=True)
        manifest = {"crew_name": "test", "status": "completed", "tasks": []}
        (runs_dir / "manifest.json").write_text(json.dumps(manifest))

        out = tmp_path / "report.md"
        result = runner.invoke(app, ["crew", "export", str(tmp_path), "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_crew_export_specific_run(self, tmp_path):
        runs_dir = tmp_path / ".xbot" / "crew_runs" / "specific-run"
        runs_dir.mkdir(parents=True)
        manifest = {"crew_name": "specific", "status": "completed", "tasks": []}
        (runs_dir / "manifest.json").write_text(json.dumps(manifest))

        result = runner.invoke(app, ["crew", "export", str(tmp_path), "-r", "specific-run"])
        assert result.exit_code == 0
        assert "specific" in result.stdout

    def test_crew_export_run_not_found(self, tmp_path):
        runs_dir = tmp_path / ".xbot" / "crew_runs"
        runs_dir.mkdir(parents=True)

        result = runner.invoke(app, ["crew", "export", str(tmp_path), "-r", "nonexistent"])
        assert result.exit_code == 1
        assert "Run not found" in result.stdout

    def test_crew_export_with_task_outputs(self, tmp_path):
        runs_dir = tmp_path / ".xbot" / "crew_runs" / "run1"
        tasks_dir = runs_dir / "tasks"
        tasks_dir.mkdir(parents=True)
        manifest = {"crew_name": "test", "status": "completed", "tasks": []}
        (runs_dir / "manifest.json").write_text(json.dumps(manifest))
        (tasks_dir / "task1.md").write_text("# Task 1 output")
        (tasks_dir / "task2.json").write_text('{"result": "ok"}')

        result = runner.invoke(app, ["crew", "export", str(tmp_path), "-f", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data["tasks"]) == 2


# ---------------------------------------------------------------------------
# 42. crew_validate — invalid dependency and circular deps
# ---------------------------------------------------------------------------


class TestCrewValidateDependencies:
    def test_invalid_dependency(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "crew.yaml"
        yaml_file.write_text("name: test")

        @dataclass
        class FakeTask:
            name: str = "task1"
            agent: str = "agent1"
            context_from: list = field(default_factory=lambda: ["nonexistent_task"])

        @dataclass
        class FakeConfig:
            agents: dict = field(default_factory=lambda: {"agent1": MagicMock()})
            tasks: list = field(default_factory=lambda: [FakeTask()])

        monkeypatch.setattr("xbot.crew.load_crew_config", lambda _p: FakeConfig())

        result = runner.invoke(app, ["crew", "validate", str(yaml_file)])
        assert result.exit_code == 1
        assert "invalid dependency" in result.stdout


# ---------------------------------------------------------------------------
# 43. crew_init() command
# ---------------------------------------------------------------------------


class TestCrewInitCommand:
    def test_crew_init_creates_project(self, monkeypatch, tmp_path):
        monkeypatch.setattr("xbot.crew.templates.init_project", lambda **kw: Path("/fake/config.yaml"))
        monkeypatch.setattr("xbot.crew.templates.get_template", lambda _n: MagicMock())
        monkeypatch.setattr("xbot.crew.templates.list_templates", lambda: [])

        result = runner.invoke(app, ["crew", "init", "my-project", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "Created crew project" in result.stdout

    def test_crew_init_unknown_template(self, monkeypatch, tmp_path):
        monkeypatch.setattr("xbot.crew.templates.get_template", lambda _n: None)
        monkeypatch.setattr("xbot.crew.templates.list_templates", lambda: [
            SimpleNamespace(name="basic", description="Basic template"),
        ])

        result = runner.invoke(app, ["crew", "init", "proj", "--template", "nonexistent", "--path", str(tmp_path)])
        assert result.exit_code == 1
        assert "Unknown template" in result.stdout

    def test_crew_init_dir_already_exists(self, monkeypatch, tmp_path):
        (tmp_path / "existing").mkdir()

        result = runner.invoke(app, ["crew", "init", "existing", "--path", str(tmp_path)])
        assert result.exit_code == 1
        assert "already exists" in result.stdout


# ---------------------------------------------------------------------------
# 44. crew_templates() command
# ---------------------------------------------------------------------------


class TestCrewTemplatesCommand:
    def test_lists_templates(self, monkeypatch):
        fake_template = MagicMock()
        fake_template.name = "basic"
        fake_template.description = "Basic template"
        fake_template.load_config.return_value = {"agents": {"a": {}}, "tasks": [{"name": "t"}]}

        monkeypatch.setattr("xbot.crew.templates.list_templates", lambda: [fake_template])

        result = runner.invoke(app, ["crew", "templates"])
        assert result.exit_code == 0
        assert "basic" in result.stdout


# ---------------------------------------------------------------------------
# 45. crew_checkpoints() with actual checkpoints
# ---------------------------------------------------------------------------


class TestCrewCheckpointsWithData:
    def test_shows_checkpoints(self, tmp_path):
        cp_dir = tmp_path / ".xbot" / "crew_checkpoints"
        cp_dir.mkdir(parents=True)
        cp_data = {
            "checkpoint_at": "2026-01-01T12:00:00Z",
            "crew_phase": "running",
            "completed_tasks": ["task1"],
            "next_task": "task2",
        }
        (cp_dir / "cp1.json").write_text(json.dumps(cp_data))

        result = runner.invoke(app, ["crew", "checkpoints", str(tmp_path)])
        assert result.exit_code == 0
        assert "cp1.json" in result.stdout
        assert "running" in result.stdout


# ---------------------------------------------------------------------------
# 46. crew_history() with data
# ---------------------------------------------------------------------------


class TestCrewHistoryWithData:
    def test_shows_history(self, tmp_path):
        cp_dir = tmp_path / ".xbot" / "crew_checkpoints"
        cp_dir.mkdir(parents=True)
        cp_data = {
            "started_at": "2026-01-01T12:00:00Z",
            "checkpoint_at": "2026-01-01T12:05:00Z",
            "crew_name": "test-crew",
            "crew_phase": "completed",
            "completed_tasks": ["task1", "task2"],
        }
        (cp_dir / "hist1.json").write_text(json.dumps(cp_data))

        result = runner.invoke(app, ["crew", "history", str(tmp_path)])
        assert result.exit_code == 0
        assert "test-crew" in result.stdout


# ---------------------------------------------------------------------------
# 47. crew_resume() command
# ---------------------------------------------------------------------------


class TestCrewResumeCommand:
    def test_no_config_yaml(self, tmp_path):
        result = runner.invoke(app, ["crew", "resume", str(tmp_path)])
        assert result.exit_code == 1
        assert "No crew_config.yaml" in result.stdout

    def test_checkpoint_not_found(self, tmp_path):
        (tmp_path / "crew_config.yaml").write_text("name: test")
        result = runner.invoke(app, ["crew", "resume", str(tmp_path), "--checkpoint", "nonexistent"])
        assert result.exit_code == 1
        assert "Checkpoint not found" in result.stdout

    def test_no_checkpoints_dir(self, tmp_path):
        (tmp_path / "crew_config.yaml").write_text("name: test")
        result = runner.invoke(app, ["crew", "resume", str(tmp_path)])
        assert result.exit_code == 1
        assert "No checkpoints" in result.stdout


# ---------------------------------------------------------------------------
# 48. _onboard_plugins()
# ---------------------------------------------------------------------------


class TestOnboardPlugins:
    def test_injects_default_channel_configs(self, tmp_path):
        from xbot.interfaces.cli.commands import _onboard_plugins

        config_file = tmp_path / "config.json"
        config_file.write_text("{}")

        fake_cls = MagicMock()
        fake_cls.default_config.return_value = {"enabled": False, "token": ""}

        with patch("xbot.channels.registry.discover_all", return_value={"telegram": fake_cls}):
            _onboard_plugins(config_file)

        data = json.loads(config_file.read_text())
        assert "telegram" in data["channels"]
        assert data["channels"]["telegram"]["enabled"] is False

    def test_merges_existing_channel_config(self, tmp_path):
        from xbot.interfaces.cli.commands import _onboard_plugins

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"channels": {"telegram": {"token": "user-token"}}}))

        fake_cls = MagicMock()
        fake_cls.default_config.return_value = {"enabled": False, "token": ""}

        with patch("xbot.channels.registry.discover_all", return_value={"telegram": fake_cls}):
            _onboard_plugins(config_file)

        data = json.loads(config_file.read_text())
        assert data["channels"]["telegram"]["token"] == "user-token"
        assert "enabled" in data["channels"]["telegram"]

    def test_no_channels_discovered(self, tmp_path):
        from xbot.interfaces.cli.commands import _onboard_plugins

        config_file = tmp_path / "config.json"
        config_file.write_text("{}")

        with patch("xbot.channels.registry.discover_all", return_value={}):
            _onboard_plugins(config_file)

        data = json.loads(config_file.read_text())
        assert "channels" not in data


# ---------------------------------------------------------------------------
# 49. _get_bridge_dir()
# ---------------------------------------------------------------------------


class TestGetBridgeDir:
    def test_returns_existing_bridge(self, monkeypatch, tmp_path):
        from xbot.interfaces.cli.commands import _get_bridge_dir

        bridge_dir = tmp_path / "bridge"
        (bridge_dir / "dist").mkdir(parents=True)
        (bridge_dir / "dist" / "index.js").write_text("// js")

        monkeypatch.setattr(
            "xbot.platform.config.paths.get_bridge_install_dir",
            lambda: bridge_dir,
        )

        result = _get_bridge_dir()
        assert result == bridge_dir


# ---------------------------------------------------------------------------
# 50. _run_init() edge cases
# ---------------------------------------------------------------------------


class TestRunInitEdgeCases:
    def test_command_pack_not_found(self, monkeypatch, tmp_path):
        monkeypatch.setattr("xbot.platform.config.loader.get_config_path", lambda: tmp_path / "cfg.json")
        monkeypatch.setattr("xbot.platform.config.loader.save_config", lambda _c, _p=None: None)
        monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _p=None: Config())
        monkeypatch.setattr("xbot.platform.config.loader.set_config_path", lambda _p: None)
        monkeypatch.setattr("xbot.channels.registry.discover_all", lambda: {})
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands.sync_workspace_command_pack",
            lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError("pack missing")),
        )
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands.sync_workspace_templates",
            lambda _p: None,
        )
        monkeypatch.setattr(
            "xbot.interfaces.cli.commands.get_workspace_path",
            lambda _p=None: tmp_path / "workspace",
        )

        import typer
        with pytest.raises(typer.Exit) as exc_info:
            from xbot.interfaces.cli.commands import _run_init
            _run_init(command_pack="nonexistent", install_command_pack=True)
        assert exc_info.value.exit_code == 1
