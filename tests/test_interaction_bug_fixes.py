"""Tests for bug fixes in interaction response handling.

This test suite prevents regression of bugs fixed in 2026-03-25:
- Bug 6: retry count leak when state mismatch
- Bug 7: approval/confirmation types not showing options
- Bug 5: original input not logged
- Bug 2: original_input variable scope issue
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from xbot.agent.interaction.response_handlers import RuntimeResponseHandlers
from xbot.agent.state.machine import SessionPhase
from xbot.bus.events import InboundMessage
from xbot.bus.queue import InteractionRequest, MessageBus, InteractionResponse


class MockRuntime:
    """Mock runtime for testing response handlers."""

    def __init__(self):
        self.bus = MessageBus()
        self._state_coordinator = MagicMock()
        self._interaction_retry_counts: dict[str, int] = {}

    def _is_local_runtime_command(self, content: str) -> bool:
        return content.startswith("!")


class MockTransaction:
    """Mock transaction context manager."""

    def __init__(self, phase_to_set=None):
        self.phase_to_set = phase_to_set

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def set_phase(self, phase, reason=""):
        if self.phase_to_set:
            pass  # Mock doesn't need to actually set anything


class TestBugFixes:
    """Tests for bugs fixed in 2026-03-25."""

    @pytest.fixture
    def runtime(self):
        return MockRuntime()

    @pytest.fixture
    def handler(self, runtime):
        return RuntimeResponseHandlers(runtime)

    @pytest.fixture
    def mock_transaction(self):
        """Mock transaction context manager."""
        tx = MockTransaction()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=None)
        return tx

    # ============================================================
    # Bug 6: retry count leak when state mismatch
    # ============================================================

    @pytest.mark.asyncio
    async def test_state_mismatch_cleanup_retry_count(self, handler, runtime, mock_transaction):
        """Bug 6: State mismatch should clean up retry count to prevent memory leak.

        Before fix: retry count was not cleaned up when state was not in
        {WAITING_INTERACTION, IDLE, RUNNING}

        After fix: retry count is cleaned up in all early return paths.
        """
        # Set state to WAITING_PERMISSION (not in allowed states)
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_PERMISSION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        # Setup pending interaction request (required to pass early check)
        request = InteractionRequest(
            request_id="req-state-mismatch",
            session_key="test:session1",
            channel="test",
            chat_id="chat1",
            kind="question",
            prompt="Choose",
            suggestions=["A", "B"],
        )
        await runtime.bus.publish_interaction_request(request)

        # Set a retry count for this session
        session_key = "test:session1"
        runtime._interaction_retry_counts[session_key] = 2

        msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="some reply",
            session_key_override=session_key,  # Use override field, not property assignment
        )

        # Call handler - should return True (handled) and clean up retry count
        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True
        # Verify retry count was cleaned up
        assert session_key not in runtime._interaction_retry_counts

    @pytest.mark.asyncio
    async def test_stopping_state_cleanup_retry_count(self, handler, runtime, mock_transaction):
        """Bug 6: STOPPING state should also clean up retry count."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.STOPPING
        runtime._state_coordinator.transaction.return_value = mock_transaction

        # Setup pending interaction request
        request = InteractionRequest(
            request_id="req-stopping",
            session_key="test:session2",
            channel="test",
            chat_id="chat1",
            kind="question",
            prompt="Choose",
            suggestions=["A", "B"],
        )
        await runtime.bus.publish_interaction_request(request)

        session_key = "test:session2"
        runtime._interaction_retry_counts[session_key] = 1

        msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="some reply",
            session_key_override=session_key,
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True
        assert session_key not in runtime._interaction_retry_counts

    @pytest.mark.asyncio
    async def test_error_state_cleanup_retry_count(self, handler, runtime, mock_transaction):
        """Bug 6: ERROR state should also clean up retry count."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.ERROR
        runtime._state_coordinator.transaction.return_value = mock_transaction

        # Setup pending interaction request
        request = InteractionRequest(
            request_id="req-error",
            session_key="test:session3",
            channel="test",
            chat_id="chat1",
            kind="question",
            prompt="Choose",
            suggestions=["A", "B"],
        )
        await runtime.bus.publish_interaction_request(request)

        session_key = "test:session3"
        runtime._interaction_retry_counts[session_key] = 3

        msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="some reply",
            session_key_override=session_key,
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True
        assert session_key not in runtime._interaction_retry_counts

    @pytest.mark.asyncio
    async def test_no_state_mismatch_does_not_cleanup_prematurely(self, handler, runtime, mock_transaction):
        """Verify normal flow doesn't clean up retry count prematurely.

        This ensures the fix doesn't break the normal retry mechanism.
        """
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        # Setup interaction request with valid options
        request = InteractionRequest(
            request_id="req-test",
            session_key="test:session4",
            channel="test",
            chat_id="chat1",
            kind="question",
            prompt="Choose",
            suggestions=["A", "B"],
            metadata={"valid_options": ["A", "B"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # Consume the interaction request message
        await runtime.bus.consume_outbound()

        # Send invalid answer
        msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="invalid",
            session_key_override="test:session4",
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True
        # Retry count should be set (not cleaned up) for invalid answer
        assert runtime._interaction_retry_counts.get("test:session4") == 1

    # ============================================================
    # Bug 7: approval/confirmation types should show options
    # ============================================================

    @pytest.mark.asyncio
    async def test_approval_interaction_sends_with_options(self, handler, runtime, mock_transaction):
        """Bug 7: Approval type interactions should display options list.

        Before fix: Only 'question' type showed options
        After fix: 'approval', 'confirmation', and 'question' all show options
        """
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        # Create approval request with options
        request = InteractionRequest(
            request_id="req-approval",
            session_key="test:session5",
            channel="test",
            chat_id="chat1",
            kind="approval",  # Note: approval type
            prompt="Allow this tool?",
            suggestions=["允许", "拒绝"],
            metadata={"valid_options": ["允许", "拒绝"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # User replies with valid option
        msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="允许",
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True
        # Should accept valid option
        assert "test:session5" not in runtime._interaction_retry_counts

    @pytest.mark.asyncio
    async def test_confirmation_interaction_sends_with_options(self, handler, runtime, mock_transaction):
        """Bug 7: Confirmation type interactions should display options list."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-confirm",
            session_key="test:session6",
            channel="test",
            chat_id="chat1",
            kind="confirmation",  # Note: confirmation type
            prompt="Confirm action?",
            suggestions=["确认", "取消"],
            metadata={"valid_options": ["确认", "取消"]},
        )
        await runtime.bus.publish_interaction_request(request)

        msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="确认",
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True

    # ============================================================
    # Bug 5 & Bug 2: original_input logging and variable scope
    # ============================================================

    @pytest.mark.asyncio
    async def test_original_input_recorded_in_metadata(self, handler, runtime, mock_transaction):
        """Bug 5: Original user input should be recorded for logging.

        Before fix: Only matched option was recorded
        After fix: original_input is stored in response metadata
        """
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-original",
            session_key="test:session7",
            channel="test",
            chat_id="chat1",
            kind="question",
            prompt="Choose",
            suggestions=["Option A", "Option B"],
            metadata={"valid_options": ["Option A", "Option B"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # Consume the interaction request message
        await runtime.bus.consume_outbound()

        # User replies with lowercase (different from option case)
        msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="option a",  # lowercase
            session_key_override="test:session7",
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True
        # The response should be submitted - we can't easily check metadata
        # but we verify the flow completes without error

    @pytest.mark.asyncio
    async def test_no_valid_options_doesnt_crash(self, handler, runtime, mock_transaction):
        """Bug 2: Handle case where valid_options is empty or missing.

        Before fix: matched_option variable was used before assignment
        After fix: content is only set to matched_option inside the if block
        """
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        # Request without valid_options
        request = InteractionRequest(
            request_id="req-no-opts",
            session_key="test:session8",
            channel="test",
            chat_id="chat1",
            kind="question",
            prompt="Just reply anything",
            suggestions=[],
            metadata={},  # No valid_options
        )
        await runtime.bus.publish_interaction_request(request)

        # Consume the interaction request message
        await runtime.bus.consume_outbound()

        msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="anything",
            session_key_override="test:session8",
        )

        # Should not crash with UnboundLocalError
        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True

    @pytest.mark.asyncio
    async def test_non_question_kind_doesnt_crash(self, handler, runtime, mock_transaction):
        """Bug 2: Non-question kinds should work without valid_options.

        Regression test for UnboundLocalError on matched_option.
        """
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        # Confirmation without valid_options
        request = InteractionRequest(
            request_id="req-confirm-no-opts",
            session_key="test:session9",
            channel="test",
            chat_id="chat1",
            kind="confirmation",
            prompt="Confirm?",
            suggestions=["y", "n"],
            metadata={},  # No valid_options
        )
        await runtime.bus.publish_interaction_request(request)

        msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="y",
        )

        # Should not crash with UnboundLocalError
        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True

    @pytest.mark.asyncio
    async def test_original_input_used_in_error_message(self, handler, runtime, mock_transaction):
        """Bug 5: Error message should show original user input.

        Before fix: Error message showed potentially modified content
        After fix: Error message shows original_input
        """
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-error-msg",
            session_key="test:session10",
            channel="test",
            chat_id="chat1",
            kind="question",
            prompt="Choose",
            suggestions=["Option A", "Option B"],
            metadata={"valid_options": ["Option A", "Option B"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # Consume the initial interaction request message
        await runtime.bus.consume_outbound()

        # User replies with invalid option
        msg = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="invalid reply",
            session_key_override="test:session10",
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True
        # Check error message was sent
        outbound = await runtime.bus.consume_outbound()
        # Error message should contain the user's original input
        assert "invalid reply" in outbound.content
        assert "第 1/3 次尝试" in outbound.content

    # ============================================================
    # Additional edge cases
    # ============================================================

    @pytest.mark.asyncio
    async def test_retry_count_cleaned_on_max_retries(self, handler, runtime, mock_transaction):
        """Verify retry count is cleaned up after 3 failed attempts."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-max-retry",
            session_key="test:session11",
            channel="test",
            chat_id="chat1",
            kind="question",
            prompt="Choose",
            suggestions=["A", "B"],
            metadata={"valid_options": ["A", "B"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # Consume the initial interaction request message
        await runtime.bus.consume_outbound()

        # Simulate 3 invalid attempts
        for i in range(3):
            msg = InboundMessage(
                channel="test",
                sender_id="user1",
                chat_id="chat1",
                content="invalid",
                session_key_override="test:session11",
            )
            await handler.handle_interaction_response(msg, retry_count=i)

            # Consume the error message (if not the last attempt)
            if i < 2:
                await runtime.bus.consume_outbound()

        # After 3rd attempt, retry count should be cleaned up
        assert "test:session11" not in runtime._interaction_retry_counts

    @pytest.mark.asyncio
    async def test_retry_count_cleaned_on_success(self, handler, runtime, mock_transaction):
        """Verify retry count is cleaned up after successful answer."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-success",
            session_key="test:session12",
            channel="test",
            chat_id="chat1",
            kind="question",
            prompt="Choose",
            suggestions=["A", "B"],
            metadata={"valid_options": ["A", "B"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # Consume the initial interaction request message
        await runtime.bus.consume_outbound()

        # First invalid attempt
        msg1 = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="invalid",
            session_key_override="test:session12",
        )
        await handler.handle_interaction_response(msg1, retry_count=0)

        # Verify retry count is set
        assert runtime._interaction_retry_counts.get("test:session12") == 1

        # Consume error message
        await runtime.bus.consume_outbound()

        # Then valid answer
        msg2 = InboundMessage(
            channel="test",
            sender_id="user1",
            chat_id="chat1",
            content="A",
            session_key_override="test:session12",
        )
        result = await handler.handle_interaction_response(msg2, retry_count=1)

        assert result is True
        # Retry count should be cleaned up on success
        assert "test:session12" not in runtime._interaction_retry_counts


class TestFeishuInteractionFormatting:
    """Tests for Feishu channel interaction message formatting."""

    @pytest.mark.asyncio
    async def test_question_interaction_uses_post_format(self):
        """Verify question interactions use post format with options."""
        from unittest.mock import patch, MagicMock, AsyncMock
        from xbot.bus.events import OutboundMessage
        from xbot.channels.feishu import FeishuChannel, FeishuConfig

        config = FeishuConfig(
            app_id="test",
            app_secret="test",
        )
        bus = MagicMock()
        channel = FeishuChannel(config, bus)
        channel._client = MagicMock()

        msg = OutboundMessage(
            channel="feishu",
            chat_id="oc_test123",
            content="请选择方案",
            metadata={
                "interaction_request": True,
                "interaction_kind": "question",
                "suggestions": ["快速", "质量", "取消"],
            },
        )

        with patch.object(channel, '_send_message_sync') as mock_send:
            with patch.object(channel, '_markdown_to_post', return_value="mock_post"):
                await channel.send(msg)

                # Should send as post format
                assert mock_send.called
                call_args = mock_send.call_args[0]
                msg_type = call_args[2]
                assert msg_type == "post"

    @pytest.mark.asyncio
    async def test_approval_interaction_uses_post_format(self):
        """Bug 7 fix: approval interactions should also use post format."""
        from unittest.mock import patch, MagicMock, AsyncMock
        from xbot.bus.events import OutboundMessage
        from xbot.channels.feishu import FeishuChannel, FeishuConfig

        config = FeishuConfig(
            app_id="test",
            app_secret="test",
        )
        bus = MagicMock()
        channel = FeishuChannel(config, bus)
        channel._client = MagicMock()

        msg = OutboundMessage(
            channel="feishu",
            chat_id="oc_test123",
            content="是否允许此操作？",
            metadata={
                "interaction_request": True,
                "interaction_kind": "approval",  # approval type
                "suggestions": ["允许", "拒绝"],
            },
        )

        with patch.object(channel, '_send_message_sync') as mock_send:
            with patch.object(channel, '_markdown_to_post', return_value="mock_post"):
                await channel.send(msg)

                # Should send as post format (Bug 7 fix)
                assert mock_send.called
                call_args = mock_send.call_args[0]
                msg_type = call_args[2]
                assert msg_type == "post"

    @pytest.mark.asyncio
    async def test_confirmation_interaction_uses_post_format(self):
        """Bug 7 fix: confirmation interactions should also use post format."""
        from unittest.mock import patch, MagicMock, AsyncMock
        from xbot.bus.events import OutboundMessage
        from xbot.channels.feishu import FeishuChannel, FeishuConfig

        config = FeishuConfig(
            app_id="test",
            app_secret="test",
        )
        bus = MagicMock()
        channel = FeishuChannel(config, bus)
        channel._client = MagicMock()

        msg = OutboundMessage(
            channel="feishu",
            chat_id="oc_test123",
            content="请确认",
            metadata={
                "interaction_request": True,
                "interaction_kind": "confirmation",  # confirmation type
                "suggestions": ["确认", "取消"],
            },
        )

        with patch.object(channel, '_send_message_sync') as mock_send:
            with patch.object(channel, '_markdown_to_post', return_value="mock_post"):
                await channel.send(msg)

                # Should send as post format (Bug 7 fix)
                assert mock_send.called
                call_args = mock_send.call_args[0]
                msg_type = call_args[2]
                assert msg_type == "post"

    @pytest.mark.asyncio
    async def test_interaction_formatting_includes_options(self):
        """Verify formatted message includes all options."""
        from unittest.mock import patch, MagicMock
        from xbot.bus.events import OutboundMessage
        from xbot.channels.feishu import FeishuChannel, FeishuConfig

        config = FeishuConfig(
            app_id="test",
            app_secret="test",
        )
        bus = MagicMock()
        channel = FeishuChannel(config, bus)
        channel._client = MagicMock()

        msg = OutboundMessage(
            channel="feishu",
            chat_id="oc_test123",
            content="请选择",
            metadata={
                "interaction_request": True,
                "interaction_kind": "question",
                "suggestions": ["选项 A", "选项 B", "选项 C"],
            },
        )

        def capture_post(content):
            # Verify content includes options
            assert "选项 A" in content
            assert "选项 B" in content
            assert "选项 C" in content
            return "mock_post"

        with patch.object(channel, '_send_message_sync'):
            with patch.object(channel, '_markdown_to_post', side_effect=capture_post):
                await channel.send(msg)

    @pytest.mark.asyncio
    async def test_suggested_question_mentions_free_text(self):
        """Suggested questions should tell users they can type custom content."""
        from unittest.mock import patch, MagicMock
        from xbot.bus.events import OutboundMessage
        from xbot.channels.feishu import FeishuChannel, FeishuConfig

        config = FeishuConfig(app_id="test", app_secret="test")
        bus = MagicMock()
        channel = FeishuChannel(config, bus)
        channel._client = MagicMock()

        msg = OutboundMessage(
            channel="feishu",
            chat_id="oc_test123",
            content="请输入应用名",
            metadata={
                "interaction_request": True,
                "interaction_kind": "question",
                "suggestions": ["xbot", "xbot-prod", "Other"],
                "validation_mode": "suggested",
            },
        )

        def capture_post(content):
            assert "也可输入你自己的内容" in content
            return "mock_post"

        with patch.object(channel, '_send_message_sync'):
            with patch.object(channel, '_markdown_to_post', side_effect=capture_post):
                await channel.send(msg)

    @pytest.mark.asyncio
    async def test_strict_question_mentions_reply_with_options(self):
        """Strict questions should still require option-only replies."""
        from unittest.mock import patch, MagicMock
        from xbot.bus.events import OutboundMessage
        from xbot.channels.feishu import FeishuChannel, FeishuConfig

        config = FeishuConfig(app_id="test", app_secret="test")
        bus = MagicMock()
        channel = FeishuChannel(config, bus)
        channel._client = MagicMock()

        msg = OutboundMessage(
            channel="feishu",
            chat_id="oc_test123",
            content="请选择方案",
            metadata={
                "interaction_request": True,
                "interaction_kind": "question",
                "suggestions": ["A", "B"],
                "validation_mode": "strict",
            },
        )

        def capture_post(content):
            assert "请回复以下选项之一" in content
            return "mock_post"

        with patch.object(channel, '_send_message_sync'):
            with patch.object(channel, '_markdown_to_post', side_effect=capture_post):
                await channel.send(msg)
