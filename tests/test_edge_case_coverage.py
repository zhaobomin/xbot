"""Additional edge case tests for state consistency and exception handling.

These tests cover scenarios identified by the Code Review Checklist:
1. Exception handling branches
2. State consistency across multiple dicts
3. All exit paths have proper cleanup
"""

import asyncio
import json
import time
import pytest

from xbot.bus.queue import (
    MessageBus,
    PermissionRequest,
    PermissionResponse,
    InteractionRequest,
    InteractionResponse,
    REQUEST_TIMEOUT_SECONDS,
)
from xbot.cron.service import CronService, _compute_next_run, _now_ms
from xbot.cron.types import CronSchedule


class TestExceptionHandlingBranches:
    """Test that exception handling preserves correct state."""

    def test_cron_json_truncated_file(self, tmp_path) -> None:
        """Truncated JSON (partial write) should not be overwritten."""
        store_path = tmp_path / "cron" / "jobs.json"
        store_path.parent.mkdir(parents=True)

        # Simulate truncated file (valid JSON start but incomplete)
        truncated = '{"version": 1, "jobs": [{"id": "test", "name": "Test'
        store_path.write_text(truncated, encoding="utf-8")

        service = CronService(store_path)
        service._load_store()

        # Should have _load_failed = True
        assert service._load_failed is True

        # File should remain unchanged
        assert store_path.read_text() == truncated

    def test_cron_empty_file(self, tmp_path) -> None:
        """Empty file should be handled gracefully."""
        store_path = tmp_path / "cron" / "jobs.json"
        store_path.parent.mkdir(parents=True)
        store_path.write_text("", encoding="utf-8")

        service = CronService(store_path)
        store = service._load_store()

        # Should have empty store but not failed (empty is valid-ish)
        assert store.jobs == []
        # Empty file is actually an error (not valid JSON)
        assert service._load_failed is True

    def test_cron_missing_jobs_key(self, tmp_path) -> None:
        """JSON without jobs key should work."""
        store_path = tmp_path / "cron" / "jobs.json"
        store_path.parent.mkdir(parents=True)

        # Valid JSON but missing jobs array
        store_path.write_text('{"version": 1}', encoding="utf-8")

        service = CronService(store_path)
        store = service._load_store()

        # Should work, jobs default to empty
        assert store.jobs == []
        assert service._load_failed is False


