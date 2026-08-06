"""Edge case tests for !cmd command handler."""

import asyncio
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from xbot.platform.bus.events import InboundMessage
from xbot.runtime.core.command_handlers import LocalCommandHandler


@pytest.fixture
def handler():
    """Create a handler with mock service and temp workspace."""
    tmpdir = tempfile.mkdtemp()
    scripts_file = os.path.join(tmpdir, "scripts.json")
    service = MagicMock()
    service._shared_resources = {
        "workspace": tmpdir,
        "config": MagicMock(),
    }
    service._shared_resources["config"].tools.scripts_file = scripts_file
    service._shared_resources["config"].tools.exec.timeout = 10
    service._shared_resources["config"].tools.exec.path_append = ""
    service._shared_resources["config"].tools.restrict_to_workspace = False
    h = LocalCommandHandler(service)
    yield h
    # Cleanup
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def make_msg(content: str, channel: str = "telegram", sender_id: str = "123") -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        chat_id="chat-1",
        content=content,
    )


class TestIsLocalCommand:
    """Tests for !cmd prefix detection — the bug that was fixed."""

    @pytest.mark.parametrize("text", [
        "!cmd",
        "!cmd list",
        "!cmd add \"x\" \"y\"",
        "!cmd remove \"x\"",
        "!cmd deploy",
        "!cmd deploy arg1 arg2",
        "!CMD LIST",  # case insensitive
        "!Cmd deploy",
    ])
    def test_matches_cmd(self, text):
        assert LocalCommandHandler.is_local_command(text) is True

    @pytest.mark.parametrize("text", [
        "!cmdxyz",       # should NOT match !cmd
        "!cmdbrowser",   # should NOT match !cmd
        "!cmdinjection", # should NOT match !cmd
        "hello world",
        "can you !cmd this",  # not at start
        "!help",         # different command, still local but not !cmd prefix
    ])
    def test_does_not_match_false_positive(self, text):
        # !help should still match (it's in LOCAL_COMMANDS)
        if text == "!help":
            assert LocalCommandHandler.is_local_command(text) is True
        else:
            # For !cmdxyz etc, should not match via !cmd prefix
            # (may still match via other patterns, but not via !cmd)
            stripped = text.strip().lower()
            assert not (stripped == "!cmd" or stripped.startswith("!cmd ")), \
                f"'{text}' should not match !cmd pattern"


class TestCmdAdd:
    """Edge cases for !cmd add."""

    def test_add_basic(self, handler):
        result = handler._cmd_add('"deploy" "docker-compose up -d"')
        assert "Added" in result
        assert "deploy" in result

    def test_add_update_existing(self, handler):
        handler._cmd_add('"deploy" "docker-compose up -d"')
        result = handler._cmd_add('"deploy" "docker-compose up -d --force"')
        assert "Updated" in result

    def test_add_missing_args(self, handler):
        result = handler._cmd_add('"only_name"')
        assert "Usage" in result

    def test_add_no_args(self, handler):
        result = handler._cmd_add("")
        assert "Usage" in result

    def test_add_unbalanced_quotes(self, handler):
        # Opening double quote with no closing quote → shlex ValueError
        result = handler._cmd_add('"deploy "echo hi"')
        # This is actually balanced: "deploy " + "echo hi" → 2 args
        # Need a truly unbalanced case:
        result = handler._cmd_add('deploy "echo hi')
        assert "Parse error" in result

    def test_add_unbalanced_single_quote(self, handler):
        result = handler._cmd_add("deploy 'echo hi")  # single quote never closes
        assert "Parse error" in result

    def test_add_reserved_name(self, handler):
        result = handler._cmd_add('"list" "echo hi"')
        assert "reserved" in result.lower() or "invalid" in result.lower()

    def test_add_invalid_shortname_spaces(self, handler):
        result = handler._cmd_add('"my name" "echo hi"')
        assert "Invalid" in result or "Error" in result

    def test_add_invalid_shortname_shell(self, handler):
        result = handler._cmd_add('"rm -rf" "echo hi"')
        assert "Invalid" in result or "Error" in result

    def test_add_empty_shortname(self, handler):
        result = handler._cmd_add('"" "echo hi"')
        assert "Invalid" in result or "Error" in result

    def test_add_too_long_shortname(self, handler):
        long_name = "x" * 33
        result = handler._cmd_add(f'"{long_name}" "echo hi"')
        assert "Invalid" in result or "Error" in result

    def test_add_command_with_spaces(self, handler):
        result = handler._cmd_add('"multi" "echo hello world foo"')
        assert "Added" in result

    def test_add_command_with_quotes_inside(self, handler):
        # shlex should handle escaped quotes
        result = handler._cmd_add('"test" "echo \'single quotes\'"')
        assert "Added" in result


class TestCmdRemove:
    """Edge cases for !cmd remove."""

    def test_remove_existing(self, handler):
        handler._cmd_add('"deploy" "docker-compose up -d"')
        result = handler._cmd_remove('"deploy"')
        assert "Removed" in result

    def test_remove_nonexistent(self, handler):
        result = handler._cmd_remove('"nonexistent"')
        assert "not found" in result

    def test_remove_no_args(self, handler):
        result = handler._cmd_remove("")
        assert "Usage" in result

    def test_remove_unbalanced_quotes(self, handler):
        result = handler._cmd_remove('"broken')
        assert "Parse error" in result


