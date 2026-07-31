"""Soak test: MessageBus long-running publish / submit / supersede cycles.

Focus areas (mirror the Layer-1 fix scope):
1. Normal publish→wait→submit lifecycle repeated many times: every tracking
   dict must return to baseline size.
2. Supersede pattern (same session_key, new request_id) repeated many times:
   the old request must not linger in _permission_requests / _pending_*.
3. aclear_session_requests loop: forced cancellation must not leak.
4. Timeout path: waiter times out → dicts must be freed.
5. Tombstone LRU: _cleared_permission_responses is bounded by
   max_pending_requests even under high churn.
6. Mixed permission + interaction workload: no cross-contamination between
   the two tracking families.

Success criteria: at the end of each loop, every internal dict returns to
its starting size (or, for tombstone dicts, remains bounded by the
configured cap).
"""

from __future__ import annotations

import asyncio
import gc

import pytest

from xbot.platform.bus.queue import (
    InteractionRequest,
    InteractionResponse,
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)

from .conftest import assert_bounded_growth, capture_sizes

pytestmark = [pytest.mark.soak, pytest.mark.asyncio]


PERMISSION_DICTS = [
    "_permission_requests",
    "_pending_permission_responses",
    "_permission_results",
    "_permission_waiter_counts",
    "_session_pending_requests",
]

INTERACTION_DICTS = [
    "_interaction_requests",
    "_pending_interaction_responses",
    "_interaction_results",
    "_interaction_waiter_counts",
    "_session_pending_interactions",
]

TOMBSTONE_DICTS = [
    "_cleared_permission_responses",
    "_cleared_interaction_responses",
]

ALL_DICTS = PERMISSION_DICTS + INTERACTION_DICTS + TOMBSTONE_DICTS


def _perm(req_id: str, session_key: str = "web:u:s") -> PermissionRequest:
    return PermissionRequest(
        request_id=req_id,
        session_key=session_key,
        channel="web",
        chat_id="chat1",
        tool_name="TestTool",
        tool_input={"x": 1},
        message="approve?",
    )


def _perm_resp(req_id: str, session_key: str = "web:u:s") -> PermissionResponse:
    return PermissionResponse(
        request_id=req_id,
        session_key=session_key,
        decision="allow",
    )


def _inter(req_id: str, session_key: str = "web:u:s") -> InteractionRequest:
    return InteractionRequest(
        request_id=req_id,
        session_key=session_key,
        channel="web",
        chat_id="chat1",
        kind="input",
        prompt="?",
    )


def _inter_resp(req_id: str, session_key: str = "web:u:s") -> InteractionResponse:
    return InteractionResponse(
        request_id=req_id,
        session_key=session_key,
        action="ok",
        content="done",
    )


async def _drain_outbound(bus: MessageBus) -> None:
    """Consume any outbound messages produced by publish_*_request."""
    while bus.outbound_size > 0:
        try:
            await asyncio.wait_for(bus.consume_outbound(), timeout=0.01)
        except asyncio.TimeoutError:
            break


# ---------------------------------------------------------------------------
# 1. Normal happy-path loop
# ---------------------------------------------------------------------------


class TestPermissionHappyPathSoak:
    """N happy-path cycles must leave every tracking dict at baseline."""

    @pytest.mark.parametrize("iterations", [2_000])
    async def test_permission_lifecycle_returns_to_baseline(self, iterations: int):
        bus = MessageBus()
        before = capture_sizes(bus, ALL_DICTS, label="before")

        async def one_cycle(idx: int) -> None:
            rid = f"perm-{idx}"
            skey = f"web:u:{idx}"  # distinct session_keys so no supersede
            req = _perm(rid, session_key=skey)
            await bus.publish_permission_request(req)

            waiter = asyncio.create_task(
                bus.wait_permission_response(rid, timeout=5.0)
            )
            # Give waiter time to register.
            await asyncio.sleep(0)
            await bus.submit_permission_response(_perm_resp(rid, session_key=skey))
            resp = await waiter
            assert resp is not None and resp.decision == "allow"

        for i in range(iterations):
            await one_cycle(i)
            if i % 200 == 0:
                await _drain_outbound(bus)

        await _drain_outbound(bus)
        gc.collect()

        after = capture_sizes(bus, ALL_DICTS, label="after")
        # Tombstones may accumulate but are bounded by max_pending_requests.
        assert_bounded_growth(
            before,
            after,
            tolerance={
                "_cleared_permission_responses": bus._max_pending_requests,
                "_cleared_interaction_responses": bus._max_pending_requests,
            },
        )


# ---------------------------------------------------------------------------
# 2. Supersede path (this is where the Layer-1 fix landed)
# ---------------------------------------------------------------------------