class TestStateConsistencyAcrossDicts:
    """Test that all related dicts are kept in sync."""

    @pytest.mark.asyncio
    async def test_permission_all_dicts_synced_on_publish(self) -> None:
        """After publish, all tracking dicts should have the request."""
        bus = MessageBus()

        req = PermissionRequest(
            request_id="sync_test",
            session_key="session_sync",
            channel="telegram",
            chat_id="chat_1",
            tool_name="tool",
            tool_input={},
            message="Sync test",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        async with bus._permission_lock:
            # All 4 dicts should have the request
            assert "sync_test" in bus._permission_requests
            assert "sync_test" in bus._pending_permission_responses
            assert bus._session_pending_requests.get("session_sync") == "sync_test"
            # _permission_results is set on response, not publish

    @pytest.mark.asyncio
    async def test_permission_all_dicts_cleaned_on_complete(self) -> None:
        """After complete response cycle, all dicts should be clean."""
        bus = MessageBus()

        req = PermissionRequest(
            request_id="complete_test",
            session_key="session_complete",
            channel="telegram",
            chat_id="chat_1",
            tool_name="tool",
            tool_input={},
            message="Complete test",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        # Start waiting
        wait_task = asyncio.create_task(
            bus.wait_permission_response("complete_test", timeout=1.0)
        )
        await asyncio.sleep(0.05)

        # Submit response
        await bus.submit_permission_response(PermissionResponse(
            request_id="complete_test",
            session_key="session_complete",
            decision="allow",
        ))

        await wait_task

        # All dicts should be empty now
        async with bus._permission_lock:
            assert "complete_test" not in bus._permission_requests
            assert "complete_test" not in bus._pending_permission_responses
            assert "complete_test" not in bus._permission_results
            assert "session_complete" not in bus._session_pending_requests

    @pytest.mark.asyncio
    async def test_interaction_all_dicts_synced_on_publish(self) -> None:
        """After publish interaction, all tracking dicts should have it."""
        bus = MessageBus()

        req = InteractionRequest(
            request_id="int_sync",
            session_key="session_int_sync",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Sync test",
        )
        await bus.publish_interaction_request(req)
        await bus.consume_outbound()

        async with bus._interaction_lock:
            assert "int_sync" in bus._interaction_requests
            assert "int_sync" in bus._pending_interaction_responses
            assert bus._session_pending_interactions.get("session_int_sync") == "int_sync"

    @pytest.mark.asyncio
    async def test_interaction_all_dicts_cleaned_on_complete(self) -> None:
        """After complete interaction cycle, all dicts should be clean."""
        bus = MessageBus()

        req = InteractionRequest(
            request_id="int_complete",
            session_key="session_int_complete",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Complete test",
        )
        await bus.publish_interaction_request(req)
        await bus.consume_outbound()

        wait_task = asyncio.create_task(
            bus.wait_interaction_response("int_complete", timeout=1.0)
        )
        await asyncio.sleep(0.05)

        await bus.submit_interaction_response(InteractionResponse(
            request_id="int_complete",
            session_key="session_int_complete",
            action="reply",
            content="Answer",
        ))

        await wait_task

        async with bus._interaction_lock:
            assert "int_complete" not in bus._interaction_requests
            assert "int_complete" not in bus._pending_interaction_responses
            assert "int_complete" not in bus._interaction_results
            assert "session_int_complete" not in bus._session_pending_interactions


class TestAllExitPathsCleanup:
    """Test that all possible exit paths properly clean up state."""

    @pytest.mark.asyncio
    async def test_permission_exit_on_success(self) -> None:
        """Success path should clean up."""
        bus = MessageBus()

        req = PermissionRequest(
            request_id="exit_success",
            session_key="session_exit_success",
            channel="telegram",
            chat_id="chat_1",
            tool_name="tool",
            tool_input={},
            message="Exit test",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        wait_task = asyncio.create_task(
            bus.wait_permission_response("exit_success", timeout=1.0)
        )
        await asyncio.sleep(0.05)

        await bus.submit_permission_response(PermissionResponse(
            request_id="exit_success",
            session_key="session_exit_success",
            decision="allow",
        ))

        result = await wait_task
        assert result.decision == "allow"

        async with bus._permission_lock:
            assert len(bus._permission_requests) == 0

    @pytest.mark.asyncio
    async def test_permission_exit_on_timeout(self) -> None:
        """Timeout path should clean up."""
        bus = MessageBus()

        req = PermissionRequest(
            request_id="exit_timeout",
            session_key="session_exit_timeout",
            channel="telegram",
            chat_id="chat_1",
            tool_name="tool",
            tool_input={},
            message="Exit test",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        result = await bus.wait_permission_response("exit_timeout", timeout=0.1)
        assert result.decision == "deny"

        async with bus._permission_lock:
            assert len(bus._permission_requests) == 0

    @pytest.mark.asyncio
    async def test_permission_exit_on_supersede(self) -> None:
        """Supersede path should clean up old request."""
        bus = MessageBus()

        req1 = PermissionRequest(
            request_id="exit_supersede_1",
            session_key="session_supersede",
            channel="telegram",
            chat_id="chat_1",
            tool_name="tool",
            tool_input={},
            message="First",
        )
        await bus.publish_permission_request(req1)
        await bus.consume_outbound()

        # Second request supersedes first
        req2 = PermissionRequest(
            request_id="exit_supersede_2",
            session_key="session_supersede",
            channel="telegram",
            chat_id="chat_1",
            tool_name="tool",
            tool_input={},
            message="Second",
        )
        await bus.publish_permission_request(req2)
        await bus.consume_outbound()

        # First should be auto-denied
        result = await bus.wait_permission_response("exit_supersede_1", timeout=0.1)
        assert result.decision == "deny"

        # First request should be cleaned from _permission_requests
        async with bus._permission_lock:
            assert "exit_supersede_1" not in bus._permission_requests
            assert "exit_supersede_2" in bus._permission_requests

        # Clean up second
        await bus.wait_permission_response("exit_supersede_2", timeout=0.1)
        async with bus._permission_lock:
            assert len(bus._permission_requests) == 0

    @pytest.mark.asyncio
    async def test_interaction_exit_on_timeout(self) -> None:
        """Interaction timeout should clean up."""
        bus = MessageBus()

        req = InteractionRequest(
            request_id="int_exit_timeout",
            session_key="session_int_exit_timeout",
            channel="telegram",
            chat_id="chat_1",
            kind="question",
            prompt="Timeout test",
        )
        await bus.publish_interaction_request(req)
        await bus.consume_outbound()

        result = await bus.wait_interaction_response("int_exit_timeout", timeout=0.1)
        assert result.action == "cancel"

        async with bus._interaction_lock:
            assert len(bus._interaction_requests) == 0


class TestComputeNextRunEdgeCases:
    """Test _compute_next_run edge cases."""

    def test_at_kind_past_time(self) -> None:
        """at kind with past time should return None."""
        past = _now_ms() - 10000
        schedule = CronSchedule(kind="at", at_ms=past)
        result = _compute_next_run(schedule, _now_ms())
        assert result is None

    def test_at_kind_future_time(self) -> None:
        """at kind with future time should return that time."""
        future = _now_ms() + 3600000
        schedule = CronSchedule(kind="at", at_ms=future)
        result = _compute_next_run(schedule, _now_ms())
        assert result == future

    def test_every_kind_zero(self) -> None:
        """every kind with zero should return None."""
        schedule = CronSchedule(kind="every", every_ms=0)
        result = _compute_next_run(schedule, _now_ms())
        assert result is None

    def test_every_kind_negative(self) -> None:
        """every kind with negative should return None."""
        schedule = CronSchedule(kind="every", every_ms=-1000)
        result = _compute_next_run(schedule, _now_ms())
        assert result is None

    def test_every_kind_positive(self) -> None:
        """every kind with positive should return next run."""
        schedule = CronSchedule(kind="every", every_ms=60000)
        now = _now_ms()
        result = _compute_next_run(schedule, now)
        assert result is not None
        assert result >= now

    def test_cron_kind_invalid_expr(self) -> None:
        """cron kind with invalid expr should return None."""
        schedule = CronSchedule(kind="cron", expr="invalid cron", tz=None)
        result = _compute_next_run(schedule, _now_ms())
        assert result is None

    def test_cron_kind_valid_expr(self) -> None:
        """cron kind with valid expr should return next run."""
        schedule = CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancouver")
        result = _compute_next_run(schedule, _now_ms())
        assert result is not None
        assert result > _now_ms()

    def test_unknown_kind(self) -> None:
        """unknown kind should return None."""
        schedule = CronSchedule(kind="unknown", at_ms=None)
        result = _compute_next_run(schedule, _now_ms())
        assert result is None


class TestPoolCapacityEdgeCases:
    """Test request pool capacity edge cases."""

    @pytest.mark.asyncio
    async def test_pool_at_exact_capacity(self) -> None:
        """Pool at exact capacity should work."""
        bus = MessageBus(max_pending_requests=3)

        for i in range(3):
            req = PermissionRequest(
                request_id=f"cap_{i}",
                session_key=f"session_cap_{i}",
                channel="telegram",
                chat_id="chat_1",
                tool_name="tool",
                tool_input={},
                message=f"Request {i}",
            )
            await bus.publish_permission_request(req)
            await bus.consume_outbound()

        async with bus._permission_lock:
            assert len(bus._permission_requests) == 3

    @pytest.mark.asyncio
    async def test_pool_cleanup_makes_room(self) -> None:
        """Cleaned up requests should make room for new ones."""
        bus = MessageBus(max_pending_requests=2)

        # Fill pool
        for i in range(2):
            req = PermissionRequest(
                request_id=f"room_{i}",
                session_key=f"session_room_{i}",
                channel="telegram",
                chat_id="chat_1",
                tool_name="tool",
                tool_input={},
                message=f"Request {i}",
            )
            await bus.publish_permission_request(req)
            await bus.consume_outbound()

        # Clean up one
        await bus.wait_permission_response("room_0", timeout=0.1)

        # Should be able to add another
        req = PermissionRequest(
            request_id="room_new",
            session_key="session_room_new",
            channel="telegram",
            chat_id="chat_1",
            tool_name="tool",
            tool_input={},
            message="New request",
        )
        await bus.publish_permission_request(req)
        await bus.consume_outbound()

        async with bus._permission_lock:
            assert len(bus._permission_requests) == 2
            assert "room_new" in bus._permission_requests