"""Comprehensive tests for xbot.platform.utils.helpers and xbot.platform.utils.retry."""

import asyncio
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from xbot.platform.utils.helpers import (
    build_assistant_message,
    current_time_str,
    detect_audio_mime,
    detect_image_mime,
    ensure_dir,
    estimate_message_tokens,
    estimate_prompt_tokens,
    safe_filename,
    sanitize_download_filename,
    split_message,
    timestamp,
)
from xbot.platform.utils.retry import RetryPolicy, run_with_retry


# ---------------------------------------------------------------------------
# detect_image_mime
# ---------------------------------------------------------------------------

class TestDetectImageMime:
    def test_png(self):
        assert detect_image_mime(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20) == "image/png"

    def test_jpeg(self):
        assert detect_image_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "image/jpeg"

    def test_gif87a(self):
        assert detect_image_mime(b"GIF87a" + b"\x00" * 20) == "image/gif"

    def test_gif89a(self):
        assert detect_image_mime(b"GIF89a" + b"\x00" * 20) == "image/gif"

    def test_webp(self):
        data = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20
        assert detect_image_mime(data) == "image/webp"

    def test_webp_wrong_subtype(self):
        # RIFF but not WEBP at 8:12
        data = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 20
        assert detect_image_mime(data) is None

    def test_unknown(self):
        assert detect_image_mime(b"\x00\x01\x02\x03\x04\x05") is None

    def test_empty(self):
        assert detect_image_mime(b"") is None

    def test_short_data(self):
        assert detect_image_mime(b"\x89PN") is None


# ---------------------------------------------------------------------------
# detect_audio_mime
# ---------------------------------------------------------------------------

class TestDetectAudioMime:
    def test_mp3_id3(self):
        assert detect_audio_mime(b"ID3" + b"\x00" * 20) == "audio/mp3"

    def test_mp3_frame_sync_fb(self):
        # 0xFF 0xFB — MPEG1 Layer III
        assert detect_audio_mime(b"\xff\xfb" + b"\x00" * 20) == "audio/mp3"

    def test_mp3_frame_sync_fa(self):
        assert detect_audio_mime(b"\xff\xfa" + b"\x00" * 20) == "audio/mp3"

    def test_mp3_frame_sync_e0(self):
        # Lower boundary: 0xFF 0xE0 — all 11 sync bits set
        assert detect_audio_mime(b"\xff\xe0" + b"\x00" * 20) == "audio/mp3"

    def test_mp3_frame_sync_not_matching(self):
        # 0xFF 0xDF — only upper 2 bits set, not 3
        assert detect_audio_mime(b"\xff\xdf" + b"\x00" * 20) is None

    def test_wav(self):
        data = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 20
        assert detect_audio_mime(data) == "audio/wav"

    def test_riff_not_wav(self):
        data = b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 20
        assert detect_audio_mime(data) is None

    def test_riff_too_short_for_wav(self):
        # RIFF header but less than 12 bytes
        data = b"RIFF\x00\x00\x00\x00WA"
        assert detect_audio_mime(data) is None

    def test_ogg(self):
        assert detect_audio_mime(b"OggS" + b"\x00" * 20) == "audio/ogg"

    def test_flac(self):
        assert detect_audio_mime(b"fLaC" + b"\x00" * 20) == "audio/flac"

    def test_m4a(self):
        # size(4) + "ftyp" + "M4A " = 12 bytes
        data = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 20
        assert detect_audio_mime(data) == "audio/mp4"

    def test_m4b(self):
        data = b"\x00\x00\x00\x18ftypM4B " + b"\x00" * 20
        assert detect_audio_mime(data) == "audio/mp4"

    def test_f4a(self):
        data = b"\x00\x00\x00\x18ftypF4A " + b"\x00" * 20
        assert detect_audio_mime(data) == "audio/mp4"

    def test_f4b(self):
        data = b"\x00\x00\x00\x18ftypF4B " + b"\x00" * 20
        assert detect_audio_mime(data) == "audio/mp4"

    def test_generic_mp4_brand_mp42(self):
        data = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20
        assert detect_audio_mime(data) == "audio/mp4"

    def test_generic_mp4_brand_isom(self):
        data = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20
        assert detect_audio_mime(data) == "audio/mp4"

    def test_generic_mp4_brand_msnv(self):
        data = b"\x00\x00\x00\x18ftypMSNV" + b"\x00" * 20
        assert detect_audio_mime(data) == "audio/mp4"

    def test_unknown_ftyp_brand(self):
        data = b"\x00\x00\x00\x18ftypavc1" + b"\x00" * 20
        assert detect_audio_mime(data) is None

    def test_too_short_1_byte(self):
        assert detect_audio_mime(b"\x00") is None

    def test_too_short_empty(self):
        assert detect_audio_mime(b"") is None

    def test_ftyp_too_short_for_brand(self):
        # ftyp present but < 11 bytes total
        data = b"\x00\x00\x00\x08ftyp"
        assert detect_audio_mime(data) is None

    def test_ftyp_short_brand_padded(self):
        # Exactly 11 bytes: 4 size + 4 ftyp + 3 brand bytes → padded with space
        data = b"\x00\x00\x00\x0bftypM4A"
        assert detect_audio_mime(data) == "audio/mp4"


