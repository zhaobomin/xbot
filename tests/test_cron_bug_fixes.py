"""Regression tests for cron service bug fixes.

Tests for:
1. Bug 1: Corrupted jobs.json should not be overwritten with empty store
2. Bug 4: Invalid schedules should be rejected at add time
3. Timer should be re-armed even when save fails
"""

import json
from unittest.mock import MagicMock
import pytest

from xbot.cron.service import CronService, _validate_schedule_for_add, _now_ms
from xbot.cron.types import CronSchedule, CronStore


class TestCronCorruptedFileProtection:
    """Test that corrupted jobs.json is not overwritten with empty data."""

    def test_corrupted_json_not_overwritten_on_start(self, tmp_path) -> None:
        """Bug 1: Corrupted JSON file should not be overwritten on start()."""
        store_path = tmp_path / "cron" / "jobs.json"
        store_path.parent.mkdir(parents=True)

        # Write corrupted JSON
        store_path.write_text("{ corrupted json", encoding="utf-8")

        service = CronService(store_path)
        # _load_store should fail but not overwrite
        store = service._load_store()

        # Should have empty in-memory store
        assert store.jobs == []

        # But the file should still contain the corrupted data
        assert "{ corrupted json" in store_path.read_text()

        # And _load_failed should be True
        assert service._load_failed is True

    def test_corrupted_json_prevents_save(self, tmp_path) -> None:
        """Bug 1: Save should be skipped when load failed."""
        store_path = tmp_path / "cron" / "jobs.json"
        store_path.parent.mkdir(parents=True)

        # Write corrupted JSON
        store_path.write_text("{ corrupted", encoding="utf-8")

        service = CronService(store_path)
        service._load_store()

        # Try to save - should be skipped
        service._save_store()

        # File should still contain corrupted data, not empty jobs
        assert "{ corrupted" in store_path.read_text()

    def test_load_failed_cleared_on_successful_save(self, tmp_path) -> None:
        """After successful save, _load_failed should be cleared."""
        store_path = tmp_path / "cron" / "jobs.json"

        service = CronService(store_path)
        service._load_store()

        # Add a valid job (this should work since file doesn't exist yet)
        service.add_job(
            name="test",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="test",
        )

        # _load_failed should be False after successful add
        assert service._load_failed is False

    def test_valid_jobs_json_loads_correctly(self, tmp_path) -> None:
        """Valid jobs.json should load correctly."""
        store_path = tmp_path / "cron" / "jobs.json"
        store_path.parent.mkdir(parents=True)

        # Write valid JSON
        valid_data = {
            "version": 1,
            "jobs": [
                {
                    "id": "test_job",
                    "name": "Test Job",
                    "enabled": True,
                    "schedule": {
                        "kind": "every",
                        "atMs": None,
                        "everyMs": 60000,
                        "expr": None,
                        "tz": None,
                    },
                    "payload": {
                        "kind": "agent_turn",
                        "message": "test",
                        "deliver": False,
                        "channel": None,
                        "to": None,
                    },
                    "state": {
                        "nextRunAtMs": 1000,
                        "lastRunAtMs": None,
                        "lastStatus": None,
                        "lastError": None,
                    },
                    "createdAtMs": 0,
                    "updatedAtMs": 0,
                    "deleteAfterRun": False,
                }
            ],
        }
        store_path.write_text(json.dumps(valid_data), encoding="utf-8")

        service = CronService(store_path)
        store = service._load_store()

        assert len(store.jobs) == 1
        assert store.jobs[0].name == "Test Job"
        assert service._load_failed is False


