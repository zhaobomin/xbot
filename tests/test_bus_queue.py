"""Tests for bus/queue.py - MessageBus and request data classes."""

import time
from unittest.mock import AsyncMock

import pytest

from xbot.bus.events import InboundMessage, OutboundMessage
from xbot.bus.queue import (
    REQUEST_TIMEOUT_SECONDS,
    InteractionRequest,
    InteractionResponse,
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)


class TestPermissionRequest:
    """Tests for PermissionRequest data class."""

    def test_create_permission_request(self):
        """Test creating a permission request."""
        req = PermissionRequest(
            request_id="req-1",
            session_key="telegram:123",
            channel="telegram",
            chat_id="123",
            tool_name="bash",
            tool_input={"command": "ls"},
            message="Allow bash command?",
        )
        assert req.request_id == "req-1"
        assert req.session_key == "telegram:123"
        assert req.tool_name == "bash"
        assert req.suggestions == []
        assert req.created_at > 0

    def test_permission_request_with_suggestions(self):
        """Test permission request with suggestions."""
        req = PermissionRequest(
            request_id="req-2",
            session_key="feishu:456",
            channel="feishu",
            chat_id="456",
            tool_name="web_fetch",
            tool_input={"url": "https://example.com"},
            message="Fetch URL?",
            suggestions=["yes", "no", "always"],
        )
        assert req.suggestions == ["yes", "no", "always"]

    def test_is_expired_false_for_new_request(self):
        """Test that new requests are not expired."""
        req = PermissionRequest(
            request_id="req-3",
            session_key="test:1",
            channel="test",
            chat_id="1",
            tool_name="test",
            tool_input={},
            message="Test?",
        )
        assert req.is_expired() is False

    def test_is_expired_true_for_old_request(self):
        """Test that old requests are expired."""
        req = PermissionRequest(
            request_id="req-4",
            session_key="test:1",
            channel="test",
            chat_id="1",
            tool_name="test",
            tool_input={},
            message="Test?",
        )
        # Manually set created_at to past
        req.created_at = time.time() - REQUEST_TIMEOUT_SECONDS - 1
        assert req.is_expired() is True

    def test_is_expired_with_custom_timeout(self):
        """Test expired check with custom timeout."""
        req = PermissionRequest(
            request_id="req-5",
            session_key="test:1",
            channel="test",
            chat_id="1",
            tool_name="test",
            tool_input={},
            message="Test?",
        )
        req.created_at = time.time() - 100
        assert req.is_expired(timeout=200) is False
        assert req.is_expired(timeout=50) is True


class TestInteractionRequest:
    """Tests for InteractionRequest data class."""

    def test_create_interaction_request(self):
        """Test creating an interaction request."""
        req = InteractionRequest(
            request_id="int-1",
            session_key="telegram:123",
            channel="telegram",
            chat_id="123",
            kind="question",
            prompt="What is your name?",
        )
        assert req.request_id == "int-1"
        assert req.kind == "question"
        assert req.prompt == "What is your name?"

    def test_interaction_request_kinds(self):
        """Test different interaction kinds."""
        for kind in ["question", "confirmation", "approval"]:
            req = InteractionRequest(
                request_id=f"int-{kind}",
                session_key="test:1",
                channel="test",
                chat_id="1",
                kind=kind,
                prompt="Test?",
            )
            assert req.kind == kind

    def test_interaction_request_expired(self):
        """Test interaction request expiration."""
        req = InteractionRequest(
            request_id="int-exp",
            session_key="test:1",
            channel="test",
            chat_id="1",
            kind="question",
            prompt="Test?",
        )
        req.created_at = time.time() - REQUEST_TIMEOUT_SECONDS - 1
        assert req.is_expired() is True


