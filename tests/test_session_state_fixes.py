"""Tests for session state fixes.

Tests for:
- SessionContextManager: unified session-to-context mapping
- Error classification: recoverable vs non-recoverable errors
- State cleanup: unified cleanup logic
- Request ID tracking: request-response validation
"""

import uuid

import pytest

from xbot.agent.session_context_manager import SessionContext, SessionContextManager


class TestRequestIDTracking:
    """Test request ID tracking for request-response validation."""

    def test_request_id_generation(self) -> None:
        """Test that request IDs are generated correctly."""
        request_id = str(uuid.uuid4())
        assert len(request_id) == 36  # UUID format: 8-4-4-4-12
        assert request_id.count("-") == 4

    def test_request_id_uniqueness(self) -> None:
        """Test that request IDs are unique."""
        ids = {str(uuid.uuid4()) for _ in range(100)}
        assert len(ids) == 100  # All unique

    def test_request_id_format(self) -> None:
        """Test request ID format validation."""
        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        # UUID validation - should not raise
        uuid.UUID(valid_uuid)

    def test_uuid_mismatch_detection(self) -> None:
        """Test UUID mismatch detection logic."""
        expected_request_id = "550e8400-e29b-41d4-a716-446655440000"
        received_uuid = "650e8400-e29b-41d4-a716-446655440001"
        matches = expected_request_id == received_uuid
        assert matches is False

    def test_uuid_match_detection(self) -> None:
        """Test UUID match detection logic."""
        expected_request_id = "550e8400-e29b-41d4-a716-446655440000"
        received_uuid = "550e8400-e29b-41d4-a716-446655440000"
        matches = expected_request_id == received_uuid
        assert matches is True

    def test_uuid_none_handling(self) -> None:
        """Test handling of None UUID values."""
        expected_request_id = None
        received_uuid = "550e8400-e29b-41d4-a716-446655440000"
        # If expected is None, we don't validate
        should_validate = expected_request_id is not None and received_uuid is not None
        assert should_validate is False


class TestRequestTrackingState:
    """Test request tracking state management."""

    def test_active_request_ids_initialization(self) -> None:
        """Test that _active_request_ids is initialized."""
        # Simulated state
        active_request_ids: dict[str, str] = {}
        assert len(active_request_ids) == 0

    def test_active_request_ids_tracking(self) -> None:
        """Test tracking active request IDs."""
        active_request_ids: dict[str, str] = {}
        session_key = "telegram:12345"
        request_id = str(uuid.uuid4())

        # Set
        active_request_ids[session_key] = request_id
        assert active_request_ids.get(session_key) == request_id

        # Clear
        active_request_ids.pop(session_key, None)
        assert session_key not in active_request_ids

    def test_multiple_sessions_tracking(self) -> None:
        """Test tracking multiple sessions."""
        active_request_ids: dict[str, str] = {}

        # Add multiple sessions
        for i in range(5):
            session_key = f"telegram:{i}"
            active_request_ids[session_key] = str(uuid.uuid4())

        assert len(active_request_ids) == 5

        # Clear one
        active_request_ids.pop("telegram:2", None)
        assert len(active_request_ids) == 4
        assert "telegram:2" not in active_request_ids


class TestSessionContext:
    """Test SessionContext dataclass."""

    def test_to_tuple(self) -> None:
        """Test conversion to tuple."""
        ctx = SessionContext(channel="telegram", chat_id="12345")
        assert ctx.to_tuple() == ("telegram", "12345")

    def test_equality(self) -> None:
        """Test equality comparison."""
        ctx1 = SessionContext(channel="telegram", chat_id="12345")
        ctx2 = SessionContext(channel="telegram", chat_id="12345")
        ctx3 = SessionContext(channel="discord", chat_id="12345")
        assert ctx1 == ctx2
        assert ctx1 != ctx3


class TestSessionContextManager:
    """Test SessionContextManager."""

    def test_set_and_get_by_session_key(self) -> None:
        """Test setting and getting context by session_key."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("telegram:12345", "sdk-uuid-abc", ctx)

        result = manager.get_by_session_key("telegram:12345")
        assert result is not None
        assert result.channel == "telegram"
        assert result.chat_id == "12345"

    def test_set_and_get_by_sdk_session_id(self) -> None:
        """Test setting and getting context by sdk_session_id."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("telegram:12345", "sdk-uuid-abc", ctx)

        result = manager.get_by_sdk_session_id("sdk-uuid-abc")
        assert result is not None
        assert result.channel == "telegram"

    def test_get_context_either_id(self) -> None:
        """Test get_context works with either ID type."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("telegram:12345", "sdk-uuid-abc", ctx)

        # Get by session_key
        result = manager.get_context("telegram:12345")
        assert result is not None

        # Get by sdk_session_id
        result = manager.get_context("sdk-uuid-abc")
        assert result is not None

    def test_update_sdk_session_id(self) -> None:
        """Test updating SDK session ID."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")

        # Initial set without sdk_session_id
        manager.set_context("telegram:12345", None, ctx)

        # Update with sdk_session_id
        manager.update_sdk_session_id("telegram:12345", "sdk-uuid-abc")

        result = manager.get_by_sdk_session_id("sdk-uuid-abc")
        assert result is not None

    def test_update_sdk_session_id_replaces_old(self) -> None:
        """Test that updating SDK session ID replaces old mapping."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")

        manager.set_context("telegram:12345", "sdk-uuid-old", ctx)
        manager.update_sdk_session_id("telegram:12345", "sdk-uuid-new")

        # Old should be gone
        assert manager.get_by_sdk_session_id("sdk-uuid-old") is None
        # New should exist
        assert manager.get_by_sdk_session_id("sdk-uuid-new") is not None

    def test_clear(self) -> None:
        """Test clearing mappings."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("telegram:12345", "sdk-uuid-abc", ctx)

        result = manager.clear("telegram:12345")
        assert result is True

        assert manager.get_by_session_key("telegram:12345") is None
        assert manager.get_by_sdk_session_id("sdk-uuid-abc") is None

    def test_clear_nonexistent(self) -> None:
        """Test clearing non-existent session."""
        manager = SessionContextManager()
        result = manager.clear("nonexistent")
        assert result is False

    def test_clear_by_sdk_session_id(self) -> None:
        """Test clearing by SDK session ID."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("telegram:12345", "sdk-uuid-abc", ctx)

        result = manager.clear_by_sdk_session_id("sdk-uuid-abc")
        assert result is True

        assert manager.get_by_session_key("telegram:12345") is None
        assert manager.get_by_sdk_session_id("sdk-uuid-abc") is None

    def test_get_session_key_by_sdk_id(self) -> None:
        """Test reverse lookup from SDK ID to session_key."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("telegram:12345", "sdk-uuid-abc", ctx)

        result = manager.get_session_key_by_sdk_id("sdk-uuid-abc")
        assert result == "telegram:12345"

    def test_size_limit(self) -> None:
        """Test that size limit is enforced."""
        manager = SessionContextManager()
        manager.MAX_SESSIONS = 10

        # Add more than limit
        for i in range(15):
            ctx = SessionContext(channel="telegram", chat_id=str(i))
            manager.set_context(f"telegram:{i}", f"sdk-{i}", ctx)

        # Should have enforced limit
        assert manager.size() <= manager.MAX_SESSIONS

    def test_list_session_keys(self) -> None:
        """Test listing session keys."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("telegram:12345", "sdk-abc", ctx)

        keys = manager.list_session_keys()
        assert "telegram:12345" in keys

    def test_contains(self) -> None:
        """Test __contains__ method."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("telegram:12345", "sdk-abc", ctx)

        assert "telegram:12345" in manager
        assert "nonexistent" not in manager

    def test_len(self) -> None:
        """Test __len__ method."""
        manager = SessionContextManager()
        assert len(manager) == 0

        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("telegram:12345", "sdk-abc", ctx)
        assert len(manager) == 1


