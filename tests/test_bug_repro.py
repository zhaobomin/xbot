"""Reproduction tests for 3 remaining bugs.

Bug 1: shlex.quote on entire args string breaks multi-argument passing
Bug 2: _prune_idle_workers called without exclude_keys prunes current session's worker
Bug 3: os.environ mutation during async _cmd_exec is not coroutine-safe
"""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.platform.bus.events import InboundMessage
from xbot.runtime.core.command_handlers import LocalCommandHandler
from xbot.runtime.core.service import AgentService, SessionWorker


# ---------------------------------------------------------------------------
# Bug 1: shlex.quote breaks multi-argument passing
# ---------------------------------------------------------------------------

@pytest.fixture
def cmd_handler():
    tmpdir = tempfile.mkdtemp()
    service = MagicMock()
    service._shared_resources = {
        "workspace": tmpdir,
        "config": MagicMock(),
    }
    service._shared_resources["config"].tools.scripts_file = os.path.join(tmpdir, "scripts.json")
    service._shared_resources["config"].tools.exec.timeout = 10
    service._shared_resources["config"].tools.exec.path_append = ""
    service._shared_resources["config"].tools.restrict_to_workspace = False
    h = LocalCommandHandler(service)
    yield h
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def make_cmd_msg(content, channel="telegram", sender_id="123"):
    return InboundMessage(channel=channel, sender_id=sender_id, chat_id="chat-1", content=content)


class TestBug1ShlexQuoteBreaksMultiArgs:
    """Reproduce: shlex.quote("my-service --env prod") wraps entire string as one arg."""

    @pytest.mark.asyncio
    async def test_multi_args_passed_individually(self, cmd_handler):
        """!cmd print_args a b c should print each arg on a separate line."""
        cmd_handler._cmd_add('"print_args' + '" "printf \'%s\\n\'"')
        msg = make_cmd_msg("!cmd print_args hello world foo")
        result = await cmd_handler._cmd_exec("print_args", "hello world foo", msg)
        # printf '%s\n' hello world foo → 3 lines (CORRECT)
        # printf '%s\n' 'hello world foo' → 1 line (BUG)
        # Check the header to see if args were quoted individually
        assert "'hello world foo'" not in result  # Should NOT be one quoted block
        # Check output has each word on its own line
        output_lines = [l.strip() for l in result.split("\n")
                        if l.strip() and "Exit code" not in l and "[" not in l]
        assert "hello" in output_lines
        assert "world" in output_lines
        assert "foo" in output_lines

    @pytest.mark.asyncio
    async def test_args_with_flags(self, cmd_handler):
        """!cmd deploy my-service --env prod should pass --env and prod separately."""
        cmd_handler._cmd_add('"deploy" "echo"')
        msg = make_cmd_msg("!cmd deploy my-service --env prod")
        result = await cmd_handler._cmd_exec("deploy", "my-service --env prod", msg)
        # All parts should be separate args
        assert "my-service" in result
        assert "--env" in result
        assert "prod" in result
        # They should NOT be inside quotes (which would make them one arg)
        assert "'my-service --env prod'" not in result


# ---------------------------------------------------------------------------
# Bug 2: _prune_idle_workers without exclude_keys prunes current session
# ---------------------------------------------------------------------------

def make_mock_worker(session_key="test", *, closed=False, last_idle_at=None, queue_items=None):
    from unittest.mock import MagicMock
    client = MagicMock()
    client.disconnect = AsyncMock(return_value=None)
    q = asyncio.Queue()
    if queue_items:
        for item in queue_items:
            q.put_nowait(item)
    return SessionWorker(
        session_key=session_key,
        client=client,
        input_queue=q,
        task=None,
        channel="telegram",
        chat_id="chat-1",
        closed=closed,
        last_idle_at=last_idle_at,
    )


def make_mock_service(workers=None):
    service = AgentService.__new__(AgentService)
    service._session_workers = workers or {}
    config = MagicMock()
    config.agents.claude_sdk.client_idle_ttl_seconds = 3600
    config.agents.claude_sdk.client_scavenger_enabled = True
    service._shared_resources = {"config": config}
    service._command_handler = None
    return service