class TestMessageBusBasics:
    """Tests for basic MessageBus functionality."""

    def test_create_message_bus(self):
        """Test creating a MessageBus."""
        bus = MessageBus()
        assert bus.inbound_size == 0
        assert bus.outbound_size == 0

    def test_create_message_bus_with_custom_size(self):
        """Test creating MessageBus with custom max size."""
        bus = MessageBus(max_queue_size=100, max_pending_requests=50)
        assert bus._max_pending_requests == 50

    @pytest.mark.asyncio
    async def test_publish_consume_inbound(self):
        """Test publishing and consuming inbound messages."""
        bus = MessageBus()
        msg = InboundMessage(
            channel="telegram",
            chat_id="123",
            content="Hello",
            sender_id="user1",
        )
        await bus.publish_inbound(msg)
        assert bus.inbound_size == 1

        consumed = await bus.consume_inbound()
        assert consumed.content == "Hello"
        assert bus.inbound_size == 0

    @pytest.mark.asyncio
    async def test_publish_consume_outbound(self):
        """Test publishing and consuming outbound messages."""
        bus = MessageBus()
        msg = OutboundMessage(
            channel="telegram",
            chat_id="123",
            content="Response",
        )
        await bus.publish_outbound(msg)
        assert bus.outbound_size == 1

        consumed = await bus.consume_outbound()
        assert consumed.content == "Response"


class TestPermissionFlow:
    """Tests for permission request/response flow."""

    @pytest.mark.asyncio
    async def test_permission_request_response_flow(self):
        """Test complete permission request and response flow."""
        bus = MessageBus()
        req = PermissionRequest(
            request_id="perm-1",
            session_key="telegram:123",
            channel="telegram",
            chat_id="123",
            tool_name="bash",
            tool_input={"command": "ls"},
            message="Allow bash?",
        )

        # Publish permission request
        await bus.publish_permission_request(req)

        # Check pending request
        assert bus.get_pending_request_for_session("telegram:123") == "perm-1"
        assert bus.has_pending_permission_request("perm-1")

        # Submit response
        resp = PermissionResponse(
            request_id="perm-1",
            session_key="telegram:123",
            decision="allow",
        )
        result = await bus.submit_permission_response(resp)
        assert result is True

        # Wait for response
        result = await bus.wait_permission_response("perm-1", timeout=1.0)
        assert result.decision == "allow"

    @pytest.mark.asyncio
    async def test_permission_request_timeout(self):
        """Test permission request timeout."""
        bus = MessageBus()

        # Wait for non-existent request should timeout
        result = await bus.wait_permission_response("nonexistent", timeout=0.1)
        assert result.decision == "deny"
        assert "Timeout" in result.reason

    @pytest.mark.asyncio
    async def test_permission_superseded(self):
        """Test that new request supersedes old one for same session."""
        bus = MessageBus()

        req1 = PermissionRequest(
            request_id="perm-old",
            session_key="telegram:123",
            channel="telegram",
            chat_id="123",
            tool_name="bash",
            tool_input={},
            message="Old request?",
        )
        req2 = PermissionRequest(
            request_id="perm-new",
            session_key="telegram:123",
            channel="telegram",
            chat_id="123",
            tool_name="bash",
            tool_input={},
            message="New request?",
        )

        await bus.publish_permission_request(req1)
        assert bus.get_pending_request_for_session("telegram:123") == "perm-old"

        # Publish new request for same session
        await bus.publish_permission_request(req2)
        assert bus.get_pending_request_for_session("telegram:123") == "perm-new"

        # Old request should be denied
        result = await bus.wait_permission_response("perm-old", timeout=0.1)
        assert result.decision == "deny"
        assert "Superseded" in result.reason

    @pytest.mark.asyncio
    async def test_submit_response_no_waiting_request(self):
        """Test submitting response when no request is waiting."""
        bus = MessageBus()
        resp = PermissionResponse(
            request_id="nonexistent",
            session_key="test:1",
            decision="allow",
        )
        result = await bus.submit_permission_response(resp)
        assert result is False


