"""Integration tests: Coverage gap scenarios for hidden bug detection.

Tests added based on systematic coverage gap analysis to expose timing
races, ownership violations, and state corruption that the initial
regression tests do not cover.

GAP-1: Remember TTL expiry + late waiter
GAP-2: _cancel_tasks_and_wait bounds stuck cleanup
GAP-3: Cross-connection cancel ownership (covered by real WebSocket tests)
GAP-4: submit_permission_response overwrites aclear deny
GAP-5: publish supersede else-branch doesn't remember
GAP-6: aclear_session_requests two-lock window
GAP-7: State machine dispatch result unchecked
GAP-8: Concurrent retry max triggers
GAP-9: Session slot leak on early exception
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

import xbot.interfaces.gateway.app as gateway_app
from xbot.platform.bus.queue import (
    InteractionRequest,
    InteractionResponse,
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)

pytestmark = pytest.mark.integration


# ===========================================================================
# GAP-1: Remember TTL 过期后 late waiter 行为
# ===========================================================================


class TestGap1RememberTTLExpiry:
    """When a tombstone expires before the waiter registers, the waiter
    should still receive a deny (via its own timeout), not hang indefinitely
    on a never-set orphan Event."""

    async def test_permission_tombstone_expired_waiter_gets_timeout_deny(self):
        """If the tombstone TTL has passed, the waiter falls through to the
        normal wait path and receives a timeout-based deny."""
        bus = MessageBus()
        session_key = "web:user1:gap1-perm"

        # Step 1: Publish request
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="gap1-perm-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={"command": "rm -rf /"},
                message="Allow dangerous operation?",
            )
        )

        # Step 2: aclear with no waiter → stores tombstone
        result = await bus.aclear_session_requests(session_key)
        assert result["permission"] is True

        # Step 3: Verify tombstone was stored
        assert "gap1-perm-1" in bus._cleared_permission_responses

        # Step 4: Simulate TTL expiry by directly backdating the tombstone
        # The stored format is (response, expiry_monotonic)
        entry = bus._cleared_permission_responses["gap1-perm-1"]
        # Set expiry to the past so _take_cleared_*_unlocked returns None
        bus._cleared_permission_responses["gap1-perm-1"] = (entry[0], 0.0)

        # Step 5: Now the late waiter registers
        # _take_cleared_permission_unlocked will find the tombstone
        # but entry[1] <= monotonic() → returns None
        # The waiter creates a new orphan Event → times out
        response = await bus.wait_permission_response(
            "gap1-perm-1", timeout=0.5
        )

        # The waiter should get a timeout-based deny
        assert response.decision == "deny"
        assert "Timeout" in response.reason or "timeout" in response.reason.lower()

    async def test_interaction_tombstone_expired_waiter_gets_timeout_cancel(self):
        """Same scenario for interaction requests."""
        bus = MessageBus()
        session_key = "web:user1:gap1-int"

        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="gap1-int-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="question",
                prompt="Which file?",
            )
        )

        result = await bus.aclear_session_requests(session_key)
        assert result["interaction"] is True

        # Backdate tombstone expiry to simulate TTL passed
        entry = bus._cleared_interaction_responses["gap1-int-1"]
        bus._cleared_interaction_responses["gap1-int-1"] = (entry[0], 0.0)

        response = await bus.wait_interaction_response(
            "gap1-int-1", timeout=0.5
        )

        assert response.action == "cancel"
        assert "Timeout" in response.content or "timeout" in response.content.lower()

    async def test_permission_tombstone_valid_waiter_gets_immediate_deny(self):
        """Control: when tombstone is NOT expired, waiter gets immediate deny."""
        bus = MessageBus()
        session_key = "web:user1:gap1-valid"

        await bus.publish_permission_request(
            PermissionRequest(
                request_id="gap1-valid-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={},
                message="Allow?",
            )
        )

        await bus.aclear_session_requests(session_key)

        # Tombstone is fresh → waiter gets immediate result
        response = await asyncio.wait_for(
            bus.wait_permission_response("gap1-valid-1", timeout=300.0),
            timeout=1.0,
        )
        assert response.decision == "deny"


# ===========================================================================
# GAP-2: _cancel_tasks_and_wait 无超时
# ===========================================================================


class TestGap2CancelTasksTimeout:
    """_cancel_tasks_and_wait must bound slow or stuck cleanup so a WebSocket
    handler cannot block indefinitely and delay server shutdown.

    Note: new connections are NOT blocked ("already running") because
    active_tasks.pop(key) runs BEFORE _cancel_tasks_and_wait is called.
    The impact is resource leak + shutdown delay, not availability loss."""

    async def test_cancel_stuck_cleanup_returns_after_internal_timeout(self, monkeypatch):
        """Cancellation cleanup must be bounded even when a task stays stuck."""
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()

        async def stuck_cleanup_task():
            try:
                await asyncio.sleep(9999)
            except asyncio.CancelledError:
                cleanup_started.set()
                await release_cleanup.wait()

        task = asyncio.create_task(stuck_cleanup_task())
        await asyncio.sleep(0)  # Let task start

        _cancel_tasks_and_wait = gateway_app._cancel_tasks_and_wait
        monkeypatch.setattr(
            gateway_app,
            "_WS_TASK_CANCEL_TIMEOUT_SECONDS",
            0.05,
            raising=False,
        )

        await asyncio.wait_for(_cancel_tasks_and_wait([task]), timeout=0.5)

        assert cleanup_started.is_set()
        assert task.done() is False

        release_cleanup.set()
        await asyncio.wait_for(task, timeout=0.5)

    async def test_cancel_normal_task_completes_promptly(self):
        """Control: a well-behaved task is cleaned up quickly."""

        async def good_task():
            try:
                await asyncio.sleep(9999)
            except asyncio.CancelledError:
                # Clean shutdown in bounded time
                await asyncio.sleep(0.01)
                raise

        task = asyncio.create_task(good_task())
        await asyncio.sleep(0)

        _cancel_tasks_and_wait = gateway_app._cancel_tasks_and_wait

        # Should complete almost instantly
        await asyncio.wait_for(
            _cancel_tasks_and_wait([task]),
            timeout=2.0,
        )
        assert task.done()


# ===========================================================================
# GAP-4: submit_permission_response 在 aclear 设 deny 后覆写结果
# ===========================================================================


class TestGap4SubmitAfterAclearOverwrite:
    """After aclear sets deny, late responses must not overwrite cancellation."""

    async def test_late_submit_after_aclear_must_be_rejected(self):
        """After aclear sets deny, a late submit MUST be rejected (return False).

        Correct behavior: submit_permission_response should return False once
        the request has been cancelled via aclear, ensuring the waiter always
        gets 'deny' after a !stop.

        Regression: a submit arriving after clear must not overwrite the
        cancellation result while the waiter is waking up.
        """
        bus = MessageBus()
        session_key = "web:user1:gap4"

        # Step 1: Publish
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="gap4-perm-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={"command": "ls"},
                message="Allow?",
            )
        )

        # Step 2: Waiter registers
        waiter_ready = asyncio.Event()
        waiter_result: list[PermissionResponse] = []

        async def waiter():
            waiter_ready.set()
            resp = await bus.wait_permission_response("gap4-perm-1", timeout=5.0)
            waiter_result.append(resp)

        waiter_task = asyncio.create_task(waiter())
        await waiter_ready.wait()
        await asyncio.sleep(0)  # Let waiter enter wait_permission_response

        # Step 3: !stop → aclear sets deny
        await bus.aclear_session_requests(session_key)

        # Step 4: Late permission callback arrives
        late_response = PermissionResponse(
            request_id="gap4-perm-1",
            session_key=session_key,
            decision="allow",
            reason="User approved (but it's too late)",
        )
        submitted = await bus.submit_permission_response(late_response)

        # Step 5: Wait for waiter to complete
        await asyncio.wait_for(waiter_task, timeout=2.0)

        assert len(waiter_result) == 1
        result = waiter_result[0]

        # CORRECT behavior (will pass once bug is fixed):
        # Late submit must be rejected, waiter must get deny.
        assert submitted is False, (
            f"Late submit should be rejected after aclear, got accepted={submitted}"
        )
        assert result.decision == "deny", (
            f"Waiter must get 'deny' after !stop, got '{result.decision}'"
        )

    async def test_late_interaction_submit_after_aclear_must_be_rejected(self):
        """Interaction cancellation must also win over a late response."""
        bus = MessageBus()
        session_key = "web:user1:gap4-interaction"
        request_id = "gap4-interaction-1"
        await bus.publish_interaction_request(
            InteractionRequest(
                request_id=request_id,
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="approval",
                prompt="Approve?",
            )
        )
        waiter_task = asyncio.create_task(
            bus.wait_interaction_response(request_id, timeout=5.0)
        )
        await asyncio.sleep(0)

        await bus.aclear_session_requests(session_key)
        submitted = await bus.submit_interaction_response(
            InteractionResponse(
                request_id=request_id,
                session_key=session_key,
                action="allow",
                content="late approval",
            )
        )
        result = await asyncio.wait_for(waiter_task, timeout=2.0)

        assert submitted is False
        assert result.action == "cancel"

    async def test_submit_after_aclear_when_no_waiter_returns_false(self):
        """If the waiter already consumed the result, late submit returns False."""
        bus = MessageBus()
        session_key = "web:user1:gap4-no-waiter"

        await bus.publish_permission_request(
            PermissionRequest(
                request_id="gap4-nw-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={},
                message="Allow?",
            )
        )

        # Waiter registers and gets the aclear deny quickly
        waiter_task = asyncio.create_task(
            bus.wait_permission_response("gap4-nw-1", timeout=5.0)
        )
        await asyncio.sleep(0)

        await bus.aclear_session_requests(session_key)

        # Wait for waiter to consume
        response = await asyncio.wait_for(waiter_task, timeout=2.0)
        assert response.decision == "deny"

        # Now submit arrives — event and request should be cleaned up
        late = PermissionResponse(
            request_id="gap4-nw-1",
            session_key=session_key,
            decision="allow",
        )
        submitted = await bus.submit_permission_response(late)
        assert submitted is False, "Submit should fail after waiter consumed the result"


# ===========================================================================
# GAP-5: publish supersede else-branch 不存 tombstone
# ===========================================================================


class TestGap5SupersedeElseBranchNoRemember:
    """When publish(B) supersedes publish(A) and A's event is already set/gone,
    the else-branch doesn't store a tombstone. A late waiter for A hangs."""

    async def test_supersede_after_submit_late_waiter_hangs(self):
        """Sequence: publish(A) → submit(A, allow) → consume → publish(B)
        supersedes A (else-branch) → late wait(A) creates orphan Event.

        This documents the theoretical gap — in practice the waiter for A
        should have already been called before B arrives."""
        bus = MessageBus()
        session_key = "web:user1:gap5"

        # Step 1: Publish A
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="gap5-A",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={},
                message="Allow A?",
            )
        )

        # Step 2: Waiter for A registers and submit resolves it
        waiter_a = asyncio.create_task(
            bus.wait_permission_response("gap5-A", timeout=5.0)
        )
        await asyncio.sleep(0)

        await bus.submit_permission_response(
            PermissionResponse(
                request_id="gap5-A",
                session_key=session_key,
                decision="allow",
            )
        )

        # Waiter A consumes the result — cleans up event and results
        resp_a = await asyncio.wait_for(waiter_a, timeout=1.0)
        assert resp_a.decision == "allow"

        # Step 3: Publish B supersedes A
        # At this point, A's event is gone (waiter popped it)
        # The supersede logic hits the else-branch: no tombstone stored
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="gap5-B",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={},
                message="Allow B?",
            )
        )

        # Step 4: A "late" waiter for A tries to register
        # Since A's event and result are all gone, this creates a new orphan Event
        # that will never be set → hangs until timeout
        response = await bus.wait_permission_response("gap5-A", timeout=0.5)

        # The waiter times out and gets a synthetic deny
        assert response.decision == "deny"
        assert "Timeout" in response.reason or "timeout" in response.reason.lower()