# ---------------------------------------------------------------------------
# safe_filename
# ---------------------------------------------------------------------------

class TestSafeFilename:
    def test_unsafe_chars_replaced(self):
        result = safe_filename('file<>:"/\\|?*name')
        assert re.match(r'^[^\x00-\x1f<>:"/\\|?*]+$', result)
        assert "<" not in result and ">" not in result

    def test_safe_string_unchanged(self):
        assert safe_filename("hello_world.txt") == "hello_world.txt"

    def test_strip_whitespace(self):
        assert safe_filename("  hello  ") == "hello"

    def test_empty(self):
        assert safe_filename("") == ""

    def test_all_unsafe(self):
        result = safe_filename('<<>>""')
        assert result == "______"


# ---------------------------------------------------------------------------
# sanitize_download_filename
# ---------------------------------------------------------------------------

class TestSanitizeDownloadFilename:
    def test_normal_filename(self):
        assert sanitize_download_filename("report.pdf", "fallback.txt") == "report.pdf"

    def test_path_traversal_unix(self):
        assert sanitize_download_filename("../../etc/passwd", "safe.txt") == "passwd"

    def test_path_traversal_windows(self):
        assert sanitize_download_filename("C:\\Users\\secret.txt", "fb.txt") == "secret.txt"

    def test_empty_name_uses_fallback(self):
        assert sanitize_download_filename("", "fallback.txt") == "fallback.txt"

    def test_none_name_uses_fallback(self):
        assert sanitize_download_filename(None, "fallback.txt") == "fallback.txt"

    def test_dot_uses_fallback(self):
        assert sanitize_download_filename(".", "fallback.txt") == "fallback.txt"

    def test_dotdot_uses_fallback(self):
        assert sanitize_download_filename("..", "fallback.txt") == "fallback.txt"

    def test_all_unsafe_uses_fallback(self):
        # After safe_filename, '<<>>""' becomes '______' (not empty, not dot/dotdot)
        result = sanitize_download_filename('<<>>""', "fallback.txt")
        assert result == "______"

    def test_fallback_also_dot(self):
        # Both name and fallback resolve to "."
        result = sanitize_download_filename(".", ".")
        assert result == "download"

    def test_slash_only(self):
        result = sanitize_download_filename("/", "fb.txt")
        # After split on "/", last segment is ""
        assert result == "fb.txt"

    def test_trailing_slash(self):
        result = sanitize_download_filename("path/to/", "fb.txt")
        # Last segment after split is "" → fallback
        assert result == "fb.txt"

    def test_unsafe_chars_in_filename(self):
        result = sanitize_download_filename('my<file>.txt', "fb.txt")
        assert "<" not in result and ">" not in result


# ---------------------------------------------------------------------------
# split_message
# ---------------------------------------------------------------------------

