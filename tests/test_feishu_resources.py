"""Tests for Feishu worker resource cleanup."""

from __future__ import annotations

import queue
from unittest.mock import MagicMock

from xbot.channels.feishu import FeishuChannel, FeishuConfig
from xbot.platform.bus.queue import MessageBus


class FakeQueue:
    def __init__(self) -> None:
        self.closed = False
        self.joined = False
        self._drained = False

    def get_nowait(self):
        if self._drained:
            raise queue.Empty
        self._drained = True
        return {"type": "message"}

    def close(self) -> None:
        self.closed = True

    def join_thread(self) -> None:
        self.joined = True


def test_cleanup_ws_resources_closes_multiprocessing_queue() -> None:
    channel = FeishuChannel(
        FeishuConfig(enabled=True, app_id="cli_test", app_secret="secret"),
        MessageBus(),
    )
    event_queue = FakeQueue()
    stop_event = MagicMock()
    channel._ws_event_queue = event_queue
    channel._ws_stop_event = stop_event

    channel._cleanup_ws_resources()

    stop_event.set.assert_called_once()
    assert event_queue.closed is True
    assert event_queue.joined is True
