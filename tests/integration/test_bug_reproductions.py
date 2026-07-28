"""Regression tests for previously reproduced concurrency bugs.

Each test preserves the triggering order and must complete promptly.

BUG-1: aclear runs before wait registers → waiter starved for 300s
BUG-2: Old task's finally evicts new task from active_tasks dict
BUG-3: aclear_interaction_request doesn't wake waiter → 300s hang
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

import xbot.interfaces.gateway.app as gateway_app
from xbot.platform.bus.queue import (
    InteractionRequest,
    MessageBus,
    PermissionRequest,
)

pytestmark = pytest.mark.integration


# ===========================================================================
# BUG-1: aclear 在 wait 注册之前执行 → waiter 饿死
#
# 时序窗口:
#   1. publish_permission_request (预注册 Event, waiter_count=0)
#   2. aclear_session_requests (sees waiter_count=0 → pops Event entirely)
#   3. wait_permission_response (creates orphan Event → hangs 300s)
#
# 预期正确行为: waiter 应在合理时间内收到 deny，而非等 300s
# ===========================================================================


class TestBug1AclearBeforeWaitRegisters:
    """BUG-1: If aclear runs after publish but before wait, waiter is starved."""

    async def test_permission_waiter_starved_when_clear_before_wait(self):
        """Reproduce: publish → aclear → wait → waiter gets stuck.

        This test will FAIL (timeout) if the bug exists, proving the waiter
        is stranded on an orphan Event that nobody will ever set.
        """
        bus = MessageBus()
        session_key = "web:user1:bug1-perm"

        # Step 1: Publish request (pre-registers Event, waiter_count=0)
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="bug1-perm-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={"command": "ls"},
                message="Allow?",
            )
        )

        # Step 2: aclear runs BEFORE waiter registers
        # At this point waiter_count == 0, so aclear pops the Event entirely
        result = await bus.aclear_session_requests(session_key)
        assert result["permission"] is True  # Clear found the request

        # Step 3: Now the waiter starts (simulating the Agent side starting late)
        # If the bug exists, this will hang for 300s then timeout with "deny"
        # A correct implementation should return immediately with deny
        try:
            response = await asyncio.wait_for(
                bus.wait_permission_response("bug1-perm-1", timeout=300.0),
                timeout=2.0,  # We only wait 2s — if it takes longer, bug confirmed
            )
            # If we get here within 2s, the bug is fixed
            assert response.decision == "deny"
        except asyncio.TimeoutError:
            pytest.fail(
                "BUG-1 CONFIRMED: waiter hung because aclear popped the Event "
                "before wait could register. The waiter will be stuck for 300s."
            )

    async def test_interaction_waiter_starved_when_clear_before_wait(self):
        """Same bug for interaction requests."""
        bus = MessageBus()
        session_key = "web:user1:bug1-int"

        # Step 1: Publish interaction request
        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="bug1-int-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="question",
                prompt="Which file?",
            )
        )

        # Step 2: aclear before waiter registers
        result = await bus.aclear_session_requests(session_key)
        assert result["interaction"] is True

        # Step 3: Waiter starts late
        try:
            response = await asyncio.wait_for(
                bus.wait_interaction_response("bug1-int-1", timeout=300.0),
                timeout=2.0,
            )
            assert response.action == "cancel"
        except asyncio.TimeoutError:
            pytest.fail(
                "BUG-1 CONFIRMED (interaction): waiter hung because aclear "
                "popped the Event before wait registered."
            )


# ===========================================================================
# BUG-2: 旧 task 的 finally 把新 task 从 active_tasks 中踢掉
#
# 时序:
#   1. Connection A 断连 → finally 从 active_tasks pop task_A
#   2. Connection B 重连 → 新 task_B 插入 active_tasks[same_key]
#   3. task_A 被 cancel，其 finally 执行 active_tasks.pop(key) → 踢掉 task_B
#   4. task_B 变成孤儿
#
# 预期正确行为: task_A 的 finally 应该只 pop 自己，不影响 task_B
# ===========================================================================


class TestBug2OldTaskFinallyEvictsNewTask:
    """BUG-2: Old task's finally block unconditionally pops from active_tasks,
    evicting a newer task that was inserted for the same session key."""

    async def test_reconnect_task_evicted_by_old_task_cleanup(self):
        """Reproduce the reconnection race condition.

        Simulates the exact pattern from gateway/app.py:
        - active_tasks is a shared dict
        - _run_agent_turn's finally does: active_tasks.pop(key, None)
        - If a new task was inserted between the cancel and the finally,
          the pop removes the WRONG task.
        """
        active_tasks: dict[str, asyncio.Task] = {}
        active_tasks_lock = asyncio.Lock()
        session_key = "web:admin:chat1"
        remove_if_current = getattr(
            gateway_app,
            "_remove_active_task_if_current",
            None,
        )
        assert callable(remove_if_current), (
            "gateway task cleanup must compare task identity before removing a session slot"
        )

        new_task_evicted = asyncio.Event()
        old_task_finally_ran = asyncio.Event()

        async def old_agent_turn():
            """Simulates the old connection's agent turn (will be cancelled)."""
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                # Simulate some cleanup delay before finally
                await asyncio.sleep(0.1)
                raise
            finally:
                await remove_if_current(
                    active_tasks,
                    active_tasks_lock,
                    session_key,
                    asyncio.current_task(),
                )
                old_task_finally_ran.set()

        async def new_agent_turn():
            """Simulates the new connection's agent turn (should keep running)."""
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                new_task_evicted.set()
                raise

        # Step 1: Old task is running
        old_task = asyncio.create_task(old_agent_turn())
        async with active_tasks_lock:
            active_tasks[session_key] = old_task
        await asyncio.sleep(0)

        # Step 2: Connection A disconnects → outer finally pops old_task
        async with active_tasks_lock:
            popped = active_tasks.pop(session_key, None)
        assert popped is old_task

        # Step 3: Connection B reconnects immediately → inserts new_task
        new_task = asyncio.create_task(new_agent_turn())
        async with active_tasks_lock:
            active_tasks[session_key] = new_task
        await asyncio.sleep(0)

        # Step 4: Old task is cancelled (part of _cancel_tasks_and_wait)
        old_task.cancel()
        # Wait for old_task's finally to run
        await asyncio.wait_for(old_task_finally_ran.wait(), timeout=2.0)

        # Step 5: Check if new_task was evicted from active_tasks
        async with active_tasks_lock:
            task_in_dict = active_tasks.get(session_key)

        if task_in_dict is None:
            # BUG CONFIRMED: old task's finally removed new_task from dict
            # Now verify new_task is still running but orphaned
            assert not new_task.done(), "new_task should still be running"
            pytest.fail(
                "BUG-2 CONFIRMED: Old task's finally did active_tasks.pop(key) "
                "which evicted the NEW task from the dict. The new task is now "
                "orphaned — it can't be cancelled or tracked."
            )
        else:
            # Bug is fixed: new_task is still in the dict
            assert task_in_dict is new_task

        # Cleanup
        new_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await new_task

    async def test_orphaned_task_cannot_be_cancelled(self):
        """Show the consequence: once evicted, the task can never be stopped.

        If a user sends "cancel" for this session, active_tasks.pop returns None,
        so the cancel is a no-op while the task keeps running.
        """
        active_tasks: dict[str, asyncio.Task] = {}
        active_tasks_lock = asyncio.Lock()
        session_key = "web:admin:orphan"

        task_still_running = asyncio.Event()

        async def agent_turn():
            """This task will become orphaned."""
            try:
                # Simulate ongoing work
                for _ in range(10):
                    await asyncio.sleep(0.1)
                    task_still_running.set()
            except asyncio.CancelledError:
                raise

        # Insert task
        task = asyncio.create_task(agent_turn())
        async with active_tasks_lock:
            active_tasks[session_key] = task
        await asyncio.sleep(0)

        # Simulate the eviction (old task's finally ran and popped it)
        async with active_tasks_lock:
            active_tasks.pop(session_key, None)

        # Now try to cancel via the normal "cancel message" path
        async with active_tasks_lock:
            task_to_cancel = active_tasks.pop(session_key, None)

        # task_to_cancel is None — the cancel is a no-op!
        assert task_to_cancel is None, "Task was already evicted from dict"

        # But the task is still running
        await asyncio.sleep(0.2)
        assert not task.done(), "Orphaned task is still running and cannot be stopped"

        # Cleanup
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


