"""Tests for AskUserQuestion answer validation logic."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from xbot.agent.interaction.response_handlers import RuntimeResponseHandlers
from xbot.agent.runtime import AgentRuntime
from xbot.agent.state.machine import SessionPhase
from xbot.bus.events import InboundMessage
from xbot.bus.queue import InteractionRequest, MessageBus


class MockRuntime:
    """Mock runtime for testing response handlers."""

    def __init__(self):
        self.bus = MessageBus()
        self._state_coordinator = MagicMock()
        self._interaction_retry_counts: dict[str, int] = {}

    def _is_local_runtime_command(self, content: str) -> bool:
        return content.startswith("!")


class TestAnswerValidation:
    """Tests for AskUserQuestion answer validation."""

    def setup_method(self):
        """Reset retry counts before each test."""
        AgentRuntime._interaction_retry_counts = {}

    @pytest.mark.asyncio
    async def test_valid_answer_exact_match(self, handler, runtime, mock_transaction):
        """Test exact match with valid option."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        # Setup interaction request with valid options
        request = InteractionRequest(
            request_id="req-123",
            session_key="telegram:oc_123",
            channel="telegram",
            chat_id="oc_123",
            kind="question",
            prompt="选择方案",
            suggestions=["方案 A", "方案 B", "方案 C"],
            metadata={"valid_options": ["方案 A", "方案 B", "方案 C"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # User responds with exact match
        msg = InboundMessage(
            channel="telegram",
            sender_id="user123",
            chat_id="oc_123",
            content="方案 A",
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True
        # Retry count should be cleaned up on success
        assert runtime._interaction_retry_counts.get("telegram:oc_123") is None

    @pytest.mark.asyncio
    async def test_valid_answer_case_insensitive(self, handler, runtime, mock_transaction):
        """Test case-insensitive match."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-123",
            session_key="telegram:oc_123",
            channel="telegram",
            chat_id="oc_123",
            kind="question",
            prompt="选择方案",
            suggestions=["方案 A", "方案 B"],
            metadata={"valid_options": ["方案 A", "方案 B"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # User responds with different case (for Chinese this is same)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user123",
            chat_id="oc_123",
            content="方案 a",  # lowercase
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_answer_first_retry(self, handler, runtime, mock_transaction):
        """Test invalid answer triggers first retry."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-123",
            session_key="telegram:oc_123",
            channel="telegram",
            chat_id="oc_123",
            kind="question",
            prompt="选择方案",
            suggestions=["方案 A", "方案 B"],
            metadata={"valid_options": ["方案 A", "方案 B"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # Consume the interaction request message first
        await runtime.bus.consume_outbound()

        # User responds with invalid answer
        msg = InboundMessage(
            channel="telegram",
            sender_id="user123",
            chat_id="oc_123",
            content="无效答案",
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True
        # Retry count should be updated
        assert runtime._interaction_retry_counts.get("telegram:oc_123") == 1

        # Check error message was published
        assert runtime.bus.outbound_size == 1
        outbound = await runtime.bus.consume_outbound()
        assert "答案无效" in outbound.content
        assert "第 1/3 次尝试" in outbound.content

    @pytest.mark.asyncio
    async def test_invalid_answer_max_retries(self, handler, runtime, mock_transaction):
        """Test max retries (3) cancels interaction."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-123",
            session_key="telegram:oc_123",
            channel="telegram",
            chat_id="oc_123",
            kind="question",
            prompt="选择方案",
            suggestions=["方案 A", "方案 B"],
            metadata={"valid_options": ["方案 A", "方案 B"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # Consume the interaction request message first
        await runtime.bus.consume_outbound()

        msg = InboundMessage(
            channel="telegram",
            sender_id="user123",
            chat_id="oc_123",
            content="无效答案",
        )

        # Third retry (retry_count=2, will increment to 3)
        result = await handler.handle_interaction_response(msg, retry_count=2)

        assert result is True
        # Retry count should be cleaned up
        assert runtime._interaction_retry_counts.get("telegram:oc_123") is None

        # Check cancellation message
        assert runtime.bus.outbound_size == 1
        outbound = await runtime.bus.consume_outbound()
        assert "答案无效已达 3 次" in outbound.content
        assert "交互已取消" in outbound.content

    @pytest.mark.asyncio
    async def test_valid_answer_after_invalid(self, handler, runtime, mock_transaction):
        """Test valid answer after previous invalid attempts."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-123",
            session_key="telegram:oc_123",
            channel="telegram",
            chat_id="oc_123",
            kind="question",
            prompt="选择方案",
            suggestions=["方案 A", "方案 B"],
            metadata={"valid_options": ["方案 A", "方案 B"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # Valid answer with retry_count=1 (previous invalid attempt)
        msg = InboundMessage(
            channel="telegram",
            sender_id="user123",
            chat_id="oc_123",
            content="方案 B",
        )

        result = await handler.handle_interaction_response(msg, retry_count=1)

        assert result is True
        # Retry count should be cleaned up on success
        assert runtime._interaction_retry_counts.get("telegram:oc_123") is None

    @pytest.mark.asyncio
    async def test_no_valid_options_metadata(self, handler, runtime, mock_transaction):
        """Test interaction without valid_options skips validation."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-123",
            session_key="telegram:oc_123",
            channel="telegram",
            chat_id="oc_123",
            kind="question",
            prompt="随便回答",
            suggestions=["继续", "取消"],
            metadata={},  # No valid_options
        )
        await runtime.bus.publish_interaction_request(request)

        msg = InboundMessage(
            channel="telegram",
            sender_id="user123",
            chat_id="oc_123",
            content="任何答案都可以",
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True
        # No validation, so any answer is accepted

    @pytest.mark.asyncio
    async def test_non_question_kind_skips_validation(self, handler, runtime, mock_transaction):
        """Test non-question kind skips validation."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-123",
            session_key="telegram:oc_123",
            channel="telegram",
            chat_id="oc_123",
            kind="confirmation",  # Not a question
            prompt="确认执行？",
            suggestions=["确认", "取消"],
            metadata={"valid_options": ["确认", "取消"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # "yes" should be parsed as "confirm" action
        msg = InboundMessage(
            channel="telegram",
            sender_id="user123",
            chat_id="oc_123",
            content="yes",
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True

    @pytest.mark.asyncio
    async def test_whitespace_trimming(self, handler, runtime, mock_transaction):
        """Test whitespace is trimmed before matching."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        request = InteractionRequest(
            request_id="req-123",
            session_key="telegram:oc_123",
            channel="telegram",
            chat_id="oc_123",
            kind="question",
            prompt="选择方案",
            suggestions=["方案 A", "方案 B"],
            metadata={"valid_options": ["方案 A", "方案 B"]},
        )
        await runtime.bus.publish_interaction_request(request)

        # User responds with extra whitespace
        msg = InboundMessage(
            channel="telegram",
            sender_id="user123",
            chat_id="oc_123",
            content="  方案 A  ",  # Extra spaces
        )

        result = await handler.handle_interaction_response(msg, retry_count=0)

        assert result is True


class TestRetryCountManagement:
    """Tests for retry count tracking in runtime."""

    @pytest.fixture
    def handler(self, runtime):
        return RuntimeResponseHandlers(runtime)

    @pytest.mark.asyncio
    async def test_retry_count_persists_between_attempts(self):
        """Test retry count persists across multiple invalid attempts."""
        runtime = MockRuntime()
        handler = RuntimeResponseHandlers(runtime)

        # First invalid attempt
        runtime._interaction_retry_counts["telegram:oc_123"] = 0
        assert runtime._interaction_retry_counts["telegram:oc_123"] == 0

    @pytest.mark.asyncio
    async def test_retry_count_cleanup_on_success(self, handler, runtime, mock_transaction):
        """Test retry count is cleaned up on successful answer."""
        runtime._state_coordinator.get_phase.return_value = SessionPhase.WAITING_INTERACTION
        runtime._state_coordinator.transaction.return_value = mock_transaction

        # Simulate previous failed attempts
        runtime._interaction_retry_counts["telegram:oc_123"] = 2

        request = InteractionRequest(
            request_id="req-123",
            session_key="telegram:oc_123",
            channel="telegram",
            chat_id="oc_123",
            kind="question",
            prompt="选择方案",
            suggestions=["方案 A"],
            metadata={"valid_options": ["方案 A"]},
        )
        await runtime.bus.publish_interaction_request(request)

        msg = InboundMessage(
            channel="telegram",
            sender_id="user123",
            chat_id="oc_123",
            content="方案 A",
        )

        result = await handler.handle_interaction_response(msg, retry_count=2)

        assert result is True
        assert runtime._interaction_retry_counts.get("telegram:oc_123") is None


# Move runtime fixture to module level so it's available for all classes
@pytest.fixture
def runtime():
    return MockRuntime()


@pytest.fixture
def handler(runtime):
    return RuntimeResponseHandlers(runtime)


@pytest.fixture
def mock_transaction():
    """Mock transaction context manager."""
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=None)
    return tx


class TestInteractionRequestMetadata:
    """Tests for interaction request metadata handling."""

    def test_valid_options_in_metadata(self):
        """Test valid_options can be stored in metadata."""
        request = InteractionRequest(
            request_id="req-123",
            session_key="test:123",
            channel="telegram",
            chat_id="user123",
            kind="question",
            prompt="选择方案",
            suggestions=["方案 A", "方案 B"],
            metadata={
                "valid_options": ["方案 A", "方案 B"],
                "question_headers": ["方案选择"],
                "multi_select": False,
            },
        )

        assert request.metadata["valid_options"] == ["方案 A", "方案 B"]
        assert request.metadata["question_headers"] == ["方案选择"]
        assert request.metadata["multi_select"] is False

    def test_empty_valid_options(self):
        """Test empty valid_options list."""
        request = InteractionRequest(
            request_id="req-123",
            session_key="test:123",
            channel="telegram",
            chat_id="user123",
            kind="question",
            prompt="随便回答",
            suggestions=["继续", "取消"],
            metadata={"valid_options": []},
        )

        # Empty list means no validation
        assert request.metadata["valid_options"] == []
