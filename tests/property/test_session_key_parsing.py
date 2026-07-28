"""Property-based tests for session key parsing and bus events.

Uses Hypothesis to fuzz to_canonical_session_key / parse_session_key with
arbitrary inputs, ensuring they never crash with unhandled exceptions.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from xbot.platform.bus.events import (
    IM_CHANNELS,
    InboundMessage,
    to_canonical_session_key,
    parse_session_key,
)


# ---------------------------------------------------------------------------
# Tests: to_canonical_session_key
# ---------------------------------------------------------------------------


class TestCanonicalSessionKey:
    """to_canonical_session_key must never raise on arbitrary string inputs."""

    @given(
        channel=st.text(min_size=0, max_size=50),
        chat_id=st.text(min_size=0, max_size=100),
    )
    @settings(max_examples=1000)
    def test_never_crashes(self, channel: str, chat_id: str):
        """Arbitrary channel + chat_id must produce a string, never crash."""
        result = to_canonical_session_key(channel, chat_id)
        assert isinstance(result, str)
        assert len(result) > 0 or (channel == "" and chat_id == "")

    @given(
        channel=st.text(min_size=0, max_size=50),
        chat_id=st.text(min_size=0, max_size=100),
        override=st.one_of(st.none(), st.text(min_size=0, max_size=100)),
    )
    @settings(max_examples=1000)
    def test_with_override_never_crashes(
        self, channel: str, chat_id: str, override: str | None
    ):
        """With optional override, must still produce a string."""
        result = to_canonical_session_key(channel, chat_id, override)
        assert isinstance(result, str)

    @given(
        channel=st.sampled_from(sorted(IM_CHANNELS)),
        chat_id=st.text(
            min_size=1,
            max_size=50,
            alphabet=st.characters(whitelist_categories=("L", "N", "P")),
        ),
    )
    @settings(max_examples=200)
    def test_im_channels_get_im_prefix(self, channel: str, chat_id: str):
        """Known IM channels must produce keys with 'im:' prefix."""
        result = to_canonical_session_key(channel, chat_id)
        assert result.startswith("im:"), f"IM channel {channel} missing im: prefix"
        assert channel in result

    @given(
        channel=st.text(min_size=1, max_size=20).filter(lambda c: c.strip() not in IM_CHANNELS),
        chat_id=st.text(
            min_size=1,
            max_size=50,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        ),
    )
    @settings(max_examples=200)
    def test_non_im_channels_no_im_prefix(self, channel: str, chat_id: str):
        """Non-IM channels must NOT get 'im:' prefix (unless override says so)."""
        result = to_canonical_session_key(channel, chat_id)
        if channel.strip() not in IM_CHANNELS:
            assert not result.startswith("im:")


# ---------------------------------------------------------------------------
# Tests: parse_session_key
# ---------------------------------------------------------------------------


class TestParseSessionKey:
    """parse_session_key must handle arbitrary input without crashing."""

    @given(key=st.text(min_size=0, max_size=200))
    @settings(max_examples=1000)
    def test_never_crashes(self, key: str):
        """Arbitrary string must parse to a 2-tuple of strings."""
        result = parse_session_key(key)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)

    @given(key=st.just(""))
    def test_empty_string(self, key: str):
        """Empty string → ('', '')."""
        channel, chat_id = parse_session_key(key)
        assert channel == ""
        assert chat_id == ""

    @given(
        channel=st.sampled_from(sorted(IM_CHANNELS)),
        chat_id=st.text(
            min_size=1,
            max_size=50,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        ),
    )
    @settings(max_examples=200)
    def test_roundtrip_im_channels(self, channel: str, chat_id: str):
        """canonical → parse should recover original channel and chat_id."""
        canonical = to_canonical_session_key(channel, chat_id)
        parsed_channel, parsed_chat_id = parse_session_key(canonical)
        assert parsed_channel == channel
        assert parsed_chat_id == chat_id

    @given(
        channel=st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("L",)),
        ).filter(lambda c: c not in IM_CHANNELS),
        chat_id=st.text(
            min_size=1,
            max_size=50,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        ),
    )
    @settings(max_examples=200)
    def test_roundtrip_non_im_channels(self, channel: str, chat_id: str):
        """Non-IM canonical → parse should recover original channel:chat_id."""
        canonical = to_canonical_session_key(channel, chat_id)
        parsed_channel, parsed_chat_id = parse_session_key(canonical)
        assert parsed_channel == channel
        assert parsed_chat_id == chat_id


# ---------------------------------------------------------------------------
# Tests: InboundMessage.session_key property
# ---------------------------------------------------------------------------


class TestInboundMessageSessionKey:
    """InboundMessage.session_key must be consistent with to_canonical_session_key."""

    @given(
        channel=st.sampled_from(["web", "telegram", "slack", "discord", "app"]),
        sender_id=st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N"))),
        chat_id=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N"))),
        content=st.text(min_size=0, max_size=100),
    )
    @settings(max_examples=200)
    def test_session_key_matches_canonical(
        self, channel: str, sender_id: str, chat_id: str, content: str
    ):
        """InboundMessage.session_key must equal to_canonical_session_key(channel, chat_id)."""
        msg = InboundMessage(
            channel=channel,
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
        )
        expected = to_canonical_session_key(channel, chat_id)
        assert msg.session_key == expected