class TestCronScheduleValidation:
    """Test that invalid schedules are rejected at add time."""

    def test_reject_past_at_time(self, tmp_path) -> None:
        """Bug 4: at_ms in the past should be rejected."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        past_time = _now_ms() - 10000  # 10 seconds ago

        with pytest.raises(ValueError, match="at_ms must be in the future"):
            service.add_job(
                name="past_job",
                schedule=CronSchedule(kind="at", at_ms=past_time),
                message="test",
            )

    def test_reject_missing_at_time(self, tmp_path) -> None:
        """Bug 4: at schedule without at_ms should be rejected."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        with pytest.raises(ValueError, match="at schedule requires at_ms"):
            service.add_job(
                name="missing_at",
                schedule=CronSchedule(kind="at", at_ms=None),
                message="test",
            )

    def test_reject_negative_every_ms(self, tmp_path) -> None:
        """Bug 4: negative every_ms should be rejected."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        with pytest.raises(ValueError, match="every_ms must be positive"):
            service.add_job(
                name="negative_every",
                schedule=CronSchedule(kind="every", every_ms=-1000),
                message="test",
            )

    def test_reject_zero_every_ms(self, tmp_path) -> None:
        """Bug 4: zero every_ms should be rejected."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        with pytest.raises(ValueError, match="every_ms must be positive"):
            service.add_job(
                name="zero_every",
                schedule=CronSchedule(kind="every", every_ms=0),
                message="test",
            )

    def test_reject_missing_every_ms(self, tmp_path) -> None:
        """Bug 4: every schedule without every_ms should be rejected."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        with pytest.raises(ValueError, match="every_ms must be positive"):
            service.add_job(
                name="missing_every",
                schedule=CronSchedule(kind="every", every_ms=None),
                message="test",
            )

    def test_reject_invalid_cron_expression(self, tmp_path) -> None:
        """Bug 4: invalid cron expression should be rejected."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        with pytest.raises(ValueError, match="invalid cron expression"):
            service.add_job(
                name="invalid_cron",
                schedule=CronSchedule(kind="cron", expr="not a valid cron", tz=None),
                message="test",
            )

    def test_reject_cron_without_expression(self, tmp_path) -> None:
        """Bug 4: cron schedule without expression should be rejected."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        with pytest.raises(ValueError, match="cron schedule requires an expression"):
            service.add_job(
                name="missing_expr",
                schedule=CronSchedule(kind="cron", expr=None, tz=None),
                message="test",
            )

    def test_reject_unknown_schedule_kind(self, tmp_path) -> None:
        """Bug 4: unknown schedule kind should be rejected."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        with pytest.raises(ValueError, match="unknown schedule kind"):
            service.add_job(
                name="unknown_kind",
                schedule=CronSchedule(kind="unknown", at_ms=None),
                message="test",
            )

    def test_valid_every_schedule_accepted(self, tmp_path) -> None:
        """Valid every schedule should be accepted."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        job = service.add_job(
            name="valid_every",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="test",
        )

        assert job.id is not None
        assert job.state.next_run_at_ms is not None


class TestCronTimerReliability:
    """Tests for timer re-arming on persistence failures."""

    @pytest.mark.asyncio
    async def test_on_timer_rearms_even_when_save_store_raises(self, tmp_path) -> None:
        service = CronService(tmp_path / "cron" / "jobs.json")
        service._store = CronStore()
        service._running = True
        service._load_store = MagicMock(return_value=service._store)
        service._save_store = MagicMock(side_effect=RuntimeError("disk full"))
        service._arm_timer = MagicMock()

        with pytest.raises(RuntimeError, match="disk full"):
            await service._on_timer()

        service._arm_timer.assert_called_once()

    def test_load_store_updates_last_mtime_after_successful_reload(self, tmp_path) -> None:
        store_path = tmp_path / "cron" / "jobs.json"
        store_path.parent.mkdir(parents=True)
        store_path.write_text(json.dumps({"version": 1, "jobs": []}), encoding="utf-8")

        service = CronService(store_path)
        service._load_store()

        assert service._last_mtime == store_path.stat().st_mtime

    def test_valid_future_at_schedule_accepted(self, tmp_path) -> None:
        """Valid future at schedule should be accepted."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        future_time = _now_ms() + 3600000  # 1 hour in the future

        job = service.add_job(
            name="valid_at",
            schedule=CronSchedule(kind="at", at_ms=future_time),
            message="test",
        )

        assert job.id is not None
        assert job.state.next_run_at_ms == future_time

    def test_valid_cron_schedule_accepted(self, tmp_path) -> None:
        """Valid cron schedule should be accepted."""
        service = CronService(tmp_path / "cron" / "jobs.json")

        job = service.add_job(
            name="valid_cron",
            schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancouver"),
            message="test",
        )

        assert job.id is not None
        assert job.state.next_run_at_ms is not None