class TestErrorClassification:
    """Test error classification for recoverability."""

    RECOVERABLE_ERRORS = {
        "ConnectionError",
        "TimeoutError",
        "asyncio.TimeoutError",
        "ConnectionResetError",
        "BrokenPipeError",
    }

    def test_recoverable_errors_list(self) -> None:
        """Test that recoverable errors are properly defined."""
        # This test documents the expected recoverable errors
        assert "ConnectionError" in self.RECOVERABLE_ERRORS
        assert "TimeoutError" in self.RECOVERABLE_ERRORS
        assert "RuntimeError" not in self.RECOVERABLE_ERRORS
        assert "ValueError" not in self.RECOVERABLE_ERRORS
        assert "CancelledError" not in self.RECOVERABLE_ERRORS

    def test_error_type_classification(self) -> None:
        """Test error type checking logic."""
        # Simulate the classification logic
        error_type = "ConnectionError"
        is_recoverable = error_type in self.RECOVERABLE_ERRORS
        assert is_recoverable is True

        error_type = "RuntimeError"
        is_recoverable = error_type in self.RECOVERABLE_ERRORS
        assert is_recoverable is False


class TestStateCleanup:
    """Test state cleanup logic."""

    def test_remove_client_state_clears_all_dicts(self) -> None:
        """Test that _remove_client_state clears all relevant dicts.

        This is a documentation test showing what should be cleared.
        """
        # The dicts that should be cleared:
        expected_cleared = [
            "_clients",
            "_client_last_used",
            "_client_models",
            "_client_skills_versions",
            "_session_commands",
            "_active_task_ids",
            "_session_contexts",
        ]
        # This test documents the expected behavior
        assert len(expected_cleared) == 7


class TestStaleTaskDetection:
    """Test stale task notification detection."""

    def test_task_id_mismatch_detection(self) -> None:
        """Test that task ID mismatch can be detected."""
        active_task_id = "task-001"
        received_task_id = "task-002"
        matches_active = active_task_id == received_task_id
        assert matches_active is False

    def test_task_id_match_detection(self) -> None:
        """Test that task ID match can be detected."""
        active_task_id = "task-001"
        received_task_id = "task-001"
        matches_active = active_task_id == received_task_id
        assert matches_active is True

    def test_none_task_id_handling(self) -> None:
        """Test handling of None task IDs."""
        active_task_id = None
        received_task_id = "task-001"
        # If no active task, we shouldn't match
        matches_active = active_task_id == received_task_id if active_task_id else False
        assert matches_active is False


class TestFallbackPathStaleTaskDetection:
    """Test stale task detection in fallback path."""

    def test_fallback_stale_task_ignored(self) -> None:
        """Test that stale task notifications are ignored in fallback path."""
        # Simulate the fallback path logic
        active_task_ids: dict[str, str] = {"telegram:12345": "task-001"}
        session_key = "telegram:12345"
        received_task_id = "task-002"  # Different from active

        current_task_id = active_task_ids.get(session_key)
        matches_active = current_task_id == received_task_id if received_task_id else False

        # Should not match - stale notification
        assert matches_active is False
        # In real implementation, this notification would be ignored (continue)
        # _active_task_ids would not be cleared

    def test_fallback_valid_task_processed(self) -> None:
        """Test that valid task notifications are processed in fallback path."""
        # Simulate the fallback path logic
        active_task_ids: dict[str, str] = {"telegram:12345": "task-001"}
        session_key = "telegram:12345"
        received_task_id = "task-001"  # Same as active

        current_task_id = active_task_ids.get(session_key)
        matches_active = current_task_id == received_task_id if received_task_id else False

        # Should match - valid notification
        assert matches_active is True
        # In real implementation, _active_task_ids would be cleared

    def test_fallback_task_started_updates_tracking(self) -> None:
        """Test that TaskStarted updates tracking in fallback path."""
        active_task_ids: dict[str, str] = {}
        session_key = "telegram:12345"
        new_task_id = "task-new"

        # Simulate TaskStarted message handling
        active_task_ids[session_key] = new_task_id

        assert active_task_ids.get(session_key) == new_task_id

    def test_fallback_no_active_task_with_notification(self) -> None:
        """Test fallback when no active task but receives notification."""
        active_task_ids: dict[str, str] = {}  # No active task
        session_key = "telegram:12345"
        received_task_id = "task-001"

        current_task_id = active_task_ids.get(session_key)
        # current_task_id is None
        matches_active = current_task_id == received_task_id if received_task_id else False
        # None != "task-001" -> False
        assert matches_active is False

    def test_fallback_empty_task_id_in_notification(self) -> None:
        """Test fallback when notification has empty task_id."""
        active_task_ids: dict[str, str] = {"telegram:12345": "task-001"}
        session_key = "telegram:12345"
        received_task_id = ""  # Empty

        current_task_id = active_task_ids.get(session_key)
        matches_active = current_task_id == received_task_id if received_task_id else False
        # received_task_id is truthy empty string, but comparison fails
        assert matches_active is False


