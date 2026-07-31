"""Property-based tests for pure functions across xbot.

Uses Hypothesis to verify algebraic properties (idempotence, monotonicity,
bounds, determinism) of key pure functions without relying on specific inputs.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from hypothesis import given, settings, assume, strategies as st

from xbot.runtime.system.cron.service import _compute_next_run
from xbot.runtime.system.cron.types import CronSchedule
from xbot.platform.config.loader import _provider_name_to_snake, _migrate_config
from xbot.crew.output.truncate import truncate_output, TruncationStrategy
from xbot.runtime.session.conversation_store import ConversationSession, ConversationStore
from xbot.platform.utils.helpers import estimate_message_tokens


# ---------------------------------------------------------------------------
# Marker: property tests (not registered in pyproject.toml yet)
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.filterwarnings("ignore::hypothesis.errors.NonInteractiveExampleWarning")


# ===========================================================================
# 1. Cron _compute_next_run
# ===========================================================================


class TestComputeNextRunEveryKind:
    """Properties for _compute_next_run with 'every' schedule kind."""

    @given(
        every_ms=st.integers(min_value=1, max_value=2**40),
        now_ms=st.integers(min_value=0, max_value=2**40),
    )
    @settings(max_examples=200)
    def test_every_positive_interval(self, every_ms: int, now_ms: int):
        """For any every_ms > 0 and now_ms >= 0, result == now_ms + every_ms."""
        schedule = CronSchedule(kind="every", every_ms=every_ms)
        result = _compute_next_run(schedule, now_ms)
        assert result == now_ms + every_ms

    @given(
        every_ms=st.integers(min_value=-2**30, max_value=0),
        now_ms=st.integers(min_value=0, max_value=2**40),
    )
    @settings(max_examples=200)
    def test_every_non_positive_interval_returns_none(self, every_ms: int, now_ms: int):
        """For every_ms <= 0, result must be None (invalid interval)."""
        schedule = CronSchedule(kind="every", every_ms=every_ms)
        result = _compute_next_run(schedule, now_ms)
        assert result is None


class TestComputeNextRunAtKind:
    """Properties for _compute_next_run with 'at' schedule kind."""

    @given(
        now_ms=st.integers(min_value=0, max_value=2**40),
        delta=st.integers(min_value=1, max_value=2**40),
    )
    @settings(max_examples=200)
    def test_at_future_returns_at_ms(self, now_ms: int, delta: int):
        """For at_ms > now_ms, result == at_ms (the scheduled time)."""
        at_ms = now_ms + delta
        schedule = CronSchedule(kind="at", at_ms=at_ms)
        result = _compute_next_run(schedule, now_ms)
        assert result == at_ms

    @given(
        now_ms=st.integers(min_value=1, max_value=2**40),
        delta=st.integers(min_value=0, max_value=2**40),
    )
    @settings(max_examples=200)
    def test_at_past_or_equal_returns_none(self, now_ms: int, delta: int):
        """For at_ms <= now_ms, result is None (already passed)."""
        at_ms = now_ms - delta  # at_ms <= now_ms
        schedule = CronSchedule(kind="at", at_ms=at_ms)
        result = _compute_next_run(schedule, now_ms)
        assert result is None


class TestComputeNextRunCronKind:
    """Properties for _compute_next_run with 'cron' schedule kind."""

    VALID_EXPRESSIONS = ["*/5 * * * *", "0 9 * * 1", "30 2 * * *", "0 0 1 * *", "15 10 * * *"]

    @given(
        expr=st.sampled_from(VALID_EXPRESSIONS),
        now_ms=st.integers(min_value=1_000_000_000_000, max_value=2_000_000_000_000),
    )
    @settings(max_examples=200)
    def test_cron_valid_always_future(self, expr: str, now_ms: int):
        """For valid cron expressions, next run is always strictly after now_ms."""
        schedule = CronSchedule(kind="cron", expr=expr, tz="UTC")
        result = _compute_next_run(schedule, now_ms)
        assert result is not None
        assert result > now_ms

    @given(
        expr=st.sampled_from(VALID_EXPRESSIONS),
        now1_ms=st.integers(min_value=1_000_000_000_000, max_value=1_500_000_000_000),
        delta=st.integers(min_value=1, max_value=500_000_000_000),
    )
    @settings(max_examples=200)
    def test_cron_monotonic(self, expr: str, now1_ms: int, delta: int):
        """For the same cron expression, if now1 < now2, then next_run(now1) <= next_run(now2)."""
        now2_ms = now1_ms + delta
        schedule = CronSchedule(kind="cron", expr=expr, tz="UTC")
        r1 = _compute_next_run(schedule, now1_ms)
        r2 = _compute_next_run(schedule, now2_ms)
        assert r1 is not None and r2 is not None
        assert r1 <= r2


# ===========================================================================
# 2. Config migration _provider_name_to_snake
# ===========================================================================


class TestProviderNameToSnake:
    """Properties for _provider_name_to_snake string transformation."""

    @given(
        s=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=50,
        )
    )
    @settings(max_examples=200)
    def test_idempotent_on_output(self, s: str):
        """Applying the function twice yields the same result as once (idempotent on output)."""
        once = _provider_name_to_snake(s)
        twice = _provider_name_to_snake(once)
        assert twice == once

    @given(
        s=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=50,
        )
    )
    @settings(max_examples=200)
    def test_no_ascii_uppercase_in_output(self, s: str):
        """Result must never contain ASCII uppercase letters (non-ASCII preserved for idempotence)."""
        result = _provider_name_to_snake(s)
        ascii_upper = [c for c in result if c.isascii() and c.isupper()]
        assert ascii_upper == [], f"ASCII uppercase found in result: {result!r}"

    @given(
        s=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=50,
        )
    )
    @settings(max_examples=200)
    def test_no_hyphens_in_output(self, s: str):
        """Result must never contain hyphens (replaced by underscores)."""
        result = _provider_name_to_snake(s)
        assert "-" not in result, f"Hyphen found in result: {result!r}"

    @given(
        s=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
            min_size=1,
            max_size=50,
        )
    )
    @settings(max_examples=200)
    def test_length_bounded(self, s: str):
        """Output length is at most 2x input (each uppercase char adds one underscore)."""
        result = _provider_name_to_snake(s)
        assert len(result) <= 2 * len(s)


# ===========================================================================
# 3. Config migration _migrate_config idempotence
# ===========================================================================


# Strategy: generate config-like dicts with realistic structure
_provider_strategy = st.fixed_dictionaries({
    "apiKey": st.text(min_size=0, max_size=20, alphabet="abcdefABCDEF0123456789"),
})

_config_strategy = st.fixed_dictionaries(
    {},
    optional={
        "providers": st.fixed_dictionaries(
            {},
            optional={
                "anthropic": _provider_strategy,
                "customProviders": st.dictionaries(
                    keys=st.text(
                        min_size=1, max_size=15,
                        alphabet=st.characters(whitelist_categories=("L", "N")),
                    ),
                    values=_provider_strategy,
                    max_size=3,
                ),
            },
        ),
        "tools": st.fixed_dictionaries(
            {},
            optional={
                "exec": st.fixed_dictionaries(
                    {},
                    optional={
                        "restrictToWorkspace": st.booleans(),
                    },
                ),
                "restrictToWorkspace": st.booleans(),
            },
        ),
        "agents": st.fixed_dictionaries(
            {},
            optional={
                "defaults": st.fixed_dictionaries(
                    {},
                    optional={
                        "provider": st.sampled_from(["auto", "anthropic", "openai", "MyProvider"]),
                        "availableModels": st.lists(
                            st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnop-_0123456789"),
                            max_size=4,
                        ),
                    },
                ),
            },
        ),
    },
)


class TestMigrateConfigIdempotence:
    """_migrate_config applied twice gives the same result as once."""

    @given(data=_config_strategy)
    @settings(max_examples=200)
    def test_idempotent(self, data: dict):
        """Migration is idempotent: migrating an already-migrated config changes nothing."""
        first = _migrate_config(deepcopy(data))
        second = _migrate_config(deepcopy(first))
        assert second == first


# ===========================================================================
# 4. truncate_output
# ===========================================================================


class TestTruncateOutput:
    """Properties for truncate_output with various strategies."""

    @given(
        content=st.text(min_size=0, max_size=5000),
        max_length=st.integers(min_value=10, max_value=10000),
    )
    @settings(max_examples=200)
    def test_hard_strategy_never_exceeds_limit(self, content: str, max_length: int):
        """For strategy=HARD, output length must never exceed max_length."""
        result = truncate_output(content, max_length=max_length, strategy=TruncationStrategy.HARD)
        assert len(result.content) <= max_length

    @given(
        content=st.text(min_size=0, max_size=5000),
        max_length=st.integers(min_value=10, max_value=10000),
    )
    @settings(max_examples=200)
    def test_identity_for_short_input(self, content: str, max_length: int):
        """If content fits within max_length, it is returned unchanged and not truncated."""
        assume(len(content) <= max_length)
        result = truncate_output(content, max_length=max_length, strategy=TruncationStrategy.HARD)
        assert result.content == content
        assert result.truncated is False

    @given(
        content=st.text(min_size=1, max_size=5000),
        max_length=st.integers(min_value=10, max_value=10000),
    )
    @settings(max_examples=200)
    def test_non_empty_output(self, content: str, max_length: int):
        """For non-empty input and max_length >= 10, output is never empty."""
        result = truncate_output(content, max_length=max_length, strategy=TruncationStrategy.HARD)
        assert len(result.content) > 0


# ===========================================================================
# 5. _filter_orphan_tool_results
# ===========================================================================


def _make_user_msg(content: str = "hello") -> dict:
    return {"role": "user", "content": content}


def _make_assistant_msg(content: str = "ok", tool_calls: list | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return msg


def _make_tool_msg(tool_call_id: str, content: str = "result") -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


# Strategies for generating message sequences
_tool_call_id_st = st.text(
    min_size=1, max_size=20,
    alphabet=st.characters(whitelist_categories=("L", "N")),
)

_user_msg_st = st.builds(_make_user_msg, content=st.text(min_size=1, max_size=30))

_tool_call_st = st.fixed_dictionaries({
    "id": _tool_call_id_st,
    "type": st.just("function"),
    "function": st.fixed_dictionaries({
        "name": st.text(min_size=1, max_size=10, alphabet="abcdefghij"),
        "arguments": st.just("{}"),
    }),
})

_assistant_msg_st = st.builds(
    _make_assistant_msg,
    content=st.text(min_size=0, max_size=30),
    tool_calls=st.one_of(st.none(), st.lists(_tool_call_st, min_size=1, max_size=3)),
)

_tool_msg_st = st.builds(
    _make_tool_msg,
    tool_call_id=_tool_call_id_st,
    content=st.text(min_size=1, max_size=30),
)

_message_st = st.one_of(_user_msg_st, _assistant_msg_st, _tool_msg_st)
_message_list_st = st.lists(_message_st, min_size=0, max_size=20)


class TestFilterOrphanToolResults:
    """Properties for ConversationSession._filter_orphan_tool_results."""

    @given(messages=_message_list_st)
    @settings(max_examples=200)
    def test_idempotent(self, messages: list):
        """Filtering twice yields the same result as filtering once."""
        first = ConversationSession._filter_orphan_tool_results(messages)
        second = ConversationSession._filter_orphan_tool_results(first)
        assert second == first

    @given(messages=_message_list_st)
    @settings(max_examples=200)
    def test_never_removes_non_tool_messages(self, messages: list):
        """User and assistant messages always survive filtering."""
        result = ConversationSession._filter_orphan_tool_results(messages)
        original_non_tool = [m for m in messages if m.get("role") != "tool"]
        result_non_tool = [m for m in result if m.get("role") != "tool"]
        assert result_non_tool == original_non_tool

    @given(messages=_message_list_st)
    @settings(max_examples=200)
    def test_output_is_subsequence(self, messages: list):
        """Output is always a subsequence of input (order preserved)."""
        result = ConversationSession._filter_orphan_tool_results(messages)
        # Check subsequence property
        it = iter(messages)
        for msg in result:
            for candidate in it:
                if candidate is msg:
                    break
            else:
                pytest.fail("Result is not a subsequence of input")

    @given(messages=_message_list_st)
    @settings(max_examples=200)
    def test_valid_tool_results_survive(self, messages: list):
        """Tool results with a matching tool_call_id in a preceding assistant survive."""
        # Collect declared tool_call_ids from assistant messages
        declared_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        declared_ids.add(str(tc["id"]))

        result = ConversationSession._filter_orphan_tool_results(messages)
        result_tool_ids = {
            str(m["tool_call_id"])
            for m in result
            if m.get("role") == "tool" and m.get("tool_call_id")
        }

        # Every tool message in the input whose id IS declared must survive
        for msg in messages:
            if msg.get("role") == "tool" and msg.get("tool_call_id"):
                if str(msg["tool_call_id"]) in declared_ids:
                    assert str(msg["tool_call_id"]) in result_tool_ids


# ===========================================================================
# 6. _hashed_session_filename
# ===========================================================================


class TestHashedSessionFilename:
    """Properties for ConversationStore._hashed_session_filename."""

    @given(key=st.text(min_size=1, max_size=200))
    @settings(max_examples=200)
    def test_deterministic(self, key: str):
        """Same key always produces the same filename."""
        a = ConversationStore._hashed_session_filename(key)
        b = ConversationStore._hashed_session_filename(key)
        assert a == b

    @given(key=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_ends_with_jsonl(self, key: str):
        """Result always ends with .jsonl extension."""
        result = ConversationStore._hashed_session_filename(key)
        assert result.endswith(".jsonl")

    @given(
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_different_keys_give_different_filenames(self, data):
        """Distinct keys must produce distinct filenames (collision resistance)."""
        key1 = data.draw(st.text(min_size=1, max_size=100))
        key2 = data.draw(st.text(min_size=1, max_size=100).filter(lambda k: k != key1))
        f1 = ConversationStore._hashed_session_filename(key1)
        f2 = ConversationStore._hashed_session_filename(key2)
        assert f1 != f2, f"Hash collision: {key1!r} and {key2!r} both map to {f1}"


# ===========================================================================
# 7. estimate_message_tokens
# ===========================================================================


class TestEstimateMessageTokens:
    """Properties for estimate_message_tokens."""

    @given(
        content=st.text(min_size=0, max_size=500),
        role=st.sampled_from(["user", "assistant", "system", "tool"]),
    )
    @settings(max_examples=200, deadline=None)
    def test_always_positive(self, content: str, role: str):
        """Result is always >= 1 for any dict with a role key."""
        msg = {"role": role, "content": content}
        result = estimate_message_tokens(msg)
        assert result >= 1

    @given(
        short=st.text(min_size=0, max_size=100),
        extra=st.text(min_size=1, max_size=400),
    )
    @settings(max_examples=200, deadline=None)
    def test_monotonic_in_content_length(self, short: str, extra: str):
        """Longer content string leads to more or equal estimated tokens."""
        longer = short + extra
        msg_short = {"role": "user", "content": short}
        msg_long = {"role": "user", "content": longer}
        tokens_short = estimate_message_tokens(msg_short)
        tokens_long = estimate_message_tokens(msg_long)
        assert tokens_long >= tokens_short

    @given(role=st.sampled_from(["user", "assistant", "system"]))
    @settings(max_examples=20, deadline=None)
    def test_empty_content_minimal(self, role: str):
        """Empty content message should yield minimal token count."""
        msg = {"role": role, "content": ""}
        result = estimate_message_tokens(msg)
        # Empty content → should be the minimum (1)
        assert result == 1
