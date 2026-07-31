"""Business boundary value tests for xbot.

These tests target specific numeric/time/string boundary conditions that are
classic sources of "silent logic bugs" — off-by-one errors, edge cases around
zero, unicode handling, and DST transitions.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from xbot.crew.output.truncate import OutputTruncator, TruncationStrategy, truncate_output
from xbot.platform.bus.events import (
    IM_CHANNELS,
    parse_session_key,
    to_canonical_session_key,
)
from xbot.platform.config.loader import _provider_name_to_snake
from xbot.runtime.session.conversation_store import ConversationSession, ConversationStore
from xbot.runtime.system.cron.service import _compute_next_run
from xbot.runtime.system.cron.types import CronSchedule


# ===========================================================================
# 1. Cron Schedule Boundaries
# ===========================================================================


class TestCronScheduleBoundaries:
    """Boundary conditions in _compute_next_run for 'at', 'every', and 'cron' kinds."""

    @pytest.mark.property
    def test_cron_next_run_at_exact_boundary_ms(self):
        """at_ms == now_ms (exactly equal) → should return None (not in future).

        The code uses strict '>' comparison: `schedule.at_ms > now_ms`.
        So when at_ms equals now_ms, the condition is False and None is returned.
        """
        now = 1_700_000_000_000  # arbitrary ms timestamp
        schedule = CronSchedule(kind="at", at_ms=now)
        result = _compute_next_run(schedule, now_ms=now)
        assert result is None, "at_ms == now_ms should NOT fire (not strictly in future)"

    @pytest.mark.property
    def test_cron_next_run_at_one_ms_future(self):
        """at_ms == now_ms + 1 → should return at_ms (just barely in the future).

        This is the boundary: the smallest positive difference that triggers a run.
        """
        now = 1_700_000_000_000
        at_ms = now + 1
        schedule = CronSchedule(kind="at", at_ms=at_ms)
        result = _compute_next_run(schedule, now_ms=now)
        assert result == at_ms

    @pytest.mark.property
    def test_cron_next_run_at_past(self):
        """at_ms < now_ms → should return None (already past)."""
        now = 1_700_000_000_000
        schedule = CronSchedule(kind="at", at_ms=now - 1)
        result = _compute_next_run(schedule, now_ms=now)
        assert result is None

    @pytest.mark.property
    def test_cron_every_zero_ms(self):
        """every_ms == 0 → should return None (guard: `every_ms <= 0`).

        The implementation checks `not schedule.every_ms or schedule.every_ms <= 0`.
        Zero is falsy in Python AND <= 0, so both checks reject it.
        """
        now = 1_700_000_000_000
        schedule = CronSchedule(kind="every", every_ms=0)
        result = _compute_next_run(schedule, now_ms=now)
        assert result is None

    @pytest.mark.property
    def test_cron_every_negative_ms(self):
        """every_ms == -1 → should return None (guard: `every_ms <= 0`)."""
        now = 1_700_000_000_000
        schedule = CronSchedule(kind="every", every_ms=-1)
        result = _compute_next_run(schedule, now_ms=now)
        assert result is None

    @pytest.mark.property
    def test_cron_every_one_ms(self):
        """every_ms == 1 (minimum positive) → should return now_ms + 1.

        The implementation does: `return now_ms + schedule.every_ms`.
        """
        now = 1_700_000_000_000
        schedule = CronSchedule(kind="every", every_ms=1)
        result = _compute_next_run(schedule, now_ms=now)
        assert result == now + 1

    @pytest.mark.property
    def test_cron_every_none(self):
        """every_ms == None → should return None.

        The guard `not schedule.every_ms` catches None.
        """
        now = 1_700_000_000_000
        schedule = CronSchedule(kind="every", every_ms=None)
        result = _compute_next_run(schedule, now_ms=now)
        assert result is None

    @pytest.mark.property
    def test_cron_very_large_now_ms(self):
        """now_ms near year 2100 → should still compute without overflow.

        Year 2100 in ms is approximately 4_102_444_800_000.
        'every' kind simply adds, so result should be now + every_ms.
        """
        # 2100-01-01T00:00:00 UTC in ms
        year_2100_ms = 4_102_444_800_000
        schedule = CronSchedule(kind="every", every_ms=60_000)
        result = _compute_next_run(schedule, now_ms=year_2100_ms)
        assert result == year_2100_ms + 60_000

    @pytest.mark.property
    def test_cron_very_large_now_ms_cron_expr(self):
        """Cron expression with now_ms in year 2100 → croniter handles it."""
        year_2100_ms = 4_102_444_800_000
        schedule = CronSchedule(kind="cron", expr="0 9 * * *", tz="UTC")
        result = _compute_next_run(schedule, now_ms=year_2100_ms)
        # Should return some future timestamp (the next 9:00 UTC after 2100-01-01)
        assert result is not None
        assert result > year_2100_ms

    @pytest.mark.property
    def test_cron_dst_spring_forward(self):
        """2:30 AM job on US DST spring-forward date (2:00 → 3:00).

        On 2024-03-10 in America/New_York, clocks jump from 2:00 AM to 3:00 AM.
        A "30 2 * * *" cron job (2:30 AM) does not exist on this day.
        croniter typically returns 3:30 AM or the next valid occurrence.

        This test documents the actual behavior rather than asserting a specific
        expectation, since croniter's DST handling is an external dependency.
        """
        from zoneinfo import ZoneInfo

        # 2024-03-10 01:00 AM EST (before spring forward)
        tz = ZoneInfo("America/New_York")
        dt = datetime(2024, 3, 10, 1, 0, 0, tzinfo=tz)
        now_ms = int(dt.timestamp() * 1000)

        schedule = CronSchedule(kind="cron", expr="30 2 * * *", tz="America/New_York")
        result = _compute_next_run(schedule, now_ms=now_ms)

        # croniter should return something — not None (the expression is valid)
        assert result is not None
        # The result should be in the future
        assert result > now_ms
        # Document: on spring-forward day, 2:30 doesn't exist.
        # croniter may return the equivalent wall-clock time in EDT (3:30 AM EDT)
        # or skip to the next day's 2:30 AM. Either way, it should not crash.
        result_dt = datetime.fromtimestamp(result / 1000, tz=tz)
        # The returned time should be on 2024-03-10 or 2024-03-11
        assert result_dt.date() in (dt.date(), dt.date() + timedelta(days=1))

    @pytest.mark.property
    def test_cron_dst_fall_back(self):
        """1:30 AM job on US DST fall-back date (2:00 → 1:00, 1:00-2:00 repeats).

        On 2024-11-03 in America/New_York, clocks fall back at 2:00 AM.
        A "30 1 * * *" cron job at 1:30 AM could theoretically fire twice.

        This test documents that croniter returns exactly one next-run time.
        """
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        # 2024-11-03 00:30 AM EDT (before fall back)
        dt = datetime(2024, 11, 3, 0, 30, 0, tzinfo=tz)
        now_ms = int(dt.timestamp() * 1000)

        schedule = CronSchedule(kind="cron", expr="30 1 * * *", tz="America/New_York")
        result = _compute_next_run(schedule, now_ms=now_ms)

        assert result is not None
        assert result > now_ms
        # croniter returns a single "next" time — it does NOT fire twice.
        # The first 1:30 AM (EDT) should be returned.
        result_dt = datetime.fromtimestamp(result / 1000, tz=tz)
        assert result_dt.hour == 1
        assert result_dt.minute == 30
        assert result_dt.date() == dt.date()

    @pytest.mark.property
    def test_cron_invalid_expr_returns_none(self):
        """Invalid cron expression → should return None (exception caught)."""
        now = 1_700_000_000_000
        schedule = CronSchedule(kind="cron", expr="invalid cron", tz="UTC")
        result = _compute_next_run(schedule, now_ms=now)
        assert result is None

    @pytest.mark.property
    def test_cron_at_ms_none(self):
        """kind='at' with at_ms=None → return None (guard: `schedule.at_ms and ...`)."""
        now = 1_700_000_000_000
        schedule = CronSchedule(kind="at", at_ms=None)
        result = _compute_next_run(schedule, now_ms=now)
        assert result is None


# ===========================================================================
# 2. Truncation Boundaries
# ===========================================================================


class TestTruncationBoundaries:
    """Boundary conditions for OutputTruncator._truncate_hard and truncate()."""

    @pytest.mark.property
    def test_truncate_exactly_at_limit(self):
        """content length == max_length → no truncation, content unchanged.

        The check is `original_length <= max_length`, so equal means no truncation.
        """
        content = "a" * 100
        result = truncate_output(content, max_length=100, strategy=TruncationStrategy.HARD)
        assert result.truncated is False
        assert result.content == content
        assert result.strategy == "none"

    @pytest.mark.property
    def test_truncate_one_over_limit(self):
        """content length == max_length + 1 → truncation kicks in.

        With HARD strategy, the marker is '\\n\\n... (output truncated)' (24 chars).
        Output: content[:max_length - 24] + marker
        """
        max_length = 100
        content = "a" * (max_length + 1)
        result = truncate_output(content, max_length=max_length, strategy=TruncationStrategy.HARD)
        assert result.truncated is True
        assert result.strategy == "hard"
        # Hard truncation: content[:76] + '\n\n... (output truncated)' (marker is 24 chars)
        marker = "\n\n... (output truncated)"
        expected = "a" * (max_length - len(marker)) + marker
        assert result.content == expected

    @pytest.mark.property
    def test_truncate_max_length_zero(self):
        """max_length=0 → _truncate_hard returns empty string.

        The code: `if max_length <= 0: truncated_content = ""`
        But note: the truncation path is only entered when len(content) > max_length.
        For content="a" and max_length=0, len("a") > 0 is True, so truncation runs.
        """
        content = "a"
        result = truncate_output(content, max_length=0, strategy=TruncationStrategy.HARD)
        assert result.truncated is True
        assert result.content == ""

    @pytest.mark.property
    def test_truncate_max_length_zero_empty_content(self):
        """max_length=0 with empty content → no truncation needed (0 <= 0)."""
        content = ""
        result = truncate_output(content, max_length=0, strategy=TruncationStrategy.HARD)
        assert result.truncated is False
        assert result.content == ""

    @pytest.mark.property
    def test_truncate_max_length_one(self):
        """max_length=1 → hard truncation with max_length < 20 path.

        Code: marker = "...", max_length(1) <= len(marker)(3),
        so truncated_content = marker[:max_length] = "."
        """
        content = "hello world"
        result = truncate_output(content, max_length=1, strategy=TruncationStrategy.HARD)
        assert result.truncated is True
        # max_length=1 <= len("...") so returns "..."[:1] = "."
        assert result.content == "."

    @pytest.mark.property
    def test_truncate_max_length_three(self):
        """max_length=3 → equals len("..."), returns full marker "..."."""
        content = "hello world"
        result = truncate_output(content, max_length=3, strategy=TruncationStrategy.HARD)
        assert result.truncated is True
        assert result.content == "..."

    @pytest.mark.property
    def test_truncate_max_length_four(self):
        """max_length=4 > len("..."), returns content[:1] + "..." = "h..."."""
        content = "hello world"
        result = truncate_output(content, max_length=4, strategy=TruncationStrategy.HARD)
        assert result.truncated is True
        # max_length(4) > len("...")(3) → content[:4-3] + "..." = "h..."
        assert result.content == "h..."

    @pytest.mark.property
    def test_truncate_max_length_nineteen(self):
        """max_length=19 (just under 20) → uses short marker path.

        content[:19-3] + "..." = content[:16] + "..."
        """
        content = "a" * 100
        result = truncate_output(content, max_length=19, strategy=TruncationStrategy.HARD)
        assert result.truncated is True
        assert result.content == "a" * 16 + "..."

    @pytest.mark.property
    def test_truncate_max_length_twenty(self):
        """max_length=20: below long marker threshold (24), uses short '...' marker.

        Since 20 < 24 (long marker length), falls into short marker branch:
        content[:20-3] + '...' = 'a'*17 + '...'
        """
        content = "a" * 100
        result = truncate_output(content, max_length=20, strategy=TruncationStrategy.HARD)
        assert result.truncated is True
        # Short marker branch: content[:17] + "..."
        assert result.content == "a" * 17 + "..."
        assert len(result.content) == 20

    @pytest.mark.property
    def test_truncate_unicode_boundary(self):
        """Multi-byte UTF-8 chars (emoji) at truncation boundary.

        Python strings are unicode, so slicing by character index never splits
        a code point. This verifies emoji is not corrupted by truncation.
        """
        # 10 emoji chars (each is 1 Python char but multi-byte in UTF-8)
        emoji = "\U0001f389"  # 🎉
        content = emoji * 10  # 10 chars
        # Set max_length = 5 which is < 20, uses short marker "..."
        result = truncate_output(content, max_length=5, strategy=TruncationStrategy.HARD)
        assert result.truncated is True
        # max_length=5 > len("...")(3) → content[:2] + "..." = "🎉🎉..."
        assert result.content == emoji * 2 + "..."
        # Verify no broken surrogate pairs
        result.content.encode("utf-8")  # should not raise

    @pytest.mark.property
    def test_truncate_unicode_at_exact_limit(self):
        """Emoji content exactly at limit → no truncation."""
        emoji = "\U0001f389"  # 🎉
        content = emoji * 5  # 5 chars
        result = truncate_output(content, max_length=5, strategy=TruncationStrategy.HARD)
        assert result.truncated is False
        assert result.content == content

    @pytest.mark.property
    def test_truncate_empty_string(self):
        """Empty input → returns empty, not truncated."""
        result = truncate_output("", max_length=100, strategy=TruncationStrategy.HARD)
        assert result.truncated is False
        assert result.content == ""
        assert result.original_length == 0


# ===========================================================================
# 3. ConversationStore Edge Cases
# ===========================================================================


class TestConversationStoreEdgeCases:
    """Edge cases for ConversationStore caching, file I/O, and key handling."""

    @pytest.mark.property
    def test_store_max_cache_size_one(self, tmp_path: Path):
        """Only 1 slot in cache. Get A, get B → A evicted. Get A again → from disk.

        With max_cache_size=1, after inserting B the cache evicts A (the oldest
        non-dirty entry). Re-fetching A loads from disk.
        """
        store = ConversationStore(workspace=tmp_path, max_cache_size=1)

        # Create and save session A
        session_a = store.get_or_create("test:a")
        session_a.add_message("user", "hello from A")
        store.save(session_a)

        # Create and save session B — this should evict A from cache
        session_b = store.get_or_create("test:b")
        session_b.add_message("user", "hello from B")
        store.save(session_b)

        # A should be evicted from cache now
        # (after save(session_b), cache has test:b; eviction runs)
        # Fetch A again — should reload from disk
        session_a_reloaded = store.get_or_create("test:a")
        assert session_a_reloaded.messages[0]["content"] == "hello from A"
        assert session_a_reloaded.messages[0]["role"] == "user"

    @pytest.mark.property
    def test_store_max_cache_size_zero(self, tmp_path: Path):
        """max_cache_size=0 → every get_or_create triggers eviction immediately.

        With capacity 0, _evict_if_needed(reserve=1) sees overflow = 0 + 1 - 0 = 1,
        but there are no candidates yet (cache is empty before insert), so the item
        is inserted. On save(), eviction runs again and removes it (since it's clean
        after save). Next get reloads from disk.
        """
        store = ConversationStore(workspace=tmp_path, max_cache_size=0)

        session = store.get_or_create("test:zero")
        session.add_message("user", "cached?")
        store.save(session)

        # After save, cache should be evicted (overflow = 1 + 0 - 0 = 1)
        # The session is clean after save, so it's evictable
        # But _evict_if_needed is called with reserve=0 in save(), overflow = 1 - 0 = 1
        # Re-fetch should work from disk
        session2 = store.get_or_create("test:zero")
        assert session2.messages[0]["content"] == "cached?"

    @pytest.mark.property
    def test_store_session_key_with_colons(self, tmp_path: Path):
        """Key with multiple colons → hashed filename, loads correctly.

        The filename is sha256(key.encode('utf-8')).hexdigest() + '.jsonl',
        so colons in the key don't cause filesystem issues.
        """
        key = "im:slack:C123:thread:456"
        store = ConversationStore(workspace=tmp_path, max_cache_size=10)

        session = store.get_or_create(key)
        session.add_message("user", "threaded message")
        store.save(session)

        # Verify file exists with hashed name
        import hashlib
        expected_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        expected_path = tmp_path / "sessions" / f"{expected_hash}.jsonl"
        assert expected_path.exists()

        # Clear cache, reload from disk
        store.invalidate(key)
        reloaded = store.get_or_create(key)
        assert reloaded.messages[0]["content"] == "threaded message"
        assert reloaded.key == key

    @pytest.mark.property
    def test_store_session_key_unicode(self, tmp_path: Path):
        """Key with unicode characters → hashed filename handles UTF-8 correctly."""
        key = "im:telegram:用户123"
        store = ConversationStore(workspace=tmp_path, max_cache_size=10)

        session = store.get_or_create(key)
        session.add_message("user", "你好")
        store.save(session)

        # Verify hashed filename
        import hashlib
        expected_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        expected_path = tmp_path / "sessions" / f"{expected_hash}.jsonl"
        assert expected_path.exists()

        # Roundtrip
        store.invalidate(key)
        reloaded = store.get_or_create(key)
        assert reloaded.messages[0]["content"] == "你好"
        assert reloaded.key == key

    @pytest.mark.property
    def test_store_session_key_very_long(self, tmp_path: Path):
        """REGRESSION: Key with 1000 chars now works thanks to OSError guard in _load.

        Previously, _session_paths_for_read constructed a legacy fallback path
        using _safe_session_filename(key) which exceeded the OS filename limit
        (255 bytes), causing .exists() to raise OSError. Fixed by catching
        OSError and skipping the problematic path.
        """
        key = "x" * 1000
        store = ConversationStore(workspace=tmp_path, max_cache_size=10)

        # Should no longer raise — the fix catches OSError on overly long paths
        session = store.get_or_create(key)
        session.messages.append({"role": "user", "content": "long key test"})
        session._new_messages.append({"role": "user", "content": "long key test"})
        store.save(session)
        store.invalidate(key)
        reloaded = store.get(key)
        assert reloaded is not None
        assert reloaded.messages[0]["content"] == "long key test"

    @pytest.mark.property
    def test_store_session_key_moderately_long(self, tmp_path: Path):
        """Key with 200 chars → works fine (safe_filename stays under 255 limit)."""
        key = "a" * 200
        store = ConversationStore(workspace=tmp_path, max_cache_size=10)

        session = store.get_or_create(key)
        session.add_message("user", "moderately long key")
        store.save(session)

        # Verify the hashed filename is used for the canonical path
        import hashlib
        expected_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        expected_path = tmp_path / "sessions" / f"{expected_hash}.jsonl"
        assert expected_path.exists()
        assert len(expected_hash) == 64

        store.invalidate(key)
        reloaded = store.get_or_create(key)
        assert reloaded.messages[0]["content"] == "moderately long key"

    @pytest.mark.property
    def test_store_empty_session_save_load(self, tmp_path: Path):
        """Save an empty session (0 messages), reload → still valid, messages=[]."""
        store = ConversationStore(workspace=tmp_path, max_cache_size=10)

        session = store.get_or_create("test:empty")
        assert session.messages == []
        # Mark dirty to force a full save even with no messages
        session.mark_metadata_dirty()
        store.save(session)

        store.invalidate("test:empty")
        reloaded = store.get("test:empty")
        assert reloaded is not None
        assert reloaded.messages == []
        assert reloaded.key == "test:empty"

    @pytest.mark.property
    def test_store_message_with_empty_content(self, tmp_path: Path):
        """Add message with content='' → saved and loaded correctly."""
        store = ConversationStore(workspace=tmp_path, max_cache_size=10)

        session = store.get_or_create("test:empty_content")
        session.add_message("user", "")
        store.save(session)

        store.invalidate("test:empty_content")
        reloaded = store.get("test:empty_content")
        assert reloaded is not None
        assert len(reloaded.messages) == 1
        assert reloaded.messages[0]["role"] == "user"
        assert reloaded.messages[0]["content"] == ""

    @pytest.mark.property
    def test_store_get_nonexistent_returns_none(self, tmp_path: Path):
        """get() for a key that doesn't exist returns None (not creating)."""
        store = ConversationStore(workspace=tmp_path, max_cache_size=10)
        result = store.get("nonexistent:key")
        assert result is None

    @pytest.mark.property
    def test_store_special_json_chars_in_content(self, tmp_path: Path):
        """Message content with JSON-special chars (quotes, backslashes, newlines)."""
        store = ConversationStore(workspace=tmp_path, max_cache_size=10)

        session = store.get_or_create("test:json_special")
        tricky_content = 'She said "hello\\nworld"\ttab\x00null'
        session.add_message("user", tricky_content)
        store.save(session)

        store.invalidate("test:json_special")
        reloaded = store.get("test:json_special")
        assert reloaded is not None
        assert reloaded.messages[0]["content"] == tricky_content