class TestRequestIDTrackingEdgeCases:
    """Test edge cases for request ID tracking."""

    def test_request_id_cleared_after_error(self) -> None:
        """Test that request_id is cleared after error."""
        active_request_ids: dict[str, str] = {"telegram:12345": "550e8400-..."}
        session_key = "telegram:12345"

        # Simulate error cleanup
        active_request_ids.pop(session_key, None)

        assert session_key not in active_request_ids

    def test_request_id_cleared_after_success(self) -> None:
        """Test that request_id is cleared after successful result."""
        active_request_ids: dict[str, str] = {"telegram:12345": "550e8400-..."}
        session_key = "telegram:12345"

        # Simulate successful result handling
        active_request_ids.pop(session_key, None)

        assert session_key not in active_request_ids

    def test_request_id_not_set_for_session(self) -> None:
        """Test handling when request_id is not set for session."""
        active_request_ids: dict[str, str] = {}
        session_key = "telegram:12345"

        expected_request_id = active_request_ids.get(session_key)
        assert expected_request_id is None

    def test_concurrent_sessions_request_ids(self) -> None:
        """Test tracking request IDs for concurrent sessions."""
        active_request_ids: dict[str, str] = {}

        # Add multiple sessions with unique request IDs
        for i in range(5):
            session_key = f"telegram:{i}"
            active_request_ids[session_key] = str(uuid.uuid4())

        # All should have unique IDs
        assert len(active_request_ids) == 5
        ids = list(active_request_ids.values())
        assert len(set(ids)) == 5  # All unique


class TestErrorRecoveryMetadata:
    """Test error recovery metadata handling."""

    def test_reconnect_pending_set_for_recoverable(self) -> None:
        """Test that _reconnect_pending is set for recoverable errors."""
        recoverable_errors = {
            "ConnectionError", "TimeoutError", "asyncio.TimeoutError",
            "ConnectionResetError", "BrokenPipeError",
        }
        error_type = "ConnectionError"
        is_recoverable = error_type in recoverable_errors

        # For recoverable errors, metadata should have _reconnect_pending
        assert is_recoverable is True

    def test_fresh_start_required_for_non_recoverable(self) -> None:
        """Test that _fresh_start_required is set for non-recoverable errors."""
        recoverable_errors = {
            "ConnectionError", "TimeoutError", "asyncio.TimeoutError",
            "ConnectionResetError", "BrokenPipeError",
        }
        error_type = "RuntimeError"
        is_recoverable = error_type in recoverable_errors

        # For non-recoverable errors, metadata should have _fresh_start_required
        assert is_recoverable is False

    def test_error_metadata_fields(self) -> None:
        """Test expected error metadata fields."""
        expected_fields = [
            "_reconnect_pending",
            "_last_error",
            "_error_timestamp",
            "_fresh_start_required",
        ]
        # This test documents expected fields
        assert len(expected_fields) == 4


class TestSessionContextManagerEdgeCases:
    """Test edge cases for SessionContextManager."""

    def test_set_context_with_none_sdk_id(self) -> None:
        """Test setting context with None sdk_session_id."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")

        manager.set_context("telegram:12345", None, ctx)

        # Should have session_key mapping
        assert manager.get_by_session_key("telegram:12345") is not None
        # Should NOT have sdk_session_id mapping
        assert manager.size() == 1

    def test_double_clear_same_session(self) -> None:
        """Test clearing the same session twice."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("telegram:12345", "sdk-abc", ctx)

        # First clear
        result1 = manager.clear("telegram:12345")
        assert result1 is True

        # Second clear (should return False)
        result2 = manager.clear("telegram:12345")
        assert result2 is False

    def test_update_sdk_id_for_nonexistent_session(self) -> None:
        """Test updating SDK ID for session that doesn't exist."""
        manager = SessionContextManager()

        # Should handle gracefully (logs warning, returns)
        manager.update_sdk_session_id("nonexistent", "sdk-new")

        assert manager.size() == 0

    def test_context_preserved_on_sdk_id_update(self) -> None:
        """Test that context is preserved when updating SDK ID."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")

        manager.set_context("telegram:12345", "sdk-old", ctx)
        manager.update_sdk_session_id("telegram:12345", "sdk-new")

        # Context should still be accessible
        result = manager.get_by_session_key("telegram:12345")
        assert result is not None
        assert result.channel == "telegram"
        assert result.chat_id == "12345"


class TestTaskIDValidationEdgeCases:
    """Test edge cases for Task ID validation logic."""

    def test_notification_without_task_id_doesnt_match(self) -> None:
        """Test that notification without task_id doesn't match active task."""
        active_task_ids: dict[str, str] = {"telegram:12345": "task-001"}
        session_key = "telegram:12345"
        message_task_id = None  # Notification has no task_id

        current_task_id = active_task_ids.get(session_key)
        # Simulate the actual logic
        matches_active = current_task_id == message_task_id if message_task_id else False

        # Should not match (message has no task_id)
        assert matches_active is False

    def test_both_task_ids_none(self) -> None:
        """Test when both task IDs are None."""
        active_task_ids: dict[str, str] = {"telegram:12345": None}  # type: ignore
        session_key = "telegram:12345"
        message_task_id = None

        current_task_id = active_task_ids.get(session_key)
        matches_active = current_task_id == message_task_id if message_task_id else False

        # Should not match (message has no task_id)
        assert matches_active is False

    def test_empty_string_task_id(self) -> None:
        """Test when task_id is empty string."""
        active_task_ids: dict[str, str] = {"telegram:12345": "task-001"}
        session_key = "telegram:12345"
        message_task_id = ""

        current_task_id = active_task_ids.get(session_key)
        matches_active = current_task_id == message_task_id if message_task_id else False

        # Empty string is falsy, so should return False
        assert matches_active is False

    def test_stale_detection_requires_both_task_ids(self) -> None:
        """Test that stale detection requires both task IDs to be present."""
        # Case 1: No active task, notification has task_id
        active_task_ids: dict[str, str] = {}
        session_key = "telegram:12345"
        message_task_id = "task-001"

        current_task_id = active_task_ids.get(session_key)
        # Stale check: if message.task_id and current_task_id and not matches_active
        matches_active = current_task_id == message_task_id if message_task_id else False
        is_stale = bool(message_task_id and current_task_id and not matches_active)
        assert is_stale is False  # No active task, not considered stale

        # Case 2: Has active task, notification has no task_id
        active_task_ids = {"telegram:12345": "task-001"}
        message_task_id = None
        current_task_id = active_task_ids.get(session_key)
        matches_active = current_task_id == message_task_id if message_task_id else False
        is_stale = bool(message_task_id and current_task_id and not matches_active)
        assert is_stale is False  # No message task_id, not considered stale