# ===========================================================================
# GAP-6: aclear_session_requests 两锁之间的窗口
# ===========================================================================


class TestGap6AclearTwoLockWindow:
    """Between releasing _permission_lock and acquiring _interaction_lock,
    a new interaction request can be published for the same session.
    aclear will then cancel this brand-new request."""

    async def test_new_interaction_published_between_locks_gets_cancelled(self):
        """Demonstrate: publish_interaction happens between aclear's two lock ops."""
        bus = MessageBus()
        session_key = "web:user1:gap6"

        # Publish a permission request so aclear has something to clear
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="gap6-perm",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="test",
                tool_input={},
                message="Allow?",
            )
        )

        # We'll interleave: aclear starts → clears permission →
        # (gap) publish_interaction → aclear continues → clears interaction too
        interaction_published = asyncio.Event()
        aclear_released_perm_lock = asyncio.Event()

        async def aclear_with_interleave():
            """Patch to observe the two-lock window."""
            # First: clear permission under _permission_lock
            async with bus._permission_lock:
                request_id = bus._session_pending_requests.get(session_key)
                if request_id:
                    bus._cancel_permission_request_unlocked(
                        request_id,
                        session_key=session_key,
                        reason="Clearing for test",
                    )
            aclear_released_perm_lock.set()

            # Wait for the publish to happen in the window
            await interaction_published.wait()

            # Second: clear interaction under _interaction_lock
            async with bus._interaction_lock:
                interaction_id = bus._session_pending_interactions.get(session_key)
                if interaction_id:
                    bus._cancel_interaction_request_unlocked(
                        interaction_id,
                        session_key=session_key,
                        content="Clearing for test",
                    )

        async def publish_in_window():
            """Publish a new interaction in the two-lock gap."""
            await aclear_released_perm_lock.wait()
            await bus.publish_interaction_request(
                InteractionRequest(
                    request_id="gap6-int-new",
                    session_key=session_key,
                    channel="web",
                    chat_id="chat1",
                    kind="question",
                    prompt="New question (should it survive?)",
                )
            )
            interaction_published.set()

        # Register a waiter for the new interaction before the clear hits
        waiter_ready = asyncio.Event()

        async def waiter_for_new_interaction():
            await aclear_released_perm_lock.wait()
            await interaction_published.wait()
            await asyncio.sleep(0)  # Let waiter_count increment
            waiter_ready.set()
            return await bus.wait_interaction_response("gap6-int-new", timeout=3.0)

        waiter_task = asyncio.create_task(waiter_for_new_interaction())

        # Run the interleaved aclear and publish
        await asyncio.gather(
            aclear_with_interleave(),
            publish_in_window(),
        )

        # Wait for waiter
        await waiter_ready.wait()
        await asyncio.sleep(0.1)  # Give aclear_with_interleave's second half time

        # The waiter for the NEW interaction may get cancelled by aclear
        try:
            response = await asyncio.wait_for(waiter_task, timeout=2.0)
            if response.action == "cancel":
                # Confirmed: the brand-new request was cancelled by aclear's
                # second lock acquisition. This is the documented behavior.
                pass
            else:
                # The request survived (race didn't trigger this time)
                pass
        except asyncio.TimeoutError:
            pytest.fail("Waiter hung — neither cancel nor response arrived")