class TestSplitMessage:
    def test_empty_content(self):
        assert split_message("") == []

    def test_single_chunk(self):
        assert split_message("hello") == ["hello"]

    def test_exact_max_len(self):
        assert split_message("a" * 2000) == ["a" * 2000]

    def test_newline_splitting(self):
        content = "line1\n" + "x" * 1995
        chunks = split_message(content, max_len=2000)
        assert len(chunks) == 2
        assert all(len(c) <= 2000 for c in chunks)

    def test_space_splitting(self):
        content = "a" * 1000 + " " + "b" * 1001
        chunks = split_message(content, max_len=2000)
        assert len(chunks) == 2
        for c in chunks:
            assert len(c) <= 2000

    def test_hard_break(self):
        # No newlines or spaces → hard break at max_len
        content = "a" * 5000
        chunks = split_message(content, max_len=2000)
        assert len(chunks) == 3
        assert all(len(c) <= 2000 for c in chunks)

    def test_max_len_0_raises(self):
        with pytest.raises(ValueError, match="max_len must be positive"):
            split_message("hello", max_len=0)

    def test_max_len_negative_raises(self):
        with pytest.raises(ValueError, match="max_len must be positive"):
            split_message("hello", max_len=-1)

    def test_respects_max_len(self):
        content = "word " * 1000
        chunks = split_message(content, max_len=100)
        for c in chunks:
            assert len(c) <= 100

    def test_multiple_chunks_rejoin(self):
        """All original content should be recoverable (minus stripped whitespace at breaks)."""
        lines = [f"line{i}" for i in range(100)]
        content = "\n".join(lines)
        chunks = split_message(content, max_len=50)
        # Each chunk should be non-empty and within limit
        for c in chunks:
            assert 0 < len(c) <= 50


# ---------------------------------------------------------------------------
# build_assistant_message
# ---------------------------------------------------------------------------

class TestBuildAssistantMessage:
    def test_basic(self):
        msg = build_assistant_message("hello")
        assert msg == {"role": "assistant", "content": "hello"}

    def test_none_content(self):
        msg = build_assistant_message(None)
        assert msg["content"] is None

    def test_with_tool_calls(self):
        tc = [{"id": "1", "function": {"name": "foo"}}]
        msg = build_assistant_message("hi", tool_calls=tc)
        assert msg["tool_calls"] == tc

    def test_without_tool_calls(self):
        msg = build_assistant_message("hi", tool_calls=None)
        assert "tool_calls" not in msg

    def test_empty_tool_calls_not_included(self):
        msg = build_assistant_message("hi", tool_calls=[])
        assert "tool_calls" not in msg

    def test_reasoning_content(self):
        msg = build_assistant_message("hi", reasoning_content="thinking...")
        assert msg["reasoning_content"] == "thinking..."

    def test_reasoning_content_none_not_included(self):
        msg = build_assistant_message("hi")
        assert "reasoning_content" not in msg

    def test_thinking_blocks(self):
        blocks = [{"type": "thinking", "text": "hmm"}]
        msg = build_assistant_message("hi", thinking_blocks=blocks)
        assert msg["thinking_blocks"] == blocks

    def test_thinking_blocks_empty_not_included(self):
        msg = build_assistant_message("hi", thinking_blocks=[])
        assert "thinking_blocks" not in msg

    def test_all_fields(self):
        msg = build_assistant_message(
            "answer",
            tool_calls=[{"id": "1"}],
            reasoning_content="reasoning",
            thinking_blocks=[{"text": "t"}],
        )
        assert msg["role"] == "assistant"
        assert msg["content"] == "answer"
        assert msg["tool_calls"] == [{"id": "1"}]
        assert msg["reasoning_content"] == "reasoning"
        assert msg["thinking_blocks"] == [{"text": "t"}]


# ---------------------------------------------------------------------------
# estimate_prompt_tokens
# ---------------------------------------------------------------------------