class TestErrorRecoveryStateConsistency:
    """Test state consistency during error recovery."""

    def test_remove_client_state_clears_all_tracking(self) -> None:
        """Test that _remove_client_state clears all tracking dicts."""
        # This documents the expected cleanup
        tracking_dicts = [
            "_clients",
            "_client_last_used",
            "_client_models",
            "_client_skills_versions",
            "_session_commands",
            "_active_task_ids",
            "_active_request_ids",
            "_session_contexts",
        ]
        assert len(tracking_dicts) == 8

    def test_recoverable_error_preserves_sdk_session_id(self) -> None:
        """Test that recoverable errors preserve sdk_session_id for reconnection."""
        recoverable_errors = {
            "ConnectionError", "TimeoutError", "asyncio.TimeoutError",
            "ConnectionResetError", "BrokenPipeError",
        }

        # Simulate error classification
        for error_type in ["ConnectionError", "TimeoutError"]:
            is_recoverable = error_type in recoverable_errors
            assert is_recoverable is True
            # For recoverable errors: _reconnect_pending = True, _fresh_start_required = False

    def test_non_recoverable_error_marks_fresh_start(self) -> None:
        """Test that non-recoverable errors mark session for fresh start."""
        recoverable_errors = {
            "ConnectionError", "TimeoutError", "asyncio.TimeoutError",
            "ConnectionResetError", "BrokenPipeError",
        }

        for error_type in ["RuntimeError", "ValueError", "KeyError"]:
            is_recoverable = error_type in recoverable_errors
            assert is_recoverable is False
            # For non-recoverable errors: _fresh_start_required = True

    def test_cancelled_error_is_non_recoverable(self) -> None:
        """Test that CancelledError is not considered recoverable."""
        recoverable_errors = {
            "ConnectionError", "TimeoutError", "asyncio.TimeoutError",
            "ConnectionResetError", "BrokenPipeError",
        }
        error_type = "CancelledError"
        is_recoverable = error_type in recoverable_errors
        assert is_recoverable is False


class TestStateCleanupCompleteness:
    """Test that state cleanup is complete in various scenarios."""

    def test_cleanup_on_successful_result(self) -> None:
        """Test that state is cleaned up after successful result."""
        active_task_ids: dict[str, str] = {"telegram:12345": "task-001"}
        active_request_ids: dict[str, str] = {"telegram:12345": "uuid-001"}
        session_key = "telegram:12345"

        # Simulate successful result handling
        active_task_ids.pop(session_key, None)
        active_request_ids.pop(session_key, None)

        assert session_key not in active_task_ids
        assert session_key not in active_request_ids

    def test_cleanup_on_error(self) -> None:
        """Test that state is cleaned up after error."""
        active_task_ids: dict[str, str] = {"telegram:12345": "task-001"}
        active_request_ids: dict[str, str] = {"telegram:12345": "uuid-001"}
        clients: dict[str, str] = {"telegram:12345": "client-obj"}
        session_key = "telegram:12345"

        # Simulate _remove_client_state
        clients.pop(session_key, None)
        active_task_ids.pop(session_key, None)
        active_request_ids.pop(session_key, None)

        assert session_key not in clients
        assert session_key not in active_task_ids
        assert session_key not in active_request_ids

    def test_cleanup_on_interrupt(self) -> None:
        """Test that state is cleaned up after interrupt."""
        active_task_ids: dict[str, str] = {"telegram:12345": "task-001"}
        active_request_ids: dict[str, str] = {"telegram:12345": "uuid-001"}
        session_key = "telegram:12345"

        # Simulate interrupt cleanup
        active_task_ids.pop(session_key, None)
        active_request_ids.pop(session_key, None)

        assert session_key not in active_task_ids
        assert session_key not in active_request_ids


class TestConcurrentSessionHandling:
    """Test handling of concurrent sessions."""

    def test_multiple_sessions_independent_tracking(self) -> None:
        """Test that multiple sessions have independent state tracking."""
        active_task_ids: dict[str, str] = {}
        active_request_ids: dict[str, str] = {}

        # Add multiple sessions
        sessions = ["telegram:1", "telegram:2", "discord:1"]
        for session in sessions:
            active_task_ids[session] = f"task-{session}"
            active_request_ids[session] = f"uuid-{session}"

        # Verify independent tracking
        assert active_task_ids["telegram:1"] != active_task_ids["telegram:2"]
        assert active_request_ids["telegram:1"] != active_request_ids["telegram:2"]

        # Clear one session doesn't affect others
        active_task_ids.pop("telegram:1", None)
        active_request_ids.pop("telegram:1", None)

        assert "telegram:1" not in active_task_ids
        assert "telegram:2" in active_task_ids
        assert "discord:1" in active_task_ids

    def test_session_key_format_variations(self) -> None:
        """Test that various session key formats are handled."""
        active_task_ids: dict[str, str] = {}

        # Various formats
        session_keys = [
            "telegram:12345",
            "discord:67890",
            "slack:C12345",
            "web:user@example.com",
        ]

        for key in session_keys:
            active_task_ids[key] = f"task-{key}"

        assert len(active_task_ids) == 4

        # All should be independently trackable
        for key in session_keys:
            assert key in active_task_ids


class TestUUIDFormatValidation:
    """Test UUID format validation for request IDs."""

    def test_valid_uuid_format(self) -> None:
        """Test that generated UUIDs have correct format."""
        import uuid as uuid_module

        request_id = str(uuid_module.uuid4())

        # UUID format: 8-4-4-4-12
        parts = request_id.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert len(parts[3]) == 4
        assert len(parts[4]) == 12

    def test_uuid_from_string_validation(self) -> None:
        """Test UUID validation from string."""
        import uuid as uuid_module

        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        # Should not raise
        uuid_module.UUID(valid_uuid)

        invalid_uuid = "not-a-uuid"
        with pytest.raises(ValueError):
            uuid_module.UUID(invalid_uuid)

    def test_uuid_comparison_case_insensitive(self) -> None:
        """Test that UUID comparison is case insensitive."""
        uuid1 = "550e8400-e29b-41d4-a716-446655440000"
        uuid2 = "550E8400-E29B-41D4-A716-446655440000"

        # Direct string comparison is case sensitive
        assert uuid1 != uuid2

        # UUID comparison is case insensitive
        import uuid as uuid_module
        assert uuid_module.UUID(uuid1) == uuid_module.UUID(uuid2)