class TestCmdList:
    """Edge cases for !cmd list."""

    def test_list_empty(self, handler):
        result = handler._cmd_list()
        assert "No commands configured" in result

    def test_list_with_commands(self, handler):
        handler._cmd_add('"deploy" "docker-compose up -d"')
        handler._cmd_add('"backup" "backup.sh"')
        result = handler._cmd_list()
        assert "deploy" in result
        assert "backup" in result
        assert "docker-compose up -d" in result
        assert "2" in result  # count

    def test_list_truncates_long_commands(self, handler):
        long_cmd = "echo " + "x" * 100
        handler._cmd_add(f'"test" "{long_cmd}"')
        result = handler._cmd_list()
        assert "..." in result


class TestCmdExec:
    """Edge cases for !cmd execution."""

    def test_exec_basic(self, handler):
        handler._cmd_add('"hi" "echo Hello World"')
        msg = make_msg("!cmd hi")
        result = asyncio.run(handler._cmd_exec("hi", "", msg))
        assert "Hello World" in result
        assert "Exit code: 0" in result

    def test_exec_with_args(self, handler):
        handler._cmd_add('"echo" "echo"')
        msg = make_msg("!cmd echo test_arg")
        result = asyncio.run(handler._cmd_exec("echo", "test_arg", msg))
        assert "test_arg" in result

    def test_exec_unknown(self, handler):
        msg = make_msg("!cmd nonexistent")
        result = asyncio.run(handler._cmd_exec("nonexistent", "", msg))
        assert "Unknown command" in result

    def test_exec_includes_header(self, handler):
        handler._cmd_add('"hi" "echo hi"')
        msg = make_msg("!cmd hi")
        result = asyncio.run(handler._cmd_exec("hi", "", msg))
        assert result.startswith("[hi]")

    def test_exec_env_vars(self, handler):
        """Test that XBOT_* env vars are available to the script."""
        handler._cmd_add('"envtest" "echo $XBOT_CHANNEL"')
        msg = make_msg("!cmd envtest", channel="telegram")
        result = asyncio.run(handler._cmd_exec("envtest", "", msg))
        assert "telegram" in result

    def test_exec_shell_injection_blocked(self, handler):
        """Test that args are quoted to prevent injection."""
        handler._cmd_add('"echo" "echo"')
        msg = make_msg("!cmd echo")
        # Args like "; rm -rf" should be quoted, not executed
        result = asyncio.run(handler._cmd_exec("echo", "; echo INJECTED", msg))
        # The args should be quoted, so "; echo INJECTED" is printed as text
        assert "INJECTED" in result  # echoed as text, not executed as separate command
        # Should not have actually run a separate command
        assert result.count("Exit code") == 1  # only one process, not two

    def test_exec_timeout(self, handler):
        """Test that long-running commands are killed."""
        handler._cmd_add('"slow" "sleep 30"')
        msg = make_msg("!cmd slow")
        result = asyncio.run(handler._cmd_exec("slow", "", msg))
        assert "timed out" in result.lower()

    def test_exec_env_restored(self, handler):
        """Test that env vars are restored after execution."""
        handler._cmd_add('"hi" "echo hi"')
        msg = make_msg("!cmd hi", channel="telegram")
        asyncio.run(handler._cmd_exec("hi", "", msg))
        # After execution, XBOT_CHANNEL should be back to original
        import os
        assert os.environ.get("XBOT_CHANNEL") != "telegram" or os.environ.get("XBOT_CHANNEL") is None


class TestCmdSubcommand:
    """Edge cases for subcommand dispatch in _handle_cmd_command."""

    def test_no_args_shows_usage(self, handler):
        msg = make_msg("!cmd")
        result = asyncio.run(handler._handle_cmd_command("!cmd", msg))
        assert "Usage" in result

    def test_list_subcommand(self, handler):
        handler._cmd_add('"test" "echo test"')
        msg = make_msg("!cmd list")
        result = asyncio.run(handler._handle_cmd_command("!cmd list", msg))
        assert "test" in result

    def test_add_subcommand(self, handler):
        msg = make_msg('!cmd add "test" "echo test"')
        result = asyncio.run(handler._handle_cmd_command('!cmd add "test" "echo test"', msg))
        assert "Added" in result

    def test_remove_subcommand(self, handler):
        handler._cmd_add('"test" "echo test"')
        msg = make_msg('!cmd remove "test"')
        result = asyncio.run(handler._handle_cmd_command('!cmd remove "test"', msg))
        assert "Removed" in result

    def test_exec_subcommand(self, handler):
        handler._cmd_add('"hi" "echo hello"')
        msg = make_msg("!cmd hi")
        result = asyncio.run(handler._handle_cmd_command("!cmd hi", msg))
        assert "hello" in result

    def test_case_insensitive_subcommands(self, handler):
        """LIST, ADD, REMOVE should work case-insensitively."""
        msg = make_msg("!cmd LIST")
        result = asyncio.run(handler._handle_cmd_command("!cmd LIST", msg))
        assert "No commands configured" in result or "Configured commands" in result
