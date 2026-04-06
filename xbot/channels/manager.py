"""Channel manager for coordinating chat channels."""

from __future__ import annotations

import asyncio
from typing import Any

from xbot.logging import get_logger

logger = get_logger(__name__)

from xbot.bus.events import OutboundMessage
from xbot.bus.queue import MessageBus
from xbot.agent.task_supervisor import ServiceTaskRegistry
from xbot.channels.base import BaseChannel
from xbot.config.schema import Config

# Retry configuration for message delivery
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff in seconds


class ChannelManager:
    """
    Manages chat channels and coordinates message routing.

    Responsibilities:
    - Initialize enabled channels (Telegram, WhatsApp, etc.)
    - Start/stop channels
    - Route outbound messages
    """

    def __init__(self, config: Config, bus: MessageBus):
        self.config = config
        self.bus = bus
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._task_registry = ServiceTaskRegistry(error_reporter=self._report_task_error)

        self._init_channels()

    @staticmethod
    def _report_task_error(owner: str, task_name: str, exc: BaseException) -> None:
        logger.error("Background task failed for owner=%s task=%s: %s", owner, task_name, exc)

    def _init_channels(self) -> None:
        """Initialize channels discovered via pkgutil scan + entry_points plugins."""
        from xbot.channels.registry import discover_all

        groq_key_secret = self.config.providers.groq.api_key
        groq_key = (
            groq_key_secret.get_secret_value()
            if hasattr(groq_key_secret, "get_secret_value")
            else str(groq_key_secret or "")
        )

        for name, cls in discover_all().items():
            section = getattr(self.config.channels, name, None)
            if section is None:
                continue
            enabled = (
                section.get("enabled", False)
                if isinstance(section, dict)
                else getattr(section, "enabled", False)
            )
            if not enabled:
                continue
            try:
                channel = cls(section, self.bus)
                channel.transcription_api_key = groq_key
                self.channels[name] = channel
                logger.info("%s channel enabled", cls.display_name)
            except Exception as e:
                logger.warning("%s channel not available: %s", name, e)

        self._validate_allow_from()

    def _validate_allow_from(self) -> None:
        for name, ch in self.channels.items():
            if getattr(ch.config, "allow_from", None) == []:
                raise SystemExit(
                    f'Error: "{name}" has empty allowFrom (denies all). '
                    f'Set ["*"] to allow everyone, or add specific user IDs.'
                )

    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        """Start a channel and log any exceptions."""
        try:
            await channel.start()
        except Exception as e:
            logger.error("Failed to start channel %s: %s", name, e)

    async def start_all(self) -> None:
        """Start all channels and the outbound dispatcher."""
        if not self.channels:
            logger.warning("No channels enabled")
            return

        # Start outbound dispatcher
        self._dispatch_task = self._task_registry.spawn(
            "channel-manager",
            self._dispatch_outbound(),
            name="outbound-dispatch",
        )

        # Start channels
        tasks = []
        for name, channel in self.channels.items():
            logger.info("Starting %s channel...", name)
            tasks.append(
                self._task_registry.spawn(
                    "channel-manager:start",
                    self._start_channel(name, channel),
                    name=f"start-{name}",
                )
            )

        # Wait for all to complete (they should run forever)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """Stop all channels and the dispatcher."""
        logger.info("Stopping all channels...")

        # Stop dispatcher
        if self._dispatch_task:
            await self._task_registry.cancel_owner("channel-manager")
            if not self._dispatch_task.done():
                self._dispatch_task.cancel()
                try:
                    await self._dispatch_task
                except asyncio.CancelledError:
                    pass

        # Stop all channels
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info("Stopped %s channel", name)
            except Exception as e:
                logger.error("Error stopping %s: %s", name, e)
        await self._task_registry.cancel_owner("channel-manager:start")

    def check_channels_health(self) -> dict[str, tuple[bool, str]]:
        """Check health of all enabled channels. Returns {name: (healthy, detail)}."""
        results = {}
        for name, channel in self.channels.items():
            try:
                results[name] = channel.check_health()
            except Exception as e:
                results[name] = (False, str(e))
        return results

    async def _dispatch_outbound(self) -> None:
        """Dispatch outbound messages to the appropriate channel."""
        logger.info("Outbound dispatcher started")

        while True:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_outbound(),
                    timeout=1.0
                )

                if msg.metadata.get("_progress"):
                    event_type = str(msg.metadata.get("_event_type", "progress"))
                    is_tool_hint = bool(msg.metadata.get("_tool_hint"))

                    if is_tool_hint:
                        if self.config.channels.send_tool_hints:
                            await self._send_with_channel(msg)
                        continue

                    if event_type == "usage":
                        if self.config.channels.send_usage_summary:
                            await self._send_with_channel(msg)
                        continue

                    # Unknown progress events follow send_progress gate.
                    if self.config.channels.send_progress:
                        await self._send_with_channel(msg)
                    continue

                await self._send_with_channel(msg)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Unexpected error in outbound dispatcher: %s", e)
                continue  # 继续运行，不退出

    async def _send_with_channel(self, msg: OutboundMessage, content: str | None = None) -> None:
        """Send message with retry on transient failures."""
        channel = self.channels.get(msg.channel)
        if channel is None:
            logger.warning("Unknown channel: %s", msg.channel)
            return
        payload = msg if content is None else OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            reply_to=msg.reply_to,
            media=list(msg.media),
            metadata=dict(msg.metadata),
        )

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                await channel.send(payload)
                return
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        "Channel %s send failed (attempt %d/%d), retrying in %ds: %s",
                        msg.channel, attempt + 1, MAX_RETRIES, delay, e
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted
        logger.error(
            "Channel %s send failed after %d attempts, message lost: %s",
            msg.channel, MAX_RETRIES, last_error
        )

    def get_channel(self, name: str) -> BaseChannel | None:
        """Get a channel by name."""
        return self.channels.get(name)

    def get_status(self) -> dict[str, Any]:
        """Get status of all channels."""
        return {
            name: {
                "enabled": True,
                "running": channel.is_running
            }
            for name, channel in self.channels.items()
        }

    @property
    def enabled_channels(self) -> list[str]:
        """Get list of enabled channel names."""
        return list(self.channels.keys())