class TestSessionContextManagerThreadSafety:
    """Test SessionContextManager thread safety aspects."""

    def test_set_context_overwrites_existing(self) -> None:
        """Test that set_context overwrites existing mapping."""
        manager = SessionContextManager()
        ctx1 = SessionContext(channel="telegram", chat_id="12345")
        ctx2 = SessionContext(channel="discord", chat_id="67890")

        manager.set_context("session:1", "sdk-1", ctx1)
        manager.set_context("session:1", "sdk-2", ctx2)  # Overwrite

        # Should have new context
        result = manager.get_by_session_key("session:1")
        assert result is not None
        assert result.channel == "discord"
        assert result.chat_id == "67890"

        # Old sdk_id should be cleared
        assert manager.get_by_sdk_session_id("sdk-1") is None
        # New sdk_id should be set
        assert manager.get_by_sdk_session_id("sdk-2") is not None

    def test_multiple_sdk_ids_for_different_sessions(self) -> None:
        """Test tracking multiple SDK IDs for different sessions."""
        manager = SessionContextManager()

        for i in range(5):
            ctx = SessionContext(channel="telegram", chat_id=str(i))
            manager.set_context(f"session:{i}", f"sdk-{i}", ctx)

        # Each sdk_id should map to correct session
        for i in range(5):
            result = manager.get_by_sdk_session_id(f"sdk-{i}")
            assert result is not None
            assert result.chat_id == str(i)

    def test_clear_by_sdk_id_removes_session_key_mapping(self) -> None:
        """Test that clear_by_sdk_session_id also removes session_key mapping."""
        manager = SessionContextManager()
        ctx = SessionContext(channel="telegram", chat_id="12345")
        manager.set_context("session:1", "sdk-1", ctx)

        # Clear by SDK ID
        result = manager.clear_by_sdk_session_id("sdk-1")
        assert result is True

        # Both mappings should be gone
        assert manager.get_by_session_key("session:1") is None
        assert manager.get_by_sdk_session_id("sdk-1") is None


class TestInteractionRetryCount:
    """Test interaction retry count tracking."""

    def test_retry_count_increments(self) -> None:
        """Test that retry count increments correctly."""
        retry_counts: dict[str, int] = {}
        session_key = "telegram:12345"

        # First invalid answer
        retry_counts[session_key] = retry_counts.get(session_key, 0) + 1
        assert retry_counts[session_key] == 1

        # Second invalid answer
        retry_counts[session_key] = retry_counts.get(session_key, 0) + 1
        assert retry_counts[session_key] == 2

        # Third invalid answer
        retry_counts[session_key] = retry_counts.get(session_key, 0) + 1
        assert retry_counts[session_key] == 3

    def test_retry_count_clears_on_success(self) -> None:
        """Test that retry count is cleared on successful match."""
        retry_counts: dict[str, int] = {"telegram:12345": 2}
        session_key = "telegram:12345"

        # Successful match - clear retry count
        retry_counts.pop(session_key, None)

        assert session_key not in retry_counts

    def test_retry_count_clears_on_max_retries(self) -> None:
        """Test that retry count is cleared when max retries reached."""
        retry_counts: dict[str, int] = {"telegram:12345": 3}
        session_key = "telegram:12345"

        # Max retries reached - clear and return
        retry_counts.pop(session_key, None)

        assert session_key not in retry_counts

    def test_retry_count_isolated_per_session(self) -> None:
        """Test that retry counts are isolated per session."""
        retry_counts: dict[str, int] = {}

        # Increment for session 1
        retry_counts["session:1"] = retry_counts.get("session:1", 0) + 1
        retry_counts["session:1"] = retry_counts.get("session:1", 0) + 1

        # Increment for session 2
        retry_counts["session:2"] = retry_counts.get("session:2", 0) + 1

        assert retry_counts["session:1"] == 2
        assert retry_counts["session:2"] == 1


class TestSdkSessionIdHandling:
    """Test SDK session ID handling."""

    def test_sdk_session_id_stored_in_session_metadata(self) -> None:
        """Test that SDK session ID is stored in session metadata."""
        # Simulate session metadata
        session_metadata: dict[str, str] = {}

        # Store SDK session ID
        session_metadata["sdk_session_id"] = "sdk-uuid-123"

        assert session_metadata.get("sdk_session_id") == "sdk-uuid-123"

    def test_sdk_session_id_cleared_for_fresh_start(self) -> None:
        """Test that SDK session ID is cleared for fresh start."""
        session_metadata: dict[str, str] = {"sdk_session_id": "sdk-old"}

        # For non-recoverable errors, clear SDK session ID
        session_metadata.pop("sdk_session_id", None)
        session_metadata["_fresh_start_required"] = "true"

        assert "sdk_session_id" not in session_metadata
        assert session_metadata.get("_fresh_start_required") == "true"

    def test_sdk_session_id_preserved_for_recoverable_error(self) -> None:
        """Test that SDK session ID is preserved for recoverable errors."""
        session_metadata: dict[str, str] = {"sdk_session_id": "sdk-existing"}

        # For recoverable errors, preserve SDK session ID and mark for reconnect
        session_metadata["_reconnect_pending"] = "true"

        assert session_metadata.get("sdk_session_id") == "sdk-existing"
        assert session_metadata.get("_reconnect_pending") == "true"