# ===========================================================================
# 4. Session Key Parse Boundaries
# ===========================================================================


class TestSessionKeyParseBoundaries:
    """Boundary conditions for parse_session_key and to_canonical_session_key."""

    @pytest.mark.property
    def test_parse_empty_string(self):
        """'' → ('', '').

        The code: `if not key: return '', ''`.
        """
        channel, chat_id = parse_session_key("")
        assert channel == ""
        assert chat_id == ""

    @pytest.mark.property
    def test_parse_no_colon(self):
        """'abc' → ('abc', '').

        No 'im:' prefix, no ':' found → falls through to `return key, ''`.
        """
        channel, chat_id = parse_session_key("abc")
        assert channel == "abc"
        assert chat_id == ""

    @pytest.mark.property
    def test_parse_only_colon(self):
        """':' → ('', '').

        No 'im:' prefix, ':' found → split(':', 1) gives ('', '').
        """
        channel, chat_id = parse_session_key(":")
        assert channel == ""
        assert chat_id == ""

    @pytest.mark.property
    def test_parse_multiple_colons(self):
        """'im:slack:C123:thread' → ('slack', 'C123:thread').

        Strip 'im:' → 'slack:C123:thread'.
        Split on first ':' → ('slack', 'C123:thread').
        """
        channel, chat_id = parse_session_key("im:slack:C123:thread")
        assert channel == "slack"
        assert chat_id == "C123:thread"

    @pytest.mark.property
    def test_parse_im_prefix_only(self):
        """'im:' → ('', '').

        Strip 'im:' → ''. Then `if ':' in key` is False for empty string.
        Fall through to `return key, ''` → ('', '').
        """
        channel, chat_id = parse_session_key("im:")
        assert channel == ""
        assert chat_id == ""

    @pytest.mark.property
    def test_parse_im_prefix_no_chat_id(self):
        """'im:slack' → ('slack', '').

        Strip 'im:' → 'slack'. No ':' → return ('slack', '').
        """
        channel, chat_id = parse_session_key("im:slack")
        assert channel == "slack"
        assert chat_id == ""

    @pytest.mark.property
    def test_parse_im_prefix_with_colon_no_chat_id(self):
        """'im:slack:' → ('slack', '').

        Strip 'im:' → 'slack:'. Split on ':' → ('slack', '').
        """
        channel, chat_id = parse_session_key("im:slack:")
        assert channel == "slack"
        assert chat_id == ""

    @pytest.mark.property
    def test_format_then_parse_with_special_chars(self):
        """Roundtrip: to_canonical_session_key → parse_session_key preserves data.

        channel='slack', chat_id='C123/thread#1' → key='im:slack:C123/thread#1'
        → parse → ('slack', 'C123/thread#1')
        """
        channel = "slack"
        chat_id = "C123/thread#1"
        key = to_canonical_session_key(channel, chat_id)
        assert key == "im:slack:C123/thread#1"

        parsed_channel, parsed_chat_id = parse_session_key(key)
        assert parsed_channel == channel
        assert parsed_chat_id == chat_id

    @pytest.mark.property
    def test_format_then_parse_non_im_channel(self):
        """Non-IM channel roundtrips: channel='web', chat_id='session123'.

        to_canonical → 'web:session123'. parse → ('web', 'session123').
        """
        channel = "web"
        chat_id = "session123"
        key = to_canonical_session_key(channel, chat_id)
        assert key == "web:session123"

        parsed_channel, parsed_chat_id = parse_session_key(key)
        assert parsed_channel == channel
        assert parsed_chat_id == chat_id

    @pytest.mark.property
    def test_parse_double_im_prefix(self):
        """'im:im:something' → first strip gives 'im:something', then split → ('im', 'something').

        This is a quirky edge case: the key starts with 'im:' so we strip it once.
        """
        channel, chat_id = parse_session_key("im:im:something")
        assert channel == "im"
        assert chat_id == "something"

    @pytest.mark.property
    def test_canonical_key_with_empty_channel_and_chat_id(self):
        """Empty channel + empty chat_id → ':' (non-IM, format is 'channel:chat_id')."""
        key = to_canonical_session_key("", "")
        # normalized_channel = "", not in IM_CHANNELS → f"{channel}:{chat_id}" = ":"
        assert key == ":"

    @pytest.mark.property
    def test_canonical_key_override_takes_precedence(self):
        """When override is provided, it takes precedence over channel:chat_id."""
        key = to_canonical_session_key("slack", "C123", override="custom:session")
        # override doesn't start with 'im:', and 'slack' is in IM_CHANNELS
        # but override doesn't start with 'slack:' → return normalized_override
        assert key == "custom:session"

    @pytest.mark.property
    def test_canonical_key_override_with_im_prefix(self):
        """Override starting with 'im:' is returned as-is."""
        key = to_canonical_session_key("slack", "C123", override="im:telegram:456")
        assert key == "im:telegram:456"


