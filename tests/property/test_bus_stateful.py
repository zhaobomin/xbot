"""Stateful property-based tests for MessageBus permission request flow.

Uses Hypothesis RuleBasedStateMachine to generate random sequences of
publish/wait/submit/supersede/cancel operations and verify invariants hold.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import MagicMock

import hypothesis.strategies as st
from hypothesis import settings, HealthCheck
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
    precondition,
)

from xbot.platform.bus.queue import (
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run a coroutine in the current or a new event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Create a new loop in a thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=10)
    else:
        return asyncio.run(coro)


def _make_permission_request(session_key: str, request_id: str):
    """Create a minimal PermissionRequest."""
    return PermissionRequest(
        request_id=request_id,
        session_key=session_key,
        channel="test",
        chat_id="test-chat",
        tool_name="test_tool",
        tool_input={"key": "value"},
        message="Test permission request",
    )


def _make_permission_response(request_id: str, session_key: str = "", decision: str = "allow"):
    """Create a minimal PermissionResponse."""
    return PermissionResponse(
        request_id=request_id,
        session_key=session_key,
        decision=decision,
    )


# ---------------------------------------------------------------------------
# Stateful machine
# ---------------------------------------------------------------------------


class MessageBusPermissionStateMachine(RuleBasedStateMachine):
    """Model-based test for MessageBus permission request lifecycle.

    Model tracks:
    - Which session_keys have pending requests (at most 1 per session)
    - Which request_ids are pending
    """

    def __init__(self):
        super().__init__()
        self._bus: MessageBus | None = None
        # Model: session_key -> request_id (at most one per session)
        self._pending: dict[str, str] = {}
        # Track explicitly submitted (fully cleaned up) request_ids
        self._submitted: set[str] = set()
        # Track superseded requests (still in bus tracking until waiter consumes)
        self._superseded: set[str] = set()

    @initialize()
    def setup(self):
        self._bus = MessageBus(max_queue_size=100, max_pending_requests=50)
        self._pending = {}
        self._submitted = set()
        self._superseded = set()

    @rule(session_idx=st.integers(min_value=0, max_value=4))
    def publish_request(self, session_idx: int):
        """Publish a permission request for a session."""
        session_key = f"session-{session_idx}"
        request_id = str(uuid.uuid4())
        req = _make_permission_request(session_key, request_id)

        async def _do():
            await self._bus.publish_permission_request(req)

        _run(_do())

        # If session already had a pending request, the old one was superseded
        old_id = self._pending.get(session_key)
        if old_id:
            self._superseded.add(old_id)

        self._pending[session_key] = request_id

    @rule(session_idx=st.integers(min_value=0, max_value=4))
    def submit_response(self, session_idx: int):
        """Submit a response for a pending request."""
        session_key = f"session-{session_idx}"
        request_id = self._pending.get(session_key)
        if request_id is None:
            return  # nothing to respond to

        resp = _make_permission_response(request_id, session_key=session_key, decision="allow")

        async def _do():
            return await self._bus.submit_permission_response(resp)

        result = _run(_do())
        # submit should succeed
        assert result is True
        # Mark as submitted (fully cleaned up)
        self._submitted.add(request_id)
        del self._pending[session_key]

    @rule(session_idx=st.integers(min_value=0, max_value=4))
    def clear_session_requests(self, session_idx: int):
        """Clear all pending requests for a session."""
        session_key = f"session-{session_idx}"

        async def _do():
            return await self._bus.aclear_session_requests(session_key)

        _run(_do())

        # If there was a pending request, it's now cleared (stays in bus until consumed)
        old_id = self._pending.pop(session_key, None)
        if old_id:
            self._superseded.add(old_id)

    @rule(session_idx=st.integers(min_value=0, max_value=4))
    def submit_to_nonexistent(self, session_idx: int):
        """Submit a response for a non-existent request — must return False."""
        fake_id = str(uuid.uuid4())
        resp = _make_permission_response(fake_id, session_key="nonexistent", decision="allow")

        async def _do():
            return await self._bus.submit_permission_response(resp)

        result = _run(_do())
        assert result is False

    @invariant()
    def at_most_one_pending_per_session(self):
        """Each session has at most one pending request."""
        if self._bus is None:
            return

        for session_key, request_id in self._pending.items():
            async def _check(sk=session_key):
                got = await self._bus.aget_pending_request_for_session(sk)
                return got

            actual = _run(_check())
            assert actual == request_id, (
                f"Session {session_key}: expected pending {request_id}, got {actual}"
            )

    @rule(session_idx=st.integers(min_value=0, max_value=4))
    def double_submit_returns_false(self, session_idx: int):
        """Submitting a response twice for the same request returns False the second time."""
        session_key = f"session-{session_idx}"
        if session_key not in self._pending:
            return
        request_id = self._pending[session_key]
        if request_id in self._submitted:
            return  # already submitted

        # First submit
        resp = _make_permission_response(request_id, session_key=session_key, decision="allow")

        async def _first():
            return await self._bus.submit_permission_response(resp)

        result1 = _run(_first())
        assert result1 is True

        # Second submit — must return False (Event already set)
        async def _second():
            return await self._bus.submit_permission_response(resp)

        result2 = _run(_second())
        assert result2 is False, "Double submit should return False"

        self._submitted.add(request_id)
        del self._pending[session_key]

    @invariant()
    def session_pending_consistent(self):
        """After submit, the session should no longer map to the old request_id."""
        if self._bus is None:
            return
        for request_id in self._submitted:
            # Find which session this belonged to — it should not map back
            for sk, rid in self._pending.items():
                assert rid != request_id, (
                    f"Submitted request {request_id} still in model pending dict"
                )

    @invariant()
    def pending_count_bounded(self):
        """Number of pending requests must not exceed max_pending_requests."""
        if self._bus is None:
            return
        assert len(self._pending) <= 50


# Hypothesis settings
TestMessageBusPermission = MessageBusPermissionStateMachine.TestCase
TestMessageBusPermission.settings = settings(
    max_examples=100,
    stateful_step_count=20,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