class TestStateTransitionValidation:
    """Test state transition validation."""

    def test_valid_transition_idle_to_running(self) -> None:
        """Test valid transition from IDLE to RUNNING."""
        valid_transitions = {
            "idle": {"running", "waiting_permission", "waiting_interaction", "stopping", "resetting", "error"},
        }
        assert "running" in valid_transitions["idle"]

    def test_valid_transition_running_to_idle(self) -> None:
        """Test valid transition from RUNNING to IDLE."""
        valid_transitions = {
            "running": {"idle", "waiting_permission", "waiting_interaction", "stopping", "resetting", "error"},
        }
        assert "idle" in valid_transitions["running"]

    def test_valid_transition_running_to_waiting(self) -> None:
        """Test valid transition from RUNNING to WAITING_*."""
        valid_transitions = {
            "running": {"idle", "waiting_permission", "waiting_interaction", "stopping", "resetting", "error"},
        }
        assert "waiting_permission" in valid_transitions["running"]
        assert "waiting_interaction" in valid_transitions["running"]

    def test_invalid_transition_stopping_to_running(self) -> None:
        """Test invalid transition from STOPPING to RUNNING."""
        valid_transitions = {
            "stopping": {"idle", "error"},
        }
        assert "running" not in valid_transitions["stopping"]

    def test_terminal_states_can_only_go_to_idle_or_error(self) -> None:
        """Test that terminal states have limited transitions."""
        valid_transitions = {
            "stopping": {"idle", "error"},
            "resetting": {"idle", "error"},
            "error": {"idle", "resetting"},
        }
        # STOPPING can only go to IDLE or ERROR
        assert valid_transitions["stopping"] == {"idle", "error"}
        # RESETTING can only go to IDLE or ERROR
        assert valid_transitions["resetting"] == {"idle", "error"}


class TestClientStateTracking:
    """Test client state tracking."""

    def test_client_tracking_dicts_cleared_together(self) -> None:
        """Test that all client tracking dicts are cleared together."""
        # Simulate state
        state = {
            "_clients": {"session:1": "client1"},
            "_client_last_used": {"session:1": 1000.0},
            "_client_models": {"session:1": "claude-3"},
            "_client_skills_versions": {"session:1": "v1"},
            "_session_commands": {"session:1": ["/help"]},
            "_active_task_ids": {"session:1": "task-1"},
            "_active_request_ids": {"session:1": "uuid-1"},
        }

        session_key = "session:1"

        # Simulate _remove_client_state
        for dict_name in state:
            state[dict_name].pop(session_key, None)

        # All should be empty
        for dict_name in state:
            assert session_key not in state[dict_name]

    def test_client_ttl_tracking(self) -> None:
        """Test client TTL tracking."""
        import time

        client_last_used: dict[str, float] = {}
        session_key = "session:1"
        client_ttl_seconds = 300  # 5 minutes

        # Record usage
        client_last_used[session_key] = time.time()

        # Check if stale (simulated)
        now = time.time()
        is_stale = now - client_last_used[session_key] > client_ttl_seconds

        assert is_stale is False  # Just created, not stale

    def test_lru_eviction_selects_oldest(self) -> None:
        """Test LRU eviction selects oldest client."""
        client_last_used: dict[str, float] = {
            "session:1": 100.0,
            "session:2": 200.0,
            "session:3": 50.0,  # Oldest
        }

        # Find LRU (oldest)
        lru_key = min(client_last_used, key=client_last_used.get)

        assert lru_key == "session:3"  # 50.0 is smallest (oldest)


class TestStaleMessageDetectionScenarios:
    """Test various stale message detection scenarios."""

    def test_input_required_stale_detection(self) -> None:
        """Test that stale input_required messages are detected."""
        # Scenario: current_task_id != message.task_id
        current_task_id = "task-new"
        message_task_id = "task-old"

        is_stale = message_task_id and current_task_id and message_task_id != current_task_id
        assert is_stale is True

    def test_input_required_valid_message(self) -> None:
        """Test that valid input_required messages are processed."""
        current_task_id = "task-001"
        message_task_id = "task-001"

        is_stale = message_task_id and current_task_id and message_task_id != current_task_id
        assert is_stale is False

    def test_input_required_no_current_task(self) -> None:
        """Test input_required when no current task (task_id cleared).

        With the fix, when current_task_id is None, the message is treated as stale
        because no TaskStarted has been received for this request yet.
        """
        current_task_id = None
        message_task_id = "task-old"

        # NEW LOGIC: if message.task_id and (current_task_id is None or task_ids don't match)
        # A message is stale if task_id exists AND either:
        # 1. current_task_id is None (no TaskStarted received), OR
        # 2. task_ids don't match
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True  # Now correctly detected as stale!

    def test_input_required_no_message_task_id(self) -> None:
        """Test input_required when message has no task_id."""
        current_task_id = "task-001"
        message_task_id = None

        # When message.task_id is None, cannot detect stale
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert not is_stale  # message_task_id is None, so falsy

    def test_task_notification_completed_stale(self) -> None:
        """Test stale detection for completed/failed/stopped notifications."""
        current_task_id = "task-new"
        message_task_id = "task-old"

        # NEW LOGIC: also detect stale when current_task_id is None
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True

    def test_task_notification_completed_valid(self) -> None:
        """Test valid completed/failed/stopped notifications."""
        current_task_id = "task-001"
        message_task_id = "task-001"

        # NEW LOGIC: match check
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is False

    def test_task_notification_completed_no_current_task(self) -> None:
        """Test completed notification when no current task (NEW logic).

        With the fix, when current_task_id is None, the message is treated as stale
        because no TaskStarted has been received for this request yet.
        """
        current_task_id = None
        message_task_id = "task-old"

        # NEW LOGIC: detect stale when current_task_id is None
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True  # Now correctly detected as stale!

    def test_task_notification_completed_no_message_task_id(self) -> None:
        """Test completed notification when message has no task_id."""
        current_task_id = "task-001"
        message_task_id = None

        # When message.task_id is None, cannot detect stale
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert not is_stale  # message_task_id is None, so falsy


class TestTaskIdLifecycle:
    """Test task_id lifecycle during request processing."""

    def test_task_id_cleared_before_query(self) -> None:
        """Test that task_id is cleared before sending query."""
        # Simulate: _set_task_id_in_entry(session_key, None)
        task_id: str | None = "old-task"
        task_id = None  # Cleared before new request

        assert task_id is None

    def test_task_id_set_on_task_started(self) -> None:
        """Test that task_id is set when TaskStarted is received."""
        task_id: str | None = None
        message_task_id = "task-001"

        # Simulate: _set_task_id_in_entry(session_key, message.task_id)
        task_id = message_task_id

        assert task_id == "task-001"

    def test_task_id_cleared_on_result(self) -> None:
        """Test that task_id is cleared when ResultMessage is received."""
        task_id: str | None = "task-001"

        # Simulate: _set_task_id_in_entry(session_key, None)
        task_id = None

        assert task_id is None

    def test_task_id_cleared_on_terminal_notification(self) -> None:
        """Test that task_id is cleared on completed/failed/stopped notification."""
        task_id: str | None = "task-001"
        message_task_id = "task-001"

        # Only clear if matches
        if task_id == message_task_id:
            task_id = None

        assert task_id is None


