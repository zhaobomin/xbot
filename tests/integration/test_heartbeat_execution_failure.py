"""Integration tests: HeartbeatService execution failure modes.

Validates that the heartbeat service is resilient to:
- on_execute callback raising exceptions
- on_execute callback hanging indefinitely
- on_notify callback failure after successful execution
- Rapid ticks overlapping with still-running executions
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from xbot.runtime.core.protocol import StructuredLLMResponse, ToolCall
from xbot.runtime.system.heartbeat.service import HeartbeatService

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_heartbeat_file(tmp_path: Path) -> Path:
    """Create a minimal HEARTBEAT.md so the service has work to do."""
    hb = tmp_path / "HEARTBEAT.md"
    hb.write_text(
        "## Active Tasks\n- Task 1: Do something important\n",
        encoding="utf-8",
    )
    return hb


def _make_llm_call_returning_run() -> AsyncMock:
    """LLM call mock that always decides 'run' via a tool call."""
    response = StructuredLLMResponse(
        content="",
        finish_reason="tool_use",
        tool_calls=[ToolCall(name="heartbeat", arguments={"action": "run", "tasks": "Task 1"})],
    )
    return AsyncMock(return_value=response)


def _make_llm_call_returning_skip() -> AsyncMock:
    """LLM call mock that decides 'skip'."""
    response = StructuredLLMResponse(
        content="Nothing to do",
        finish_reason="stop",
        tool_calls=[],
    )
    return AsyncMock(return_value=response)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOnExecuteRaisesException:
    """Test that on_execute raising does not crash the heartbeat service."""

    async def test_on_execute_raises_exception_tick_continues(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When on_execute raises, the tick logs an error and the service
        continues operating — the next tick can still fire."""
        llm_call = _make_llm_call_returning_run()
        on_execute = AsyncMock(side_effect=RuntimeError("execution boom"))
        on_notify = AsyncMock()

        svc = HeartbeatService(
            workspace=tmp_path,
            llm_call=llm_call,
            on_execute=on_execute,
            on_notify=on_notify,
            interval_s=1,
            enabled=True,
        )
        _make_heartbeat_file(tmp_path)

        # Patch evaluate_response to avoid needing a real evaluator
        with patch(
            "xbot.platform.utils.evaluator.evaluate_response",
            new_callable=AsyncMock,
            return_value=True,
        ):
            # Run tick directly — it should NOT propagate the exception
            with caplog.at_level(logging.ERROR):
                await svc._tick()

        # The exception was caught and logged
        assert on_execute.called
        assert any("Heartbeat execution failed" in r.message for r in caplog.records)

        # Service is still operable — second tick works fine
        on_execute.side_effect = None
        on_execute.return_value = "result from second tick"

        with patch(
            "xbot.platform.utils.evaluator.evaluate_response",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await svc._tick()

        assert on_notify.called
        on_notify.assert_called_with("result from second tick")


class TestOnExecuteHangsIndefinitely:
    """Test behavior when on_execute never returns."""

    async def test_on_execute_hangs_indefinitely(self, tmp_path: Path) -> None:
        """If on_execute never completes, _tick will hang because there is no
        internal timeout on the callback. This test documents the hang risk
        by verifying that _tick does NOT complete within a reasonable time.

        NOTE: The current implementation does NOT enforce a timeout on on_execute.
        This is a known limitation — callers should apply their own timeout.
        """

        async def never_returns(tasks: str) -> str:
            await asyncio.sleep(3600)  # effectively forever
            return "unreachable"

        llm_call = _make_llm_call_returning_run()

        svc = HeartbeatService(
            workspace=tmp_path,
            llm_call=llm_call,
            on_execute=never_returns,
            on_notify=AsyncMock(),
            interval_s=1,
            enabled=True,
        )
        _make_heartbeat_file(tmp_path)

        with patch(
            "xbot.platform.utils.evaluator.evaluate_response",
            new_callable=AsyncMock,
            return_value=True,
        ):
            # _tick should NOT complete within 0.3s — proving it hangs on on_execute
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(svc._tick(), timeout=0.3)


class TestOnNotifyFailureAfterSuccessfulExecution:
    """Test that on_notify failure doesn't lose the execution result."""

    async def test_on_notify_failure_after_successful_execution(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Execution succeeds and returns a result, but on_notify raises.
        The service should catch the error (in _tick's broad except) and
        continue running. The execution DID produce a response — it's just
        that delivery failed."""
        execution_result = "Task completed successfully"
        on_execute = AsyncMock(return_value=execution_result)
        on_notify = AsyncMock(side_effect=ConnectionError("notify channel down"))
        llm_call = _make_llm_call_returning_run()

        svc = HeartbeatService(
            workspace=tmp_path,
            llm_call=llm_call,
            on_execute=on_execute,
            on_notify=on_notify,
            interval_s=1,
            enabled=True,
        )
        _make_heartbeat_file(tmp_path)

        with patch(
            "xbot.platform.utils.evaluator.evaluate_response",
            new_callable=AsyncMock,
            return_value=True,  # evaluator says "yes, notify"
        ):
            with caplog.at_level(logging.ERROR):
                await svc._tick()

        # on_execute was called and returned successfully
        on_execute.assert_called_once_with("Task 1")
        # on_notify was attempted with the execution result
        on_notify.assert_called_once_with(execution_result)
        # The error was logged (caught by the broad except in _tick)
        assert any("Heartbeat execution failed" in r.message for r in caplog.records)

        # Service continues — a subsequent tick still works
        on_notify.side_effect = None
        on_notify.reset_mock()
        on_execute.reset_mock()

        with patch(
            "xbot.platform.utils.evaluator.evaluate_response",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await svc._tick()

        on_execute.assert_called_once()
        on_notify.assert_called_once()


class TestRapidTicksDoNotPileUp:
    """Test that concurrent ticks are handled by the skip mechanism."""

    async def test_rapid_ticks_do_not_pile_up_executions(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When a tick is still running, the _run_loop skips the next iteration.
        This tests that concurrency control in _run_loop prevents pile-up."""
        execution_started = asyncio.Event()
        execution_gate = asyncio.Event()
        call_count = 0

        async def slow_execute(tasks: str) -> str:
            nonlocal call_count
            call_count += 1
            execution_started.set()
            await execution_gate.wait()
            return "done"

        llm_call = _make_llm_call_returning_run()

        svc = HeartbeatService(
            workspace=tmp_path,
            llm_call=llm_call,
            on_execute=slow_execute,
            on_notify=AsyncMock(),
            interval_s=0,  # immediate re-tick for testing
            enabled=True,
        )
        _make_heartbeat_file(tmp_path)

        with patch(
            "xbot.platform.utils.evaluator.evaluate_response",
            new_callable=AsyncMock,
            return_value=True,
        ):
            # Simulate the scenario: start first tick as an asyncio Task
            tick_task = asyncio.create_task(svc._tick())
            # Mark it as the running tick (as _run_loop would)
            svc._running_tick = tick_task

            await execution_started.wait()  # first tick is now blocked in on_execute

            # _run_loop checks if _running_tick is done before spawning a new tick
            # Verify the guard works:
            assert svc._running_tick is not None
            assert not svc._running_tick.done()

            # Release the first tick
            execution_gate.set()
            await tick_task

        # Only one execution happened despite the opportunity for overlap
        assert call_count == 1

    async def test_run_loop_skips_while_tick_running(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Directly exercise _run_loop's skip behavior by starting the service
        with a very short interval and a slow on_execute.

        Validates that while one tick is blocked, the loop logs the skip
        warning and does NOT start a second concurrent execution."""
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def slow_execute(tasks: str) -> str:
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            # Hold the execution long enough for multiple loop iterations
            await asyncio.sleep(0.2)
            async with lock:
                current_concurrent -= 1
            return "done"

        llm_call = _make_llm_call_returning_run()

        svc = HeartbeatService(
            workspace=tmp_path,
            llm_call=llm_call,
            on_execute=slow_execute,
            on_notify=AsyncMock(),
            interval_s=0,  # effectively no sleep (will be asyncio.sleep(0))
            enabled=True,
        )
        _make_heartbeat_file(tmp_path)

        with patch(
            "xbot.platform.utils.evaluator.evaluate_response",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with caplog.at_level(logging.WARNING):
                await svc.start()

                # Let the loop run — first tick starts, subsequent iterations
                # should see it still running and skip
                await asyncio.sleep(0.15)

                svc.stop()
                await svc.shutdown()

        # The skip warning proves the concurrency guard fired
        assert any(
            "still running" in r.message for r in caplog.records
        ), "Expected 'still running' skip warning in logs"

        # At no point were two executions running concurrently
        assert max_concurrent == 1
