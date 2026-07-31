"""Chaos test: MessageBus permission flow under cancel/submit/aclear races.

We fuzz the permission request lifecycle by racing three actions per
request:
  * a waiter that calls `wait_permission_response` (may be cancelled mid-wait)
  * a submitter that calls `submit_permission_response`
  * an aclear that calls `aclear_session_requests`

Random delays and cancellation timings are injected via the seeded
`Chaos` object.  After N cycles the bus internal dicts MUST return to
baseline — a growth of even 1 across many cycles indicates a resource
leak.

Focus: bus internal state hygiene under adversarial ordering.  We are
NOT testing that any particular waiter gets any particular response —
under cancellation the waiter is expected to raise CancelledError; under
timeout it gets a synthetic 'deny'; under submit it gets the real
response.  What we care about is: **no orphan entries left behind**.
"""

from __future__ import annotations

import asyncio
import gc

import pytest

from tests.soak.conftest import assert_bounded_growth, capture_sizes

from xbot.platform.bus.queue import (
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)

pytestmark = [pytest.mark.chaos]


BUS_INTERNAL_DICTS = [
    "_pending_permission_responses",
    "_permission_results",
    "_permission_requests",
    "_session_pending_requests",
    "_permission_waiter_counts",
    "_cleared_permission_responses",
]