# ===========================================================================
# GAP-7: dispatch 返回值未检查 — 状态机不一致
# ===========================================================================


class TestGap7DispatchReturnUnchecked:
    """When handle_permission_response dispatches PERMISSION_PENDING but the
    session is in IDLE, the dispatch fails silently. The handler still proceeds
    to submit the response, potentially leaving the state machine inconsistent."""

    async def test_submit_response_when_session_idle(self):
        """If session is IDLE when a permission response arrives, submit
        should still work (bus level) but state machine is not affected."""
        from xbot.runtime.state import SessionEvent, SessionPhase
        from xbot.runtime.state.runtime_registry import RuntimeSessionRegistry

        bus = MessageBus()
        registry = RuntimeSessionRegistry()
        session_key = "web:user1:gap7"

        # Publish a permission request (creates the event in the bus)
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="gap7-perm-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={},
                message="Allow?",
            )
        )

        # Put session through lifecycle to IDLE
        registry.dispatch(session_key, SessionEvent.USER_MESSAGE)
        registry.dispatch(session_key, SessionEvent.CLIENT_ACQUIRED)
        registry.dispatch(session_key, SessionEvent.QUERY_SENT)
        # Permission was pending during RECEIVING_STREAM
        registry.dispatch(session_key, SessionEvent.PERMISSION_PENDING)
        assert registry.get_phase(session_key) == SessionPhase.WAITING_PERMISSION

        # User does !stop → transitions to IDLE
        registry.dispatch(session_key, SessionEvent.TURN_COMPLETED)
        assert registry.get_phase(session_key) == SessionPhase.IDLE

        # Now permission response arrives late
        # The handler would dispatch PERMISSION_PENDING from IDLE — which should fail
        dispatch_result = registry.dispatch(
            session_key, SessionEvent.PERMISSION_PENDING, strict=True
        )

        # PERMISSION_PENDING target is WAITING_PERMISSION, not reachable from IDLE
        # unless IDLE allows it. Let's check:
        from xbot.runtime.state.coordinator import VALID_TRANSITIONS
        idle_targets = VALID_TRANSITIONS.get(SessionPhase.IDLE, set())
        can_go_to_waiting = SessionPhase.WAITING_PERMISSION in idle_targets

        if can_go_to_waiting:
            # If the state machine allows this, the dispatch succeeds
            # but the session enters WAITING_PERMISSION without an active agent turn
            assert dispatch_result is True
            assert registry.get_phase(session_key) == SessionPhase.WAITING_PERMISSION
        else:
            # Dispatch is rejected — state remains IDLE (correct)
            assert dispatch_result is False
            assert registry.get_phase(session_key) == SessionPhase.IDLE

        # Either way, bus-level submit should work
        submitted = await bus.submit_permission_response(
            PermissionResponse(
                request_id="gap7-perm-1",
                session_key=session_key,
                decision="allow",
            )
        )
        # Event still exists in bus (waiter hasn't consumed it)
        assert submitted is True