class TestSupersedeSoak:
    """Repeatedly supersede requests on the SAME session_key.

    Old code leaked: _permission_requests kept the previous request until
    it expired 600s later. After the eager-cleanup fix, each supersede
    should immediately release the previous entry (tombstone is used for
    late waiters).
    """

    @pytest.mark.parametrize("iterations", [1_500])
    async def test_permission_supersede_frees_old(self, iterations: int):
        bus = MessageBus()
        session_key = "web:u:hotseat"
        # Seed with an initial request (no waiter) so we exercise the
        # "no-waiter supersede" branch on every iteration.
        await bus.publish_permission_request(_perm("perm-0", session_key=session_key))
        before = capture_sizes(bus, ALL_DICTS, label="before")

        for i in range(1, iterations + 1):
            await bus.publish_permission_request(
                _perm(f"perm-{i}", session_key=session_key)
            )
            if i % 500 == 0:
                await _drain_outbound(bus)

        await _drain_outbound(bus)
        gc.collect()

        after = capture_sizes(bus, ALL_DICTS, label="after")
        # Only the latest request should be live on this session.
        assert len(bus._permission_requests) == 1
        assert len(bus._pending_permission_responses) == 1
        assert len(bus._session_pending_requests) == 1
        # Tombstones are bounded by max_pending_requests.
        assert (
            len(bus._cleared_permission_responses) <= bus._max_pending_requests
        )
        assert_bounded_growth(
            before,
            after,
            tolerance={
                "_permission_requests": 0,
                "_pending_permission_responses": 0,
                "_permission_results": 0,
                "_permission_waiter_counts": 0,
                "_session_pending_requests": 0,
                "_cleared_permission_responses": bus._max_pending_requests,
            },
        )

    @pytest.mark.parametrize("iterations", [1_500])
    async def test_interaction_supersede_frees_old(self, iterations: int):
        bus = MessageBus()
        session_key = "web:u:hotseat"
        await bus.publish_interaction_request(_inter("i-0", session_key=session_key))
        before = capture_sizes(bus, ALL_DICTS, label="before")

        for i in range(1, iterations + 1):
            await bus.publish_interaction_request(
                _inter(f"i-{i}", session_key=session_key)
            )
            if i % 500 == 0:
                await _drain_outbound(bus)

        await _drain_outbound(bus)
        gc.collect()

        assert len(bus._interaction_requests) == 1
        assert len(bus._pending_interaction_responses) == 1
        assert len(bus._session_pending_interactions) == 1
        assert (
            len(bus._cleared_interaction_responses) <= bus._max_pending_requests
        )


# ---------------------------------------------------------------------------
# 3. aclear_session_requests churn
# ---------------------------------------------------------------------------


class TestAclearSoak:
    """Publish then aclear repeatedly — a real-world !stop / !reset pattern."""

    @pytest.mark.parametrize("iterations", [1_500])
    async def test_publish_then_aclear_returns_to_baseline(self, iterations: int):
        bus = MessageBus()
        before = capture_sizes(bus, ALL_DICTS, label="before")

        for i in range(iterations):
            skey = f"web:u:s{i}"
            rid = f"perm-{i}"
            await bus.publish_permission_request(_perm(rid, session_key=skey))
            waiter = asyncio.create_task(
                bus.wait_permission_response(rid, timeout=5.0)
            )
            await asyncio.sleep(0)
            await bus.aclear_session_requests(skey)
            # Waiter should wake up with a synthetic "deny" response
            # (Bus deliberately returns a decision object instead of None
            # so callers can distinguish clear vs. wait failure).
            resp = await waiter
            assert resp is not None
            assert resp.decision == "deny"
            if i % 300 == 0:
                await _drain_outbound(bus)

        await _drain_outbound(bus)
        gc.collect()

        after = capture_sizes(bus, ALL_DICTS, label="after")
        assert_bounded_growth(
            before,
            after,
            tolerance={
                "_cleared_permission_responses": bus._max_pending_requests,
            },
        )


# ---------------------------------------------------------------------------
# 4. Waiter-timeout path
# ---------------------------------------------------------------------------


class TestTimeoutSoak:
    """Waiter-timeout must free every tracking dict."""

    @pytest.mark.parametrize("iterations", [800])
    async def test_wait_timeout_frees_all(self, iterations: int):
        bus = MessageBus()
        before = capture_sizes(bus, ALL_DICTS, label="before")

        for i in range(iterations):
            rid = f"perm-{i}"
            skey = f"web:u:s{i}"
            await bus.publish_permission_request(_perm(rid, session_key=skey))
            # Use a very short timeout so the waiter times out immediately.
            resp = await bus.wait_permission_response(rid, timeout=0.001)
            # On timeout wait_permission_response returns a synthetic
            # deny/timeout response (never None); the tracking dicts still
            # must be freed.
            assert resp is not None
            assert resp.decision == "deny"
            assert "Timeout" in resp.reason
            if i % 200 == 0:
                await _drain_outbound(bus)

        await _drain_outbound(bus)
        gc.collect()

        after = capture_sizes(bus, ALL_DICTS, label="after")
        assert_bounded_growth(
            before,
            after,
            tolerance={
                "_cleared_permission_responses": bus._max_pending_requests,
            },
        )


