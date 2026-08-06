"""Integration tests for !cmd system.

Tests the full flow: config loading → command handler → ScriptCommandManager → ExecTool → output.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from xbot.platform.bus.events import InboundMessage, OutboundMessage
from xbot.runtime.core.command_handlers import LocalCommandHandler
from xbot.runtime.core.script_commands import ScriptCommandManager


@pytest.fixture
def setup_xbot_env():
    """Create a full mock xbot environment with workspace, config, and handler."""
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

    handler = LocalCommandHandler(service)

    yield {
        "handler": handler,
        "scripts_file": scripts_file,
        "tmpdir": tmpdir,
        "service": service,
    }

    # Cleanup
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def make_msg(content, channel="telegram", sender_id="user-123", chat_id="chat-1"):
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        chat_id=chat_id,
        content=content,
    )


class TestFullAddListRemove:
    """Full lifecycle: add → list → execute → remove → list."""

    def test_full_lifecycle(self, setup_xbot_env):
        env = setup_xbot_env
        handler = env["handler"]

        # 1. Start empty
        msg = make_msg("!cmd list")
        result = handler._cmd_list()
        assert "No commands configured" in result

        # 2. Add a command
        result = handler._cmd_add('"deploy" "echo deploying"')
        assert "Added" in result

        # 3. List shows it
        result = handler._cmd_list()
        assert "deploy" in result
        assert "echo deploying" in result

        # 4. Execute it
        msg = make_msg("!cmd deploy")
        result = asyncio.run(handler._cmd_exec("deploy", "", msg))
        assert "deploying" in result
        assert "Exit code: 0" in result

        # 5. Remove it
        result = handler._cmd_remove('"deploy"')
        assert "Removed" in result

        # 6. List is empty
        result = handler._cmd_list()
        assert "No commands configured" in result

        # 7. Execute fails
        msg = make_msg("!cmd deploy")
        result = asyncio.run(handler._cmd_exec("deploy", "", msg))
        assert "Unknown command" in result


class TestFilePersistence:
    """Test that commands persist across handler instances."""

    def test_persistence_across_instances(self, setup_xbot_env):
        env = setup_xbot_env
        handler1 = env["handler"]
        scripts_file = env["scripts_file"]

        # Add a command with first handler
        handler1._cmd_add('"persist_test" "echo persisted"')

        # Create a new handler pointing at the same file
        handler2 = LocalCommandHandler(env["service"])

        # New handler should see the command
        assert handler2._get_script_manager().get("persist_test") == "echo persisted"

    def test_file_is_valid_json(self, setup_xbot_env):
        env = setup_xbot_env
        handler = env["handler"]
        handler._cmd_add('"test" "echo hi"')
        handler._cmd_add('"test2" "echo bye"')

        with open(env["scripts_file"]) as f:
            data = json.load(f)
        assert data == {"test": "echo hi", "test2": "echo bye"}

    def test_manual_file_edit_visible_to_handler(self, setup_xbot_env):
        env = setup_xbot_env
        handler = env["handler"]

        # Manually write a scripts.json
        with open(env["scripts_file"], "w") as f:
            json.dump({"manual_cmd": "echo manual"}, f)

        # Handler should see it (hot reload)
        assert handler._get_script_manager().get("manual_cmd") == "echo manual"


class TestExecutionWithEnvContext:
    """Test that environment variables are correctly passed to scripts."""

    def test_all_env_vars_available(self, setup_xbot_env):
        env = setup_xbot_env
        handler = env["handler"]
        handler._cmd_add('"envtest" "echo $XBOT_CHANNEL:$XBOT_CHAT_ID:$XBOT_USER"')

        msg = make_msg("!cmd envtest", channel="feishu", sender_id="user-456", chat_id="chat-789")
        result = asyncio.run(handler._cmd_exec("envtest", "", msg))
        assert "feishu" in result
        assert "chat-789" in result
        assert "user-456" in result

    def test_env_vars_restored_after_exec(self, setup_xbot_env):
        env = setup_xbot_env
        handler = env["handler"]
        handler._cmd_add('"hi" "echo hi"')

        original_channel = os.environ.get("XBOT_CHANNEL")
        msg = make_msg("!cmd hi", channel="telegram")
        asyncio.run(handler._cmd_exec("hi", "", msg))
        assert os.environ.get("XBOT_CHANNEL") == original_channel


class TestSecurityGuards:
    """Test that ExecTool's security guards still work for !cmd execution."""

    def test_dangerous_command_blocked(self, setup_xbot_env):
        """rm -rf should be blocked by ExecTool deny patterns."""
        env = setup_xbot_env
        handler = env["handler"]
        handler._cmd_add('"dangerous" "rm -rf /tmp/test_dir_xbot"')

        msg = make_msg("!cmd dangerous")
        result = asyncio.run(handler._cmd_exec("dangerous", "", msg))
        # ExecTool should block it
        assert "blocked" in result.lower() or "denied" in result.lower() or "error" in result.lower()

    def test_args_are_quoted_not_executed(self, setup_xbot_env):
        """Args should be quoted, not executed as shell commands."""
        env = setup_xbot_env
        handler = env["handler"]
        handler._cmd_add('"echo" "echo"')

        msg = make_msg("!cmd echo ; echo INJECTED")
        result = asyncio.run(handler._cmd_exec("echo", "; echo INJECTED", msg))
        # The "; echo INJECTED" should be printed as text by echo, not executed
        assert "INJECTED" in result
        # Only one "Exit code" means one process ran
        assert result.count("Exit code") == 1


class TestHandleMethod:
    """Test the full handle() method with bus mock."""

    def test_handle_cmd_list_publishes_outbound(self, setup_xbot_env):
        env = setup_xbot_env
        handler = env["handler"]

        # Mock bus
        bus = MagicMock()
        published = []
        async def mock_publish(msg):
            published.append(msg)
        bus.publish_outbound = mock_publish

        msg = make_msg("!cmd list")
        asyncio.run(handler.handle(msg, bus))
        assert len(published) == 1
        assert "No commands configured" in published[0].content

    def test_handle_cmd_add_publishes_result(self, setup_xbot_env):
        env = setup_xbot_env
        handler = env["handler"]

        bus = MagicMock()
        published = []
        async def mock_publish(msg):
            published.append(msg)
        bus.publish_outbound = mock_publish

        msg = make_msg('!cmd add "test" "echo hi"')
        asyncio.run(handler.handle(msg, bus))
        assert len(published) == 1
        assert "Added" in published[0].content

    def test_handle_cmd_exec_publishes_output(self, setup_xbot_env):
        env = setup_xbot_env
        handler = env["handler"]
        handler._cmd_add('"hi" "echo Hello"')

        bus = MagicMock()
        published = []
        async def mock_publish(msg):
            published.append(msg)
        bus.publish_outbound = mock_publish

        msg = make_msg("!cmd hi")
        asyncio.run(handler.handle(msg, bus))
        assert len(published) == 1
        assert "Hello" in published[0].content