# ===========================================================================
# 5. Config/Capacity Boundaries — _provider_name_to_snake
# ===========================================================================


class TestProviderNameToSnakeBoundaries:
    """Boundary conditions for _provider_name_to_snake conversion."""

    @pytest.mark.property
    def test_provider_name_to_snake_empty_string(self):
        """'' → ''.

        No characters to iterate, output list is empty, join gives ''.
        """
        assert _provider_name_to_snake("") == ""

    @pytest.mark.property
    def test_provider_name_to_snake_all_uppercase(self):
        """'ABC' → 'a_b_c'.

        A(index=0, no underscore) → 'a'
        B(index=1, uppercase) → '_b'
        C(index=2, uppercase) → '_c'
        Result: 'a_b_c'
        """
        assert _provider_name_to_snake("ABC") == "a_b_c"

    @pytest.mark.property
    def test_provider_name_to_snake_already_snake(self):
        """'my_provider' → 'my_provider' (unchanged).

        All lowercase, underscores stay, no dashes.
        """
        assert _provider_name_to_snake("my_provider") == "my_provider"

    @pytest.mark.property
    def test_provider_name_to_snake_with_numbers(self):
        """'gpt4Turbo' → 'gpt4_turbo'.

        '4' is not uppercase so no underscore before it.
        'T' at index 4 is uppercase → '_t'.
        """
        assert _provider_name_to_snake("gpt4Turbo") == "gpt4_turbo"

    @pytest.mark.property
    def test_provider_name_to_snake_unicode(self):
        """'模型Provider' → '模型_provider'.

        '模' and '型' are not uppercase (isupper() is False for CJK).
        'P' at index 2 is uppercase → '_p'.
        Result: '模型_provider'
        """
        result = _provider_name_to_snake("模型Provider")
        assert result == "模型_provider"

    @pytest.mark.property
    def test_provider_name_to_snake_with_dashes(self):
        """'my-provider' → 'my_provider' (dashes converted to underscores).

        The final .replace('-', '_') handles dashes.
        """
        assert _provider_name_to_snake("my-provider") == "my_provider"

    @pytest.mark.property
    def test_provider_name_to_snake_camel_case(self):
        """'aliyunCodingPlan' → 'aliyun_coding_plan'."""
        assert _provider_name_to_snake("aliyunCodingPlan") == "aliyun_coding_plan"

    @pytest.mark.property
    def test_provider_name_to_snake_single_char_lower(self):
        """'a' → 'a'."""
        assert _provider_name_to_snake("a") == "a"

    @pytest.mark.property
    def test_provider_name_to_snake_single_char_upper(self):
        """'A' → 'a' (index=0, no underscore prepended)."""
        assert _provider_name_to_snake("A") == "a"

    @pytest.mark.property
    def test_provider_name_to_snake_consecutive_uppercase(self):
        """'GPTModel' → 'g_p_t_model'.

        Each uppercase char at index > 0 gets an underscore prefix.
        Note: this is naive camelCase splitting, not acronym-aware.
        """
        assert _provider_name_to_snake("GPTModel") == "g_p_t_model"

    @pytest.mark.property
    def test_provider_name_to_snake_dash_and_camel(self):
        """'my-GPTModel' → 'my__g_p_t_model'.

        Dash → underscore, then G/P/T each get underscore prefix.
        The double underscore is a known artifact of this naive algorithm.
        """
        result = _provider_name_to_snake("my-GPTModel")
        # '-' becomes '_', then 'G' at next pos is uppercase → '_g'
        # So 'my-G' → 'my_' + '_g' = 'my__g'
        assert result == "my__g_p_t_model"
