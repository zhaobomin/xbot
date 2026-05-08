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
        self._rlock = FakeSemaphore("/fake-queue-rlock")
        self._wlock = FakeSemaphore("/fake-queue-wlock")
        self._sem = FakeSemaphore("/fake-queue-sem")

    def get_nowait(self):
        if self._drained:
            raise queue.Empty
        self._drained = True
        return {"type": "message"}

    def close(self) -> None:
        self.closed = True

    def join_thread(self) -> None:
        self.joined = True


class FakeSemLock:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeSemaphore:
    def __init__(self, name: str) -> None:
        self._semlock = FakeSemLock(name)


class FakeCondition:
    def __init__(self) -> None:
        self._lock = FakeSemaphore("/fake-lock")
        self._sleeping_count = FakeSemaphore("/fake-sleeping")
        self._woken_count = FakeSemaphore("/fake-woken")
        self._wait_semaphore = FakeSemaphore("/fake-wait")


class FakeEvent:
    def __init__(self) -> None:
        self._flag = FakeSemaphore("/fake-flag")
        self._cond = FakeCondition()
        self.set = MagicMock()


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


def test_cleanup_ws_resources_unlinks_event_semaphores(monkeypatch) -> None:
    channel = FeishuChannel(
        FeishuConfig(enabled=True, app_id="cli_test", app_secret="secret"),
        MessageBus(),
    )
    event = FakeEvent()
    cleaned: list[str] = []

    monkeypatch.setattr(
        "xbot.channels.feishu.SemLock._cleanup",
        lambda name: cleaned.append(name),
    )

    channel._ws_stop_event = event

    channel._cleanup_ws_resources()

    event.set.assert_called_once()
    assert cleaned == [
        "/fake-flag",
        "/fake-lock",
        "/fake-sleeping",
        "/fake-woken",
        "/fake-wait",
    ]


def test_cleanup_ws_resources_unlinks_queue_and_event_semaphores(monkeypatch) -> None:
    channel = FeishuChannel(
        FeishuConfig(enabled=True, app_id="cli_test", app_secret="secret"),
        MessageBus(),
    )
    cleaned: list[str] = []

    monkeypatch.setattr(
        "xbot.channels.feishu.SemLock._cleanup",
        lambda name: cleaned.append(name),
    )

    channel._ws_event_queue = FakeQueue()
    channel._ws_stop_event = FakeEvent()

    channel._cleanup_ws_resources()

    assert len(cleaned) == 8
    assert cleaned[:3] == [
        "/fake-queue-rlock",
        "/fake-queue-wlock",
        "/fake-queue-sem",
    ]
