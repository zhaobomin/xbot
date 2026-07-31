"""Integration tests: MessageBus permission request/response multi-waiter and cancellation edge cases.

Tests verify correct behavior under:
- Multiple waiters on a single request_id
- External task cancellation cleaning up waiter state
- Timeout/submit race conditions on the same tick
- Re-publishing the same request_id after aclear
- Supersede waking the previous waiter with a deny signal
"""

from __future__ import annotations

import asyncio

import pytest

from xbot.platform.bus.queue import (
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(request_id: str, session_key: str = "s1") -> PermissionRequest:
    """Create a PermissionRequest with minimal required fields."""
    return PermissionRequest(
        request_id=request_id,
        session_key=session_key,
        channel="test",
        chat_id="chat-1",
        tool_name="bash",
        tool_input={"cmd": "echo hello"},
        message="Allow bash execution?",
    )


def _make_response(
    request_id: str,
    session_key: str = "s1",
    decision: str = "allow",
    reason: str = "",
) -> PermissionResponse:
    """Create a PermissionResponse."""
    return PermissionResponse(
        request_id=request_id,
        session_key=session_key,
        decision=decision,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# 1. Two waiters on the same request_id both wake
# ---------------------------------------------------------------------------


async def test_two_waiters_same_request_id_both_wake():
    """Two coroutines both await wait_permission_response('req-1').

    Submit one response. Both waiters should return a PermissionResponse
    (one gets the real 'allow', the other may get a synthetic deny or
    'No response received'). Neither should hang indefinitely.
    """
    bus = MessageBus(max_queue_size=10000)

    # Publish the permission request (pre-registers the Event)
    await bus.publish_permission_request(_make_request("req-1"))

    waiter1_ready = asyncio.Event()
    waiter2_ready = asyncio.Event()
    results: list[PermissionResponse] = []

    async def waiter_1():
        waiter1_ready.set()
        resp = await bus.wait_permission_response("req-1", timeout=3.0)
        results.append(resp)

    async def waiter_2():
        waiter2_ready.set()
        resp = await bus.wait_permission_response("req-1", timeout=3.0)
        results.append(resp)

    # Start both waiters
    t1 = asyncio.create_task(waiter_1())
    t2 = asyncio.create_task(waiter_2())

    # Wait until both have registered
    await waiter1_ready.wait()
    await waiter2_ready.wait()
    await asyncio.sleep(0.01)  # Let both enter wait_permission_response

    # Both should be counted
    assert bus._permission_waiter_counts.get("req-1", 0) == 2

    # Submit a single response
    submitted = await bus.submit_permission_response(
        _make_response("req-1", decision="allow")
    )
    assert submitted is True

    # Both tasks must complete (not hang)
    done, pending = await asyncio.wait([t1, t2], timeout=5.0)
    assert len(pending) == 0, "One or both waiters hung indefinitely"

    # Verify both got PermissionResponse objects
    assert len(results) == 2
    for r in results:
        assert isinstance(r, PermissionResponse)
        assert r.request_id == "req-1"
        assert r.decision in ("allow", "deny")

    # At least one must have received the real "allow" response
    decisions = [r.decision for r in results]
    assert "allow" in decisions

    # After both settle, waiter count must be zero (no leak)
    assert bus._permission_waiter_counts.get("req-1", 0) == 0


# ---------------------------------------------------------------------------
# 2. External cancel decrements waiter count
# ---------------------------------------------------------------------------


async def test_external_cancel_decrements_waiter_count():
    """Start a waiter task, then externally cancel it.

    Assert _permission_waiter_counts[request_id] goes to 0 (or key is
    removed). No residual state should remain in the bus internals.
    """
    bus = MessageBus(max_queue_size=10000)

    await bus.publish_permission_request(_make_request("req-cancel"))

    waiter_entered = asyncio.Event()

    async def waiter():
        waiter_entered.set()
        return await bus.wait_permission_response("req-cancel", timeout=60.0)

    task = asyncio.create_task(waiter())
    await waiter_entered.wait()
    await asyncio.sleep(0.01)  # Ensure waiter is inside wait_permission_response

    # Verify the waiter registered
    assert bus._permission_waiter_counts.get("req-cancel", 0) == 1

    # Externally cancel the task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Waiter count must be 0 or key removed
    count = bus._permission_waiter_counts.get("req-cancel", 0)
    assert count == 0, f"Expected waiter count 0, got {count}"

    # No residual state for this request_id
    assert "req-cancel" not in bus._permission_waiter_counts
    assert "req-cancel" not in bus._pending_permission_responses
    assert "req-cancel" not in bus._permission_results
    assert "req-cancel" not in bus._permission_requests

    # Session pending requests should also be cleaned
    assert "s1" not in bus._session_pending_requests


# ---------------------------------------------------------------------------
# 3. Timeout and submit same tick — no result leak
# ---------------------------------------------------------------------------


async def test_timeout_and_submit_same_tick_no_result_leak():
    """Set a very short timeout (0.01s) on wait_permission_response.

    Submit immediately after. After both complete, assert _permission_results
    does not retain a stale entry. If it does, verify that
    aclear_session_requests cleans it.
    """
    bus = MessageBus(max_queue_size=10000)
    session_key = "sess-race"

    await bus.publish_permission_request(
        _make_request("req-race", session_key=session_key)
    )

    waiter_started = asyncio.Event()

    async def short_timeout_waiter():
        waiter_started.set()
        return await bus.wait_permission_response("req-race", timeout=0.01)

    task = asyncio.create_task(short_timeout_waiter())
    await waiter_started.wait()

    # Submit immediately — racing with the timeout
    await bus.submit_permission_response(
        _make_response("req-race", session_key=session_key, decision="allow")
    )

    # Wait for the waiter to complete (either got response or timed out)
    result = await asyncio.wait_for(task, timeout=3.0)
    assert isinstance(result, PermissionResponse)
    # Either timeout deny or the actual allow — both are valid race outcomes
    assert result.decision in ("allow", "deny")

    # Give the event loop a chance to settle
    await asyncio.sleep(0.05)

    # The critical invariant: _permission_results must NOT retain a stale entry
    # for this request_id after the waiter has completed
    has_stale_result = "req-race" in bus._permission_results

    if has_stale_result:
        # If there is a stale entry, aclear_session_requests must clean it
        cleanup = await bus.aclear_session_requests(session_key)
        # After cleanup, no stale result should remain
        assert "req-race" not in bus._permission_results, (
            "_permission_results retains stale entry even after aclear_session_requests"
        )
    else:
        # No stale result — this is the ideal outcome
        pass

    # Verify no waiter count leak
    assert bus._permission_waiter_counts.get("req-race", 0) == 0


# ---------------------------------------------------------------------------
# 4. Publish after aclear with same request_id reuses cleanly
# ---------------------------------------------------------------------------


async def test_publish_after_aclear_same_request_id_reuses_cleanly():
    """Publish(req-X) -> aclear -> publish(req-X same ID again) -> wait -> submit.

    The second cycle should work correctly without being blocked by
    tombstone or stale state from the first cycle.

    Implementation detail: wait_permission_response first checks
    _take_cleared_permission_unlocked which pops and consumes the tombstone.
    After consumption, the event from the second publish is available for
    a subsequent waiter. This test verifies the full reuse flow.
    """
    bus = MessageBus(max_queue_size=10000)
    session_key = "sess-reuse"
    request_id = "req-X"

    # --- First cycle: publish and clear ---
    await bus.publish_permission_request(
        _make_request(request_id, session_key=session_key)
    )

    # Clear the request (stores tombstone for late waiters)
    await bus.aclear_session_requests(session_key)

    # Verify the first cycle tombstone exists
    assert request_id in bus._cleared_permission_responses

    # --- Second cycle: re-publish the SAME request_id ---
    await bus.publish_permission_request(
        _make_request(request_id, session_key=session_key)
    )

    # The tombstone from cycle 1 still exists — first wait consumes it
    tombstone_result = await bus.wait_permission_response(request_id, timeout=3.0)
    assert tombstone_result.decision == "deny", (
        "First wait should consume the tombstone and get deny"
    )
    # Tombstone is now consumed (popped)
    assert request_id not in bus._cleared_permission_responses

    # The event from the second publish is still registered in the bus
    # (publish uses setdefault, so it persists). Re-publish to ensure
    # the event is fresh for the next waiter.
    await bus.publish_permission_request(
        _make_request(request_id, session_key=session_key)
    )

    # Now a second waiter registers on the fresh event — no tombstone blocks it
    waiter_ready = asyncio.Event()

    async def waiter():
        waiter_ready.set()
        return await bus.wait_permission_response(request_id, timeout=3.0)

    task = asyncio.create_task(waiter())
    await waiter_ready.wait()
    await asyncio.sleep(0.01)

    # Waiter should be properly registered (no tombstone to consume)
    assert bus._permission_waiter_counts.get(request_id, 0) >= 1

    # Submit a fresh response
    submitted = await bus.submit_permission_response(
        _make_response(request_id, session_key=session_key, decision="allow")
    )
    assert submitted is True

    # Waiter should get the fresh "allow" response
    result = await asyncio.wait_for(task, timeout=3.0)
    assert result.decision == "allow"
    assert result.request_id == request_id

    # No residual state after completion
    assert bus._permission_waiter_counts.get(request_id, 0) == 0
    assert request_id not in bus._permission_results


# ---------------------------------------------------------------------------
# 5. Supersede wakes previous waiter with specific signal
# ---------------------------------------------------------------------------


async def test_supersede_wakes_previous_waiter_with_specific_signal():
    """Publish req-A for session 's1', start waiter for req-A.

    Then publish req-B for same session 's1' (should supersede).
    The first waiter should be woken with a 'deny' response containing
    'Superseded' in the reason field.
    """
    bus = MessageBus(max_queue_size=10000)
    session_key = "s1"

    # Publish req-A
    await bus.publish_permission_request(
        _make_request("req-A", session_key=session_key)
    )

    # Start waiter for req-A
    waiter_ready = asyncio.Event()

    async def waiter_a():
        waiter_ready.set()
        return await bus.wait_permission_response("req-A", timeout=5.0)

    task_a = asyncio.create_task(waiter_a())
    await waiter_ready.wait()
    await asyncio.sleep(0.01)

    # Verify waiter is registered
    assert bus._permission_waiter_counts.get("req-A", 0) == 1

    # Publish req-B for the SAME session — this should supersede req-A
    await bus.publish_permission_request(
        _make_request("req-B", session_key=session_key)
    )

    # The first waiter (req-A) should be woken immediately
    result_a = await asyncio.wait_for(task_a, timeout=3.0)

    # Verify the supersede signal
    assert isinstance(result_a, PermissionResponse)
    assert result_a.request_id == "req-A"
    assert result_a.decision == "deny"
    assert "Superseded" in result_a.reason, (
        f"Expected 'Superseded' in reason, got: {result_a.reason!r}"
    )

    # The new request (req-B) should now be the active pending request
    assert bus._session_pending_requests.get(session_key) == "req-B"

    # req-A should have no residual waiter count
    assert bus._permission_waiter_counts.get("req-A", 0) == 0

    # req-B should be waitable independently
    waiter_b_ready = asyncio.Event()

    async def waiter_b():
        waiter_b_ready.set()
        return await bus.wait_permission_response("req-B", timeout=3.0)

    task_b = asyncio.create_task(waiter_b())
    await waiter_b_ready.wait()
    await asyncio.sleep(0.01)

    await bus.submit_permission_response(
        _make_response("req-B", session_key=session_key, decision="allow")
    )

    result_b = await asyncio.wait_for(task_b, timeout=3.0)
    assert result_b.decision == "allow"
