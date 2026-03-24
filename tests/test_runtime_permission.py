"""Tests for AgentRuntime permission response handling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.bus.events import InboundMessage
from xbot.bus.queue import InteractionRequest, MessageBus, PermissionResponse


class TestRuntimePermissionResponse:
    """Tests for _handle_permission_response in AgentRuntime."""

    @pytest.fixture
    def bus(self):
        return MessageBus()

    @pytest.fixture
    def mock_runtime(self, bus):
        """Create a mock runtime with just the parts needed for testing."""
        from xbot.agent.runtime import AgentRuntime, SessionStateMachine
        from xbot.agent.state_coordinator import SessionStateCoordinator
        from xbot.agent.state_checker import StateConsistencyChecker
        from xbot.config.schema import Config

        config = Config()
        runtime = MagicMock(spec=AgentRuntime)
        runtime.bus = bus

        # Add state components needed for coordinator transactions
        runtime._state_machine = SessionStateMachine()
        runtime._active_tasks = {}
        runtime._session_locks = {}
        runtime._state_checker = StateConsistencyChecker(runtime)
        runtime._state_coordinator = SessionStateCoordinator(runtime)

        runtime._handle_permission_response = AgentRuntime._handle_permission_response.__get__(runtime, AgentRuntime)
        runtime._handle_interaction_response = AgentRuntime._handle_interaction_response.__get__(runtime, AgentRuntime)
        return runtime

    @pytest.mark.asyncio
    async def test_no_pending_request(self, mock_runtime, bus):
        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="允许",
        )
        # No pending request - should consume message and inform user
        result = await mock_runtime._handle_permission_response(msg)
        # Returns True to consume the message and inform user about stale request
        assert result is True
        # Check that user was notified
        notification = await bus.consume_outbound()
        assert "没有待处理的权限请求" in notification.content

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

    @pytest.mark.asyncio
    async def test_stale_pending_request_falls_back_to_normal_message(self, mock_runtime, bus):
        """If request mapping exists but no waiter is alive, runtime should inform user."""
        bus._session_pending_requests["telegram:456"] = "req-stale"
        # Intentionally do not create _pending_permission_responses["req-stale"]

        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="allow",
        )

        result = await mock_runtime._handle_permission_response(msg)
        # Returns True to consume the message and inform user about stale request
        assert result is True
        # User should be notified about the stale/expired request
        notification = await bus.consume_outbound()
        assert "权限请求已过期" in notification.content or "没有待处理" in notification.content
        assert "req-stale" not in bus._permission_results

    @pytest.mark.asyncio
    async def test_generic_interaction_response_is_captured(self, bus):
        from xbot.agent.runtime import AgentRuntime, SessionStateMachine
        from xbot.agent.state_coordinator import SessionStateCoordinator
        from xbot.agent.state_checker import StateConsistencyChecker

        runtime = MagicMock(spec=AgentRuntime)
        runtime.bus = bus
        runtime._is_local_runtime_command = AgentRuntime._is_local_runtime_command
        runtime._state_machine = SessionStateMachine()
        runtime._active_tasks = {}
        runtime._session_locks = {}
        runtime._state_checker = StateConsistencyChecker(runtime)
        runtime._state_coordinator = SessionStateCoordinator(runtime)
        runtime._handle_interaction_response = AgentRuntime._handle_interaction_response.__get__(runtime, AgentRuntime)

        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="ir-42",
                session_key="telegram:456",
                channel="telegram",
                chat_id="456",
                kind="question",
                prompt="继续吗？",
            )
        )
        _ = await bus.consume_outbound()

        waiter = asyncio.create_task(bus.wait_interaction_response("ir-42", timeout=1.0))
        await asyncio.sleep(0.05)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="继续",
        )
        handled = await runtime._handle_interaction_response(msg)
        assert handled is True
        resp = await waiter
        assert resp.action == "reply"
        assert resp.content == "继续"

    @pytest.mark.asyncio
    async def test_runtime_command_not_consumed_as_interaction_reply(self, bus):
        from xbot.agent.runtime import AgentRuntime, SessionStateMachine
        from xbot.agent.state_coordinator import SessionStateCoordinator
        from xbot.agent.state_checker import StateConsistencyChecker

        runtime = MagicMock(spec=AgentRuntime)
        runtime.bus = bus
        runtime._is_local_runtime_command = AgentRuntime._is_local_runtime_command
        runtime._state_machine = SessionStateMachine()
        runtime._active_tasks = {}
        runtime._session_locks = {}
        runtime._state_checker = StateConsistencyChecker(runtime)
        runtime._state_coordinator = SessionStateCoordinator(runtime)
        runtime._handle_interaction_response = AgentRuntime._handle_interaction_response.__get__(runtime, AgentRuntime)

        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="ir-99",
                session_key="telegram:456",
                channel="telegram",
                chat_id="456",
                kind="question",
                prompt="继续吗？",
            )
        )
        _ = await bus.consume_outbound()

        handled = await runtime._handle_interaction_response(
            InboundMessage(
                channel="telegram",
                sender_id="user",
                chat_id="456",
                content="!stop",  # Use !stop for local command (all / commands go to SDK)
            )
        )
        assert handled is False

    @pytest.mark.asyncio
    async def test_permission_response_takes_priority_over_interaction(self, bus):
        from xbot.agent.runtime import AgentRuntime, SessionStateMachine
        from xbot.agent.state_coordinator import SessionStateCoordinator
        from xbot.agent.state_checker import StateConsistencyChecker

        runtime = MagicMock(spec=AgentRuntime)
        runtime.bus = bus
        runtime._is_local_runtime_command = AgentRuntime._is_local_runtime_command
        runtime._state_machine = SessionStateMachine()
        runtime._active_tasks = {}
        runtime._session_locks = {}
        runtime._state_checker = StateConsistencyChecker(runtime)
        runtime._state_coordinator = SessionStateCoordinator(runtime)
        runtime._handle_permission_response = AgentRuntime._handle_permission_response.__get__(runtime, AgentRuntime)
        runtime._handle_interaction_response = AgentRuntime._handle_interaction_response.__get__(runtime, AgentRuntime)

        # both pending on same session
        bus._session_pending_requests["telegram:456"] = "perm-1"
        bus._pending_permission_responses["perm-1"] = asyncio.Event()
        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="ir-priority",
                session_key="telegram:456",
                channel="telegram",
                chat_id="456",
                kind="question",
                prompt="继续吗？",
            )
        )
        _ = await bus.consume_outbound()

        msg = InboundMessage(
            channel="telegram",
            sender_id="user",
            chat_id="456",
            content="允许",
        )

        handled_perm = await runtime._handle_permission_response(msg)
        if handled_perm:
            handled_interaction = False
        else:
            handled_interaction = await runtime._handle_interaction_response(msg)

        assert handled_perm is True
        assert bus._permission_results["perm-1"].decision == "allow"
        assert handled_interaction is False
        assert bus.get_pending_interaction_for_session("telegram:456") == "ir-priority"

    @pytest.mark.asyncio
    async def test_confirmation_interaction_free_text_is_consumed(self, bus):
        from xbot.agent.runtime import AgentRuntime, SessionStateMachine
        from xbot.agent.state_coordinator import SessionStateCoordinator
        from xbot.agent.state_checker import StateConsistencyChecker

        runtime = MagicMock(spec=AgentRuntime)
        runtime.bus = bus
        runtime._is_local_runtime_command = AgentRuntime._is_local_runtime_command
        runtime._state_machine = SessionStateMachine()
        runtime._active_tasks = {}
        runtime._session_locks = {}
        runtime._state_checker = StateConsistencyChecker(runtime)
        runtime._state_coordinator = SessionStateCoordinator(runtime)
        runtime._handle_interaction_response = AgentRuntime._handle_interaction_response.__get__(runtime, AgentRuntime)

        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="ir-confirm-1",
                session_key="telegram:456",
                channel="telegram",
                chat_id="456",
                kind="confirmation",
                prompt="请确认",
            )
        )
        _ = await bus.consume_outbound()

        waiter = asyncio.create_task(bus.wait_interaction_response("ir-confirm-1", timeout=1.0))
        await asyncio.sleep(0.05)

        handled = await runtime._handle_interaction_response(
            InboundMessage(
                channel="telegram",
                sender_id="user",
                chat_id="456",
                content="我再想想",
            )
        )
        assert handled is True
        resp = await waiter
        assert resp.action == "reply"
        assert resp.content == "我再想想"

    @pytest.mark.asyncio
    async def test_approval_interaction_free_text_is_consumed(self, bus):
        from xbot.agent.runtime import AgentRuntime, SessionStateMachine
        from xbot.agent.state_coordinator import SessionStateCoordinator
        from xbot.agent.state_checker import StateConsistencyChecker

        runtime = MagicMock(spec=AgentRuntime)
        runtime.bus = bus
        runtime._is_local_runtime_command = AgentRuntime._is_local_runtime_command
        runtime._state_machine = SessionStateMachine()
        runtime._active_tasks = {}
        runtime._session_locks = {}
        runtime._state_checker = StateConsistencyChecker(runtime)
        runtime._state_coordinator = SessionStateCoordinator(runtime)
        runtime._handle_interaction_response = AgentRuntime._handle_interaction_response.__get__(runtime, AgentRuntime)

        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="ir-approval-1",
                session_key="telegram:456",
                channel="telegram",
                chat_id="456",
                kind="approval",
                prompt="请审批",
            )
        )
        _ = await bus.consume_outbound()

        waiter = asyncio.create_task(bus.wait_interaction_response("ir-approval-1", timeout=1.0))
        await asyncio.sleep(0.05)

        handled = await runtime._handle_interaction_response(
            InboundMessage(
                channel="telegram",
                sender_id="user",
                chat_id="456",
                content="先看下差异再决定",
            )
        )
        assert handled is True
        resp = await waiter
        assert resp.action == "reply"
        assert resp.content == "先看下差异再决定"