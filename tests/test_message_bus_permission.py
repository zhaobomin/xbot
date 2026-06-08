"""Tests for MessageBus permission request/response functionality."""

import asyncio

import pytest

from xbot.platform.bus.queue import (
    InteractionRequest,
    InteractionResponse,
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)
from xbot.platform.bus.events import InboundMessage


def test_im_channel_inbound_message_uses_im_session_namespace():
    msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="456", content="hello")

    assert msg.session_key == "im:telegram:456"


def test_im_channel_thread_override_uses_im_session_namespace():
    msg = InboundMessage(
        channel="slack",
        sender_id="u1",
        chat_id="C1",
        content="hello",
        session_key_override="slack:C1:thread:1",
    )

    assert msg.session_key == "im:slack:C1:thread:1"


class TestPermissionRequest:
    """Tests for PermissionRequest dataclass."""

    def test_create_request(self):
        req = PermissionRequest(
            request_id="req-123",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            tool_name="exec",
            tool_input={"command": "ls"},
            message="Need permission",
        )
        assert req.request_id == "req-123"
        assert req.session_key == "telegram:456"
        assert req.tool_name == "exec"
        assert req.suggestions == []

    def test_create_request_with_suggestions(self):
        req = PermissionRequest(
            request_id="req-123",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            tool_name="exec",
            tool_input={"command": "ls"},
            message="Need permission",
            suggestions=["允许", "拒绝"],
        )
        assert req.suggestions == ["允许", "拒绝"]


class TestPermissionResponse:
    """Tests for PermissionResponse dataclass."""

    def test_create_response_allow(self):
        resp = PermissionResponse(
            request_id="req-123",
            session_key="telegram:456",
            decision="allow",
        )
        assert resp.decision == "allow"
        assert resp.reason == ""
        assert resp.updated_input is None

    def test_create_response_deny(self):
        resp = PermissionResponse(
            request_id="req-123",
            session_key="telegram:456",
            decision="deny",
            reason="User denied",
        )
        assert resp.decision == "deny"
        assert resp.reason == "User denied"

    def test_create_response_with_updated_input(self):
        resp = PermissionResponse(
            request_id="req-123",
            session_key="telegram:456",
            decision="allow",
            updated_input={"command": "ls -la"},
        )
        assert resp.updated_input == {"command": "ls -la"}


