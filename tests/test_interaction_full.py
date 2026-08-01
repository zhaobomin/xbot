"""Full coverage tests for interaction modules.

Tests event_formatter, ask_user_validation, and progress_coalescer.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from xbot.interaction.event_formatter import (
    format_compact_event,
    format_rate_limit_event,
    format_task_notification,
    format_usage_summary,
)
from xbot.interaction.ask_user_validation import (
    VALIDATION_MODE_ALIASES,
    match_option,
    normalize_validation_mode,
    split_answers,
)
from xbot.interaction.progress_coalescer import (
    CoalescedEvent,
    ProgressCoalescer,
)


# ── event_formatter ──────────────────────────────────────────────────────


class TestFormatCompactEvent:
    def test_with_both_token_counts(self) -> None:
        result = format_compact_event(pre_tokens=100_000, post_tokens=40_000)
        assert "100,000" in result
        assert "40,000" in result
        assert "60,000" in result  # saved
        assert "Context compacted" in result

    def test_with_trigger(self) -> None:
        result = format_compact_event(pre_tokens=50_000, post_tokens=20_000, trigger="auto")
        assert "(auto)" in result

    def test_without_trigger(self) -> None:
        result = format_compact_event(pre_tokens=50_000, post_tokens=20_000)
        assert "(" not in result or "Context" in result

    def test_with_only_pre_tokens(self) -> None:
        result = format_compact_event(pre_tokens=50_000, post_tokens=None)
        assert result == "Context compacted."

    def test_with_only_post_tokens(self) -> None:
        result = format_compact_event(pre_tokens=None, post_tokens=20_000)
        assert result == "Context compacted."

    def test_with_both_none(self) -> None:
        result = format_compact_event(pre_tokens=None, post_tokens=None)
        assert result == "Context compacted."

    def test_with_zero_tokens(self) -> None:
        result = format_compact_event(pre_tokens=0, post_tokens=0)
        assert "0" in result

    def test_with_negative_saved(self) -> None:
        # post > pre → negative saved (edge case)
        result = format_compact_event(pre_tokens=10, post_tokens=100)
        assert "-90" in result


class TestFormatTaskNotification:
    def test_completed(self) -> None:
        result = format_task_notification(status="completed", summary="Done!")
        assert "Task completed" in result
        assert "Done!" in result

    def test_failed(self) -> None:
        result = format_task_notification(status="failed", summary="Error occurred")
        assert "Task failed" in result

    def test_stopped(self) -> None:
        result = format_task_notification(status="stopped", summary=None)
        assert "Task stopped" in result

    def test_unknown_status(self) -> None:
        result = format_task_notification(status="unknown", summary=None)
        assert "Task update" in result

    def test_none_status(self) -> None:
        result = format_task_notification(status=None, summary=None)
        assert "Task update" in result

    def test_with_task_id(self) -> None:
        result = format_task_notification(status="completed", summary=None, task_id="abc-123")
        assert "id=abc-123" in result

    def test_with_output_file(self) -> None:
        result = format_task_notification(
            status="completed", summary=None, output_file="/tmp/out.txt"
        )
        assert "output=/tmp/out.txt" in result

    def test_with_task_id_and_output(self) -> None:
        result = format_task_notification(
            status="completed", summary="ok", task_id="t1", output_file="/f"
        )
        assert "id=t1" in result
        assert "output=/f" in result

    def test_empty_summary_and_status(self) -> None:
        result = format_task_notification(status="", summary="")
        assert "Task update" in result
        # No trailing colon when detail is empty
        assert ":" not in result.split("(")[0] if "(" in result else ":" not in result

    def test_case_insensitive_status(self) -> None:
        result = format_task_notification(status="COMPLETED", summary=None)
        assert "Task completed" in result


class TestFormatUsageSummary:
    def test_normal_usage(self) -> None:
        result = format_usage_summary({"input_tokens": 1000, "output_tokens": 500})
        assert "1,000" in result
        assert "500" in result
        assert "input" in result
        assert "output" in result

    def test_none_usage(self) -> None:
        assert format_usage_summary(None) is None

    def test_empty_dict(self) -> None:
        assert format_usage_summary({}) is None

    def test_zero_zero_usage(self) -> None:
        # 0/0 is considered placeholder
        assert format_usage_summary({"input_tokens": 0, "output_tokens": 0}) is None

    def test_negative_zero(self) -> None:
        assert format_usage_summary({"input_tokens": -1, "output_tokens": 0}) is None

    def test_non_int_tokens(self) -> None:
        assert format_usage_summary({"input_tokens": "abc", "output_tokens": 500}) is None

    def test_only_input(self) -> None:
        # output_tokens not an int → None
        assert format_usage_summary({"input_tokens": 100}) is None


class TestFormatRateLimitEvent:
    def test_allowed(self) -> None:
        info = SimpleNamespace(status="allowed", rate_limit_type=None, utilization=None, resets_at=None)
        result = format_rate_limit_event(info)
        assert "Rate limit check" in result
        assert "retry later" in result

    def test_allowed_warning(self) -> None:
        info = SimpleNamespace(status="allowed_warning", rate_limit_type="requests", utilization=0.85, resets_at=None)
        result = format_rate_limit_event(info)
        assert "Rate limit warning" in result
        assert "type=requests" in result
        assert "85%" in result

    def test_rejected(self) -> None:
        info = SimpleNamespace(status="rejected", rate_limit_type=None, utilization=None, resets_at=1700000000)
        result = format_rate_limit_event(info)
        assert "Rate limited" in result
        assert "resets_at=" in result

    def test_unknown_status(self) -> None:
        info = SimpleNamespace(status="mystery", rate_limit_type=None, utilization=None, resets_at=None)
        result = format_rate_limit_event(info)
        assert "Rate limit update" in result

    def test_utilization_int(self) -> None:
        info = SimpleNamespace(status="allowed", rate_limit_type=None, utilization=1, resets_at=None)
        result = format_rate_limit_event(info)
        assert "100%" in result

    def test_utilization_float(self) -> None:
        info = SimpleNamespace(status="allowed", rate_limit_type=None, utilization=0.5, resets_at=None)
        result = format_rate_limit_event(info)
        assert "50%" in result


# ── ask_user_validation ──────────────────────────────────────────────────


class TestNormalizeValidationMode:
    def test_strict(self) -> None:
        assert normalize_validation_mode("strict") == "strict"

    def test_suggested(self) -> None:
        assert normalize_validation_mode("suggested") == "suggested"

    def test_open_alias(self) -> None:
        assert normalize_validation_mode("open") == "suggested"

    def test_loose_alias(self) -> None:
        assert normalize_validation_mode("loose") == "suggested"

    def test_none(self) -> None:
        assert normalize_validation_mode(None) == "suggested"

    def test_empty(self) -> None:
        assert normalize_validation_mode("") == "suggested"

    def test_case_insensitive(self) -> None:
        assert normalize_validation_mode("STRICT") == "strict"
        assert normalize_validation_mode("Suggested") == "suggested"

    def test_whitespace(self) -> None:
        assert normalize_validation_mode("  strict  ") == "strict"

    def test_unknown_falls_back(self) -> None:
        assert normalize_validation_mode("xyz") == "suggested"


class TestSplitAnswers:
    def test_comma_separated(self) -> None:
        assert split_answers("a,b,c") == ["a", "b", "c"]

    def test_chinese_comma(self) -> None:
        assert split_answers("a，b，c") == ["a", "b", "c"]

    def test_mixed_separators(self) -> None:
        assert split_answers("a,b，c") == ["a", "b", "c"]

    def test_dunhao_separator(self) -> None:
        assert split_answers("a、b、c") == ["a", "b", "c"]

    def test_empty(self) -> None:
        assert split_answers("") == []

    def test_none(self) -> None:
        assert split_answers(None) == []

    def test_whitespace_stripped(self) -> None:
        assert split_answers(" a , b , c ") == ["a", "b", "c"]

    def test_empty_parts_filtered(self) -> None:
        assert split_answers("a,,b") == ["a", "b"]

    def test_single_item(self) -> None:
        assert split_answers("hello") == ["hello"]

    def test_consecutive_separators(self) -> None:
        assert split_answers("a,,,b") == ["a", "b"]


class TestMatchOption:
    def test_exact_match(self) -> None:
        assert match_option("Option A", ["Option A", "Option B"]) == "Option A"

    def test_case_insensitive_match(self) -> None:
        assert match_option("option a", ["Option A", "Option B"]) == "Option A"

    def test_prefix_match(self) -> None:
        assert match_option("opt", ["Option A", "Option B"]) is None  # ambiguous

    def test_unique_prefix(self) -> None:
        assert match_option("A", ["Apple", "Banana"]) == "Apple"

    def test_none_candidate(self) -> None:
        assert match_option(None, ["A", "B"]) is None

    def test_empty_candidate(self) -> None:
        assert match_option("", ["A", "B"]) is None

    def test_no_match(self) -> None:
        assert match_option("C", ["Apple", "Banana"]) is None

    def test_whitespace_handling(self) -> None:
        assert match_option("  Apple  ", ["Apple", "Banana"]) == "Apple"

    def test_ambiguous_prefix_returns_none(self) -> None:
        # Both start with "A"
        assert match_option("A", ["Apple", "Avocado"]) is None


# ── progress_coalescer ───────────────────────────────────────────────────


class TestProgressCoalescer:
    def test_push_non_bufferable_returns_immediately(self) -> None:
        c = ProgressCoalescer()
        events = c.push(key="k1", text="hello", event_type="tool_call", tool_hint=False)
        assert len(events) == 1
        assert events[0].text == "hello"
        assert events[0].key == "k1"

    def test_push_tool_hint_flushes_and_returns(self) -> None:
        c = ProgressCoalescer()
        # First push creates buffer
        c.push(key="k1", text="buffered", event_type="content_delta", tool_hint=False)
        # Tool hint should flush buffer + return tool hint
        events = c.push(key="k1", text="hint!", event_type="content_delta", tool_hint=True)
        assert len(events) == 2
        assert events[0].text == "buffered"  # flushed
        assert events[1].text == "hint!"  # tool hint

    def test_push_empty_text_returns_empty(self) -> None:
        c = ProgressCoalescer()
        events = c.push(key="k1", text="", event_type="content_delta", tool_hint=False)
        assert events == []

    def test_buffering_accumulates(self) -> None:
        c = ProgressCoalescer(debounce_ms=1000, max_wait_ms=5000, max_chars=1000)
        now = time.monotonic()
        # First push creates buffer — returns empty
        events = c.push(key="k1", text="hello", event_type="content_delta", tool_hint=False, now=now)
        assert events == []
        # Second push accumulates — still within limits
        events = c.push(key="k1", text=" world", event_type="content_delta", tool_hint=False, now=now + 0.1)
        assert events == []

    def test_max_wait_triggers_flush(self) -> None:
        c = ProgressCoalescer(debounce_ms=1000, max_wait_ms=100, max_chars=10000)
        now = time.monotonic()
        c.push(key="k1", text="hello", event_type="content_delta", tool_hint=False, now=now)
        # After max_wait → flush
        events = c.push(key="k1", text=" world", event_type="content_delta", tool_hint=False, now=now + 0.2)
        assert len(events) == 1
        assert "hello" in events[0].text

    def test_max_chars_triggers_flush(self) -> None:
        c = ProgressCoalescer(debounce_ms=10000, max_wait_ms=100000, max_chars=32)
        now = time.monotonic()
        c.push(key="k1", text="a" * 20, event_type="content_delta", tool_hint=False, now=now)
        # Adding more that pushes rendered length >= 32
        events = c.push(key="k1", text="b" * 20, event_type="content_delta", tool_hint=False, now=now + 0.01)
        assert len(events) == 1

    def test_kind_change_flushes(self) -> None:
        c = ProgressCoalescer()
        now = time.monotonic()
        c.push(key="k1", text="plain text", event_type="content_delta", tool_hint=False, now=now)
        # Switch to thinking → flushes plain, starts thinking buffer
        events = c.push(key="k1", text="Thinking: deep thought", event_type="thinking", tool_hint=False, now=now + 0.01)
        assert len(events) == 1
        assert events[0].text == "plain text"

    def test_flush_due(self) -> None:
        c = ProgressCoalescer(debounce_ms=100, max_wait_ms=5000)
        now = time.monotonic()
        c.push(key="k1", text="hello", event_type="content_delta", tool_hint=False, now=now)
        # Before debounce → nothing
        events = c.flush_due(now=now + 0.05)
        assert events == []
        # After debounce → flushed
        events = c.flush_due(now=now + 0.2)
        assert len(events) == 1
        assert events[0].text == "hello"

    def test_flush_due_max_wait(self) -> None:
        c = ProgressCoalescer(debounce_ms=10000, max_wait_ms=100)
        now = time.monotonic()
        c.push(key="k1", text="hello", event_type="content_delta", tool_hint=False, now=now)
        events = c.flush_due(now=now + 0.2)
        assert len(events) == 1

    def test_flush_key(self) -> None:
        c = ProgressCoalescer()
        c.push(key="k1", text="hello", event_type="content_delta", tool_hint=False)
        events = c.flush_key("k1")
        assert len(events) == 1
        assert events[0].text == "hello"

    def test_flush_key_missing(self) -> None:
        c = ProgressCoalescer()
        assert c.flush_key("nonexistent") == []

    def test_flush_all(self) -> None:
        c = ProgressCoalescer()
        c.push(key="k1", text="a", event_type="content_delta", tool_hint=False)
        c.push(key="k2", text="b", event_type="content_delta", tool_hint=False)
        events = c.flush_all()
        assert len(events) == 2
        texts = {e.text for e in events}
        assert "a" in texts
        assert "b" in texts

    def test_thinking_prefix_normalization(self) -> None:
        c = ProgressCoalescer()
        c.push(key="k1", text="Thinking: let me think", event_type="thinking", tool_hint=False)
        events = c.flush_key("k1")
        assert len(events) == 1
        assert events[0].text.startswith("Thinking:")
        assert "let me think" in events[0].text

    def test_append_body_spacing(self) -> None:
        # Test the _append_body static method
        assert ProgressCoalescer._append_body("plain", "hello", "world") == "hello world"
        assert ProgressCoalescer._append_body("plain", "hello ", "world") == "hello world"
        assert ProgressCoalescer._append_body("plain", "hello", " world") == "hello world"
        assert ProgressCoalescer._append_body("plain", "", "world") == "world"
        assert ProgressCoalescer._append_body("plain", "hello", "") == "hello"
        # Chinese punctuation — both sides checked symmetrically after BUG-001 fix
        assert ProgressCoalescer._append_body("plain", "你好", "，世界") == "你好，世界"
        assert ProgressCoalescer._append_body("plain", "你好，", "世界") == "你好，世界"
        # Thinking concatenates directly
        assert ProgressCoalescer._append_body("thinking", "hello", "world") == "helloworld"

    def test_render_empty_body(self) -> None:
        c = ProgressCoalescer()
        c.push(key="k1", text="  ", event_type="content_delta", tool_hint=False)
        # Whitespace-only body → flush returns empty
        events = c.flush_key("k1")
        # Buffer was created with stripped empty body
        assert events == []

    def test_multiple_keys_independent(self) -> None:
        c = ProgressCoalescer()
        c.push(key="k1", text="first", event_type="content_delta", tool_hint=False)
        c.push(key="k2", text="second", event_type="content_delta", tool_hint=False)
        events1 = c.flush_key("k1")
        events2 = c.flush_key("k2")
        assert events1[0].text == "first"
        assert events2[0].text == "second"