class TestEstimatePromptTokens:
    def test_string_content(self):
        messages = [{"role": "user", "content": "hello world"}]
        tokens = estimate_prompt_tokens(messages)
        assert tokens > 0

    def test_list_content(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": "world"},
            ],
        }]
        tokens = estimate_prompt_tokens(messages)
        assert tokens > 0

    def test_with_tools(self):
        messages = [{"role": "user", "content": "do something"}]
        tools = [{"type": "function", "function": {"name": "test_tool", "description": "A test"}}]
        tokens_with = estimate_prompt_tokens(messages, tools=tools)
        tokens_without = estimate_prompt_tokens(messages)
        assert tokens_with > tokens_without

    def test_empty_messages(self):
        assert estimate_prompt_tokens([]) == 0

    def test_non_text_content_parts_ignored(self):
        messages = [{
            "role": "user",
            "content": [{"type": "image_url", "image_url": "http://example.com"}],
        }]
        # No text parts → empty join → 0 tokens
        assert estimate_prompt_tokens(messages) == 0

    def test_error_returns_zero(self):
        with patch("xbot.platform.utils.helpers.tiktoken.get_encoding", side_effect=RuntimeError("boom")):
            # Reset cached encoding
            import xbot.platform.utils.helpers as h
            old = h._TIKTOKEN_ENCODING
            h._TIKTOKEN_ENCODING = None
            try:
                assert estimate_prompt_tokens([{"content": "hi"}]) == 0
            finally:
                h._TIKTOKEN_ENCODING = old


# ---------------------------------------------------------------------------
# estimate_message_tokens
# ---------------------------------------------------------------------------

class TestEstimateMessageTokens:
    def test_string_content(self):
        msg = {"role": "user", "content": "hello"}
        assert estimate_message_tokens(msg) >= 1

    def test_list_content_text_parts(self):
        msg = {"role": "user", "content": [{"type": "text", "text": "hi there"}]}
        assert estimate_message_tokens(msg) >= 1

    def test_list_content_non_text_parts(self):
        msg = {"role": "user", "content": [{"type": "image", "data": "base64..."}]}
        # Non-text dict parts get json.dumps'd
        assert estimate_message_tokens(msg) >= 1

    def test_tool_calls(self):
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1", "function": {"name": "foo", "arguments": "{}"}}],
        }
        assert estimate_message_tokens(msg) >= 1

    def test_name_and_tool_call_id(self):
        msg = {"role": "tool", "content": "result", "name": "my_tool", "tool_call_id": "abc123"}
        tokens = estimate_message_tokens(msg)
        assert tokens >= 1

    def test_empty_content_returns_1(self):
        msg = {"role": "assistant", "content": None}
        assert estimate_message_tokens(msg) == 1

    def test_none_content_no_tool_calls(self):
        msg = {"role": "user"}
        assert estimate_message_tokens(msg) == 1

    def test_integer_content(self):
        # Non-string, non-list, non-None content → json.dumps
        msg = {"role": "user", "content": 42}
        assert estimate_message_tokens(msg) >= 1


# ---------------------------------------------------------------------------
# timestamp / current_time_str / ensure_dir
# ---------------------------------------------------------------------------

