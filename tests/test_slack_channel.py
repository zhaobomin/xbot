from __future__ import annotations

import pytest

from xbot.bus.events import OutboundMessage
from xbot.bus.queue import MessageBus
from xbot.channels.slack import SlackChannel
from xbot.channels.slack import SlackConfig


class _FakeAsyncWebClient:
    def __init__(self) -> None:
        self.chat_post_calls: list[dict[str, object | None]] = []
        self.file_upload_calls: list[dict[str, object | None]] = []
        self.reactions_add_calls: list[dict[str, object | None]] = []
        self.reactions_remove_calls: list[dict[str, object | None]] = []

    async def chat_postMessage(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
    ) -> None:
        self.chat_post_calls.append(
            {
                "channel": channel,
                "text": text,
                "thread_ts": thread_ts,
            }
        )

    async def files_upload_v2(
        self,
        *,
        channel: str,
        file: str,
        thread_ts: str | None = None,
    ) -> None:
        self.file_upload_calls.append(
            {
                "channel": channel,
                "file": file,
                "thread_ts": thread_ts,
            }
        )

    async def reactions_add(
        self,
        *,
        channel: str,
        name: str,
        timestamp: str,
    ) -> None:
        self.reactions_add_calls.append(
            {
                "channel": channel,
                "name": name,
                "timestamp": timestamp,
            }
        )

    async def reactions_remove(
        self,
        *,
        channel: str,
        name: str,
        timestamp: str,
    ) -> None:
        self.reactions_remove_calls.append(
            {
                "channel": channel,
                "name": name,
                "timestamp": timestamp,
            }
        )


@pytest.mark.asyncio
async def test_send_uses_thread_for_channel_messages() -> None:
    channel = SlackChannel(SlackConfig(enabled=True), MessageBus())
    fake_web = _FakeAsyncWebClient()
    channel._web_client = fake_web

    await channel.send(
        OutboundMessage(
            channel="slack",
            chat_id="C123",
            content="hello",
            media=["/tmp/demo.txt"],
            metadata={"slack": {"thread_ts": "1700000000.000100", "channel_type": "channel"}},
        )
    )

    assert len(fake_web.chat_post_calls) == 1
    assert fake_web.chat_post_calls[0]["text"] == "hello\n"
    assert fake_web.chat_post_calls[0]["thread_ts"] == "1700000000.000100"
    assert len(fake_web.file_upload_calls) == 1
    assert fake_web.file_upload_calls[0]["thread_ts"] == "1700000000.000100"


@pytest.mark.asyncio
async def test_send_omits_thread_for_dm_messages() -> None:
    channel = SlackChannel(SlackConfig(enabled=True), MessageBus())
    fake_web = _FakeAsyncWebClient()
    channel._web_client = fake_web

    await channel.send(
        OutboundMessage(
            channel="slack",
            chat_id="D123",
            content="hello",
            media=["/tmp/demo.txt"],
            metadata={"slack": {"thread_ts": "1700000000.000100", "channel_type": "im"}},
        )
    )

    assert len(fake_web.chat_post_calls) == 1
    assert fake_web.chat_post_calls[0]["text"] == "hello\n"
    assert fake_web.chat_post_calls[0]["thread_ts"] is None
    assert len(fake_web.file_upload_calls) == 1
    assert fake_web.file_upload_calls[0]["thread_ts"] is None


@pytest.mark.asyncio
async def test_send_updates_reaction_when_final_response_sent() -> None:
    channel = SlackChannel(SlackConfig(enabled=True, react_emoji="eyes"), MessageBus())
    fake_web = _FakeAsyncWebClient()
    channel._web_client = fake_web

    await channel.send(
        OutboundMessage(
            channel="slack",
            chat_id="C123",
            content="done",
            metadata={
                "slack": {"event": {"ts": "1700000000.000100"}, "channel_type": "channel"},
            },
        )
    )

    assert fake_web.reactions_remove_calls == [
        {"channel": "C123", "name": "eyes", "timestamp": "1700000000.000100"}
    ]
    assert fake_web.reactions_add_calls == [
        {"channel": "C123", "name": "white_check_mark", "timestamp": "1700000000.000100"}
    ]


