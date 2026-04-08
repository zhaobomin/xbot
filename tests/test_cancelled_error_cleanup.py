"""Tests for CancelledError cleanup in wait functions.

Tests that CancelledError properly cleans up state in:
- wait_permission_response
- wait_interaction_response
- aclear_session_requests
"""

import asyncio

import pytest

from xbot.platform.bus.queue import (
    InteractionRequest,
    MessageBus,
    PermissionRequest,
)


class TestWaitPermissionResponseCancelledError:
    """Test that wait_permission_response cleans up on CancelledError."""

    @pytest.mark.asyncio
    async def test_wait_permission_response_cleans_on_cancel(self) -> None:
        """CancelledError should clean up all permission request state."""
        bus = MessageBus()

        req = PermissionRequest(
            request_id="cancel_test",
            session_key="session_cancel",
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="Cancel test",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        # Verify request is in the pool
        async with bus._permission_lock:
            assert "cancel_test" in bus._permission_requests
            assert "cancel_test" in bus._pending_permission_responses
            assert bus._session_pending_requests.get("session_cancel") == "cancel_test"

        # Create a task that waits, then cancel it
        wait_task = asyncio.create_task(
            bus.wait_permission_response("cancel_test", timeout=10.0)
        )
        await asyncio.sleep(0.05)
        wait_task.cancel()

        try:
            await wait_task
        except asyncio.CancelledError:
            pass

        # Verify all dicts are cleaned up
        async with bus._permission_lock:
            assert "cancel_test" not in bus._permission_requests
            assert "cancel_test" not in bus._pending_permission_responses
            assert "session_cancel" not in bus._session_pending_requests

    @pytest.mark.asyncio
    async def test_wait_permission_response_cleans_results_on_cancel(self) -> None:
        """CancelledError should also clean up _permission_results if present."""
        bus = MessageBus()

        req = PermissionRequest(
            request_id="cancel_result_test",
            session_key="session_cancel_result",
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="Cancel result test",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        # Add a result manually
        async with bus._permission_lock:
            bus._permission_results["cancel_result_test"] = None

        # Create a task that waits, then cancel it
        wait_task = asyncio.create_task(
            bus.wait_permission_response("cancel_result_test", timeout=10.0)
        )
        await asyncio.sleep(0.05)
        wait_task.cancel()

        try:
            await wait_task
        except asyncio.CancelledError:
            pass

        # Verify results dict is also cleaned
        async with bus._permission_lock:
            assert "cancel_result_test" not in bus._permission_results


class TestWaitInteractionResponseCancelledError:
    """Test that wait_interaction_response cleans up on CancelledError."""

    @pytest.mark.asyncio
    async def test_wait_interaction_response_cleans_on_cancel(self) -> None:
        """CancelledError should clean up all interaction request state."""
        bus = MessageBus()

        req = InteractionRequest(
            request_id="int_cancel_test",
            session_key="session_int_cancel",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Cancel test",
        )
        await bus.publish_interaction_request(req)
        await bus.consume_outbound()

        # Verify request is in the pool
        async with bus._interaction_lock:
            assert "int_cancel_test" in bus._interaction_requests
            assert "int_cancel_test" in bus._pending_interaction_responses
            assert bus._session_pending_interactions.get("session_int_cancel") == "int_cancel_test"

        # Create a task that waits, then cancel it
        wait_task = asyncio.create_task(
            bus.wait_interaction_response("int_cancel_test", timeout=10.0)
        )
        await asyncio.sleep(0.05)
        wait_task.cancel()

        try:
            await wait_task
        except asyncio.CancelledError:
            pass

        # Verify all dicts are cleaned up
        async with bus._interaction_lock:
            assert "int_cancel_test" not in bus._interaction_requests
            assert "int_cancel_test" not in bus._pending_interaction_responses
            assert "session_int_cancel" not in bus._session_pending_interactions

    @pytest.mark.asyncio
    async def test_wait_interaction_response_cleans_results_on_cancel(self) -> None:
        """CancelledError should also clean up _interaction_results if present."""
        bus = MessageBus()

        req = InteractionRequest(
            request_id="int_cancel_result_test",
            session_key="session_int_cancel_result",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Cancel result test",
        )
        await bus.publish_interaction_request(req)
        await bus.consume_outbound()

        # Add a result manually
        async with bus._interaction_lock:
            bus._interaction_results["int_cancel_result_test"] = None

        # Create a task that waits, then cancel it
        wait_task = asyncio.create_task(
            bus.wait_interaction_response("int_cancel_result_test", timeout=10.0)
        )
        await asyncio.sleep(0.05)
        wait_task.cancel()

        try:
            await wait_task
        except asyncio.CancelledError:
            pass

        # Verify results dict is also cleaned
        async with bus._interaction_lock:
            assert "int_cancel_result_test" not in bus._interaction_results


class TestAclearSessionRequestsPermissionCleanup:
    """Test that aclear_session_requests cleans up _permission_requests."""

    @pytest.mark.asyncio
    async def test_aclear_session_requests_cleans_permission_requests(self) -> None:
        """aclear_session_requests should clean _permission_requests dict."""
        bus = MessageBus()

        req = PermissionRequest(
            request_id="session_clear_test",
            session_key="session_to_clear",
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="Session clear test",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        # Verify request is in all relevant dicts
        async with bus._permission_lock:
            assert "session_clear_test" in bus._permission_requests
            assert "session_clear_test" in bus._pending_permission_responses
            assert bus._session_pending_requests.get("session_to_clear") == "session_clear_test"

        # Clear session requests
        result = await bus.aclear_session_requests("session_to_clear")

        assert result["permission"] is True

        # Verify ALL dicts are cleaned
        async with bus._permission_lock:
            assert "session_clear_test" not in bus._permission_requests
            assert "session_clear_test" not in bus._pending_permission_responses
            assert "session_to_clear" not in bus._session_pending_requests

    @pytest.mark.asyncio
    async def test_aclear_session_requests_cleans_both_types(self) -> None:
        """aclear_session_requests should clean both permission and interaction."""
        bus = MessageBus()

        # Create both types
        perm_req = PermissionRequest(
            request_id="both_perm",
            session_key="session_both",
            channel="telegram",
            chat_id="chat_1",
            tool_name="test_tool",
            tool_input={},
            message="Permission",
        )
        await bus.publish_permission_request(perm_req)
        await bus.consume_outbound()

        int_req = InteractionRequest(
            request_id="both_int",
            session_key="session_both",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Interaction",
        )
        await bus.publish_interaction_request(int_req)
        await bus.consume_outbound()

        # Clear session
        result = await bus.aclear_session_requests("session_both")

        assert result["permission"] is True
        assert result["interaction"] is True

        # Verify all dicts are cleaned
        async with bus._permission_lock:
            assert "both_perm" not in bus._permission_requests
            assert "both_perm" not in bus._pending_permission_responses

        async with bus._interaction_lock:
            assert "both_int" not in bus._interaction_requests
            assert "both_int" not in bus._pending_interaction_responses
