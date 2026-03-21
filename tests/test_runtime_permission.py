"""Tests for AgentRuntime permission response handling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.bus.events import InboundMessage
from xbot.bus.queue import MessageBus, PermissionResponse


class TestRuntimePermissionResponse:
    """Tests for _handle_permission_response in AgentRuntime."""

    @pytest.fixture
    def bus(self):
        return MessageBus()

    @pytest.fixture
    def mock_runtime(self, bus):
        """Create a mock runtime with just the parts needed for testing."""
        from xbot.agent.runtime import AgentRuntime
        from xbot.config.schema import Config

        config = Config()
        runtime = MagicMock(spec=AgentRuntime)
        runtime.bus = bus
        runtime._handle_permission_response = AgentRuntime._handle_permission_response.__get__(runtime, AgentRuntime)
        return runtime

    @pytest.mark.asyncio
    async def test_no_pending_request(self, mock_runtime, bus):
        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="允许",
        )
        # No pending request
        result = await mock_runtime._handle_permission_response(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_allow_response(self, mock_runtime, bus):
        # Set up pending request
        bus._session_pending_requests["telegram:456"] = "req-123"
        bus._pending_permission_responses["req-123"] = asyncio.Event()

        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="允许",
        )

        result = await mock_runtime._handle_permission_response(msg)
        assert result is True

        # Check response was submitted
        assert "req-123" in bus._permission_results
        assert bus._permission_results["req-123"].decision == "allow"

    @pytest.mark.asyncio
    async def test_deny_response(self, mock_runtime, bus):
        # Set up pending request
        bus._session_pending_requests["telegram:456"] = "req-123"
        bus._pending_permission_responses["req-123"] = asyncio.Event()

        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="拒绝",
        )

        result = await mock_runtime._handle_permission_response(msg)
        assert result is True

        # Check response was submitted
        assert "req-123" in bus._permission_results
        assert bus._permission_results["req-123"].decision == "deny"

    @pytest.mark.asyncio
    async def test_allow_response_english(self, mock_runtime, bus):
        # Set up pending request
        bus._session_pending_requests["telegram:456"] = "req-123"
        bus._pending_permission_responses["req-123"] = asyncio.Event()

        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="allow",
        )

        result = await mock_runtime._handle_permission_response(msg)
        assert result is True
        assert bus._permission_results["req-123"].decision == "allow"

    @pytest.mark.asyncio
    async def test_deny_response_english(self, mock_runtime, bus):
        # Set up pending request
        bus._session_pending_requests["telegram:456"] = "req-123"
        bus._pending_permission_responses["req-123"] = asyncio.Event()

        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="deny",
        )

        result = await mock_runtime._handle_permission_response(msg)
        assert result is True
        assert bus._permission_results["req-123"].decision == "deny"

    @pytest.mark.asyncio
    async def test_allow_variations(self, mock_runtime, bus):
        """Test various allow response variations."""
        allow_variations = ["允许", "allow", "yes", "y", "是", "ok", "同意", "确认"]

        for variation in allow_variations:
            # Reset
            bus._session_pending_requests.clear()
            bus._permission_results.clear()
            bus._pending_permission_responses.clear()

            # Set up
            request_id = f"req-{variation}"
            bus._session_pending_requests["telegram:456"] = request_id
            bus._pending_permission_responses[request_id] = asyncio.Event()

            msg = InboundMessage(
                channel="telegram",
                sender_id="user",
                chat_id="456",
                content=variation,
            )

            result = await mock_runtime._handle_permission_response(msg)
            assert result is True, f"Failed for variation: {variation}"
            assert bus._permission_results[request_id].decision == "allow", f"Failed for variation: {variation}"

    @pytest.mark.asyncio
    async def test_deny_variations(self, mock_runtime, bus):
        """Test various deny response variations."""
        deny_variations = ["拒绝", "deny", "no", "n", "否", "取消"]

        for variation in deny_variations:
            # Reset
            bus._session_pending_requests.clear()
            bus._permission_results.clear()
            bus._pending_permission_responses.clear()

            # Set up
            request_id = f"req-{variation}"
            bus._session_pending_requests["telegram:456"] = request_id
            bus._pending_permission_responses[request_id] = asyncio.Event()

            msg = InboundMessage(
                channel="telegram",
                sender_id="user",
                chat_id="456",
                content=variation,
            )

            result = await mock_runtime._handle_permission_response(msg)
            assert result is True, f"Failed for variation: {variation}"
            assert bus._permission_results[request_id].decision == "deny", f"Failed for variation: {variation}"

    @pytest.mark.asyncio
    async def test_unclear_response_not_handled(self, mock_runtime, bus):
        """Test that unclear responses are not handled as permission responses."""
        # Set up pending request
        bus._session_pending_requests["telegram:456"] = "req-123"
        bus._pending_permission_responses["req-123"] = asyncio.Event()

        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="what is the weather?",
        )

        result = await mock_runtime._handle_permission_response(msg)
        assert result is False  # Not a permission response

        # No response submitted
        assert "req-123" not in bus._permission_results

    @pytest.mark.asyncio
    async def test_no_bus(self, mock_runtime):
        """Test that None bus returns False."""
        mock_runtime.bus = None
        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="允许",
        )
        result = await mock_runtime._handle_permission_response(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_case_insensitive(self, mock_runtime, bus):
        """Test that responses are case-insensitive."""
        bus._session_pending_requests["telegram:456"] = "req-123"
        bus._pending_permission_responses["req-123"] = asyncio.Event()

        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="ALLOW",  # uppercase
        )

        result = await mock_runtime._handle_permission_response(msg)
        assert result is True
        assert bus._permission_results["req-123"].decision == "allow"

    @pytest.mark.asyncio
    async def test_whitespace_handling(self, mock_runtime, bus):
        """Test that leading/trailing whitespace is handled."""
        bus._session_pending_requests["telegram:456"] = "req-123"
        bus._pending_permission_responses["req-123"] = asyncio.Event()

        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="  允许  ",  # with whitespace
        )

        result = await mock_runtime._handle_permission_response(msg)
        assert result is True
        assert bus._permission_results["req-123"].decision == "allow"