class _RateLimitedWebClient:
    """Mock Slack client that simulates rate limiting."""

    def __init__(self, rate_limit_times: int = 1, retry_after: int = 1):
        self.rate_limit_times = rate_limit_times
        self.retry_after = retry_after
        self.call_count = 0
        self.chat_post_calls: list[dict[str, object | None]] = []

    async def chat_postMessage(self, **kwargs) -> dict:
        self.call_count += 1

        if self.call_count <= self.rate_limit_times:
            # Simulate rate limit error
            error = Exception("rate limited")
            error.response = {"headers": {"Retry-After": str(self.retry_after)}}
            raise error

        self.chat_post_calls.append(kwargs)
        return {"ok": True}


class _TransientErrorWebClient:
    """Mock Slack client that simulates transient errors."""

    def __init__(self, error_times: int = 2):
        self.error_times = error_times
        self.call_count = 0
        self.chat_post_calls: list[dict[str, object | None]] = []

    async def chat_postMessage(self, **kwargs) -> dict:
        self.call_count += 1

        if self.call_count <= self.error_times:
            # Simulate 500 error
            raise Exception("500 internal server error")

        self.chat_post_calls.append(kwargs)
        return {"ok": True}


class _AuthErrorWebClient:
    """Mock Slack client that simulates auth errors."""

    def __init__(self):
        self.call_count = 0

    async def chat_postMessage(self, **kwargs) -> dict:
        self.call_count += 1
        raise Exception("401 invalid_auth")


class TestSlackRateLimit:
    """Tests for Slack rate limit handling."""

    @pytest.mark.asyncio
    async def test_rate_limit_respects_retry_after(self, monkeypatch):
        """Test that Slack rate limit (429) respects Retry-After header."""
        import time

        channel = SlackChannel(SlackConfig(enabled=True), MessageBus())
        mock_web = _RateLimitedWebClient(rate_limit_times=1, retry_after=1)
        channel._web_client = mock_web

        start = time.time()
        await channel.send(
            OutboundMessage(channel="slack", chat_id="C123", content="test")
        )
        elapsed = time.time() - start

        # Should have waited for Retry-After duration
        assert elapsed >= 0.9  # Allow some timing variance
        assert mock_web.call_count == 2  # Initial call + retry

    @pytest.mark.asyncio
    async def test_transient_error_retries(self):
        """Test that transient errors (500/502/503) trigger retries."""
        channel = SlackChannel(SlackConfig(enabled=True), MessageBus())
        mock_web = _TransientErrorWebClient(error_times=2)
        channel._web_client = mock_web

        await channel.send(
            OutboundMessage(channel="slack", chat_id="C123", content="test")
        )

        # Should have retried and eventually succeeded
        assert mock_web.call_count == 3  # 2 failures + 1 success
        assert len(mock_web.chat_post_calls) == 1

    @pytest.mark.asyncio
    async def test_auth_error_no_retry(self):
        """Test that auth errors (401/403) do not retry."""
        channel = SlackChannel(SlackConfig(enabled=True), MessageBus())
        mock_web = _AuthErrorWebClient()
        channel._web_client = mock_web

        with pytest.raises(Exception, match="invalid_auth"):
            await channel.send(
                OutboundMessage(channel="slack", chat_id="C123", content="test")
            )

        # Should not have retried
        assert mock_web.call_count == 1

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_raises(self):
        """Test that exhausting max retries raises the last error."""
        from xbot.channels.slack import SLACK_MAX_RETRIES

        class _AlwaysFailWebClient:
            def __init__(self):
                self.call_count = 0

            async def chat_postMessage(self, **kwargs):
                self.call_count += 1
                raise Exception("persistent error")

        channel = SlackChannel(SlackConfig(enabled=True), MessageBus())
        mock_web = _AlwaysFailWebClient()
        channel._web_client = mock_web

        with pytest.raises(Exception, match="persistent error"):
            await channel.send(
                OutboundMessage(channel="slack", chat_id="C123", content="test")
            )

        assert mock_web.call_count == SLACK_MAX_RETRIES