class TestMessageBusPermission:
    """Tests for MessageBus permission request/response methods."""

    @pytest.fixture
    def bus(self):
        return MessageBus()

    def test_initial_state(self, bus):
        assert bus._pending_permission_responses == {}
        assert bus._permission_results == {}
        assert bus._session_pending_requests == {}

    @pytest.mark.asyncio
    async def test_publish_permission_request(self, bus):
        req = PermissionRequest(
            request_id="req-123",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            tool_name="exec",
            tool_input={"command": "ls"},
            message="Need permission",
            metadata={"message_thread_id": 7},
        )

        await bus.publish_permission_request(req)

        # Check session tracking
        assert bus.get_pending_request_for_session("telegram:456") == "req-123"

        # Check outbound message was published
        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert msg.channel == "telegram"
        assert msg.chat_id == "456"
        assert "Need permission" in msg.content
        assert msg.metadata.get("permission_request") is True
        assert msg.metadata.get("permission_request_id") == "req-123"
        assert msg.metadata.get("message_thread_id") == 7

    @pytest.mark.asyncio
    async def test_wait_permission_response(self, bus):
        # Set up waiting
        request_id = "req-123"

        async def wait_for_response():
            return await bus.wait_permission_response(request_id, timeout=0.5)

        # Start waiting
        task = asyncio.create_task(wait_for_response())

        # Wait a bit for the wait to be registered
        await asyncio.sleep(0.1)

        # Check that we're waiting
        assert bus.has_pending_permission_request(request_id)

        # Submit response
        resp = PermissionResponse(
            request_id=request_id,
            session_key="telegram:456",
            decision="allow",
        )
        result = await bus.submit_permission_response(resp)
        assert result is True

        # Get the wait result
        response = await task
        assert response.decision == "allow"

    @pytest.mark.asyncio
    async def test_wait_permission_response_timeout(self, bus):
        response = await bus.wait_permission_response("req-123", timeout=0.2)
        assert response.decision == "deny"
        assert "Timeout" in response.reason

    @pytest.mark.asyncio
    async def test_wait_permission_response_cleans_state_on_success(self, bus):
        req = PermissionRequest(
            request_id="req-clean",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            tool_name="exec",
            tool_input={"command": "ls"},
            message="Need permission",
        )
        await bus.publish_permission_request(req)
        _ = await bus.consume_outbound()

        waiter = asyncio.create_task(bus.wait_permission_response("req-clean", timeout=1.0))
        await asyncio.sleep(0.05)
        ok = await bus.submit_permission_response(
            PermissionResponse(
                request_id="req-clean",
                session_key="telegram:456",
                decision="allow",
            )
        )
        assert ok is True
        response = await waiter
        assert response.decision == "allow"
        assert "req-clean" not in bus._pending_permission_responses
        assert bus.get_pending_request_for_session("telegram:456") is None

    @pytest.mark.asyncio
    async def test_wait_permission_response_timeout_cleans_session_mapping(self, bus):
        bus._session_pending_requests["telegram:456"] = "req-timeout"
        response = await bus.wait_permission_response("req-timeout", timeout=0.05)
        assert response.decision == "deny"
        assert bus.get_pending_request_for_session("telegram:456") is None
        assert "req-timeout" not in bus._pending_permission_responses

    @pytest.mark.asyncio
    async def test_wait_permission_response_timeout_cleans_stale_result(self, bus):
        req = PermissionRequest(
            request_id="req-timeout-clean",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            tool_name="exec",
            tool_input={"command": "ls"},
            message="Need permission",
        )
        await bus.publish_permission_request(req)
        _ = await bus.consume_outbound()
        bus._permission_results["req-timeout-clean"] = PermissionResponse(
            request_id="req-timeout-clean",
            session_key="telegram:456",
            decision="allow",
        )

        response = await bus.wait_permission_response("req-timeout-clean", timeout=0.01)
        assert response.decision == "deny"
        assert "req-timeout-clean" not in bus._permission_results

    @pytest.mark.asyncio
    async def test_submit_permission_response_no_waiter(self, bus):
        resp = PermissionResponse(
            request_id="nonexistent",
            session_key="telegram:456",
            decision="allow",
        )
        result = await bus.submit_permission_response(resp)
        assert result is False

    @pytest.mark.asyncio
    async def test_permission_response_before_waiter_registration_is_not_lost(self, bus):
        req = PermissionRequest(
            request_id="req-race-1",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            tool_name="exec",
            tool_input={"command": "ls"},
            message="Need permission",
        )
        await bus.publish_permission_request(req)
        _ = await bus.consume_outbound()

        # Submit response before wait_permission_response is called.
        ok = await bus.submit_permission_response(
            PermissionResponse(
                request_id="req-race-1",
                session_key="telegram:456",
                decision="allow",
            )
        )
        assert ok is True

        response = await bus.wait_permission_response("req-race-1", timeout=0.2)
        assert response.decision == "allow"

    @pytest.mark.asyncio
    async def test_multiple_requests_same_session(self, bus):
        # First request
        req1 = PermissionRequest(
            request_id="req-1",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            tool_name="exec",
            tool_input={"command": "ls"},
            message="Request 1",
        )
        await bus.publish_permission_request(req1)
        assert bus.get_pending_request_for_session("telegram:456") == "req-1"

        # Respond to first
        resp1 = PermissionResponse(
            request_id="req-1",
            session_key="telegram:456",
            decision="allow",
        )

        # Wait for req-1
        task1 = asyncio.create_task(bus.wait_permission_response("req-1", timeout=1.0))
        await asyncio.sleep(0.1)
        await bus.submit_permission_response(resp1)
        result1 = await task1
        assert result1.decision == "allow"

        # Session tracking should be cleared
        assert bus.get_pending_request_for_session("telegram:456") is None

        # Second request
        req2 = PermissionRequest(
            request_id="req-2",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            tool_name="write_file",
            tool_input={"path": "/tmp/test"},
            message="Request 2",
        )
        await bus.publish_permission_request(req2)
        assert bus.get_pending_request_for_session("telegram:456") == "req-2"

    @pytest.mark.asyncio
    async def test_publish_permission_request_supersedes_previous_session_request(self, bus):
        req1 = PermissionRequest(
            request_id="req-old",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            tool_name="exec",
            tool_input={"command": "ls"},
            message="Request old",
        )
        await bus.publish_permission_request(req1)
        _ = await bus.consume_outbound()

        waiter = asyncio.create_task(bus.wait_permission_response("req-old", timeout=1.0))
        await asyncio.sleep(0.05)

        req2 = PermissionRequest(
            request_id="req-new",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            tool_name="write_file",
            tool_input={"path": "/tmp/a"},
            message="Request new",
        )
        await bus.publish_permission_request(req2)
        _ = await bus.consume_outbound()

        old_resp = await waiter
        assert old_resp.decision == "deny"
        assert "Superseded" in old_resp.reason
        assert bus.get_pending_request_for_session("telegram:456") == "req-new"

    def test_clear_permission_request(self, bus):
        bus._pending_permission_responses["req-123"] = asyncio.Event()
        bus._permission_results["req-123"] = PermissionResponse(
            request_id="req-123",
            session_key="test:123",
            decision="allow",
        )
        bus._session_pending_requests["test:123"] = "req-123"

        bus.clear_permission_request("req-123")

        assert "req-123" not in bus._pending_permission_responses
        assert "req-123" not in bus._permission_results
        assert "test:123" not in bus._session_pending_requests

    def test_get_pending_request_for_session(self, bus):
        bus._session_pending_requests["telegram:456"] = "req-123"
        assert bus.get_pending_request_for_session("telegram:456") == "req-123"
        assert bus.get_pending_request_for_session("telegram:789") is None

    @pytest.mark.asyncio
    async def test_async_pending_permission_accessors_use_lock(self, bus):
        async with bus._permission_lock:
            read_task = asyncio.create_task(bus.aget_pending_request_for_session("telegram:456"))
            has_task = asyncio.create_task(bus.ahas_pending_permission_request("req-123"))
            await asyncio.sleep(0)
            assert not read_task.done()
            assert not has_task.done()
            bus._session_pending_requests["telegram:456"] = "req-123"
            bus._pending_permission_responses["req-123"] = asyncio.Event()

        assert await read_task == "req-123"
        assert await has_task is True

    def test_has_pending_permission_request(self, bus):
        assert bus.has_pending_permission_request("req-123") is False
        bus._pending_permission_responses["req-123"] = asyncio.Event()
        assert bus.has_pending_permission_request("req-123") is True

    @pytest.mark.asyncio
    async def test_interaction_request_roundtrip(self, bus):
        req = InteractionRequest(
            request_id="ir-1",
            session_key="slack:C1:thread:1",
            channel="slack",
            chat_id="C1",
            kind="question",
            prompt="Please confirm",
            suggestions=["继续", "取消"],
            metadata={"slack": {"thread_ts": "1"}},
        )
        await bus.publish_interaction_request(req)

        outbound = await bus.consume_outbound()
        assert outbound.metadata.get("interaction_request") is True
        assert outbound.metadata.get("interaction_request_id") == "ir-1"
        assert outbound.metadata.get("interaction_kind") == "question"
        assert outbound.metadata.get("slack", {}).get("thread_ts") == "1"
        assert bus.get_pending_interaction_for_session("slack:C1:thread:1") == "ir-1"

        waiter = asyncio.create_task(bus.wait_interaction_response("ir-1", timeout=1.0))
        await asyncio.sleep(0.05)
        ok = await bus.submit_interaction_response(
            InteractionResponse(
                request_id="ir-1",
                session_key="slack:C1:thread:1",
                action="reply",
                content="继续",
            )
        )
        assert ok is True

        resp = await waiter
        assert resp.content == "继续"

    @pytest.mark.asyncio
    async def test_interaction_response_before_waiter_registration_is_not_lost(self, bus):
        req = InteractionRequest(
            request_id="ir-race-1",
            session_key="slack:C1:thread:1",
            channel="slack",
            chat_id="C1",
            kind="question",
            prompt="继续吗？",
        )
        await bus.publish_interaction_request(req)
        _ = await bus.consume_outbound()

        ok = await bus.submit_interaction_response(
            InteractionResponse(
                request_id="ir-race-1",
                session_key="slack:C1:thread:1",
                action="reply",
                content="继续",
            )
        )
        assert ok is True

        response = await bus.wait_interaction_response("ir-race-1", timeout=0.2)
        assert response.action == "reply"
        assert response.content == "继续"
        assert bus.get_pending_interaction_for_session("slack:C1:thread:1") is None

    @pytest.mark.asyncio
    async def test_publish_interaction_request_supersedes_previous_session_request(self, bus):
        req1 = InteractionRequest(
            request_id="ir-old",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            kind="question",
            prompt="old",
        )
        await bus.publish_interaction_request(req1)
        _ = await bus.consume_outbound()

        waiter = asyncio.create_task(bus.wait_interaction_response("ir-old", timeout=1.0))
        await asyncio.sleep(0.05)

        req2 = InteractionRequest(
            request_id="ir-new",
            session_key="telegram:456",
            channel="telegram",
            chat_id="456",
            kind="question",
            prompt="new",
        )
        await bus.publish_interaction_request(req2)
        _ = await bus.consume_outbound()

        old_resp = await waiter
        assert old_resp.action == "cancel"
        assert "Superseded" in old_resp.content
        assert bus.get_pending_interaction_for_session("telegram:456") == "ir-new"
