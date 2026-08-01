"""Deep tests for RuntimeResponseHandlers and other response modules."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.interaction.response_handlers import RuntimeResponseHandlers
from xbot.platform.bus.events import InboundMessage, OutboundMessage
from xbot.platform.bus.queue import InteractionResponse, PermissionResponse
from xbot.runtime.state import SessionEvent, SessionPhase


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_runtime(
    *,
    bus: Any = None,
    coordinator: Any = None,
    phase: SessionPhase = SessionPhase.IDLE,
    interaction_retry_counts: dict | None = None,
) -> MagicMock:
    """Build a mock AgentService for RuntimeResponseHandlers."""
    rt = MagicMock()
    rt._shared_resources = {
        "bus": bus or MagicMock(),
        "runtime_registry": coordinator or MagicMock(),
    }
    # State coordinator returns given phase
    coord = rt._shared_resources["runtime_registry"]
    coord.get_phase = MagicMock(return_value=phase)
    coord.dispatch = MagicMock()
    if interaction_retry_counts is not None:
        rt._interaction_retry_counts = interaction_retry_counts
    rt._is_local_runtime_command = MagicMock(return_value=False)
    return rt


def _make_inbound(
    content: str = "yes",
    channel: str = "telegram",
    chat_id: str = "chat1",
    session_key: str = "im:telegram:chat1",
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id="user1",
        chat_id=chat_id,
        content=content,
    )


# ── RuntimeResponseHandlers ──────────────────────────────────────────────


class TestRuntimeResponseHandlersInit:
    def test_init(self) -> None:
        rt = _make_runtime()
        handler = RuntimeResponseHandlers(rt)
        assert handler._runtime is rt

    def test_bus_from_shared_resources(self) -> None:
        bus = MagicMock()
        rt = _make_runtime(bus=bus)
        handler = RuntimeResponseHandlers(rt)
        assert handler._bus is bus

    def test_bus_from_runtime_attr(self) -> None:
        bus = MagicMock()
        rt = MagicMock()
        rt._shared_resources = {}
        rt.bus = bus
        handler = RuntimeResponseHandlers(rt)
        assert handler._bus is bus

    def test_bus_none_when_missing(self) -> None:
        rt = MagicMock(spec=[])  # spec=[] prevents auto-attribute creation
        handler = RuntimeResponseHandlers(rt)
        assert handler._bus is None

    def test_state_coordinator_missing_raises(self) -> None:
        rt = MagicMock(spec=[])
        handler = RuntimeResponseHandlers(rt)
        with pytest.raises(RuntimeError, match="runtime_registry"):
            _ = handler._state_coordinator

    def test_interaction_retry_counts_delegates(self) -> None:
        counts = {"session1": 2}
        rt = _make_runtime(interaction_retry_counts=counts)
        handler = RuntimeResponseHandlers(rt)
        assert handler._interaction_retry_counts is counts

    def test_interaction_retry_counts_own(self) -> None:
        rt = _make_runtime()
        # Remove the delegated attr
        del rt._interaction_retry_counts
        handler = RuntimeResponseHandlers(rt)
        assert isinstance(handler._interaction_retry_counts, dict)


class TestRetryStateCleanup:
    def test_clear_session(self) -> None:
        counts = {"im:telegram:chat1": 3, "im:telegram:chat1:req1": 1, "other": 2}
        rt = _make_runtime(interaction_retry_counts=counts)
        handler = RuntimeResponseHandlers(rt)
        handler._clear_interaction_retry_state("im:telegram:chat1", request_id="req1", clear_session=True)
        assert "im:telegram:chat1" not in counts
        assert "im:telegram:chat1:req1" not in counts
        assert "other" in counts

    def test_clear_without_request_id(self) -> None:
        counts = {"session1": 2}
        rt = _make_runtime(interaction_retry_counts=counts)
        handler = RuntimeResponseHandlers(rt)
        handler._clear_interaction_retry_state("session1")
        assert "session1" not in counts

    def test_clear_nonexistent_key(self) -> None:
        rt = _make_runtime(interaction_retry_counts={})
        handler = RuntimeResponseHandlers(rt)
        handler._clear_interaction_retry_state("nonexistent")  # should not raise


class TestInteractionRetryKey:
    def test_format(self) -> None:
        rt = _make_runtime()
        handler = RuntimeResponseHandlers(rt)
        assert handler._interaction_retry_key("session1", "req1") == "session1:req1"


class TestMatchInteractionOption:
    def test_single_question_match(self) -> None:
        rt = _make_runtime()
        handler = RuntimeResponseHandlers(rt)
        metadata = {"valid_options": ["Apple", "Banana", "Cherry"]}
        result = handler._match_interaction_option("apple", metadata)
        assert result == "Apple"

    def test_single_question_no_match(self) -> None:
        rt = _make_runtime()
        handler = RuntimeResponseHandlers(rt)
        metadata = {"valid_options": ["Apple", "Banana"]}
        result = handler._match_interaction_option("grape", metadata)
        assert result is None

    def test_multi_question_match(self) -> None:
        rt = _make_runtime()
        handler = RuntimeResponseHandlers(rt)
        metadata = {
            "question_count": 2,
            "question_options_map": [["Apple", "Banana"], ["Red", "Blue"]],
        }
        result = handler._match_interaction_option("apple,red", metadata)
        assert result == "Apple, Red"

    def test_multi_question_wrong_count(self) -> None:
        rt = _make_runtime()
        handler = RuntimeResponseHandlers(rt)
        metadata = {
            "question_count": 2,
            "question_options_map": [["Apple"], ["Red"]],
        }
        result = handler._match_interaction_option("just_one", metadata)
        assert result is None

    def test_multi_question_partial_match_fails(self) -> None:
        rt = _make_runtime()
        handler = RuntimeResponseHandlers(rt)
        metadata = {
            "question_count": 2,
            "question_options_map": [["Apple", "Banana"], ["Red", "Blue"]],
        }
        result = handler._match_interaction_option("apple,grape", metadata)
        assert result is None

    def test_empty_options(self) -> None:
        rt = _make_runtime()
        handler = RuntimeResponseHandlers(rt)
        result = handler._match_interaction_option("anything", {})
        assert result is None


class TestHandlePermissionResponse:
    async def test_no_bus_returns_false(self) -> None:
        rt = MagicMock(spec=[])  # no bus, no shared_resources
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("yes")
        result = await handler.handle_permission_response(msg)
        assert result is False

    async def test_non_keyword_returns_false(self) -> None:
        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value=None)
        rt = _make_runtime(bus=bus)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("random text that is not a keyword")
        result = await handler.handle_permission_response(msg)
        assert result is False

    async def test_no_pending_request_with_keyword(self) -> None:
        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value=None)
        bus.publish_outbound = AsyncMock()
        rt = _make_runtime(bus=bus)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("yes")
        result = await handler.handle_permission_response(msg)
        assert result is True
        bus.publish_outbound.assert_called_once()
        outbound = bus.publish_outbound.call_args[0][0]
        assert "超时" in outbound.content or "过期" in outbound.content

    async def test_wrong_phase_ignored(self) -> None:
        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value="req-1")
        rt = _make_runtime(bus=bus, phase=SessionPhase.RUNNING)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("yes")
        result = await handler.handle_permission_response(msg)
        assert result is True  # consumed but ignored

    async def test_successful_approval(self) -> None:
        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value="req-1")
        bus.submit_permission_response = AsyncMock(return_value=True)
        rt = _make_runtime(bus=bus, phase=SessionPhase.WAITING_PERMISSION)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("yes")
        result = await handler.handle_permission_response(msg)
        assert result is True
        bus.submit_permission_response.assert_called_once()
        response = bus.submit_permission_response.call_args[0][0]
        assert isinstance(response, PermissionResponse)
        assert response.decision == "allow"

    async def test_successful_denial(self) -> None:
        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value="req-2")
        bus.submit_permission_response = AsyncMock(return_value=True)
        rt = _make_runtime(bus=bus, phase=SessionPhase.WAITING_PERMISSION)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("no")
        result = await handler.handle_permission_response(msg)
        assert result is True
        response = bus.submit_permission_response.call_args[0][0]
        assert response.decision == "deny"

    async def test_expired_response(self) -> None:
        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value="req-3")
        bus.submit_permission_response = AsyncMock(return_value=False)
        bus.publish_outbound = AsyncMock()
        rt = _make_runtime(bus=bus, phase=SessionPhase.WAITING_PERMISSION)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("yes")
        result = await handler.handle_permission_response(msg)
        assert result is True
        bus.publish_outbound.assert_called_once()
        rt._shared_resources["runtime_registry"].dispatch.assert_called()

    async def test_idle_phase_transitions(self) -> None:
        """IDLE phase should dispatch PERMISSION_PENDING before processing."""
        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value="req-4")
        bus.submit_permission_response = AsyncMock(return_value=True)
        rt = _make_runtime(bus=bus, phase=SessionPhase.IDLE)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("allow")
        result = await handler.handle_permission_response(msg)
        assert result is True
        coord = rt._shared_resources["runtime_registry"]
        coord.dispatch.assert_any_call(
            msg.session_key,
            SessionEvent.PERMISSION_PENDING,
            reason="pending_permission_detected",
        )


class TestHandleInteractionResponse:
    async def test_no_bus_returns_false(self) -> None:
        rt = MagicMock()
        rt._shared_resources = {}
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("hello")
        result = await handler.handle_interaction_response(msg)
        assert result is False

    async def test_local_command_returns_false(self) -> None:
        rt = _make_runtime()
        rt._is_local_runtime_command = MagicMock(return_value=True)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("/help")
        result = await handler.handle_interaction_response(msg)
        assert result is False

    async def test_no_pending_no_keyword(self) -> None:
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value=None)
        rt = _make_runtime(bus=bus)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("just some text")
        result = await handler.handle_interaction_response(msg)
        assert result is False

    async def test_no_pending_with_keyword_notifies(self) -> None:
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value=None)
        bus.publish_outbound = AsyncMock()
        rt = _make_runtime(bus=bus)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("yes")
        result = await handler.handle_interaction_response(msg)
        assert result is True
        bus.publish_outbound.assert_called_once()

    async def test_wrong_phase_notifies_user(self) -> None:
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value="req-1")
        bus.publish_outbound = AsyncMock()
        rt = _make_runtime(bus=bus, phase=SessionPhase.WAITING_PERMISSION)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("my answer")
        result = await handler.handle_interaction_response(msg)
        assert result is True
        outbound = bus.publish_outbound.call_args[0][0]
        assert "权限" in outbound.content

    async def test_expired_request(self) -> None:
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value="req-1")
        bus.get_interaction_request = MagicMock(return_value=None)
        bus.publish_outbound = AsyncMock()
        rt = _make_runtime(bus=bus, phase=SessionPhase.WAITING_INTERACTION)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("answer")
        result = await handler.handle_interaction_response(msg)
        assert result is True
        bus.publish_outbound.assert_called_once()

    async def test_strict_validation_rejects_invalid(self) -> None:
        req = MagicMock()
        req.kind = "question"
        req.metadata = {
            "valid_options": ["Apple", "Banana"],
            "validation_mode": "strict",
        }
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value="req-1")
        bus.get_interaction_request = MagicMock(return_value=req)
        bus.publish_outbound = AsyncMock()
        rt = _make_runtime(bus=bus, phase=SessionPhase.WAITING_INTERACTION)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("grape")
        result = await handler.handle_interaction_response(msg)
        assert result is True
        # Should have sent a retry warning
        bus.publish_outbound.assert_called_once()

    async def test_strict_validation_max_retries(self) -> None:
        req = MagicMock()
        req.kind = "question"
        req.metadata = {
            "valid_options": ["Apple", "Banana"],
            "validation_mode": "strict",
        }
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value="req-1")
        bus.get_interaction_request = MagicMock(return_value=req)
        bus.publish_outbound = AsyncMock()
        bus.aclear_interaction_request = AsyncMock()
        retry_counts = {}
        rt = _make_runtime(bus=bus, phase=SessionPhase.WAITING_INTERACTION, interaction_retry_counts=retry_counts)
        handler = RuntimeResponseHandlers(rt)

        # Simulate 3rd retry
        retry_counts["im:telegram:chat1:req-1"] = 2

        msg = _make_inbound("grape")
        result = await handler.handle_interaction_response(msg)
        assert result is True
        # Should have expired the interaction
        bus.aclear_interaction_request.assert_called_once_with("req-1")

    async def test_suggested_mode_allows_custom_input(self) -> None:
        req = MagicMock()
        req.kind = "question"
        req.metadata = {
            "valid_options": ["Apple", "Banana"],
            "validation_mode": "suggested",
        }
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value="req-1")
        bus.get_interaction_request = MagicMock(return_value=req)
        bus.submit_interaction_response = AsyncMock(return_value=True)
        rt = _make_runtime(bus=bus, phase=SessionPhase.WAITING_INTERACTION)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("my custom answer")
        result = await handler.handle_interaction_response(msg)
        assert result is True
        bus.submit_interaction_response.assert_called_once()
        response = bus.submit_interaction_response.call_args[0][0]
        assert response.content == "my custom answer"

    async def test_successful_interaction(self) -> None:
        req = MagicMock()
        req.kind = "approval"
        req.metadata = {}
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value="req-1")
        bus.get_interaction_request = MagicMock(return_value=req)
        bus.submit_interaction_response = AsyncMock(return_value=True)
        rt = _make_runtime(bus=bus, phase=SessionPhase.WAITING_INTERACTION)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("yes")
        result = await handler.handle_interaction_response(msg)
        assert result is True
        bus.submit_interaction_response.assert_called_once()

    async def test_interaction_response_expired(self) -> None:
        req = MagicMock()
        req.kind = "approval"
        req.metadata = {}
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value="req-1")
        bus.get_interaction_request = MagicMock(return_value=req)
        bus.submit_interaction_response = AsyncMock(return_value=False)
        bus.publish_outbound = AsyncMock()
        rt = _make_runtime(bus=bus, phase=SessionPhase.WAITING_INTERACTION)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("yes")
        result = await handler.handle_interaction_response(msg)
        assert result is True
        bus.publish_outbound.assert_called_once()

    async def test_idle_phase_dispatches_pending(self) -> None:
        req = MagicMock()
        req.kind = "question"
        req.metadata = {}
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value="req-1")
        bus.get_interaction_request = MagicMock(return_value=req)
        bus.submit_interaction_response = AsyncMock(return_value=True)
        rt = _make_runtime(bus=bus, phase=SessionPhase.IDLE)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("answer")
        result = await handler.handle_interaction_response(msg)
        assert result is True
        coord = rt._shared_resources["runtime_registry"]
        coord.dispatch.assert_any_call(
            msg.session_key,
            SessionEvent.INTERACTION_PENDING,
            reason="pending_interaction_detected",
        )


# ── Edge case: stopping phase message ────────────────────────────────────


class TestInteractionPhaseMessages:
    """Test that different phases produce appropriate user-facing messages."""

    @pytest.mark.parametrize("phase,expected_keyword", [
        (SessionPhase.STOPPING, "停止"),
        (SessionPhase.RELEASING_CLIENT, "释放"),
        (SessionPhase.BROKEN, "恢复"),
        (SessionPhase.ACQUIRING_CLIENT, "初始化"),
    ])
    async def test_phase_specific_messages(self, phase: SessionPhase, expected_keyword: str) -> None:
        bus = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value="req-1")
        bus.publish_outbound = AsyncMock()
        rt = _make_runtime(bus=bus, phase=phase)
        handler = RuntimeResponseHandlers(rt)
        msg = _make_inbound("my answer")
        result = await handler.handle_interaction_response(msg)
        assert result is True
        outbound = bus.publish_outbound.call_args[0][0]
        assert expected_keyword in outbound.content