# ===========================================================================
# BUG-3: aclear_interaction_request 不唤醒 waiter
#
# 对比:
#   - aclear_session_requests: 检查 waiter_count, 设置 event → 唤醒 waiter ✓
#   - aclear_interaction_request: 只是 pop event → waiter 永远收不到信号 ✗
#
# 触发路径: handle_interaction_response 中 retry 次数达到上限(3次) → 调用
#           aclear_interaction_request → Agent 侧 waiter 挂死 300s
#
# 预期正确行为: aclear_interaction_request 应像 aclear_session_requests 一样
#              检查 waiter 并 set event
# ===========================================================================


class TestBug3AclearInteractionRequestWakeup:
    """BUG-3: single-request cleanup must wake an existing interaction waiter."""

    async def test_waiter_woken_after_aclear_interaction_request(self):
        """Publish → wait → clear must return a cancellation without a 300s hang."""
        bus = MessageBus()
        session_key = "web:user1:bug3"

        # Step 1: Publish interaction request
        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="bug3-int-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="question",
                prompt="Choose a file",
                suggestions=["a.txt", "b.txt"],
            )
        )

        # Step 2: Agent-side waiter starts (registers event + increments waiter_count)
        waiter = asyncio.create_task(
            bus.wait_interaction_response("bug3-int-1", timeout=300.0)
        )
        await asyncio.sleep(0)  # Let waiter register

        # Step 3: User gave 3 invalid answers → handler calls aclear_interaction_request
        # This is what response_handlers.py line 318 does when max retries hit
        await bus.aclear_interaction_request("bug3-int-1")

        # Step 4: Check if waiter is woken up within 2s
        try:
            response = await asyncio.wait_for(waiter, timeout=2.0)
            # If we get here, the bug is fixed
            assert response.action == "cancel"
        except asyncio.TimeoutError:
            pytest.fail(
                "BUG-3 CONFIRMED: aclear_interaction_request popped the Event "
                "without setting it. The waiter is stuck waiting on a deleted "
                "Event and will hang for 300s until its own timeout fires."
            )

    async def test_contrast_aclear_session_requests_does_wake(self):
        """Contrast: aclear_session_requests correctly wakes the interaction waiter.

        This test should PASS — it demonstrates the correct behavior that
        aclear_interaction_request SHOULD have but doesn't.
        """
        bus = MessageBus()
        session_key = "web:user1:bug3-contrast"

        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="bug3-contrast-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="question",
                prompt="Choose a file",
            )
        )

        waiter = asyncio.create_task(
            bus.wait_interaction_response("bug3-contrast-1", timeout=300.0)
        )
        await asyncio.sleep(0)

        # aclear_session_requests DOES check waiter_count and sets the event
        result = await bus.aclear_session_requests(session_key)
        assert result["interaction"] is True

        # This should work — waiter is properly woken
        response = await asyncio.wait_for(waiter, timeout=2.0)
        assert response.action == "cancel"

    async def test_aclear_interaction_request_matches_session_clear_semantics(self):
        """Single-request and session cleanup must both signal registered waiters."""
        bus = MessageBus()
        session_key = "web:user1:bug3-api"

        # Setup: publish + register waiter
        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="bug3-api-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="confirmation",
                prompt="Proceed?",
            )
        )

        waiter = asyncio.create_task(
            bus.wait_interaction_response("bug3-api-1", timeout=300.0)
        )
        await asyncio.sleep(0)

        # Verify waiter is registered (waiter_count > 0)
        async with bus._interaction_lock:
            count = bus._interaction_waiter_counts.get("bug3-api-1", 0)
        assert count > 0, "Waiter should be registered"

        # Now call aclear_interaction_request
        await bus.aclear_interaction_request("bug3-api-1")

        # A registered waiter must retain a set Event until it consumes the
        # cancellation result.
        async with bus._interaction_lock:
            event = bus._pending_interaction_responses.get("bug3-api-1")
            result = bus._interaction_results.get("bug3-api-1")
        assert event is not None and event.is_set()
        assert result is not None and result.action == "cancel"

        response = await asyncio.wait_for(waiter, timeout=0.5)
        assert response.action == "cancel"
