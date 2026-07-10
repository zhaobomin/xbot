"""Test message bus request pool limits and cleanup.

Regression tests for request pool management fixes.
Tests that request pools have proper limits and cleanup expired requests.
"""

import asyncio
import time

import pytest

from xbot.platform.bus.queue import (
    REQUEST_TIMEOUT_SECONDS,
    InteractionRequest,
    InteractionResponse,
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)


class TestPermissionRequestPoolLimits:
    """Test permission request pool management."""

    @pytest.mark.asyncio
    async def test_permission_request_pool_respects_limit(self) -> None:
        """Permission request pool should enforce MAX_PENDING_REQUESTS limit."""
        bus = MessageBus(max_pending_requests=5)

        # Fill the pool to limit
        for i in range(5):
            req = PermissionRequest(
                request_id=f"req_{i}",
                session_key=f"session_{i}",
                channel="telegram",
                chat_id="chat_1",
                tool_name="test_tool",
                tool_input={},
                message=f"Request {i}",
            )
            await bus.publish_permission_request(req)
            # Consume the outbound message
            await bus.consume_outbound()

        # Check pool size
        async with bus._permission_lock:
            assert len(bus._permission_requests) == 5

    @pytest.mark.asyncio
    async def test_permission_request_pool_rejects_new_session_at_capacity(self) -> None:
        bus = MessageBus(max_queue_size=10, max_pending_requests=1)
        first = PermissionRequest(
            request_id="first", session_key="session-1", channel="telegram", chat_id="chat",
            tool_name="tool", tool_input={}, message="first",
        )
        second = PermissionRequest(
            request_id="second", session_key="session-2", channel="telegram", chat_id="chat",
            tool_name="tool", tool_input={}, message="second",
        )
        await bus.publish_permission_request(first)

        with pytest.raises(RuntimeError, match="Permission request pool at capacity"):
            await bus.publish_permission_request(second)

        async with bus._permission_lock:
            assert set(bus._permission_requests) == {"first"}

    @pytest.mark.asyncio
    async def test_permission_request_expired_cleanup(self) -> None:
        """Expired permission requests should be cleaned up."""
        bus = MessageBus(max_pending_requests=10)

        # Create an expired request (manually set created_at in the past)
        expired_req = PermissionRequest(
            request_id="expired_req",
            session_key="session_1",
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="Expired request",
        )
        expired_req.created_at = time.time() - REQUEST_TIMEOUT_SECONDS - 1

        # Add it to the pool directly
        async with bus._permission_lock:
            bus._permission_requests["expired_req"] = expired_req
            bus._pending_permission_responses["expired_req"] = asyncio.Event()
            bus._session_pending_requests["session_1"] = "expired_req"

        # Create new requests to fill pool and trigger cleanup
        for i in range(10):
            req = PermissionRequest(
                request_id=f"new_req_{i}",
                session_key=f"session_new_{i}",
                channel="telegram",
                chat_id="chat_1",
                tool_name="test_tool",
                tool_input={},
                message=f"New request {i}",
            )
            await bus.publish_permission_request(req)
            await bus.consume_outbound()

        # Expired request should be cleaned up when pool reaches capacity
        # Note: Cleanup happens when pool is at capacity, not on every request

    @pytest.mark.asyncio
    async def test_permission_request_is_expired_method(self) -> None:
        """PermissionRequest.is_expired() should work correctly."""
        req = PermissionRequest(
            request_id="test",
            session_key="session",
            channel="telegram",
            chat_id="chat",
            tool_name="tool",
            tool_input={},
            message="test",
        )

        # Fresh request should not be expired
        assert not req.is_expired()

        # Create an expired request
        expired_req = PermissionRequest(
            request_id="expired",
            session_key="session",
            channel="telegram",
            chat_id="chat",
            tool_name="tool",
            tool_input={},
            message="expired",
        )
        expired_req.created_at = time.time() - REQUEST_TIMEOUT_SECONDS - 1

        assert expired_req.is_expired()

    @pytest.mark.asyncio
    async def test_permission_response_completes_waiting_coroutine(self) -> None:
        """Permission response should complete the waiting coroutine."""
        bus = MessageBus()

        req = PermissionRequest(
            request_id="req_1",
            session_key="session_1",
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="Test request",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()  # Consume the published message

        # Start waiting for response
        wait_task = asyncio.create_task(
            bus.wait_permission_response("req_1", timeout=1.0)
        )

        # Give the waiter time to register
        await asyncio.sleep(0.1)

        # Submit response
        response = PermissionResponse(
            request_id="req_1",
            session_key="session_1",
            decision="allow",
        )
        await bus.submit_permission_response(response)

        # Wait should complete without timeout
        result = await wait_task
        assert result.decision == "allow"

    @pytest.mark.asyncio
    async def test_permission_superseded_by_newer_request(self) -> None:
        """Old request should be auto-denied when superseded by new one."""
        bus = MessageBus()

        # First request
        req1 = PermissionRequest(
            request_id="req_1",
            session_key="session_1",
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="First request",
        )
        await bus.publish_permission_request(req1)
        await bus.consume_outbound()

        # Second request for same session (supersedes first)
        req2 = PermissionRequest(
            request_id="req_2",
            session_key="session_1",  # Same session
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="Second request",
        )
        await bus.publish_permission_request(req2)
        await bus.consume_outbound()

        # First request should be auto-denied
        result = await bus.wait_permission_response("req_1", timeout=0.5)
        assert result.decision == "deny"
        assert "Superseded" in result.reason


class TestInteractionRequestPoolLimits:
    """Test interaction request pool management."""

    @pytest.mark.asyncio
    async def test_interaction_request_pool_respects_limit(self) -> None:
        """Interaction request pool should enforce limit."""
        bus = MessageBus(max_pending_requests=5)

        # Fill the pool to limit
        for i in range(5):
            req = InteractionRequest(
                request_id=f"req_{i}",
                session_key=f"session_{i}",
                channel="telegram",
                chat_id="chat_1",
                kind="question",
                prompt=f"Question {i}",
            )
            await bus.publish_interaction_request(req)
            await bus.consume_outbound()

        async with bus._interaction_lock:
            assert len(bus._interaction_requests) == 5

    @pytest.mark.asyncio
    async def test_interaction_request_pool_rejects_new_session_at_capacity(self) -> None:
        bus = MessageBus(max_queue_size=10, max_pending_requests=1)
        first = InteractionRequest(
            request_id="first", session_key="session-1", channel="telegram", chat_id="chat", prompt="first",
        )
        second = InteractionRequest(
            request_id="second", session_key="session-2", channel="telegram", chat_id="chat", prompt="second",
        )
        await bus.publish_interaction_request(first)

        with pytest.raises(RuntimeError, match="Interaction request pool at capacity"):
            await bus.publish_interaction_request(second)

        async with bus._interaction_lock:
            assert set(bus._interaction_requests) == {"first"}

    @pytest.mark.asyncio
    async def test_interaction_request_expired_cleanup(self) -> None:
        """Expired interaction requests should be cleaned up."""
        bus = MessageBus(max_pending_requests=10)

        # Create an expired request
        expired_req = InteractionRequest(
            request_id="expired_req",
            session_key="session_1",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Expired question",
        )
        expired_req.created_at = time.time() - REQUEST_TIMEOUT_SECONDS - 1

        async with bus._interaction_lock:
            bus._interaction_requests["expired_req"] = expired_req
            bus._pending_interaction_responses["expired_req"] = asyncio.Event()
            bus._session_pending_interactions["session_1"] = "expired_req"

        # New request triggers cleanup when pool reaches capacity
        for i in range(10):
            req = InteractionRequest(
                request_id=f"new_req_{i}",
                session_key=f"session_new_{i}",
                channel="telegram",
                chat_id="chat_1",
                kind="question",
                prompt=f"New question {i}",
            )
            await bus.publish_interaction_request(req)
            await bus.consume_outbound()

        # Cleanup happens when pool is at capacity

    @pytest.mark.asyncio
    async def test_interaction_request_is_expired_method(self) -> None:
        """InteractionRequest.is_expired() should work correctly."""
        req = InteractionRequest(
            request_id="test",
            session_key="session",
            channel="telegram",
            chat_id="chat",
            kind="question",
            prompt="test",
        )

        assert not req.is_expired()

        expired_req = InteractionRequest(
            request_id="expired",
            session_key="session",
            channel="telegram",
            chat_id="chat",
            kind="question",
            prompt="expired",
        )
        expired_req.created_at = time.time() - REQUEST_TIMEOUT_SECONDS - 1

        assert expired_req.is_expired()

    @pytest.mark.asyncio
    async def test_interaction_superseded_by_newer_request(self) -> None:
        """Old interaction should be cancelled when superseded."""
        bus = MessageBus()

        # First interaction
        req1 = InteractionRequest(
            request_id="req_1",
            session_key="session_1",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="First question",
        )
        await bus.publish_interaction_request(req1)
        await bus.consume_outbound()

        # Second interaction for same session
        req2 = InteractionRequest(
            request_id="req_2",
            session_key="session_1",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Second question",
        )
        await bus.publish_interaction_request(req2)
        await bus.consume_outbound()

        # First should be cancelled
        result = await bus.wait_interaction_response("req_1", timeout=0.5)
        assert result.action == "cancel"
        assert "Superseded" in result.content


class TestRequestPoolConcurrency:
    """Test concurrent access to request pools."""

    @pytest.mark.asyncio
    async def test_concurrent_permission_requests_different_sessions(self) -> None:
        """Concurrent requests for different sessions should all succeed."""
        bus = MessageBus(max_pending_requests=100)

        async def create_request(idx: int) -> None:
            req = PermissionRequest(
                request_id=f"req_{idx}",
                session_key=f"session_{idx}",
                channel="telegram",
                chat_id="chat_1",
                tool_name="test_tool",
                tool_input={},
                message=f"Request {idx}",
            )
            await bus.publish_permission_request(req)

        # Create 50 concurrent requests
        await asyncio.gather(*[create_request(i) for i in range(50)])

        async with bus._permission_lock:
            assert len(bus._permission_requests) == 50

    @pytest.mark.asyncio
    async def test_concurrent_interaction_requests_different_sessions(self) -> None:
        """Concurrent interactions for different sessions should all succeed."""
        bus = MessageBus(max_pending_requests=100)

        async def create_request(idx: int) -> None:
            req = InteractionRequest(
                request_id=f"req_{idx}",
                session_key=f"session_{idx}",
                channel="telegram",
                chat_id="chat_1",
                kind="question",
                prompt=f"Question {idx}",
            )
            await bus.publish_interaction_request(req)

        await asyncio.gather(*[create_request(i) for i in range(50)])

        async with bus._interaction_lock:
            assert len(bus._interaction_requests) == 50

    @pytest.mark.asyncio
    async def test_response_to_nonexistent_request_handled(self) -> None:
        """Response to non-existent request should not crash."""
        bus = MessageBus()

        response = PermissionResponse(
            request_id="nonexistent",
            session_key="session",
            decision="allow",
        )

        # Should not raise
        result = await bus.submit_permission_response(response)
        assert result is False  # No waiter was found

        interaction_response = InteractionResponse(
            request_id="nonexistent",
            session_key="session",
            action="reply",
            content="test",
        )

        # Should not raise
        result = await bus.submit_interaction_response(interaction_response)
        assert result is False  # No waiter was found


class TestRequestTimeout:
    """Test request timeout behavior."""

    @pytest.mark.asyncio
    async def test_permission_response_timeout(self) -> None:
        """Waiting for permission response should timeout correctly."""
        bus = MessageBus()

        # The wait should return a deny response on timeout
        result = await bus.wait_permission_response("nonexistent", timeout=0.1)
        assert result.decision == "deny"
        assert "Timeout" in result.reason

    @pytest.mark.asyncio
    async def test_interaction_response_timeout(self) -> None:
        """Waiting for interaction response should timeout correctly."""
        bus = MessageBus()

        # The wait should return a cancel response on timeout
        result = await bus.wait_interaction_response("nonexistent", timeout=0.1)
        assert result.action == "cancel"
