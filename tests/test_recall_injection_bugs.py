"""Tests for Bug fixes: multimodal recall injection + recall_task cancellation cleanup."""
from __future__ import annotations

import asyncio

import pytest


# ---- Test _build_multimodal_query with recall_task ----


async def test_multimodal_query_yields_recall_memory() -> None:
    """_build_multimodal_query should yield memory message after user message when recall_task is provided."""
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

    backend = ClaudeSDKBackend()

    async def fake_recall() -> str:
        return "You prefer dark mode."

    recall_task = asyncio.create_task(fake_recall())
    await asyncio.sleep(0)  # let recall complete

    messages: list[dict] = []
    async for msg in backend._build_multimodal_query(
        "hello",
        [],  # no media -> falls back to plain text content
        "sid-1",
        "req-1",
        recall_task=recall_task,
    ):
        messages.append(msg)

    assert len(messages) == 2
    # First message is the user message
    assert messages[0]["message"]["content"] == "hello"
    # Second message is the recalled memory
    assert "Relevant Memories" in messages[1]["message"]["content"]
    assert "dark mode" in messages[1]["message"]["content"]


async def test_multimodal_query_no_recall_yields_single_message() -> None:
    """Without recall_task, _build_multimodal_query yields only the user message."""
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

    backend = ClaudeSDKBackend()
    messages: list[dict] = []
    async for msg in backend._build_multimodal_query(
        "hello", [], "sid-1", "req-1", recall_task=None,
    ):
        messages.append(msg)

    assert len(messages) == 1


async def test_multimodal_query_recall_failure_yields_single_message() -> None:
    """Failed recall should not block multimodal query and should yield only user message."""
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

    backend = ClaudeSDKBackend()

    async def failing_recall() -> str:
        raise RuntimeError("API error")

    recall_task = asyncio.create_task(failing_recall())
    await asyncio.sleep(0)

    messages: list[dict] = []
    async for msg in backend._build_multimodal_query(
        "hello", [], "sid-1", "req-1", recall_task=recall_task,
    ):
        messages.append(msg)

    assert len(messages) == 1


async def test_multimodal_query_empty_recall_yields_single_message() -> None:
    """Empty recall result should not yield a second message."""
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

    backend = ClaudeSDKBackend()

    async def empty_recall() -> str:
        return ""

    recall_task = asyncio.create_task(empty_recall())
    await asyncio.sleep(0)

    messages: list[dict] = []
    async for msg in backend._build_multimodal_query(
        "hello", [], "sid-1", "req-1", recall_task=recall_task,
    ):
        messages.append(msg)

    assert len(messages) == 1


# ---- Test recall_task cancellation cleanup ----


async def test_recall_task_cancel_is_awaited() -> None:
    """Verify that cancelling a recall_task and awaiting it does not raise."""
    was_cancelled = False

    async def slow_recall() -> str:
        nonlocal was_cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            was_cancelled = True
            raise
        return "never"

    task = asyncio.create_task(slow_recall())
    await asyncio.sleep(0)  # let task start

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert was_cancelled
    assert task.done()
    assert task.cancelled()


# ---- Test text_query recall is consistent with multimodal ----


async def test_text_query_yields_recall_memory() -> None:
    """_build_text_query should yield memory as second message, matching multimodal behavior."""
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

    backend = ClaudeSDKBackend()

    async def fake_recall() -> str:
        return "Project uses PostgreSQL."

    recall_task = asyncio.create_task(fake_recall())
    await asyncio.sleep(0)

    messages: list[dict] = []
    async for msg in backend._build_text_query(
        "hello", "sid-1", "req-1", recall_task=recall_task,
    ):
        messages.append(msg)

    assert len(messages) == 2
    assert messages[0]["message"]["content"] == "hello"
    assert "Relevant Memories" in messages[1]["message"]["content"]
    assert "PostgreSQL" in messages[1]["message"]["content"]