async def _drain_outbound(bus: MessageBus, stop_event: asyncio.Event) -> None:
    """Background task draining the outbound queue so publish never blocks."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(bus.outbound.get(), timeout=0.01)
        except asyncio.TimeoutError:
            pass


def _mkreq(session_key: str, request_id: str) -> PermissionRequest:
    return PermissionRequest(
        request_id=request_id,
        session_key=session_key,
        channel="web",
        chat_id="u",
        tool_name="test",
        tool_input={},
        message="?",
    )


def _mkresp(session_key: str, request_id: str) -> PermissionResponse:
    return PermissionResponse(
        request_id=request_id,
        session_key=session_key,
        decision="allow",
        reason="chaos",
    )


class TestPermissionLifecycleChaos:
    """Randomised race between waiter cancellation and response submission."""

    async def test_cancel_vs_submit_returns_to_baseline(self, chaos_rng):
        """N cycles of publish → race(cancel|submit) — internal dicts
        return to baseline after all tasks settle.
        """
        bus = MessageBus(max_queue_size=10000)

        # Prime baseline (first cycle allocates the dicts).
        await bus.publish_permission_request(_mkreq("sess:warm", "warmup"))
        await bus.submit_permission_response(_mkresp("sess:warm", "warmup"))
        await bus.wait_permission_response("warmup", timeout=0.01)
        gc.collect()

        baseline = capture_sizes(bus, BUS_INTERNAL_DICTS, label="baseline")

        for cycle in range(200):
            rid = f"chaos-{cycle}"
            sk = f"sess:{cycle % 5}"

            await bus.publish_permission_request(_mkreq(sk, rid))

            waiter = asyncio.create_task(bus.wait_permission_response(rid, timeout=0.05))

            # Give the waiter a chance to register.
            await asyncio.sleep(0)

            action = chaos_rng.choose(["cancel_first", "submit_first", "both_immediate", "submit_only"])
            if action == "cancel_first":
                waiter.cancel()
                await asyncio.sleep(0)
                await bus.submit_permission_response(_mkresp(sk, rid))
            elif action == "submit_first":
                await bus.submit_permission_response(_mkresp(sk, rid))
                await asyncio.sleep(0)
                waiter.cancel()
            elif action == "both_immediate":
                submit = asyncio.create_task(bus.submit_permission_response(_mkresp(sk, rid)))
                waiter.cancel()
                await asyncio.gather(submit, return_exceptions=True)
            else:  # submit_only
                await bus.submit_permission_response(_mkresp(sk, rid))

            # Drain waiter (may be cancelled or return a response).
            try:
                await waiter
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        gc.collect()
        final = capture_sizes(bus, BUS_INTERNAL_DICTS, label="final")

        # Tolerance: `_cleared_permission_responses` is a tombstone LRU
        # buffer with a bounded TTL — it MAY hold up to
        # `_max_pending_requests` entries (default 1000) and that is by
        # design.  All other dicts must return to baseline.
        assert_bounded_growth(
            baseline,
            final,
            tolerance={"_cleared_permission_responses": 1000},
        )

    async def test_aclear_vs_wait_returns_to_baseline(self, chaos_rng):
        """Race aclear_session_requests against wait_permission_response.

        Bug class hunted: `aclear` writes tombstone but forgets to
        decrement waiter counts, leaving `_permission_waiter_counts`
        entries that never expire.
        """
        bus = MessageBus(max_queue_size=10000)
        await bus.publish_permission_request(_mkreq("sess:warm", "warm"))
        await bus.aclear_session_requests("sess:warm")
        gc.collect()

        baseline = capture_sizes(bus, BUS_INTERNAL_DICTS, label="baseline")

        for cycle in range(150):
            rid = f"clr-{cycle}"
            sk = f"sess:{cycle % 3}"

            await bus.publish_permission_request(_mkreq(sk, rid))
            waiter = asyncio.create_task(bus.wait_permission_response(rid, timeout=0.05))
            await asyncio.sleep(0)

            if chaos_rng.coin(0.5):
                # aclear then submit (submit will find no waiter)
                await bus.aclear_session_requests(sk)
                if chaos_rng.coin(0.5):
                    await bus.submit_permission_response(_mkresp(sk, rid))
            else:
                # submit then aclear (aclear will find nothing to clear)
                if chaos_rng.coin(0.5):
                    await bus.submit_permission_response(_mkresp(sk, rid))
                await bus.aclear_session_requests(sk)

            # Waiter should always return a PermissionResponse (never hang).
            try:
                resp = await asyncio.wait_for(waiter, timeout=1.0)
                assert isinstance(resp, PermissionResponse), (
                    f"waiter got wrong type: {type(resp).__name__}"
                )
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                waiter.cancel()
                raise AssertionError(
                    f"WAITER HUNG after aclear/submit race "
                    f"(cycle={cycle}, action pattern in bus state)"
                )

        gc.collect()
        final = capture_sizes(bus, BUS_INTERNAL_DICTS, label="final")
        assert_bounded_growth(
            baseline,
            final,
            tolerance={"_cleared_permission_responses": 1000},
        )

    async def test_double_waiter_same_request(self, chaos_rng):
        """Two coroutines call wait_permission_response with the SAME
        request_id.  This is unusual (SDK only creates one waiter per
        request) but the current implementation counts waiters — verify
        the counter returns to zero after both settle, no matter which
        one wins the result.
        """
        bus = MessageBus(max_queue_size=10000)

        for cycle in range(50):
            rid = f"dup-{cycle}"
            sk = "sess:dup"

            await bus.publish_permission_request(_mkreq(sk, rid))

            w1 = asyncio.create_task(bus.wait_permission_response(rid, timeout=0.1))
            w2 = asyncio.create_task(bus.wait_permission_response(rid, timeout=0.1))
            await asyncio.sleep(0)

            await bus.submit_permission_response(_mkresp(sk, rid))

            r1, r2 = await asyncio.gather(w1, w2, return_exceptions=True)

            # Both should receive a PermissionResponse (one real, one
            # synthetic 'No response received').  Neither should hang.
            for r in (r1, r2):
                assert not isinstance(r, BaseException), (
                    f"waiter raised unexpectedly: {r!r}"
                )
                assert isinstance(r, PermissionResponse)

            # After both settle: waiter count for this request MUST be 0.
            assert rid not in bus._permission_waiter_counts, (
                f"waiter count for {rid} leaked: "
                f"{bus._permission_waiter_counts.get(rid)}"
            )

        # Final sanity: no waiter counts left at all.
        assert bus._permission_waiter_counts == {}, (
            f"waiter counts leaked across cycles: {bus._permission_waiter_counts}"
        )

    async def test_cancel_between_setevent_and_reacquire(self, chaos_rng):
        """Adversarial: cancel the waiter task the instant after the
        submitter has set the event but BEFORE the waiter has re-acquired
        the lock to pop the result.

        Bug class hunted: `_permission_results[rid]` set by submit and
        then leaked because the waiter's cleanup path is skipped when
        cancel wins the race.

        We rely on the fact that a submit + a waiter's `event.wait()`
        completion + a cancel are all separated only by event-loop
        yields; injecting `asyncio.sleep(0)` in between gives us
        opportunities to interleave.
        """
        bus = MessageBus(max_queue_size=10000)
        baseline = capture_sizes(bus, BUS_INTERNAL_DICTS, label="baseline")

        for cycle in range(200):
            rid = f"csr-{cycle}"
            sk = f"sess:{cycle % 4}"

            await bus.publish_permission_request(_mkreq(sk, rid))
            waiter = asyncio.create_task(bus.wait_permission_response(rid, timeout=0.5))
            await asyncio.sleep(0)  # let waiter register

            # Submit sets the event.
            await bus.submit_permission_response(_mkresp(sk, rid))
            # Immediately cancel — before the waiter can reacquire the lock.
            waiter.cancel()

            try:
                await waiter
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        # Drive one more submit/wait cycle to force any orphan cleanup
        # opportunities.
        gc.collect()
        final = capture_sizes(bus, BUS_INTERNAL_DICTS, label="final")

        assert_bounded_growth(
            baseline,
            final,
            tolerance={"_cleared_permission_responses": 1000},
        )
