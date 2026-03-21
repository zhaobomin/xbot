"""Error alerting service for xbot.

Sends alerts to configured channels when critical errors occur.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

if TYPE_CHECKING:
    from xbot.bus.queue import MessageBus


@dataclass
class AlertRule:
    """Rule for triggering alerts."""

    name: str
    condition: Callable[[dict], bool]  # Check if alert should trigger
    cooldown_seconds: float = 300.0  # Minimum time between alerts
    last_triggered: float = 0.0


@dataclass
class AlertConfig:
    """Configuration for alerting."""

    enabled: bool = True
    channel: str = "telegram"  # Default channel for alerts
    chat_id: str = ""  # Chat ID to send alerts to
    max_alerts_per_hour: int = 10
    cooldown_seconds: float = 300.0  # Default cooldown


class AlertService:
    """Service for sending error alerts.

    Monitors errors and sends notifications when critical issues occur.
    """

    def __init__(
        self,
        bus: MessageBus,
        config: AlertConfig | None = None,
    ):
        """Initialize alert service.

        Args:
            bus: Message bus for sending alerts
            config: Alert configuration
        """
        self.bus = bus
        self.config = config or AlertConfig()
        self._alert_count = 0
        self._hour_start = time.time()
        self._last_alert_time: dict[str, float] = {}

    def _should_alert(self, rule_name: str) -> bool:
        """Check if an alert should be sent based on rate limits."""
        if not self.config.enabled:
            return False

        # Reset hourly counter
        now = time.time()
        if now - self._hour_start > 3600:
            self._alert_count = 0
            self._hour_start = now

        # Check hourly limit
        if self._alert_count >= self.config.max_alerts_per_hour:
            logger.warning(f"Alert rate limit reached, skipping: {rule_name}")
            return False

        # Check cooldown
        last_time = self._last_alert_time.get(rule_name, 0)
        if now - last_time < self.config.cooldown_seconds:
            return False

        return True

    async def send_alert(
        self,
        title: str,
        message: str,
        severity: str = "error",
        details: dict[str, Any] | None = None,
    ) -> bool:
        """Send an alert notification.

        Args:
            title: Alert title
            message: Alert message
            severity: Alert severity (error, warning, critical)
            details: Additional details

        Returns:
            True if alert was sent
        """
        if not self._should_alert(title):
            return False

        from xbot.bus.events import OutboundMessage

        # Format alert message
        severity_emoji = {
            "error": "❌",
            "warning": "⚠️",
            "critical": "🚨",
            "info": "ℹ️",
        }.get(severity, "❗")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        alert_text = f"""{severity_emoji} **{title}**

{message}

时间: {timestamp}
严重程度: {severity}"""

        if details:
            alert_text += f"\n\n详情:\n"
            for key, value in details.items():
                alert_text += f"- {key}: {value}\n"

        try:
            await self.bus.publish_outbound(OutboundMessage(
                channel=self.config.channel,
                chat_id=self.config.chat_id,
                content=alert_text,
            ))

            self._alert_count += 1
            self._last_alert_time[title] = time.time()
            logger.info(f"Alert sent: {title}")
            return True

        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
            return False

    async def alert_error(self, error: Exception, context: str = "") -> bool:
        """Send an error alert.

        Args:
            error: The exception that occurred
            context: Additional context

        Returns:
            True if alert was sent
        """
        return await self.send_alert(
            title="xbot 错误",
            message=f"{context}\n\n错误: {type(error).__name__}: {str(error)}",
            severity="error",
        )

    async def alert_critical(self, error: Exception, context: str = "") -> bool:
        """Send a critical error alert.

        Args:
            error: The exception that occurred
            context: Additional context

        Returns:
            True if alert was sent
        """
        return await self.send_alert(
            title="xbot 严重错误",
            message=f"{context}\n\n错误: {type(error).__name__}: {str(error)}",
            severity="critical",
        )

    async def alert_memory_warning(self, usage_percent: float) -> bool:
        """Send a memory usage warning.

        Args:
            usage_percent: Memory usage percentage

        Returns:
            True if alert was sent
        """
        return await self.send_alert(
            title="内存使用警告",
            message=f"内存使用率达到 {usage_percent:.1f}%，请检查系统状态。",
            severity="warning",
        )


# Global alert service instance
_alert_service: AlertService | None = None


def init_alert_service(bus: MessageBus, config: AlertConfig | None = None) -> AlertService:
    """Initialize the global alert service.

    Args:
        bus: Message bus
        config: Alert configuration

    Returns:
        AlertService instance
    """
    global _alert_service
    _alert_service = AlertService(bus, config)
    return _alert_service


def get_alert_service() -> AlertService | None:
    """Get the global alert service."""
    return _alert_service


async def alert_error(error: Exception, context: str = "") -> bool:
    """Send an error alert using the global service."""
    if _alert_service:
        return await _alert_service.alert_error(error, context)
    return False


async def alert_critical(error: Exception, context: str = "") -> bool:
    """Send a critical error alert using the global service."""
    if _alert_service:
        return await _alert_service.alert_critical(error, context)
    return False