class TestInteractionFlow:
    """Tests for interaction request/response flow."""

    @pytest.mark.asyncio
    async def test_interaction_request_response_flow(self):
        """Test complete interaction request and response flow."""
        bus = MessageBus()
        req = InteractionRequest(
            request_id="int-1",
            session_key="telegram:123",
            channel="telegram",
            chat_id="123",
            kind="question",
            prompt="What is your favorite color?",
            suggestions=["red", "blue", "green"],
        )

        await bus.publish_interaction_request(req)
        assert bus.get_pending_interaction_for_session("telegram:123") == "int-1"

        # Submit response
        resp = InteractionResponse(
            request_id="int-1",
            session_key="telegram:123",
            action="reply",
            content="blue",
        )
        result = await bus.submit_interaction_response(resp)
        assert result is True

        # Wait for response
        result = await bus.wait_interaction_response("int-1", timeout=1.0)
        assert result.action == "reply"
        assert result.content == "blue"

    @pytest.mark.asyncio
    async def test_interaction_timeout(self):
        """Test interaction request timeout."""
        bus = MessageBus()

        result = await bus.wait_interaction_response("nonexistent", timeout=0.1)
        assert result.action == "cancel"
        assert "Timeout" in result.content

    @pytest.mark.asyncio
    async def test_get_interaction_request(self):
        """Test getting interaction request details."""
        bus = MessageBus()
        req = InteractionRequest(
            request_id="int-detail",
            session_key="test:1",
            channel="test",
            chat_id="1",
            kind="confirmation",
            prompt="Are you sure?",
        )

        await bus.publish_interaction_request(req)
        stored = bus.get_interaction_request("int-detail")
        assert stored is not None
        assert stored.kind == "confirmation"
        assert stored.prompt == "Are you sure?"


class TestSessionCleanup:
    """Tests for session cleanup operations."""

    def test_clear_session_requests_delegates_to_async_variant(self, monkeypatch):
        bus = MessageBus()
        delegated = AsyncMock(return_value={"permission": True, "interaction": False})
        monkeypatch.setattr(bus, "aclear_session_requests", delegated)

        result = bus.clear_session_requests("test:clear")

        delegated.assert_awaited_once_with("test:clear")
        assert result == {"permission": True, "interaction": False}

    @pytest.mark.asyncio
    async def test_aclear_session_requests(self):
        """Test async clearing of session requests."""
        bus = MessageBus()

        # Create both types of requests
        perm_req = PermissionRequest(
            request_id="perm-clear",
            session_key="test:clear",
            channel="test",
            chat_id="1",
            tool_name="test",
            tool_input={},
            message="Test?",
        )
        int_req = InteractionRequest(
            request_id="int-clear",
            session_key="test:clear",
            channel="test",
            chat_id="1",
            kind="question",
            prompt="Test?",
        )

        await bus.publish_permission_request(perm_req)
        await bus.publish_interaction_request(int_req)

        # Clear session
        result = await bus.aclear_session_requests("test:clear")
        assert result["permission"] is True
        assert result["interaction"] is True

        # Verify cleared
        assert bus.get_pending_request_for_session("test:clear") is None
        assert bus.get_pending_interaction_for_session("test:clear") is None

    @pytest.mark.asyncio
    async def test_aclear_session_requests_nonexistent(self):
        """Test clearing session with no pending requests."""
        bus = MessageBus()
        result = await bus.aclear_session_requests("nonexistent")
        assert result["permission"] is False
        assert result["interaction"] is False


class TestExpiredCleanup:
    """Tests for expired request cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_expired_permission_requests(self):
        """Test cleanup of expired permission requests."""
        bus = MessageBus()

        # Create an already-expired request
        req = PermissionRequest(
            request_id="perm-expired",
            session_key="test:expired",
            channel="test",
            chat_id="1",
            tool_name="test",
            tool_input={},
            message="Test?",
        )
        req.created_at = time.time() - REQUEST_TIMEOUT_SECONDS - 1

        await bus.publish_permission_request(req)

        # Trigger cleanup by adding another request
        new_req = PermissionRequest(
            request_id="perm-new",
            session_key="test:new",
            channel="test",
            chat_id="1",
            tool_name="test",
            tool_input={},
            message="Test?",
        )
        # Set max pending to 1 to trigger cleanup
        bus._max_pending_requests = 1
        await bus.publish_permission_request(new_req)

        # Old request should be cleaned up
        assert bus.has_pending_permission_request("perm-expired") is False