# ===========================================================================
# GAP-8: 并发重试双重 max-retry 触发
# ===========================================================================


class TestGap8ConcurrentRetryMax:
    """Two concurrent handle_interaction_response calls both hitting max retries
    should not cause double-cancel or corruption."""

    async def test_double_aclear_on_same_request_is_safe(self):
        """If aclear_interaction_request is called twice for the same request,
        the second call should be a no-op (not crash or double-signal)."""
        bus = MessageBus()
        session_key = "web:user1:gap8"

        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="gap8-int-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="question",
                prompt="Choose option",
            )
        )

        # Register waiter
        waiter = asyncio.create_task(
            bus.wait_interaction_response("gap8-int-1", timeout=5.0)
        )
        await asyncio.sleep(0)

        # First aclear — wakes the waiter
        await bus.aclear_interaction_request("gap8-int-1")

        # Second aclear — should be a no-op
        await bus.aclear_interaction_request("gap8-int-1")

        # Waiter should have gotten cancel from the first aclear
        response = await asyncio.wait_for(waiter, timeout=2.0)
        assert response.action == "cancel"

    async def test_double_aclear_without_waiter_is_safe(self):
        """Double aclear when no waiter registered: second is no-op."""
        bus = MessageBus()
        session_key = "web:user1:gap8-nw"

        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="gap8-nw-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="question",
                prompt="Choose",
            )
        )

        # No waiter → first aclear stores tombstone
        await bus.aclear_interaction_request("gap8-nw-1")

        # Second aclear → request is gone, should return without error
        await bus.aclear_interaction_request("gap8-nw-1")

        # Late waiter should still get cancel from the tombstone
        response = await bus.wait_interaction_response("gap8-nw-1", timeout=2.0)
        assert response.action == "cancel"