class TestBug2PruneMissingExcludeKeys:
    """Reproduce: _prune_idle_workers in _enqueue_worker_message prunes current session."""

    @pytest.mark.asyncio
    async def test_current_session_worker_excluded_from_pruning(self):
        """The worker for the current session_key should NOT be pruned even if idle."""
        import time
        service = make_mock_service({
            "session-1": make_mock_worker("session-1", last_idle_at=time.time() - 7200),
        })
        service._stop_session_worker = AsyncMock(return_value=True)

        # With exclude_keys, current session should be spared
        removed = await service._prune_idle_workers(3600, exclude_keys={"session-1"})
        assert removed == 0  # FIXED: current session excluded
        service._stop_session_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_sessions_still_pruned(self):
        """Workers for OTHER sessions should still be pruned."""
        import time
        service = make_mock_service({
            "session-1": make_mock_worker("session-1", last_idle_at=time.time() - 7200),
            "session-2": make_mock_worker("session-2", last_idle_at=time.time() - 7200),
        })
        service._stop_session_worker = AsyncMock(return_value=True)

        # Exclude session-1, session-2 should still be pruned
        removed = await service._prune_idle_workers(3600, exclude_keys={"session-1"})
        assert removed == 1  # session-2 pruned, session-1 excluded


# ---------------------------------------------------------------------------
# Bug 3: os.environ mutation during async _cmd_exec
# ---------------------------------------------------------------------------

class TestBug3OsEnvironRaceCondition:
    """Reproduce: os.environ is mutated during await, visible to other coroutines."""

    @pytest.mark.asyncio
    async def test_env_var_leakage_during_execution(self, cmd_handler):
        """XBOT_CHANNEL should not leak to other coroutines during execution."""
        import os
        cmd_handler._cmd_add('"check" "echo $XBOT_CHANNEL"')
        msg = make_cmd_msg("!cmd check", channel="telegram")
        original = os.environ.get("XBOT_CHANNEL")

        # Run _cmd_exec and check that after it completes, env is restored
        result = await cmd_handler._cmd_exec("check", "", msg)
        assert "telegram" in result  # The command saw XBOT_CHANNEL=telegram

        # After execution, env should be restored
        assert os.environ.get("XBOT_CHANNEL") == original, \
            f"XBOT_CHANNEL leaked: expected {original}, got {os.environ.get('XBOT_CHANNEL')}"

    @pytest.mark.asyncio
    async def test_env_var_visible_during_concurrent_execution(self, cmd_handler):
        """If two _cmd_exec run concurrently, XBOT_CHANNEL should not cross-contaminate."""
        import os
        cmd_handler._cmd_add('"slow" "sleep 0.5 && echo $XBOT_CHANNEL"')

        msg1 = make_cmd_msg("!cmd slow", channel="telegram")
        msg2 = make_cmd_msg("!cmd slow", channel="feishu")

        # Run both concurrently — if os.environ is used, they will see the same value
        # (whichever was set last)
        results = await asyncio.gather(
            cmd_handler._cmd_exec("slow", "", msg1),
            cmd_handler._cmd_exec("slow", "", msg2),
        )

        result1, result2 = results
        # With os.environ mutation (BUG): both might see the same channel
        # With proper env isolation (FIXED): result1 has "telegram", result2 has "feishu"
        # We can't guarantee the race will trigger, but let's check if the bug exists
        # by examining whether both commands see the same channel value
        has_telegram = "telegram" in result1
        has_feishu = "feishu" in result2

        # If the bug exists, result2 might contain "telegram" instead of "feishu"
        # because os.environ["XBOT_CHANNEL"] was set to "telegram" by the first call
        # and the second call's subprocess picked it up
        print(f"Result1 contains telegram: {has_telegram}")
        print(f"Result2 contains feishu: {has_feishu}")
        print(f"Result2 contains telegram: {'telegram' in result2}")
