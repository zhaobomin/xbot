from __future__ import annotations

from xbot.interaction.progress_coalescer import ProgressCoalescer


def test_coalescer_merges_thinking_chunks_without_repeating_prefix() -> None:
    c = ProgressCoalescer(debounce_ms=200, max_wait_ms=1000, max_chars=1000)
    key = ("feishu", "chat-1", "thinking")

    assert c.push(
        key=key,
        text="Thinking: 用户",
        event_type="thinking",
        tool_hint=False,
        now=0.0,
    ) == []
    assert c.push(
        key=key,
        text="Thinking: 说了你好",
        event_type="thinking",
        tool_hint=False,
        now=0.05,
    ) == []

    ready = c.flush_due(now=0.4)
    assert len(ready) == 1
    assert ready[0].text == "Thinking: 用户说了你好"


def test_coalescer_flushes_before_non_bufferable_event() -> None:
    c = ProgressCoalescer(debounce_ms=200, max_wait_ms=1000, max_chars=1000)
    key = ("feishu", "chat-1", "thinking")

    c.push(
        key=key,
        text="Thinking: 正在分析",
        event_type="thinking",
        tool_hint=False,
        now=0.0,
    )
    ready = c.push(
        key=key,
        text="Usage: input 10 tokens, output 5 tokens",
        event_type="usage",
        tool_hint=False,
        now=0.1,
    )

    assert len(ready) == 2
    assert ready[0].text == "Thinking: 正在分析"
    assert "Usage:" in ready[1].text


def test_coalescer_flushes_on_max_wait() -> None:
    c = ProgressCoalescer(debounce_ms=200, max_wait_ms=500, max_chars=1000)
    key = ("cli", "direct", "content_delta")

    c.push(
        key=key,
        text="第一段",
        event_type="content_delta",
        tool_hint=False,
        now=0.0,
    )
    ready = c.push(
        key=key,
        text="第二段",
        event_type="content_delta",
        tool_hint=False,
        now=0.8,
    )

    assert len(ready) == 1
    assert ready[0].text == "第一段 第二段"


def test_flush_due_honors_max_wait_even_when_debounce_has_not_elapsed() -> None:
    c = ProgressCoalescer(debounce_ms=10_000, max_wait_ms=500, max_chars=1000)
    key = ("cli", "direct", "content_delta")

    c.push(
        key=key,
        text="still waiting",
        event_type="content_delta",
        tool_hint=False,
        now=0.0,
    )

    ready = c.flush_due(now=0.6)

    assert len(ready) == 1
    assert ready[0].text == "still waiting"