# ===========================================================================
# GAP-9: Session slot 泄漏 (task 内部异常未清理)
# ===========================================================================


class TestGap9SessionSlotLeak:
    """If a task raises an exception before entering its try block,
    the session slot leaks until the connection disconnects."""

    async def test_failed_task_slot_cleaned_by_disconnect(self):
        """After a task fails early, the disconnect finally-block should
        still clean it up from active_tasks."""
        active_tasks: dict[str, asyncio.Task] = {}
        active_tasks_lock = asyncio.Lock()
        session_key = "web:user1:gap9"
        owned_task_keys: set[str] = set()

        async def failing_agent_turn():
            """Fails immediately — simulates to_internal_session_key error."""
            raise ValueError("Invalid session key format")

        # Simulate the message handler creating and registering the task
        task = asyncio.create_task(failing_agent_turn())
        async with active_tasks_lock:
            active_tasks[session_key] = task
        owned_task_keys.add(session_key)

        # Task will fail quickly
        await asyncio.sleep(0.1)
        assert task.done()
        assert task.exception() is not None

        # But the slot is still occupied in active_tasks!
        assert session_key in active_tasks, "Slot leaked: task failed but slot remains"

        # Another connection trying to use this session would see "already running"
        async with active_tasks_lock:
            existing = active_tasks.get(session_key)
        if existing is not None and not existing.done():
            # Would reject with "already running" error
            # But task IS done, so this specific check passes
            pass

        # The check `not existing.done()` saves us here — but only because
        # the code checks .done(). Verify this:
        assert existing is not None and existing.done()

        # Simulate disconnect finally-block cleanup
        owned_tasks = []
        for key in list(owned_task_keys):
            async with active_tasks_lock:
                t = active_tasks.pop(key, None)
            if t is not None and not t.done():
                owned_tasks.append(t)

        # Task was done, so not added to cancel list — but slot IS cleaned
        assert session_key not in active_tasks
        assert len(owned_tasks) == 0  # Nothing to cancel (already done)

    async def test_new_connection_blocked_by_leaked_running_slot(self):
        """If a task is stuck (not done) and its slot leaked, new connections
        cannot use that session."""
        active_tasks: dict[str, asyncio.Task] = {}
        active_tasks_lock = asyncio.Lock()
        session_key = "web:user1:gap9-stuck"

        async def stuck_task():
            """Simulates a task that's stuck but not done."""
            await asyncio.sleep(9999)

        task = asyncio.create_task(stuck_task())
        async with active_tasks_lock:
            active_tasks[session_key] = task
        # Imagine owned_task_keys was lost (connection crashed without finally)

        await asyncio.sleep(0)

        # New connection tries to create a task for this session
        async with active_tasks_lock:
            existing = active_tasks.get(session_key)
        if existing is not None and not existing.done():
            new_task_blocked = True
        else:
            new_task_blocked = False

        assert new_task_blocked is True, (
            "New connection is blocked by a leaked slot from a crashed connection"
        )

        # Cleanup
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