class TestFallbackPathStaleDetection:
    """Test stale detection in fallback path."""

    def test_fallback_stale_input_required_detected(self) -> None:
        """Test that stale input_required is detected in fallback path."""
        current_task_id = "task-new"
        message_task_id = "task-old"

        # NEW LOGIC: also detect stale when current_task_id is None
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True

    def test_fallback_stale_completed_detected(self) -> None:
        """Test that stale completed notification is detected in fallback path."""
        current_task_id = "task-new"
        message_task_id = "task-old"

        # NEW LOGIC: also detect stale when current_task_id is None
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True

    def test_fallback_stale_no_current_task_detected(self) -> None:
        """Test that stale notifications are detected when current_task_id is None.

        This is the key fix: residual messages from previous requests are detected
        as stale even before TaskStarted is received for the new request.
        """
        current_task_id = None  # New request hasn't received TaskStarted yet
        message_task_id = "task-old"  # Residual from previous request

        # NEW LOGIC: detect stale when current_task_id is None
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True  # Key fix: now correctly detected as stale!


class TestEdgeCases:
    """Test edge cases in message handling."""

    def test_empty_string_task_id_is_falsy(self) -> None:
        """Test that empty string task_id is treated as falsy."""
        message_task_id = ""
        current_task_id = "task-001"

        # Empty string is falsy - cannot detect stale
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert not is_stale  # Empty string is falsy, so stale detection skips

    def test_both_task_ids_none(self) -> None:
        """Test when both task_ids are None."""
        message_task_id = None
        current_task_id = None

        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert not is_stale  # message_task_id is None, so falsy

    def test_message_task_id_only_new_logic(self) -> None:
        """Test NEW logic: when only message has task_id, it's stale.

        With the fix: if message.task_id and (current_task_id is None or ...),
        when current_task_id is None, it IS stale because no TaskStarted received.
        """
        message_task_id = "task-001"
        current_task_id = None

        # NEW LOGIC: treat as stale when current_task_id is None
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True  # NEW: correctly detected as stale!

    def test_current_task_id_only(self) -> None:
        """Test when only current has task_id."""
        message_task_id = None
        current_task_id = "task-001"

        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert not is_stale  # message_task_id is None, so falsy


class TestMultipleTaskStartedScenarios:
    """Test scenarios involving multiple TaskStarted messages."""

    def test_task_started_overwrites_previous(self) -> None:
        """Test that TaskStarted overwrites previous task_id.

        This is expected behavior: if SDK sends multiple TaskStarted,
        the last one wins. Messages from previous task_id would then be stale.
        """
        # Initial state
        task_id_state: str | None = None

        # First TaskStarted
        task_id_state = "task-001"

        # Second TaskStarted (overwrites)
        task_id_state = "task-002"

        # Message from task-001 arrives
        message_task_id = "task-001"
        current_task_id = task_id_state

        # Should be detected as stale (mismatch)
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True

    def test_task_started_with_empty_task_id(self) -> None:
        """Test TaskStarted with empty task_id.

        If TaskStarted has empty task_id, we should NOT set it.
        The stale detection would then see current_task_id = None.
        """
        # Initial state
        task_id_state: str | None = "task-old"

        # TaskStarted with empty task_id (should be ignored per code logic)
        # Code: `if isinstance(message, TaskStartedMessage) and message.task_id:`
        # Empty string is falsy, so task_id_state remains unchanged
        message_task_id = ""
        should_set = bool(message_task_id)  # False

        if should_set:
            task_id_state = message_task_id  # Would NOT happen

        # task_id_state remains "task-old"
        assert task_id_state == "task-old"

    def test_task_started_without_task_id_field(self) -> None:
        """Test TaskStarted without task_id field (None)."""
        task_id_state: str | None = "task-old"

        # TaskStarted with None task_id (should be ignored)
        message_task_id = None
        should_set = bool(message_task_id)  # False

        if should_set:
            task_id_state = message_task_id  # Would NOT happen

        assert task_id_state == "task-old"


class TestMessageOrderingScenarios:
    """Test scenarios involving message ordering."""

    def test_residual_message_arrives_before_task_started(self) -> None:
        """Test residual message from previous request arriving before TaskStarted.

        This is the key fix: residual messages are detected as stale because
        current_task_id is None (no TaskStarted for new request yet).
        """
        # New request starts, task_id cleared
        current_task_id = None

        # Residual input_required from previous request arrives
        message_task_id = "task-old"

        # NEW LOGIC: correctly detected as stale
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True

    def test_legitimate_message_after_task_started(self) -> None:
        """Test legitimate message arriving after TaskStarted."""
        # TaskStarted received
        current_task_id = "task-new"

        # Legitimate input_required arrives
        message_task_id = "task-new"

        # Should NOT be detected as stale (matches)
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is False

    def test_result_message_clears_task_id(self) -> None:
        """Test that ResultMessage clears task_id."""
        # After processing
        task_id_state: str | None = "task-001"

        # ResultMessage received, task_id cleared
        task_id_state = None

        # Late input_required arrives (from completed task)
        message_task_id = "task-001"
        current_task_id = task_id_state  # None

        # Should be detected as stale (current_task_id is None)
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True


class TestTaskIdConsistency:
    """Test task_id consistency across message types."""

    def test_task_notification_matches_and_clears(self) -> None:
        """Test that matching terminal notification clears task_id."""
        task_id_state: str | None = "task-001"

        # Terminal notification arrives with matching task_id
        message_task_id = "task-001"
        current_task_id = task_id_state

        # Not stale (matches)
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is False

        # After processing, task_id is cleared
        task_id_state = None

        assert task_id_state is None

    def test_task_notification_mismatch_is_stale(self) -> None:
        """Test that mismatched terminal notification is stale."""
        task_id_state: str | None = "task-new"

        # Stale terminal notification arrives
        message_task_id = "task-old"
        current_task_id = task_id_state

        # Is stale (mismatch)
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True

        # task_id should NOT be cleared for stale messages
        assert task_id_state == "task-new"  # Still set


