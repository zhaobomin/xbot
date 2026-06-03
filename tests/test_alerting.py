"""Tests for alerting service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.runtime.system.monitoring.alerting import (
    AlertConfig,
    AlertService,
)


class TestAlertConfig:
    """Tests for AlertConfig."""

    def test_defaults(self) -> None:
        """Test default configuration."""
        config = AlertConfig()
        assert config.enabled is True
        assert config.channel == "telegram"
        assert config.max_alerts_per_hour == 10
        assert config.cooldown_seconds == 300.0


class TestAlertService:
    """Tests for AlertService."""

    @pytest.fixture
    def mock_bus(self):
        """Create a mock message bus."""
        bus = MagicMock()
        bus.publish_outbound = AsyncMock()
        return bus

    @pytest.fixture
    def service(self, mock_bus) -> AlertService:
        """Create an alert service for testing."""
        config = AlertConfig(chat_id="test_chat")
        return AlertService(mock_bus, config)

    def test_init(self, service: AlertService) -> None:
        """Test service initialization."""
        assert service.config.chat_id == "test_chat"
        assert service._rate_lock is None

    @pytest.mark.asyncio
    async def test_reserve_alert_slot_disabled(self, mock_bus) -> None:
        """Test that alerts are skipped when disabled."""
        config = AlertConfig(enabled=False)
        service = AlertService(mock_bus, config)
        assert await service._reserve_alert_slot("test") is False

    @pytest.mark.asyncio
    async def test_reserve_alert_slot_updates_cooldown_state(self, service: AlertService) -> None:
        """Reserving a slot should update state atomically."""
        assert await service._reserve_alert_slot("test") is True
        assert await service._reserve_alert_slot("test") is False

    @pytest.mark.asyncio
    async def test_send_alert(self, service: AlertService, mock_bus) -> None:
        """Test sending an alert."""
        result = await service.send_alert(
            title="Test Alert",
            message="This is a test",
            severity="error",
        )

        assert result is True
        mock_bus.publish_outbound.assert_called_once()

        # Check the message content
        call_args = mock_bus.publish_outbound.call_args
        assert call_args[0][0].chat_id == "test_chat"
        assert "Test Alert" in call_args[0][0].content

    @pytest.mark.asyncio
    async def test_send_alert_rate_limited(self, service: AlertService, mock_bus) -> None:
        """Test that rate limiting prevents duplicate alerts."""
        # First alert should go through
        await service.send_alert(title="Test", message="First")

        # Second alert should be blocked by cooldown
        result = await service.send_alert(title="Test", message="Second")
        assert result is False

        # Should only have one call
        assert mock_bus.publish_outbound.call_count == 1

    @pytest.mark.asyncio
    async def test_alert_error(self, service: AlertService, mock_bus) -> None:
        """Test sending an error alert."""
        error = RuntimeError("Something went wrong")
        result = await service.alert_error(error, "Test context")

        assert result is True
        call_args = mock_bus.publish_outbound.call_args
        assert "RuntimeError" in call_args[0][0].content
        assert "Something went wrong" in call_args[0][0].content

    @pytest.mark.asyncio
    async def test_alert_critical(self, service: AlertService, mock_bus) -> None:
        """Test sending a critical alert."""
        error = RuntimeError("Critical failure")
        result = await service.alert_critical(error, "System failure")

        assert result is True
        call_args = mock_bus.publish_outbound.call_args
        assert "严重错误" in call_args[0][0].content