# ---------------------------------------------------------------------------
# 5. Tombstone LRU cap
# ---------------------------------------------------------------------------


class TestTombstoneCapSoak:
    """Under massive churn, tombstone dict must stay bounded."""

    async def test_tombstone_bounded_under_10k_submits(self):
        bus = MessageBus(max_pending_requests=100)  # tight cap
        for i in range(10_000):
            rid = f"perm-{i}"
            skey = f"web:u:s{i}"
            await bus.publish_permission_request(_perm(rid, session_key=skey))
            # Submit WITHOUT registering a waiter — this triggers the
            # tombstone path in submit_permission_response.
            await bus.submit_permission_response(_perm_resp(rid, session_key=skey))
            if i % 500 == 0:
                await _drain_outbound(bus)

        assert len(bus._cleared_permission_responses) <= 100, (
            f"tombstone dict grew to {len(bus._cleared_permission_responses)} "
            f"— cap was 100"
        )
        # Non-tombstone dicts should be empty (no waiters, submit path
        # eagerly freed everything).
        assert len(bus._permission_requests) == 0
        assert len(bus._pending_permission_responses) == 0
        assert len(bus._permission_results) == 0
        assert len(bus._session_pending_requests) == 0


# ---------------------------------------------------------------------------
# 6. Mixed permission + interaction workload
# ---------------------------------------------------------------------------


class TestMixedSoak:
    """Interleave permission and interaction lifecycles — no cross-contamination."""

    @pytest.mark.parametrize("iterations", [1_000])
    async def test_mixed_return_to_baseline(self, iterations: int):
        bus = MessageBus()
        before = capture_sizes(bus, ALL_DICTS, label="before")

        for i in range(iterations):
            skey = f"web:u:s{i}"
            perm_id = f"perm-{i}"
            inter_id = f"inter-{i}"

            await bus.publish_permission_request(_perm(perm_id, session_key=skey))
            await bus.publish_interaction_request(_inter(inter_id, session_key=skey))

            perm_waiter = asyncio.create_task(
                bus.wait_permission_response(perm_id, timeout=5.0)
            )
            inter_waiter = asyncio.create_task(
                bus.wait_interaction_response(inter_id, timeout=5.0)
            )
            await asyncio.sleep(0)

            await bus.submit_permission_response(_perm_resp(perm_id, session_key=skey))
            await bus.submit_interaction_response(_inter_resp(inter_id, session_key=skey))

            await perm_waiter
            await inter_waiter

            if i % 250 == 0:
                await _drain_outbound(bus)

        await _drain_outbound(bus)
        gc.collect()

        after = capture_sizes(bus, ALL_DICTS, label="after")
        assert_bounded_growth(
            before,
            after,
            tolerance={
                "_cleared_permission_responses": bus._max_pending_requests,
                "_cleared_interaction_responses": bus._max_pending_requests,
            },
        )


# ---------------------------------------------------------------------------
# 7. Concurrent waiters + submitters
# ---------------------------------------------------------------------------


class TestConcurrentSoak:
    """Many concurrent publish/wait/submit tasks — final state must be clean."""

    @pytest.mark.parametrize("fanout,rounds", [(20, 100)])
    async def test_concurrent_publishers_dont_leak(self, fanout: int, rounds: int):
        bus = MessageBus()
        before = capture_sizes(bus, ALL_DICTS, label="before")

        async def one_round(base: int) -> None:
            async def cycle(idx: int) -> None:
                rid = f"perm-{base}-{idx}"
                skey = f"web:u:s{base}-{idx}"
                await bus.publish_permission_request(_perm(rid, session_key=skey))
                waiter = asyncio.create_task(
                    bus.wait_permission_response(rid, timeout=5.0)
                )
                await asyncio.sleep(0)
                await bus.submit_permission_response(_perm_resp(rid, session_key=skey))
                await waiter

            await asyncio.gather(*[cycle(i) for i in range(fanout)])

        for r in range(rounds):
            await one_round(r)
            if r % 20 == 0:
                await _drain_outbound(bus)

        await _drain_outbound(bus)
        gc.collect()

        after = capture_sizes(bus, ALL_DICTS, label="after")
        assert_bounded_growth(
            before,
            after,
            tolerance={
                "_cleared_permission_responses": bus._max_pending_requests,
                "_cleared_interaction_responses": bus._max_pending_requests,
            },
        )