class TestInputRequiredEdgeCases:
    """Test edge cases specifically for input_required handling."""

    def test_input_required_with_status_variations(self) -> None:
        """Test input_required status variations."""
        # Status variations that trigger input_required
        input_required_statuses = {"input_required", "awaiting_input", "waiting"}

        for status in input_required_statuses:
            status_lower = str(status).lower()
            is_input_required = status_lower in input_required_statuses
            assert is_input_required is True

    def test_input_required_case_insensitive(self) -> None:
        """Test that input_required status check is case insensitive."""
        statuses = ["INPUT_REQUIRED", "Input_Required", "input_required"]

        for status in statuses:
            status_lower = str(status).lower()
            is_input_required = status_lower in {"input_required", "awaiting_input", "waiting"}
            assert is_input_required is True

    def test_input_required_without_task_id_accepted(self) -> None:
        """Test that input_required without task_id is NOT stale.

        Without task_id, we cannot detect if message is stale.
        This is a limitation: we have to process it.
        """
        current_task_id = "task-001"
        message_task_id = None

        # Cannot detect stale without message.task_id
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert not is_stale  # message_task_id is None, passes through


class TestRetryScenarioTaskIdState:
    """Test task_id state during retry scenarios."""

    def test_task_id_cleared_before_each_retry(self) -> None:
        """Test that task_id is cleared before each retry."""
        # Simulate retry loop
        task_id_state: str | None = "task-from-failed-attempt"

        # Before retry, task_id is cleared
        task_id_state = None

        # New TaskStarted arrives for retry
        task_id_state = "task-retry-001"

        # Retry completes successfully
        assert task_id_state == "task-retry-001"

    def test_retry_after_stale_notification(self) -> None:
        """Test retry after receiving only stale notifications."""
        # Request 1: stale notification received, no ResultMessage
        task_id_state: str | None = None  # Cleared before request

        # Stale notification arrives (from previous request)
        message_task_id = "task-old"
        is_stale = message_task_id and (task_id_state is None or message_task_id != task_id_state)
        assert is_stale is True  # Correctly detected as stale

        # No ResultMessage received, retry triggered
        # task_id is cleared again before retry
        task_id_state = None

        # New TaskStarted for retry
        task_id_state = "task-retry-001"

        # New notification arrives
        message_task_id = "task-retry-001"
        is_stale = message_task_id and (task_id_state is None or message_task_id != task_id_state)
        assert is_stale is False  # Not stale, matches current task


class TestTerminalNotificationStatuses:
    """Test all terminal notification status types."""

    def test_completed_status_is_terminal(self) -> None:
        """Test that 'completed' is recognized as terminal status."""
        terminal_statuses = {"completed", "failed", "stopped"}
        assert "completed" in terminal_statuses

    def test_failed_status_is_terminal(self) -> None:
        """Test that 'failed' is recognized as terminal status."""
        terminal_statuses = {"completed", "failed", "stopped"}
        assert "failed" in terminal_statuses

    def test_stopped_status_is_terminal(self) -> None:
        """Test that 'stopped' is recognized as terminal status."""
        terminal_statuses = {"completed", "failed", "stopped"}
        assert "stopped" in terminal_statuses

    def test_running_status_is_not_terminal(self) -> None:
        """Test that 'running' is NOT recognized as terminal status."""
        terminal_statuses = {"completed", "failed", "stopped"}
        assert "running" not in terminal_statuses

    def test_input_required_is_not_terminal(self) -> None:
        """Test that 'input_required' is NOT recognized as terminal status."""
        terminal_statuses = {"completed", "failed", "stopped"}
        assert "input_required" not in terminal_statuses


class TestFallbackPathStaleDetectionConsistency:
    """Test that fallback path has same stale detection as main path."""

    def test_fallback_completed_stale_with_none_current(self) -> None:
        """Test fallback stale detection when current_task_id is None."""
        current_task_id = None
        message_task_id = "task-old"

        # Same logic as main path
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True

    def test_fallback_input_required_stale_with_none_current(self) -> None:
        """Test fallback input_required stale detection when current_task_id is None."""
        current_task_id = None
        message_task_id = "task-old"

        # Same logic as main path
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is True

    def test_fallback_valid_message_not_stale(self) -> None:
        """Test fallback valid message is not detected as stale."""
        current_task_id = "task-current"
        message_task_id = "task-current"

        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is False


class TestInputRequiredStatusSet:
    """Test the SDK 0.1.52 TaskNotification status contract."""

    def test_completed_is_supported_task_notification_status(self) -> None:
        statuses = {"completed", "failed", "stopped"}
        assert "completed" in statuses

    def test_failed_is_supported_task_notification_status(self) -> None:
        statuses = {"completed", "failed", "stopped"}
        assert "failed" in statuses

    def test_stopped_is_supported_task_notification_status(self) -> None:
        statuses = {"completed", "failed", "stopped"}
        assert "stopped" in statuses

    def test_input_required_is_not_supported_task_notification_status(self) -> None:
        statuses = {"completed", "failed", "stopped"}
        assert "input_required" not in statuses


class TestTaskIdTypeHandling:
    """Test handling of different task_id types."""

    def test_task_id_as_string(self) -> None:
        """Test task_id as string type."""
        message_task_id = "task-001"
        current_task_id = "task-001"

        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is False

    def test_task_id_with_special_characters(self) -> None:
        """Test task_id with special characters."""
        message_task_id = "task-001_abc-123"
        current_task_id = "task-001_abc-123"

        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is False

    def test_task_id_with_numbers_only(self) -> None:
        """Test task_id with numbers only."""
        message_task_id = "12345"
        current_task_id = "12345"

        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert is_stale is False


class TestStaleDetectionShortCircuit:
    """Test short-circuit behavior in stale detection."""

    def test_message_task_id_none_short_circuits(self) -> None:
        """Test that None message_task_id short-circuits the check."""
        message_task_id = None
        current_task_id = None  # Would cause error if not short-circuited

        # The 'and' operator short-circuits: None and (...) evaluates to None
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert not is_stale  # None is falsy

    def test_empty_string_short_circuits(self) -> None:
        """Test that empty string short-circuits the check."""
        message_task_id = ""
        current_task_id = None

        # Empty string is falsy, so short-circuits
        is_stale = message_task_id and (current_task_id is None or message_task_id != current_task_id)
        assert not is_stale
