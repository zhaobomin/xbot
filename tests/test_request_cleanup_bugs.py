"""Regression tests for permission/interaction request cleanup bugs.

Tests for:
1. Bug 2: Permission request leak - _permission_requests not cleaned up
2. Bug 3: Interaction state residue after cancel
"""

import asyncio

import pytest

from xbot.platform.bus.queue import (
    InteractionRequest,
    InteractionResponse,
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)


class TestPermissionRequestCleanup:
    """Test that _permission_requests is properly cleaned up."""

    @pytest.mark.asyncio
    async def test_permission_request_cleaned_on_response(self) -> None:
        """Bug 2: _permission_requests should be cleaned when response received."""
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
        await bus.consume_outbound()

        # Verify request is in the pool
        async with bus._permission_lock:
            assert "req_1" in bus._permission_requests
            assert len(bus._permission_requests) == 1

        # Start waiting and submit response
        wait_task = asyncio.create_task(
            bus.wait_permission_response("req_1", timeout=1.0)
        )
        await asyncio.sleep(0.05)

        response = PermissionResponse(
            request_id="req_1",
            session_key="session_1",
            decision="allow",
        )
        await bus.submit_permission_response(response)

        await wait_task

        # Verify request is cleaned up
        async with bus._permission_lock:
            assert "req_1" not in bus._permission_requests
            assert len(bus._permission_requests) == 0

    @pytest.mark.asyncio
    async def test_permission_request_cleaned_on_timeout(self) -> None:
        """Bug 2: _permission_requests should be cleaned on timeout."""
        bus = MessageBus()

        req = PermissionRequest(
            request_id="req_timeout",
            session_key="session_timeout",
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="Timeout test",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        # Verify request is in the pool
        async with bus._permission_lock:
            assert "req_timeout" in bus._permission_requests

        # Wait with short timeout
        result = await bus.wait_permission_response("req_timeout", timeout=0.1)
        assert result.decision == "deny"

        # Verify request is cleaned up after timeout
        async with bus._permission_lock:
            assert "req_timeout" not in bus._permission_requests
            assert len(bus._permission_requests) == 0

    @pytest.mark.asyncio
    async def test_clear_permission_request_cleans_all_dicts(self) -> None:
        """Bug 2: clear_permission_request should clean _permission_requests."""
        bus = MessageBus()

        req = PermissionRequest(
            request_id="req_clear",
            session_key="session_clear",
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="Clear test",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        # Verify all dicts have the request
        async with bus._permission_lock:
            assert "req_clear" in bus._permission_requests
            assert "req_clear" in bus._pending_permission_responses
            assert bus._session_pending_requests.get("session_clear") == "req_clear"

        # Clear the request
        await bus.aclear_permission_request("req_clear")

        # Verify all dicts are cleaned
        async with bus._permission_lock:
            assert "req_clear" not in bus._permission_requests
            assert "req_clear" not in bus._pending_permission_responses
            assert "session_clear" not in bus._session_pending_requests


class TestInteractionRequestCleanup:
    """Test that interaction state is properly cleaned up."""

    @pytest.mark.asyncio
    async def test_interaction_request_cleaned_on_response(self) -> None:
        """Interaction request should be cleaned when response received."""
        bus = MessageBus()

        req = InteractionRequest(
            request_id="int_1",
            session_key="session_int",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Test question",
        )
        await bus.publish_interaction_request(req)
        await bus.consume_outbound()

        # Verify request is in the pool
        async with bus._interaction_lock:
            assert "int_1" in bus._interaction_requests

        # Submit response
        wait_task = asyncio.create_task(
            bus.wait_interaction_response("int_1", timeout=1.0)
        )
        await asyncio.sleep(0.05)

        response = InteractionResponse(
            request_id="int_1",
            session_key="session_int",
            action="reply",
            content="Test answer",
        )
        await bus.submit_interaction_response(response)

        await wait_task

        # Verify request is cleaned up
        async with bus._interaction_lock:
            assert "int_1" not in bus._interaction_requests

    @pytest.mark.asyncio
    async def test_interaction_request_cleaned_on_timeout(self) -> None:
        """Interaction request should be cleaned on timeout."""
        bus = MessageBus()

        req = InteractionRequest(
            request_id="int_timeout",
            session_key="session_int_timeout",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Timeout test",
        )
        await bus.publish_interaction_request(req)
        await bus.consume_outbound()

        # Verify request is in the pool
        async with bus._interaction_lock:
            assert "int_timeout" in bus._interaction_requests

        # Wait with short timeout
        result = await bus.wait_interaction_response("int_timeout", timeout=0.1)
        assert result.action == "cancel"

        # Verify request is cleaned up
        async with bus._interaction_lock:
            assert "int_timeout" not in bus._interaction_requests

    @pytest.mark.asyncio
    async def test_clear_interaction_request_cleans_all_dicts(self) -> None:
        """clear_interaction_request should clean all related dicts."""
        bus = MessageBus()

        req = InteractionRequest(
            request_id="int_clear",
            session_key="session_int_clear",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Clear test",
        )
        await bus.publish_interaction_request(req)
        await bus.consume_outbound()

        # Clear the request
        await bus.aclear_interaction_request("int_clear")

        # Verify all dicts are cleaned
        async with bus._interaction_lock:
            assert "int_clear" not in bus._interaction_requests
            assert "int_clear" not in bus._pending_interaction_responses
            assert "session_int_clear" not in bus._session_pending_interactions


class TestRequestPoolConsistency:
    """Test that request pools remain consistent."""

    @pytest.mark.asyncio
    async def test_multiple_requests_all_cleaned(self) -> None:
        """Multiple requests should all be cleaned properly."""
        bus = MessageBus(max_pending_requests=10)

        # Create multiple requests
        for i in range(5):
            req = PermissionRequest(
                request_id=f"multi_req_{i}",
                session_key=f"session_multi_{i}",
                channel="telegram",
                chat_id="chat_1",
                tool_name="test_tool",
                tool_input={},
                message=f"Request {i}",
            )
            await bus.publish_permission_request(req)
            await bus.consume_outbound()

        # Verify all are in the pool
        async with bus._permission_lock:
            assert len(bus._permission_requests) == 5

        # Respond to all
        for i in range(5):
            response = PermissionResponse(
                request_id=f"multi_req_{i}",
                session_key=f"session_multi_{i}",
                decision="allow",
            )
            await bus.submit_permission_response(response)
            # Also clean up the waiter
            await bus.wait_permission_response(f"multi_req_{i}", timeout=0.1)

        # Verify all are cleaned
        async with bus._permission_lock:
            assert len(bus._permission_requests) == 0

    @pytest.mark.asyncio
    async def test_pool_size_decreases_after_cleanup(self) -> None:
        """Pool size should decrease after request is completed."""
        bus = MessageBus(max_pending_requests=10)

        req = PermissionRequest(
            request_id="size_test",
            session_key="session_size",
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="Size test",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        async with bus._permission_lock:
            initial_size = len(bus._permission_requests)
            assert initial_size == 1

        # Timeout the request
        await bus.wait_permission_response("size_test", timeout=0.1)

        async with bus._permission_lock:
            final_size = len(bus._permission_requests)
            assert final_size == 0