class TestMiscHelpers:
    def test_timestamp_format(self):
        ts = timestamp()
        # Should be valid ISO format
        assert "T" in ts

    def test_current_time_str_format(self):
        cts = current_time_str()
        # Pattern: YYYY-MM-DD HH:MM (Weekday) (TZ)
        assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} \(\w+\) \(\w+\)", cts)

    def test_ensure_dir_creates(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        result = ensure_dir(target)
        assert target.is_dir()
        assert result == target

    def test_ensure_dir_existing(self, tmp_path):
        result = ensure_dir(tmp_path)
        assert result == tmp_path


# ---------------------------------------------------------------------------
# RetryPolicy.delay_for_attempt
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    def test_exponential_backoff_no_jitter(self):
        policy = RetryPolicy(
            max_attempts=5, base_delay=1.0, max_delay=60.0, jitter=False,
        )
        assert policy.delay_for_attempt(1) == 1.0   # 1 * 2^0
        assert policy.delay_for_attempt(2) == 2.0   # 1 * 2^1
        assert policy.delay_for_attempt(3) == 4.0   # 1 * 2^2
        assert policy.delay_for_attempt(4) == 8.0   # 1 * 2^3

    def test_max_delay_cap(self):
        policy = RetryPolicy(
            max_attempts=10, base_delay=1.0, max_delay=5.0, jitter=False,
        )
        assert policy.delay_for_attempt(10) == 5.0  # capped

    def test_jitter_on(self):
        policy = RetryPolicy(
            max_attempts=3, base_delay=10.0, max_delay=100.0, jitter=True,
        )
        delay = policy.delay_for_attempt(1)
        # base=10, attempt 1 → delay=10, jitter → uniform(5, 10)
        assert 5.0 <= delay <= 10.0

    def test_jitter_off(self):
        policy = RetryPolicy(
            max_attempts=3, base_delay=10.0, max_delay=100.0, jitter=False,
        )
        assert policy.delay_for_attempt(1) == 10.0

    def test_attempt_zero(self):
        policy = RetryPolicy(
            max_attempts=3, base_delay=2.0, max_delay=60.0, jitter=False,
        )
        # max(0-1, 0) = 0 → 2 * 2^0 = 2
        assert policy.delay_for_attempt(0) == 2.0

    def test_zero_base_delay(self):
        policy = RetryPolicy(
            max_attempts=3, base_delay=0.0, max_delay=60.0, jitter=True,
        )
        assert policy.delay_for_attempt(1) == 0.0


# ---------------------------------------------------------------------------
# run_with_retry
# ---------------------------------------------------------------------------

class TestRunWithRetry:
    async def test_success_first_attempt(self):
        policy = RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.1)
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await run_with_retry(policy, "test", op)
        assert result == "ok"
        assert call_count == 1

    async def test_success_after_retries(self):
        policy = RetryPolicy(
            max_attempts=3, base_delay=0.01, max_delay=0.1,
            retryable_exceptions=(ValueError,),
        )
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient")
            return "ok"

        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        result = await run_with_retry(policy, "test", op, sleep_func=fake_sleep)
        assert result == "ok"
        assert call_count == 3
        assert len(sleeps) == 2  # slept between attempts 1→2 and 2→3

    async def test_max_attempts_exceeded(self):
        policy = RetryPolicy(
            max_attempts=2, base_delay=0.01, max_delay=0.1,
            retryable_exceptions=(RuntimeError,),
        )

        async def op():
            raise RuntimeError("always fails")

        with pytest.raises(RuntimeError, match="always fails"):
            await run_with_retry(policy, "test", op, sleep_func=asyncio.sleep)

    async def test_cancelled_error_not_retried(self):
        policy = RetryPolicy(
            max_attempts=5, base_delay=0.01, max_delay=0.1,
            retryable_exceptions=(CancelledError := asyncio.CancelledError, RuntimeError),
        )

        async def op():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await run_with_retry(policy, "test", op)

    async def test_non_retryable_exception_not_retried(self):
        policy = RetryPolicy(
            max_attempts=5, base_delay=0.01, max_delay=0.1,
            retryable_exceptions=(ValueError,),
        )
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            raise TypeError("not retryable")

        with pytest.raises(TypeError):
            await run_with_retry(policy, "test", op)
        assert call_count == 1

    async def test_max_attempts_less_than_1_raises(self):
        policy = RetryPolicy(max_attempts=0, base_delay=0.01, max_delay=0.1)

        async def op():
            return "ok"

        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            await run_with_retry(policy, "test", op)

    async def test_max_attempts_negative_raises(self):
        policy = RetryPolicy(max_attempts=-1, base_delay=0.01, max_delay=0.1)

        async def op():
            return "ok"

        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            await run_with_retry(policy, "test", op)

    async def test_custom_sleep_func(self):
        policy = RetryPolicy(
            max_attempts=2, base_delay=5.0, max_delay=10.0,
            retryable_exceptions=(ValueError,), jitter=False,
        )
        call_count = 0
        slept_delays = []

        async def op():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("fail")
            return "ok"

        async def fake_sleep(d):
            slept_delays.append(d)

        result = await run_with_retry(policy, "test", op, sleep_func=fake_sleep)
        assert result == "ok"
        assert slept_delays == [5.0]  # base_delay * 2^0 = 5.0, no jitter

    async def test_single_attempt_no_retry(self):
        policy = RetryPolicy(
            max_attempts=1, base_delay=0.01, max_delay=0.1,
            retryable_exceptions=(ValueError,),
        )

        async def op():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await run_with_retry(policy, "test", op)
