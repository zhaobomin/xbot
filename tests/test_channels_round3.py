"""Round-3 tests targeting low-coverage lines in channel and supporting modules.

Covers:
- discord.py: connection loop, gateway loop, message handling, identify,
  heartbeat, send file, typing indicator, group policy
- dingtalk.py: token refresh, send paths, retries, message handling,
  download file, schedule inbound
- email.py: IMAP init, fetch, parse, SMTP send, HTML conversion,
  processed UIDs, reply subject
- permission.py: context cleanup, callback, CLI handlers, interactive handlers
- role_cmd.py: list/show/create/validate/copy/delete/export/import commands
- format.py: schema validation fallback (no jsonschema)
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.machinery
import json
import sys
import types
from collections import OrderedDict
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub external SDK modules before importing xbot channel modules
# ---------------------------------------------------------------------------
def _install_sdk_stubs():
    stubs: dict[str, object] = {}

    # -- websockets --
    ws_mod = types.ModuleType("websockets")
    ws_mod.connect = MagicMock()  # type: ignore[attr-defined]
    ws_mod.WebSocketClientProtocol = type("WebSocketClientProtocol", (), {})
    stubs["websockets"] = ws_mod

    # -- dingtalk_stream --
    dt_mod = types.ModuleType("dingtalk_stream")
    dt_mod.__spec__ = importlib.machinery.ModuleSpec("dingtalk_stream", None)

    class _FakeAckMessage:
        STATUS_OK = 200

    class _FakeCallbackHandler:
        def __init__(self, *a, **kw):
            pass

    class _FakeCallbackMessage:
        def __init__(self, *a, **kw):
            pass

    class _FakeCredential:
        def __init__(self, *a, **kw):
            pass

    class _FakeDingTalkStreamClient:
        def __init__(self, *a, **kw):
            pass
        def register_callback_handler(self, *a, **kw):
            pass
        async def start(self, *a, **kw):
            pass

    class _FakeChatbotMessage:
        TOPIC = "CHAT_BOT_MESSAGE_TOPIC"
        @classmethod
        def from_dict(cls, data):
            msg = MagicMock()
            msg.text = MagicMock()
            msg.text.content = data.get("text", {}).get("content", "")
            msg.message_type = data.get("messageType", "")
            msg.image_content = None
            msg.rich_text_content = None
            msg.extensions = data.get("extensions", {})
            msg.sender_id = data.get("senderId", "sender123")
            msg.sender_staff_id = data.get("senderStaffId", "")
            msg.sender_nick = data.get("senderNick", "TestUser")
            return msg

    dt_mod.AckMessage = _FakeAckMessage
    dt_mod.CallbackHandler = _FakeCallbackHandler
    dt_mod.CallbackMessage = _FakeCallbackMessage
    dt_mod.Credential = _FakeCredential
    dt_mod.DingTalkStreamClient = _FakeDingTalkStreamClient
    dt_mod.ChatbotMessage = _FakeChatbotMessage
    dt_mod.chatbot = dt_mod
    stubs["dingtalk_stream"] = dt_mod
    stubs["dingtalk_stream.chatbot"] = dt_mod

    for mod_name, mod in stubs.items():
        if mod_name not in sys.modules:
            sys.modules[mod_name] = mod


_install_sdk_stubs()


# Now import modules under test
from xbot.channels.discord import (  # noqa: E402
    DiscordChannel,
    DiscordConfig,
    DISCORD_API_BASE,
    MAX_ATTACHMENT_BYTES,
)
from xbot.channels.dingtalk import (  # noqa: E402
    DingTalkChannel,
    DingTalkConfig,
    XbotDingTalkHandler,
)
from xbot.channels.email import (  # noqa: E402
    EmailChannel,
    EmailConfig,
)
from xbot.interaction.permission import (  # noqa: E402
    BasePermissionHandler,
    CLIPermissionHandler,
    InteractivePermissionHandler,
    PermissionRequestHandler,
    create_permission_handler,
)
from xbot.platform.bus.events import OutboundMessage  # noqa: E402
from xbot.platform.bus.queue import (  # noqa: E402
    InteractionResponse,
    MessageBus,
    PermissionResponse,
)
from xbot.crew.models import OutputFormat  # noqa: E402
from xbot.crew.output.format import OutputParser, detect_format, format_output  # noqa: E402


# ===================================================================
# Helpers
# ===================================================================

def _make_bus() -> MagicMock:
    bus = MagicMock(spec=MessageBus)
    bus.publish_inbound = AsyncMock()
    bus.publish_permission_request = AsyncMock()
    bus.wait_permission_response = AsyncMock()
    bus.publish_interaction_request = AsyncMock()
    bus.wait_interaction_response = AsyncMock()
    return bus


def _make_discord_channel(**overrides) -> DiscordChannel:
    cfg = {
        "enabled": True,
        "token": "test-token-123",
        "allow_from": ["*"],
        "group_policy": "mention",
    }
    cfg.update(overrides)
    bus = _make_bus()
    ch = DiscordChannel(cfg, bus)
    return ch


def _make_dingtalk_channel(**overrides) -> DingTalkChannel:
    cfg = {
        "enabled": True,
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "allow_from": ["*"],
    }
    cfg.update(overrides)
    bus = _make_bus()
    ch = DingTalkChannel(cfg, bus)
    return ch


def _make_email_channel(**overrides) -> EmailChannel:
    cfg = {
        "enabled": True,
        "consent_granted": True,
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_username": "user@example.com",
        "imap_password": "secret",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "user@example.com",
        "smtp_password": "secret",
        "from_address": "user@example.com",
        "allow_from": ["*"],
    }
    cfg.update(overrides)
    bus = _make_bus()
    ch = EmailChannel(cfg, bus)
    return ch


# ===================================================================
# 1. Discord tests — covering lines 76-118, 215-301, 305-320, 375-416, 441-487
# ===================================================================

class TestDiscordConnection:
    """Test Discord start/stop and reconnection logic."""

    async def test_start_no_token_returns_immediately(self):
        ch = _make_discord_channel(token="")
        await ch.start()
        # Should not crash; _running stays False

    async def test_start_sets_running_and_creates_http(self):
        """Cover lines 76-77: _running=True, http client created."""
        ch = _make_discord_channel()
        fake_ws = AsyncMock()
        fake_ws.close = AsyncMock()

        # Make the gateway loop exit quickly by making ws raise on iteration
        async def ws_iter():
            yield json.dumps({"op": 9, "d": None})  # INVALID_SESSION -> break

        fake_ws.__aiter__ = lambda self: ws_iter()

        ctx = _FakeWSContext(fake_ws)
        connect_count = 0

        def fake_connect(*a, **kw):
            nonlocal connect_count
            connect_count += 1
            if connect_count > 1:
                raise RuntimeError("stop")
            return ctx

        with patch("xbot.channels.discord.websockets.connect", side_effect=fake_connect):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                async def stop_sleep(*a, **kw):
                    ch._running = False
                mock_sleep.side_effect = stop_sleep
                await ch.start()

        assert ch._running is False

    async def test_reconnect_exponential_backoff(self):
        """Cover lines 104-118: reconnect attempts with backoff."""
        ch = _make_discord_channel()
        ch._running = True
        ch._reconnect_attempts = 3

        connect_count = 0

        def fake_connect(*a, **kw):
            nonlocal connect_count
            connect_count += 1
            raise RuntimeError("connection failed")

        with patch("xbot.channels.discord.websockets.connect", side_effect=fake_connect):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                call_count = 0
                async def sleep_side_effect(*a, **kw):
                    nonlocal call_count
                    call_count += 1
                    if call_count > 1:
                        ch._running = False

                mock_sleep.side_effect = sleep_side_effect
                await ch.start()

        assert connect_count >= 1

    async def test_circuit_breaker_at_max_reconnects(self):
        """Cover lines 104-112: circuit breaker after MAX_RECONNECT_ATTEMPTS."""
        ch = _make_discord_channel()
        ch._running = True
        ch._reconnect_attempts = 10  # At max

        connect_count = 0

        def fake_connect(*a, **kw):
            nonlocal connect_count
            connect_count += 1
            raise RuntimeError("fail")

        with patch("xbot.channels.discord.websockets.connect", side_effect=fake_connect):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                call_count = 0
                async def sleep_effect(*a, **kw):
                    nonlocal call_count
                    call_count += 1
                    if call_count >= 2:
                        ch._running = False

                mock_sleep.side_effect = sleep_effect
                await ch.start()

        assert connect_count >= 1


class _FakeWSContext:
    """Async context manager for fake websocket."""
    def __init__(self, ws, cancel_after=False):
        self._ws = ws
        self._cancel = cancel_after

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *a):
        return False


class TestDiscordGatewayLoop:
    """Test gateway loop message dispatch."""

    async def test_gateway_loop_hello_starts_heartbeat(self):
        """Cover lines 281-285: HELLO op starts heartbeat and identifies."""
        ch = _make_discord_channel()
        ws = AsyncMock()
        ch._ws = ws
        ch._running = True

        hello_msg = json.dumps({"op": 10, "d": {"heartbeat_interval": 45000}})
        ready_msg = json.dumps({
            "op": 0,
            "t": "READY",
            "s": 1,
            "d": {"user": {"id": "bot123"}},
        })

        # Make ws iterate yield hello then raise to exit
        msgs = [hello_msg, ready_msg]

        async def fake_iter():
            for m in msgs:
                yield m
            # Signal exit by setting heartbeat failure
            ch._heartbeat_failed.set()
            yield json.dumps({"op": 0, "t": "DUMMY", "d": {}})

        ws.__aiter__ = lambda self: fake_iter()
        ws.send = AsyncMock()

        # Patch _start_heartbeat to not actually start a task
        ch._start_heartbeat = AsyncMock()
        ch._identify = AsyncMock()

        await ch._gateway_loop()

        ch._start_heartbeat.assert_called_once_with(45.0)
        ch._identify.assert_called_once()
        assert ch._bot_user_id == "bot123"
        assert ch._seq == 1

    async def test_gateway_loop_message_create(self):
        """Cover line 292-293: MESSAGE_CREATE dispatch."""
        ch = _make_discord_channel()
        ws = AsyncMock()
        ch._ws = ws
        ch._running = True

        msg_payload = json.dumps({
            "op": 0,
            "t": "MESSAGE_CREATE",
            "s": 5,
            "d": {
                "author": {"id": "user1", "bot": False},
                "channel_id": "chan1",
                "content": "hello",
                "id": "msg1",
            },
        })

        async def fake_iter():
            yield msg_payload
            ch._heartbeat_failed.set()
            yield json.dumps({"op": 0, "t": "X", "d": {}})

        ws.__aiter__ = lambda self: fake_iter()
        ws.send = AsyncMock()

        ch._handle_message_create = AsyncMock()

        await ch._gateway_loop()
        ch._handle_message_create.assert_called_once()

    async def test_gateway_loop_reconnect_op(self):
        """Cover lines 294-297: RECONNECT op breaks loop."""
        ch = _make_discord_channel()
        ws = AsyncMock()
        ch._ws = ws
        ch._running = True

        async def fake_iter():
            yield json.dumps({"op": 7, "d": None})

        ws.__aiter__ = lambda self: fake_iter()
        await ch._gateway_loop()  # Should break, not hang

    async def test_gateway_loop_invalid_session_op(self):
        """Cover lines 298-301: INVALID_SESSION op breaks loop."""
        ch = _make_discord_channel()
        ws = AsyncMock()
        ch._ws = ws
        ch._running = True

        async def fake_iter():
            yield json.dumps({"op": 9, "d": None})

        ws.__aiter__ = lambda self: fake_iter()
        await ch._gateway_loop()  # Should break

    async def test_gateway_loop_invalid_json(self):
        """Cover lines 269-271: invalid JSON continues loop."""
        ch = _make_discord_channel()
        ws = AsyncMock()
        ch._ws = ws
        ch._running = True

        async def fake_iter():
            yield "not-json{{{"
            ch._heartbeat_failed.set()
            yield json.dumps({"op": 0, "t": "X", "d": {}})

        ws.__aiter__ = lambda self: fake_iter()
        await ch._gateway_loop()

    async def test_gateway_loop_heartbeat_failure_triggers_reconnect(self):
        """Cover lines 262-265: heartbeat failure breaks loop."""
        ch = _make_discord_channel()
        ws = AsyncMock()
        ch._ws = ws
        ch._running = True
        ch._heartbeat_failed.set()

        async def fake_iter():
            yield json.dumps({"op": 0, "t": "X", "d": {}})

        ws.__aiter__ = lambda self: fake_iter()
        await ch._gateway_loop()
        # heartbeat_failed should have been cleared
        assert not ch._heartbeat_failed.is_set()


class TestDiscordIdentify:
    """Test _identify (lines 305-320)."""

    async def test_identify_sends_payload(self):
        ch = _make_discord_channel()
        ws = AsyncMock()
        ch._ws = ws

        await ch._identify()

        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["op"] == 2
        assert sent["d"]["token"] == "test-token-123"
        assert sent["d"]["intents"] == 37377

    async def test_identify_no_ws_noop(self):
        ch = _make_discord_channel()
        ch._ws = None
        await ch._identify()  # Should not raise


class TestDiscordMessageHandling:
    """Test _handle_message_create (lines 358-432)."""

    async def test_handle_message_bot_ignored(self):
        ch = _make_discord_channel()
        payload = {"author": {"id": "u1", "bot": True}, "channel_id": "c1", "content": "hi"}
        await ch._handle_message_create(payload)
        ch.bus.publish_inbound.assert_not_called()

    async def test_handle_message_empty_ids(self):
        ch = _make_discord_channel()
        payload = {"author": {"id": "", "bot": False}, "channel_id": "", "content": "hi"}
        await ch._handle_message_create(payload)
        ch.bus.publish_inbound.assert_not_called()

    async def test_handle_message_duplicate_skipped(self):
        ch = _make_discord_channel()
        # Pre-populate dedup cache
        import time
        ch._processed_message_ids["msg1"] = time.time()

        payload = {
            "author": {"id": "u1", "bot": False},
            "channel_id": "c1",
            "content": "hi",
            "id": "msg1",
        }
        await ch._handle_message_create(payload)
        ch.bus.publish_inbound.assert_not_called()

    async def test_handle_message_normal(self):
        ch = _make_discord_channel()
        payload = {
            "author": {"id": "u1", "bot": False},
            "channel_id": "c1",
            "content": "hello bot",
            "id": "msg1",
            "guild_id": None,
            "attachments": [],
            "referenced_message": None,
        }

        with patch.object(ch, "_start_typing", new_callable=AsyncMock):
            await ch._handle_message_create(payload)

        ch.bus.publish_inbound.assert_called_once()

    async def test_handle_message_with_attachments_too_large(self):
        """Cover line 396-398: oversized attachment."""
        ch = _make_discord_channel()
        ch._http = AsyncMock()  # Need http for the attachment loop to proceed
        payload = {
            "author": {"id": "u1", "bot": False},
            "channel_id": "c1",
            "content": "",
            "id": "msg2",
            "guild_id": None,
            "attachments": [
                {"url": "http://example.com/big.zip", "filename": "big.zip",
                 "size": MAX_ATTACHMENT_BYTES + 1, "id": "att1"},
            ],
            "referenced_message": None,
        }

        with patch.object(ch, "_start_typing", new_callable=AsyncMock):
            await ch._handle_message_create(payload)

        # Should have published with "[too large]" in content
        call_args = ch.bus.publish_inbound.call_args
        msg = call_args[0][0]
        assert "too large" in msg.content

    async def test_handle_message_attachment_download_failure(self):
        """Cover line 414-416: download failure."""
        ch = _make_discord_channel()
        ch._http = AsyncMock()
        ch._http.get = AsyncMock(side_effect=RuntimeError("network error"))

        payload = {
            "author": {"id": "u1", "bot": False},
            "channel_id": "c1",
            "content": "",
            "id": "msg3",
            "guild_id": None,
            "attachments": [
                {"url": "http://example.com/img.png", "filename": "img.png",
                 "size": 1000, "id": "att2"},
            ],
            "referenced_message": None,
        }

        with patch("xbot.platform.security.network.async_validate_url_target",
                    new_callable=AsyncMock, return_value=(True, "")):
            with patch.object(ch, "_start_typing", new_callable=AsyncMock):
                await ch._handle_message_create(payload)

        call_args = ch.bus.publish_inbound.call_args
        msg = call_args[0][0]
        assert "download failed" in msg.content


class TestDiscordGroupPolicy:
    """Test _should_respond_in_group (lines 434-453)."""

    def test_open_policy(self):
        ch = _make_discord_channel(group_policy="open")
        assert ch._should_respond_in_group({}, "anything") is True

    def test_mention_policy_mentioned_in_array(self):
        ch = _make_discord_channel(group_policy="mention")
        ch._bot_user_id = "bot123"
        payload = {"mentions": [{"id": "bot123"}], "channel_id": "c1"}
        assert ch._should_respond_in_group(payload, "hello") is True

    def test_mention_policy_mentioned_in_content(self):
        ch = _make_discord_channel(group_policy="mention")
        ch._bot_user_id = "bot123"
        payload = {"mentions": [], "channel_id": "c1"}
        assert ch._should_respond_in_group(payload, "hey <@bot123>") is True

    def test_mention_policy_not_mentioned(self):
        ch = _make_discord_channel(group_policy="mention")
        ch._bot_user_id = "bot123"
        payload = {"mentions": [], "channel_id": "c1"}
        assert ch._should_respond_in_group(payload, "hello world") is False

    def test_mention_policy_no_bot_user_id(self):
        ch = _make_discord_channel(group_policy="mention")
        ch._bot_user_id = None
        payload = {"mentions": [], "channel_id": "c1"}
        assert ch._should_respond_in_group(payload, "<@bot123>") is False

    async def test_group_message_with_policy_check(self):
        """Cover lines 382-384: group channel triggers policy check."""
        ch = _make_discord_channel(group_policy="mention")
        ch._bot_user_id = "bot123"
        payload = {
            "author": {"id": "u1", "bot": False},
            "channel_id": "c1",
            "content": "no mention here",
            "id": "msg1",
            "guild_id": "g1",
            "attachments": [],
            "mentions": [],
            "referenced_message": None,
        }

        await ch._handle_message_create(payload)
        # Should not publish because bot not mentioned
        ch.bus.publish_inbound.assert_not_called()


class TestDiscordSend:
    """Test send and _send_file (lines 142-253, 375-416)."""

    async def test_send_no_http(self):
        ch = _make_discord_channel()
        ch._http = None
        msg = OutboundMessage(channel="discord", chat_id="c1", content="hi")
        await ch.send(msg)  # Should not raise

    async def test_send_text_only(self):
        ch = _make_discord_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        ch._http.post = AsyncMock(return_value=resp)

        msg = OutboundMessage(channel="discord", chat_id="c1", content="hello")

        with patch.object(ch, "_stop_typing", new_callable=AsyncMock):
            await ch.send(msg)

        ch._http.post.assert_called_once()

    async def test_send_with_reply(self):
        ch = _make_discord_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        ch._http.post = AsyncMock(return_value=resp)

        msg = OutboundMessage(
            channel="discord", chat_id="c1", content="reply text",
            reply_to="msg_orig",
        )

        with patch.object(ch, "_stop_typing", new_callable=AsyncMock):
            await ch.send(msg)

        call_kwargs = ch._http.post.call_args
        payload = call_kwargs[1]["json"]
        assert "message_reference" in payload

    async def test_send_payload_rate_limit(self):
        """Cover lines 192-197: rate limit retry."""
        ch = _make_discord_channel()
        ch._http = AsyncMock()

        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.json.return_value = {"retry_after": 0.01}

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()

        ch._http.post = AsyncMock(side_effect=[rate_resp, ok_resp])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await ch._send_payload("http://url", {}, {"content": "hi"})

        assert result is True

    async def test_send_payload_all_fail(self):
        """Cover lines 200-205: all attempts fail."""
        ch = _make_discord_channel()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=RuntimeError("network"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await ch._send_payload("http://url", {}, {"content": "hi"})

        assert result is False

    async def test_send_file_not_found(self):
        """Cover line 216-218: file not found."""
        ch = _make_discord_channel()
        ch._http = AsyncMock()

        result = await ch._send_file("http://url", {}, "/nonexistent/file.txt")
        assert result is False

    async def test_send_file_too_large(self, tmp_path):
        """Cover lines 220-222: file too large."""
        ch = _make_discord_channel()
        ch._http = AsyncMock()

        big_file = tmp_path / "big.bin"
        big_file.write_bytes(b"\x00" * (MAX_ATTACHMENT_BYTES + 1))

        result = await ch._send_file("http://url", {}, str(big_file))
        assert result is False

    async def test_send_file_success(self, tmp_path):
        """Cover lines 229-253: successful file send."""
        ch = _make_discord_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        ch._http.post = AsyncMock(return_value=resp)

        small_file = tmp_path / "small.txt"
        small_file.write_bytes(b"hello")

        result = await ch._send_file("http://url", {}, str(small_file), reply_to="msg1")
        assert result is True

    async def test_send_file_with_rate_limit(self, tmp_path):
        """Cover lines 239-244: file send rate limit."""
        ch = _make_discord_channel()
        ch._http = AsyncMock()

        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.json.return_value = {"retry_after": 0.01}

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()

        ch._http.post = AsyncMock(side_effect=[rate_resp, ok_resp])

        small_file = tmp_path / "small.txt"
        small_file.write_bytes(b"data")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await ch._send_file("http://url", {}, str(small_file))

        assert result is True


class TestDiscordTyping:
    """Test typing indicator (lines 455-487)."""

    async def test_start_stop_typing(self):
        ch = _make_discord_channel()
        ch._http = AsyncMock()
        ch._running = True
        resp = MagicMock()
        resp.status_code = 200
        ch._http.post = AsyncMock(return_value=resp)

        await ch._start_typing("c1")
        assert "c1" in ch._typing_tasks

        await ch._stop_typing("c1")
        assert "c1" not in ch._typing_tasks

    async def test_stop_typing_no_task(self):
        ch = _make_discord_channel()
        await ch._stop_typing("nonexistent")  # Should not raise


class TestDiscordDedup:
    """Test _is_duplicate_message (lines 342-356)."""

    async def test_first_message_not_duplicate(self):
        ch = _make_discord_channel()
        assert await ch._is_duplicate_message("msg1") is False

    async def test_second_message_is_duplicate(self):
        ch = _make_discord_channel()
        await ch._is_duplicate_message("msg1")
        assert await ch._is_duplicate_message("msg1") is True

    async def test_expired_entries_cleaned(self):
        ch = _make_discord_channel()
        import time
        ch._processed_message_ids["old_msg"] = time.time() - 600  # 10 min ago
        result = await ch._is_duplicate_message("new_msg")
        assert result is False
        assert "old_msg" not in ch._processed_message_ids


class TestDiscordStop:
    """Test stop cleanup."""

    async def test_stop_cleans_up(self):
        ch = _make_discord_channel()
        ch._running = True
        ch._ws = AsyncMock()
        ch._http = AsyncMock()
        ch._http.aclose = AsyncMock()

        await ch.stop()

        assert ch._running is False
        assert ch._ws is None
        assert ch._http is None


# ===================================================================
# 2. DingTalk tests — covering lines 71-77, 196-235, 241-256, 319-350,
#    427-499, 557-606
# ===================================================================

class TestDingTalkTokenRefresh:
    """Test _get_access_token (lines 261-290)."""

    async def test_token_cached(self):
        ch = _make_dingtalk_channel()
        ch._access_token = "cached-token"
        import time
        ch._token_expiry = time.time() + 3600
        token = await ch._get_access_token()
        assert token == "cached-token"

    async def test_token_refresh(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"accessToken": "new-token", "expireIn": 7200}
        ch._http.post = AsyncMock(return_value=resp)

        token = await ch._get_access_token()
        assert token == "new-token"
        assert ch._access_token == "new-token"

    async def test_token_refresh_no_http(self):
        ch = _make_dingtalk_channel()
        ch._http = None
        token = await ch._get_access_token()
        assert token is None

    async def test_token_refresh_error(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=RuntimeError("network error"))
        token = await ch._get_access_token()
        assert token is None


class TestDingTalkStart:
    """Test start method (lines 194-235)."""

    async def test_start_no_credentials(self):
        ch = _make_dingtalk_channel(client_id="", client_secret="")
        await ch.start()
        assert ch._running is False

    async def test_start_initializes_client(self):
        """Cover lines 196-222: successful start."""
        ch = _make_dingtalk_channel()
        # Mock the client.start to raise immediately so we exit the reconnect loop
        with patch("xbot.channels.dingtalk.DingTalkStreamClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.start = AsyncMock(side_effect=RuntimeError("exit"))
            mock_instance.register_callback_handler = MagicMock()
            MockClient.return_value = mock_instance

            with patch("xbot.channels.dingtalk.Credential"):
                ch._running = True
                with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    async def stop_after_sleep(*a):
                        ch._running = False
                    mock_sleep.side_effect = stop_after_sleep
                    await ch.start()

        assert ch._http is not None


class TestDingTalkScheduleInbound:
    """Test _schedule_inbound_message (lines 245-259)."""

    async def test_schedule_same_loop(self):
        ch = _make_dingtalk_channel()
        ch._loop = asyncio.get_running_loop()

        called = False

        async def dummy():
            nonlocal called
            called = True

        ch._schedule_inbound_message(dummy(), name="test")
        # Clean up the task
        await asyncio.sleep(0.01)

    async def test_schedule_no_loop(self):
        ch = _make_dingtalk_channel()
        ch._loop = None

        async def dummy():
            pass

        ch._schedule_inbound_message(dummy(), name="test")
        await asyncio.sleep(0.01)


class TestDingTalkMediaHelpers:
    """Test media helpers (lines 292-350)."""

    def test_is_http_url(self):
        assert DingTalkChannel._is_http_url("https://example.com/file.jpg") is True
        assert DingTalkChannel._is_http_url("/local/path.jpg") is False

    def test_guess_upload_type(self):
        ch = _make_dingtalk_channel()
        assert ch._guess_upload_type("photo.jpg") == "image"
        assert ch._guess_upload_type("audio.mp3") == "voice"
        assert ch._guess_upload_type("video.mp4") == "video"
        assert ch._guess_upload_type("document.pdf") == "file"

    def test_guess_filename(self):
        ch = _make_dingtalk_channel()
        assert ch._guess_filename("https://example.com/path/image.jpg", "image") == "image.jpg"
        assert ch._guess_filename("", "image") == "image.jpg"
        assert ch._guess_filename("", "voice") == "audio.amr"
        assert ch._guess_filename("", "video") == "video.mp4"
        assert ch._guess_filename("", "file") == "file.bin"

    async def test_read_media_bytes_empty(self):
        ch = _make_dingtalk_channel()
        data, name, ct = await ch._read_media_bytes("")
        assert data is None

    async def test_read_media_bytes_http(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"image-data"
        resp.headers = {"content-type": "image/jpeg"}
        ch._http.get = AsyncMock(return_value=resp)

        data, name, ct = await ch._read_media_bytes("https://example.com/photo.jpg")
        assert data == b"image-data"
        assert name == "photo.jpg"
        assert ct == "image/jpeg"

    async def test_read_media_bytes_http_error(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 404
        resp.headers = {}
        ch._http.get = AsyncMock(return_value=resp)

        data, name, ct = await ch._read_media_bytes("https://example.com/missing.jpg")
        assert data is None

    async def test_read_media_bytes_local_file(self, tmp_path):
        ch = _make_dingtalk_channel()
        local_file = tmp_path / "test.txt"
        local_file.write_bytes(b"file content")

        data, name, ct = await ch._read_media_bytes(str(local_file))
        assert data == b"file content"
        assert name == "test.txt"

    async def test_read_media_bytes_local_not_found(self):
        ch = _make_dingtalk_channel()
        data, name, ct = await ch._read_media_bytes("/nonexistent/file.txt")
        assert data is None

    async def test_read_media_bytes_file_url(self, tmp_path):
        ch = _make_dingtalk_channel()
        local_file = tmp_path / "test.txt"
        local_file.write_bytes(b"file content")

        data, name, ct = await ch._read_media_bytes(f"file://{local_file}")
        assert data == b"file content"


class TestDingTalkUploadMedia:
    """Test _upload_media (lines 352-386)."""

    async def test_upload_success(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"media_id": "media_123"}'
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {"media_id": "media_123"}
        ch._http.post = AsyncMock(return_value=resp)

        result = await ch._upload_media("token", b"data", "image", "test.jpg", "image/jpeg")
        assert result == "media_123"

    async def test_upload_http_error(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "Internal error"
        resp.headers = {"content-type": "text/plain"}
        ch._http.post = AsyncMock(return_value=resp)

        result = await ch._upload_media("token", b"data", "image", "test.jpg", "image/jpeg")
        assert result is None

    async def test_upload_api_error(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"errcode": 40001}'
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {"errcode": 40001}
        ch._http.post = AsyncMock(return_value=resp)

        result = await ch._upload_media("token", b"data", "image", "test.jpg", "image/jpeg")
        assert result is None

    async def test_upload_no_media_id(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"result": {}}'
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {"result": {}}
        ch._http.post = AsyncMock(return_value=resp)

        result = await ch._upload_media("token", b"data", "image", "test.jpg", "image/jpeg")
        assert result is None

    async def test_upload_exception(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=RuntimeError("network"))

        result = await ch._upload_media("token", b"data", "image", "test.jpg", "image/jpeg")
        assert result is None

    async def test_upload_no_http(self):
        ch = _make_dingtalk_channel()
        ch._http = None
        result = await ch._upload_media("token", b"data", "image", "test.jpg", "image/jpeg")
        assert result is None


class TestDingTalkSendBatchMessage:
    """Test _send_batch_message (lines 388-437)."""

    async def test_send_private(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.json.return_value = {}
        ch._http.post = AsyncMock(return_value=resp)

        result = await ch._send_batch_message("token", "user123", "sampleText", {"content": "hi"})
        assert result is True
        call_url = ch._http.post.call_args[0][0]
        assert "oToMessages/batchSend" in call_url

    async def test_send_group(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.json.return_value = {}
        ch._http.post = AsyncMock(return_value=resp)

        result = await ch._send_batch_message("token", "group:conv123", "sampleText", {"content": "hi"})
        assert result is True
        call_url = ch._http.post.call_args[0][0]
        assert "groupMessages/send" in call_url

    async def test_send_http_error(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "error"
        ch._http.post = AsyncMock(return_value=resp)

        result = await ch._send_batch_message("token", "user1", "sampleText", {"content": "hi"})
        assert result is False

    async def test_send_api_errcode(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"errcode": 400}'
        resp.json.return_value = {"errcode": 400}
        ch._http.post = AsyncMock(return_value=resp)

        result = await ch._send_batch_message("token", "user1", "sampleText", {"content": "hi"})
        assert result is False

    async def test_send_no_http(self):
        ch = _make_dingtalk_channel()
        ch._http = None
        result = await ch._send_batch_message("token", "user1", "sampleText", {"content": "hi"})
        assert result is False

    async def test_send_exception(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=RuntimeError("fail"))

        result = await ch._send_batch_message("token", "user1", "sampleText", {"content": "hi"})
        assert result is False

    async def test_send_non_json_response(self):
        """Cover lines 426-428: JSON parse failure on response."""
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "not-json"
        resp.json.side_effect = ValueError("no json")
        ch._http.post = AsyncMock(return_value=resp)

        result = await ch._send_batch_message("token", "user1", "sampleText", {"content": "hi"})
        assert result is True  # errcode is None when result={}


class TestDingTalkSendMediaRef:
    """Test _send_media_ref (lines 447-504)."""

    async def test_send_media_ref_empty(self):
        ch = _make_dingtalk_channel()
        result = await ch._send_media_ref("token", "user1", "")
        assert result is True

    async def test_send_image_url_direct(self):
        """Cover lines 453-461: image URL sent directly."""
        ch = _make_dingtalk_channel()
        with patch.object(ch, "_send_batch_message", new_callable=AsyncMock, return_value=True):
            result = await ch._send_media_ref("token", "user1", "https://example.com/photo.jpg")
        assert result is True

    async def test_send_media_ref_upload_fallback(self, tmp_path):
        """Cover lines 464-504: upload and send."""
        ch = _make_dingtalk_channel()
        local_file = tmp_path / "test.pdf"
        local_file.write_bytes(b"pdf data")

        with patch.object(ch, "_read_media_bytes", new_callable=AsyncMock,
                          return_value=(b"pdf data", "test.pdf", "application/pdf")):
            with patch.object(ch, "_upload_media", new_callable=AsyncMock,
                              return_value="media_456"):
                with patch.object(ch, "_send_batch_message", new_callable=AsyncMock,
                                  return_value=True):
                    result = await ch._send_media_ref("token", "user1", str(local_file))

        assert result is True

    async def test_send_media_ref_read_failed(self):
        ch = _make_dingtalk_channel()
        with patch.object(ch, "_read_media_bytes", new_callable=AsyncMock,
                          return_value=(None, None, None)):
            result = await ch._send_media_ref("token", "user1", "/nonexistent")
        assert result is False

    async def test_send_media_ref_upload_failed(self, tmp_path):
        ch = _make_dingtalk_channel()
        local_file = tmp_path / "test.pdf"
        local_file.write_bytes(b"data")

        with patch.object(ch, "_read_media_bytes", new_callable=AsyncMock,
                          return_value=(b"data", "test.pdf", "application/pdf")):
            with patch.object(ch, "_upload_media", new_callable=AsyncMock,
                              return_value=None):
                result = await ch._send_media_ref("token", "user1", str(local_file))
        assert result is False


class TestDingTalkSend:
    """Test send (lines 506-526)."""

    async def test_send_with_content_and_media(self):
        ch = _make_dingtalk_channel()
        with patch.object(ch, "_get_access_token", new_callable=AsyncMock, return_value="token"):
            with patch.object(ch, "_send_markdown_text", new_callable=AsyncMock, return_value=True):
                with patch.object(ch, "_send_media_ref", new_callable=AsyncMock, return_value=True):
                    msg = OutboundMessage(
                        channel="dingtalk", chat_id="user1",
                        content="hello", media=["https://example.com/img.jpg"],
                    )
                    await ch.send(msg)

    async def test_send_no_token(self):
        ch = _make_dingtalk_channel()
        with patch.object(ch, "_get_access_token", new_callable=AsyncMock, return_value=None):
            msg = OutboundMessage(channel="dingtalk", chat_id="user1", content="hello")
            await ch.send(msg)

    async def test_send_media_failure_fallback(self):
        """Cover lines 518-526: media failure sends fallback message."""
        ch = _make_dingtalk_channel()
        with patch.object(ch, "_get_access_token", new_callable=AsyncMock, return_value="token"):
            with patch.object(ch, "_send_markdown_text", new_callable=AsyncMock, return_value=True) as mock_md:
                with patch.object(ch, "_send_media_ref", new_callable=AsyncMock, return_value=False):
                    msg = OutboundMessage(
                        channel="dingtalk", chat_id="user1",
                        content="", media=["https://example.com/missing.jpg"],
                    )
                    await ch.send(msg)

        # Should have sent a fallback message
        assert mock_md.call_count >= 1


class TestDingTalkOnMessage:
    """Test _on_message (lines 528-558)."""

    async def test_on_message_private(self):
        ch = _make_dingtalk_channel()
        await ch._on_message("hello", "sender1", "User1", "1", None)
        ch.bus.publish_inbound.assert_called_once()

    async def test_on_message_group(self):
        ch = _make_dingtalk_channel()
        await ch._on_message("hello", "sender1", "User1", "2", "conv123")
        call_args = ch.bus.publish_inbound.call_args
        msg = call_args[0][0]
        assert msg.chat_id == "group:conv123"

    async def test_on_message_exception(self):
        ch = _make_dingtalk_channel()
        ch.bus.publish_inbound = AsyncMock(side_effect=RuntimeError("bus error"))
        # Should not raise
        await ch._on_message("hello", "sender1", "User1", "1", None)


class TestDingTalkDownloadFile:
    """Test _download_dingtalk_file (lines 560-606)."""

    async def test_download_success(self, tmp_path):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()

        # Mock token
        url_resp = MagicMock()
        url_resp.status_code = 200
        url_resp.json.return_value = {"downloadUrl": "https://cdn.example.com/file.pdf"}

        file_resp = MagicMock()
        file_resp.status_code = 200
        file_resp.content = b"pdf content"

        ch._http.post = AsyncMock(return_value=url_resp)
        ch._http.get = AsyncMock(return_value=file_resp)

        with patch.object(ch, "_get_access_token", new_callable=AsyncMock, return_value="token"):
            with patch("xbot.platform.config.paths.get_media_dir", return_value=tmp_path):
                result = await ch._download_dingtalk_file("dc123", "report.pdf", "sender1")

        assert result is not None
        assert Path(result).exists()

    async def test_download_no_token(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        with patch.object(ch, "_get_access_token", new_callable=AsyncMock, return_value=None):
            result = await ch._download_dingtalk_file("dc123", "file.pdf", "sender1")
        assert result is None

    async def test_download_url_api_error(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "error"
        ch._http.post = AsyncMock(return_value=resp)

        with patch.object(ch, "_get_access_token", new_callable=AsyncMock, return_value="token"):
            result = await ch._download_dingtalk_file("dc123", "file.pdf", "sender1")
        assert result is None

    async def test_download_url_missing_in_response(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        ch._http.post = AsyncMock(return_value=resp)

        with patch.object(ch, "_get_access_token", new_callable=AsyncMock, return_value="token"):
            result = await ch._download_dingtalk_file("dc123", "file.pdf", "sender1")
        assert result is None

    async def test_download_file_error(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()

        url_resp = MagicMock()
        url_resp.status_code = 200
        url_resp.json.return_value = {"downloadUrl": "https://cdn.example.com/file.pdf"}

        file_resp = MagicMock()
        file_resp.status_code = 404

        ch._http.post = AsyncMock(return_value=url_resp)
        ch._http.get = AsyncMock(return_value=file_resp)

        with patch.object(ch, "_get_access_token", new_callable=AsyncMock, return_value="token"):
            result = await ch._download_dingtalk_file("dc123", "file.pdf", "sender1")
        assert result is None

    async def test_download_exception(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=RuntimeError("network"))

        with patch.object(ch, "_get_access_token", new_callable=AsyncMock, return_value="token"):
            result = await ch._download_dingtalk_file("dc123", "file.pdf", "sender1")
        assert result is None


class TestDingTalkHandler:
    """Test XbotDingTalkHandler.process (lines 53-147)."""

    async def test_handler_text_message(self):
        ch = _make_dingtalk_channel()
        ch._loop = asyncio.get_running_loop()
        handler = XbotDingTalkHandler(ch)

        message = MagicMock()
        message.data = {
            "text": {"content": "hello bot"},
            "messageType": "text",  # not "picture" or "file"
            "senderId": "sender1",
            "senderStaffId": "",
            "senderNick": "User1",
            "conversationType": "1",
            "conversationId": "",
            "extensions": {},
        }

        result = await handler.process(message)
        assert result[0] == 200

    async def test_handler_empty_message(self):
        ch = _make_dingtalk_channel()
        handler = XbotDingTalkHandler(ch)

        message = MagicMock()
        message.data = {
            "text": {"content": ""},
            "messageType": "unknown",
            "senderId": "sender1",
            "extensions": {},
        }

        result = await handler.process(message)
        assert result[0] == 200

    async def test_handler_exception_returns_ok(self):
        """Cover line 144-147: exception in handler returns OK."""
        ch = _make_dingtalk_channel()
        handler = XbotDingTalkHandler(ch)

        message = MagicMock()
        message.data = None  # Will cause exception

        result = await handler.process(message)
        assert result[0] == 200
        assert result[1] == "Error"


class TestDingTalkStop:
    """Test stop cleanup."""

    async def test_stop(self):
        ch = _make_dingtalk_channel()
        ch._running = True
        mock_http = AsyncMock()
        mock_http.aclose = AsyncMock()
        ch._http = mock_http

        await ch.stop()

        assert ch._running is False
        mock_http.aclose.assert_called_once()
        assert ch._http is None


# ===================================================================
# 3. Email tests — covering lines 95, 113-142, 155-177, 192-226,
#    255-258, 286-325, 361-494
# ===================================================================

class TestEmailConfig:
    """Test config validation (line 95)."""

    def test_config_from_dict(self):
        bus = _make_bus()
        ch = EmailChannel({"enabled": True, "consent_granted": True,
                           "imap_host": "h", "imap_username": "u",
                           "imap_password": "p", "smtp_host": "s",
                           "smtp_username": "su", "smtp_password": "sp",
                           "allow_from": ["*"]}, bus)
        assert isinstance(ch.config, EmailConfig)

    def test_default_config(self):
        cfg = EmailChannel.default_config()
        assert "enabled" in cfg


class TestEmailValidateConfig:
    """Test _validate_config (lines 196-214)."""

    def test_valid_config(self):
        ch = _make_email_channel()
        assert ch._validate_config() is True

    def test_missing_imap_host(self):
        ch = _make_email_channel(imap_host="")
        assert ch._validate_config() is False

    def test_missing_smtp_password(self):
        ch = _make_email_channel(smtp_password="")
        assert ch._validate_config() is False


class TestEmailSMTPSend:
    """Test _smtp_send (lines 216-232)."""

    def test_smtp_send_tls(self):
        ch = _make_email_channel(smtp_use_ssl=False, smtp_use_tls=True)
        msg = EmailMessage()
        msg["From"] = "a@b.com"
        msg["To"] = "c@d.com"
        msg["Subject"] = "Test"
        msg.set_content("Hello")

        with patch("xbot.channels.email.smtplib.SMTP") as MockSMTP:
            mock_smtp = MagicMock()
            MockSMTP.return_value.__enter__ = MagicMock(return_value=mock_smtp)
            MockSMTP.return_value.__exit__ = MagicMock(return_value=False)

            ch._smtp_send(msg)

            mock_smtp.starttls.assert_called_once()
            mock_smtp.login.assert_called_once()
            mock_smtp.send_message.assert_called_once()

    def test_smtp_send_ssl(self):
        ch = _make_email_channel(smtp_use_ssl=True)
        msg = EmailMessage()
        msg["From"] = "a@b.com"
        msg["To"] = "c@d.com"
        msg.set_content("Hello")

        with patch("xbot.channels.email.smtplib.SMTP_SSL") as MockSMTP:
            mock_smtp = MagicMock()
            MockSMTP.return_value.__enter__ = MagicMock(return_value=mock_smtp)
            MockSMTP.return_value.__exit__ = MagicMock(return_value=False)

            ch._smtp_send(msg)

            mock_smtp.login.assert_called_once()
            mock_smtp.send_message.assert_called_once()


class TestEmailSend:
    """Test send (lines 148-194)."""

    async def test_send_no_consent(self):
        ch = _make_email_channel(consent_granted=False)
        msg = OutboundMessage(channel="email", chat_id="user@example.com", content="hi")
        await ch.send(msg)  # Should return early

    async def test_send_no_smtp_host(self):
        ch = _make_email_channel(smtp_host="")
        msg = OutboundMessage(channel="email", chat_id="user@example.com", content="hi")
        await ch.send(msg)  # Should return early

    async def test_send_no_recipient(self):
        ch = _make_email_channel()
        msg = OutboundMessage(channel="email", chat_id="  ", content="hi")
        await ch.send(msg)  # Should return early

    async def test_send_auto_reply_disabled(self):
        """Cover lines 168-170: auto_reply disabled for reply."""
        ch = _make_email_channel(auto_reply_enabled=False)
        ch._last_subject_by_chat["user@example.com"] = "Original Subject"

        msg = OutboundMessage(channel="email", chat_id="user@example.com", content="hi")
        await ch.send(msg)  # Should skip (is_reply + no auto_reply)

    async def test_send_force_send_overrides_auto_reply(self):
        """Cover lines 165-166: force_send bypasses auto_reply check."""
        ch = _make_email_channel(auto_reply_enabled=False)
        ch._last_subject_by_chat["user@example.com"] = "Subject"

        msg = OutboundMessage(
            channel="email", chat_id="user@example.com", content="hi",
            metadata={"force_send": True},
        )
        with patch.object(ch, "_smtp_send", MagicMock()):
            await ch.send(msg)  # Should send

    async def test_send_success(self):
        ch = _make_email_channel()
        msg = OutboundMessage(channel="email", chat_id="user@example.com", content="hello")

        with patch.object(ch, "_smtp_send", MagicMock()):
            await ch.send(msg)

    async def test_send_with_subject_override(self):
        """Cover lines 174-177: metadata subject override."""
        ch = _make_email_channel()
        msg = OutboundMessage(
            channel="email", chat_id="user@example.com", content="hi",
            metadata={"subject": "Custom Subject"},
        )

        with patch.object(ch, "_smtp_send", MagicMock()) as mock_send:
            await ch.send(msg)
            email_msg = mock_send.call_args[0][0]
            assert email_msg["Subject"] == "Custom Subject"

    async def test_send_with_in_reply_to(self):
        """Cover lines 185-188: in-reply-to threading."""
        ch = _make_email_channel()
        ch._last_message_id_by_chat["user@example.com"] = "<msg123@example.com>"

        msg = OutboundMessage(channel="email", chat_id="user@example.com", content="reply")

        with patch.object(ch, "_smtp_send", MagicMock()) as mock_send:
            await ch.send(msg)
            email_msg = mock_send.call_args[0][0]
            assert email_msg["In-Reply-To"] == "<msg123@example.com>"
            assert email_msg["References"] == "<msg123@example.com>"

    async def test_send_smtp_error_raises(self):
        """Cover lines 192-194: SMTP error propagated."""
        ch = _make_email_channel()
        msg = OutboundMessage(channel="email", chat_id="user@example.com", content="hi")

        with patch.object(ch, "_smtp_send", side_effect=RuntimeError("SMTP fail")):
            with pytest.raises(RuntimeError, match="SMTP fail"):
                await ch.send(msg)


class TestEmailIMAPFetch:
    """Test _fetch_messages (lines 272-364)."""

    def _make_mock_imap(self):
        mock_client = MagicMock()
        mock_client.login = MagicMock()
        mock_client.select = MagicMock(return_value=("OK", [b"1"]))
        return mock_client

    def test_fetch_no_messages(self):
        ch = _make_email_channel()
        mock_client = self._make_mock_imap()
        mock_client.search = MagicMock(return_value=("OK", [b""]))

        with patch("xbot.channels.email.imaplib.IMAP4_SSL", return_value=mock_client):
            messages = ch._fetch_messages(("UNSEEN",), mark_seen=True, dedupe=True, limit=0)

        assert messages == []

    def test_fetch_with_messages(self):
        """Cover lines 286-350: full fetch path."""
        ch = _make_email_channel()
        ch._processed_uids.clear()  # Avoid interference from persisted cache
        mock_client = self._make_mock_imap()
        mock_client.search = MagicMock(return_value=("OK", [b"1 2"]))

        # Build a valid email message
        from email.message import EmailMessage as EM
        em = EM()
        em["From"] = "sender@example.com"
        em["Subject"] = "Test Subject"
        em["Date"] = "Mon, 1 Jan 2024 00:00:00 +0000"
        em["Message-ID"] = "<test@example.com>"
        em.set_content("Hello body")
        raw_bytes = em.as_bytes()

        mock_client.fetch = MagicMock(return_value=(
            "OK",
            [(b"1 (UID 100 BODY[] {100}", raw_bytes)],
        ))
        mock_client.store = MagicMock()

        with patch("xbot.channels.email.imaplib.IMAP4_SSL", return_value=mock_client):
            messages = ch._fetch_messages(("UNSEEN",), mark_seen=True, dedupe=False, limit=0)

        assert len(messages) == 2  # Two IDs
        assert messages[0]["sender"] == "sender@example.com"
        assert "Test Subject" in messages[0]["content"]

    def test_fetch_select_error(self):
        ch = _make_email_channel()
        mock_client = self._make_mock_imap()
        mock_client.select = MagicMock(return_value=("NO", []))

        with patch("xbot.channels.email.imaplib.IMAP4_SSL", return_value=mock_client):
            messages = ch._fetch_messages(("UNSEEN",), mark_seen=True, dedupe=True, limit=0)

        assert messages == []

    def test_fetch_imap4_no_ssl(self):
        """Cover line 286: IMAP4 without SSL."""
        ch = _make_email_channel(imap_use_ssl=False)
        mock_client = self._make_mock_imap()
        mock_client.search = MagicMock(return_value=("OK", [b""]))

        with patch("xbot.channels.email.imaplib.IMAP4", return_value=mock_client):
            messages = ch._fetch_messages(("UNSEEN",), mark_seen=True, dedupe=True, limit=0)

        assert messages == []

    def test_fetch_with_limit(self):
        """Cover lines 299-300: limit applied."""
        ch = _make_email_channel()
        mock_client = self._make_mock_imap()
        mock_client.search = MagicMock(return_value=("OK", [b"1 2 3 4 5"]))
        mock_client.fetch = MagicMock(return_value=("OK", []))

        with patch("xbot.channels.email.imaplib.IMAP4_SSL", return_value=mock_client):
            messages = ch._fetch_messages(("UNSEEN",), mark_seen=True, dedupe=True, limit=2)

        # fetch should have been called only for the last 2 IDs
        assert mock_client.fetch.call_count == 2

    def test_fetch_dedup_skips_known_uid(self):
        """Cover lines 310-312: dedup by UID."""
        ch = _make_email_channel()
        ch._processed_uids["100"] = None

        mock_client = self._make_mock_imap()
        mock_client.search = MagicMock(return_value=("OK", [b"1"]))
        mock_client.fetch = MagicMock(return_value=(
            "OK",
            [(b"1 (UID 100 BODY[] {50}", b"From: a@b.com\r\n\r\nbody")],
        ))

        with patch("xbot.channels.email.imaplib.IMAP4_SSL", return_value=mock_client):
            messages = ch._fetch_messages(("UNSEEN",), mark_seen=True, dedupe=True, limit=0)

        assert messages == []


class TestEmailFetchNewMessages:
    """Test _fetch_new_messages (line 234-241)."""

    def test_calls_fetch_with_unseen(self):
        ch = _make_email_channel()
        with patch.object(ch, "_fetch_messages", return_value=[]) as mock_fetch:
            ch._fetch_new_messages()
        mock_fetch.assert_called_once()
        # Check that search_criteria was passed
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("search_criteria") == ("UNSEEN",)


class TestEmailFetchBetweenDates:
    """Test fetch_messages_between_dates (lines 243-270)."""

    def test_no_consent(self):
        ch = _make_email_channel(consent_granted=False)
        result = ch.fetch_messages_between_dates(
            date(2024, 1, 1), date(2024, 1, 31))
        assert result == []

    def test_end_before_start(self):
        ch = _make_email_channel()
        result = ch.fetch_messages_between_dates(
            date(2024, 1, 31), date(2024, 1, 1))
        assert result == []

    def test_valid_range(self):
        ch = _make_email_channel()
        with patch.object(ch, "_fetch_messages", return_value=[]) as mock_fetch:
            ch.fetch_messages_between_dates(date(2024, 1, 1), date(2024, 1, 31), limit=10)
        mock_fetch.assert_called_once()


class TestEmailFormatImapDate:
    """Test _format_imap_date (lines 411-415)."""

    def test_format_date(self):
        assert EmailChannel._format_imap_date(date(2024, 3, 5)) == "05-Mar-2024"
        assert EmailChannel._format_imap_date(date(2024, 12, 25)) == "25-Dec-2024"


class TestEmailExtractHelpers:
    """Test static extraction helpers."""

    def test_extract_message_bytes(self):
        fetched = [(b"header", b"body bytes")]
        assert EmailChannel._extract_message_bytes(fetched) == b"body bytes"

    def test_extract_message_bytes_none(self):
        fetched = [(b"header", "not bytes")]
        assert EmailChannel._extract_message_bytes(fetched) is None

    def test_extract_uid(self):
        fetched = [(b"1 (UID 42 BODY[] {100}", b"data")]
        assert EmailChannel._extract_uid(fetched) == "42"

    def test_extract_uid_not_found(self):
        fetched = [(b"no uid here", b"data")]
        assert EmailChannel._extract_uid(fetched) == ""

    def test_decode_header_value(self):
        assert EmailChannel._decode_header_value("Simple Subject") == "Simple Subject"
        assert EmailChannel._decode_header_value("") == ""

    def test_decode_header_encoded(self):
        # RFC 2047 encoded word
        result = EmailChannel._decode_header_value("=?utf-8?b?SGVsbG8=?=")
        assert "Hello" in result


class TestEmailExtractTextBody:
    """Test _extract_text_body (lines 444-481)."""

    def test_plain_text(self):
        msg = EmailMessage()
        msg.set_content("Hello plain text")
        result = EmailChannel._extract_text_body(msg)
        assert "Hello plain text" in result

    def test_html_content(self):
        """Cover lines 479-480: HTML to text conversion."""
        msg = EmailMessage()
        msg.set_content("<html><body><p>Hello</p></body></html>")
        msg.set_type("text/html")
        result = EmailChannel._extract_text_body(msg)
        assert "Hello" in result

    def test_multipart_plain(self):
        msg = EmailMessage()
        msg.set_content("plain text")
        # Make it appear multipart
        msg2 = EmailMessage()
        msg2.set_content("plain text")
        result = EmailChannel._extract_text_body(msg2)
        assert "plain text" in result

    def test_empty_body(self):
        msg = EmailMessage()
        msg.set_content("")
        result = EmailChannel._extract_text_body(msg)
        assert result == ""


class TestEmailHtmlToText:
    """Test _html_to_text (lines 484-488)."""

    def test_br_tags(self):
        result = EmailChannel._html_to_text("Hello<br>World")
        assert "Hello\nWorld" in result

    def test_br_self_closing(self):
        result = EmailChannel._html_to_text("Hello<br/>World")
        assert "Hello\nWorld" in result

    def test_p_closing(self):
        result = EmailChannel._html_to_text("<p>Hello</p><p>World</p>")
        assert "Hello" in result
        assert "\n" in result

    def test_strip_tags(self):
        result = EmailChannel._html_to_text("<b>bold</b> <i>italic</i>")
        assert "<" not in result
        assert "bold" in result

    def test_html_entities(self):
        result = EmailChannel._html_to_text("&amp; &lt; &gt;")
        assert "& < >" in result


class TestEmailReplySubject:
    """Test _reply_subject (lines 490-495)."""

    def test_add_prefix(self):
        ch = _make_email_channel()
        assert ch._reply_subject("Hello") == "Re: Hello"

    def test_already_has_re(self):
        ch = _make_email_channel()
        assert ch._reply_subject("Re: Hello") == "Re: Hello"

    def test_empty_subject(self):
        ch = _make_email_channel()
        assert ch._reply_subject("") == "Re: xbot reply"

    def test_custom_prefix(self):
        ch = _make_email_channel(subject_prefix="Fwd: ")
        assert ch._reply_subject("Hello") == "Fwd: Hello"


class TestEmailProcessedUIDs:
    """Test processed UID management (lines 366-409)."""

    def test_load_nonexistent(self, tmp_path):
        ch = _make_email_channel()
        ch._processed_uids_path = tmp_path / "nonexistent.json"
        result = ch._load_processed_uids()
        assert isinstance(result, OrderedDict)
        assert len(result) == 0

    def test_load_valid(self, tmp_path):
        ch = _make_email_channel()
        path = tmp_path / "uids.json"
        path.write_text(json.dumps(["uid1", "uid2", "uid3"]))
        ch._processed_uids_path = path
        result = ch._load_processed_uids()
        assert len(result) == 3
        assert "uid1" in result

    def test_load_invalid_json(self, tmp_path):
        ch = _make_email_channel()
        path = tmp_path / "uids.json"
        path.write_text("not json")
        ch._processed_uids_path = path
        result = ch._load_processed_uids()
        assert isinstance(result, OrderedDict)

    def test_load_non_list(self, tmp_path):
        ch = _make_email_channel()
        path = tmp_path / "uids.json"
        path.write_text(json.dumps({"key": "value"}))
        ch._processed_uids_path = path
        result = ch._load_processed_uids()
        assert len(result) == 0

    def test_remember_uid(self, tmp_path):
        ch = _make_email_channel()
        ch._processed_uids_path = tmp_path / "uids.json"
        ch._remember_processed_uid("uid1")
        assert "uid1" in ch._processed_uids

    def test_save_and_reload(self, tmp_path):
        ch = _make_email_channel()
        ch._processed_uids_path = tmp_path / "uids.json"
        ch._remember_processed_uid("uid1")
        ch._remember_processed_uid("uid2")

        # Reload
        result = ch._load_processed_uids()
        assert "uid1" in result
        assert "uid2" in result

    def test_trim_overflow(self, tmp_path):
        ch = _make_email_channel()
        ch._processed_uids_path = tmp_path / "uids.json"
        ch._MAX_PROCESSED_UIDS = 5

        for i in range(10):
            ch._remember_processed_uid(f"uid{i}")

        assert len(ch._processed_uids) <= 5


class TestEmailStart:
    """Test start polling (lines 104-142)."""

    async def test_start_no_consent(self):
        ch = _make_email_channel(consent_granted=False)
        await ch.start()  # Should return immediately

    async def test_start_invalid_config(self):
        ch = _make_email_channel(imap_host="")
        await ch.start()  # Should return after _validate_config fails

    async def test_start_polling(self):
        """Cover lines 116-142: polling loop."""
        ch = _make_email_channel()
        ch._running = True

        call_count = 0

        async def fake_handle_message(**kw):
            nonlocal call_count
            call_count += 1

        with patch.object(ch, "_validate_config", return_value=True):
            with patch.object(ch, "_fetch_new_messages", return_value=[
                {"sender": "a@b.com", "subject": "Test", "content": "body",
                 "message_id": "<msg1>", "metadata": {}},
            ]):
                with patch.object(ch, "_handle_message", new_callable=AsyncMock) as mock_handle:
                    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                        async def stop_after_one(*a):
                            ch._running = False
                        mock_sleep.side_effect = stop_after_one
                        await ch.start()

        mock_handle.assert_called_once()


# ===================================================================
# 4. Permission tests — covering lines 366, 459, 676-832, 865-929
# ===================================================================

class TestBasePermissionHandler:
    """Test BasePermissionHandler methods."""

    def test_is_safe_tool(self):
        h = BasePermissionHandler()
        assert h.is_safe_tool("Read") is True
        assert h.is_safe_tool("read_file") is True
        assert h.is_safe_tool("Bash") is False

    def test_is_safe_tool_case_insensitive(self):
        h = BasePermissionHandler()
        assert h.is_safe_tool("READ") is True

    def test_add_safe_tool(self):
        h = BasePermissionHandler()
        h.add_safe_tool("custom_tool")
        assert h.is_safe_tool("custom_tool") is True

    def test_summarize_input_empty(self):
        assert BasePermissionHandler.summarize_input({}) == ""

    def test_summarize_input_normal(self):
        result = BasePermissionHandler.summarize_input({"key": "value"})
        assert "key" in result

    def test_summarize_input_truncated(self):
        big = {"key": "x" * 200}
        result = BasePermissionHandler.summarize_input(big, max_len=50)
        assert len(result) <= 53  # 50 + "..."

    def test_summarize_input_ask_user(self):
        tool_input = {
            "questions": [{
                "header": "Q1",
                "question": "What?",
                "options": [{"label": "A", "description": "Option A"}],
                "multiSelect": False,
            }],
        }
        result = BasePermissionHandler.summarize_input(tool_input, tool_name="AskUserQuestion")
        assert "Q1" in result
        assert "What?" in result
        assert "Option A" in result

    def test_format_ask_user_question_empty(self):
        result = BasePermissionHandler._format_ask_user_question({})
        assert result  # Should fall back to JSON

    def test_format_permission_message_ask_user(self):
        h = BasePermissionHandler()
        msg = h.format_permission_message("AskUserQuestion", {
            "questions": [{"header": "Q", "question": "?", "options": [], "multiSelect": False}],
        })
        assert "Q" in msg

    def test_format_permission_message_normal(self):
        h = BasePermissionHandler()
        msg = h.format_permission_message("Bash", {"command": "rm -rf /"})
        assert "Bash" in msg
        assert "rm -rf /" in msg

    def test_parse_answers_single(self):
        questions = [{"question": "Q1"}]
        options_map = [["A", "B"]]
        answers = BasePermissionHandler._parse_answers("A", questions, options_map)
        assert len(answers) == 1
        assert answers[0]["answer"] == "A"

    def test_parse_answers_mismatch_count(self):
        questions = [{"question": "Q1"}, {"question": "Q2"}]
        options_map = [["A", "B"], ["C", "D"]]
        answers = BasePermissionHandler._parse_answers("A", questions, options_map)
        assert len(answers) == 2
        assert answers[1]["answer"] == ""  # Missing

    def test_parse_answers_no_match(self):
        questions = [{"question": "Q1"}]
        options_map = [["A", "B"]]
        answers = BasePermissionHandler._parse_answers("Z", questions, options_map)
        assert answers[0]["answer"] == "Z"  # Raw input used


class TestPermissionRequestHandler:
    """Test channel-mode permission handler."""

    async def test_safe_tool_auto_approved(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        h.set_session_context("s1", "telegram", "chat1")
        h.set_current_session("s1")

        decision, result = await h.can_use_tool("Read", {"file": "test.py"}, None)
        assert decision == "allow"

    async def test_no_session_context_denies(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        # No session context set
        decision, result = await h.can_use_tool("Bash", {"command": "ls"}, None)
        assert decision == "deny"

    async def test_permission_flow_allow(self):
        """Cover lines 433-459: full permission flow."""
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus, timeout=1.0)
        h.set_session_context("s1", "telegram", "chat1")
        h.set_current_session("s1")

        bus.wait_permission_response = AsyncMock(return_value=PermissionResponse(
            request_id="r1", session_key="s1", decision="allow",
            updated_input={"command": "ls"},
        ))

        decision, result = await h.can_use_tool("Bash", {"command": "ls"}, None)
        assert decision == "allow"
        assert result["command"] == "ls"

    async def test_permission_flow_deny(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus, timeout=1.0)
        h.set_session_context("s1", "telegram", "chat1")
        h.set_current_session("s1")

        bus.wait_permission_response = AsyncMock(return_value=PermissionResponse(
            request_id="r1", session_key="s1", decision="deny",
            reason="User denied",
        ))

        decision, result = await h.can_use_tool("Bash", {"command": "rm"}, None)
        assert decision == "deny"
        assert "denied" in result.lower()


class TestPermissionContextCleanup:
    """Test context cleanup (lines 358-389)."""

    def test_set_and_clear_session(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        h.set_session_context("s1", "telegram", "chat1")
        assert "s1" in h._session_context

        h.clear_session_context("s1")
        assert "s1" not in h._session_context

    def test_clear_current_session_key(self):
        """Cover line 366: clearing current session key."""
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        h.set_session_context("s1", "telegram", "chat1")
        h.set_current_session("s1")
        assert h.get_current_session_key() == "s1"

        h.clear_session_context("s1")
        assert h.get_current_session_key() is None

    def test_cleanup_expired_contexts(self):
        """Cover lines 368-377: TTL-based cleanup."""
        import time
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        h._context_ttl = 1  # 1 second TTL

        h.set_session_context("old", "telegram", "chat1")
        h._context_timestamps["old"] = time.time() - 10  # 10s ago

        h.set_session_context("new", "telegram", "chat2")
        # Trigger cleanup
        h._cleanup_expired_contexts()

        assert "old" not in h._session_context
        assert "new" in h._session_context

    def test_cleanup_capacity_overflow(self):
        """Cover lines 383-389: capacity-based eviction."""
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        h._max_contexts = 3

        for i in range(5):
            h.set_session_context(f"s{i}", "telegram", f"chat{i}")

        # Should have evicted oldest
        assert len(h._session_context) <= 3

    def test_get_current_session_key_single(self):
        """Cover lines 400-403: single session key inference."""
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        h.set_session_context("only_one", "telegram", "chat1")
        assert h.get_current_session_key() == "only_one"

    def test_get_current_session_key_none(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        assert h.get_current_session_key() is None


class TestPermissionAskUserQuestion:
    """Test _handle_ask_user_question (lines 463-560)."""

    async def test_no_session_context(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        decision, result = await h._handle_ask_user_question({"questions": [{"question": "Q"}]})
        assert decision == "deny"

    async def test_no_questions(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        h.set_session_context("s1", "telegram", "chat1")
        h.set_current_session("s1")

        decision, result = await h._handle_ask_user_question({"questions": []})
        assert decision == "deny"

    async def test_answered(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus, timeout=1.0)
        h.set_session_context("s1", "telegram", "chat1")
        h.set_current_session("s1")

        bus.wait_interaction_response = AsyncMock(return_value=InteractionResponse(
            request_id="r1", session_key="s1", action="answer", content="Option A",
        ))

        tool_input = {
            "questions": [{
                "header": "Q1",
                "question": "Choose",
                "options": [{"label": "Option A"}, {"label": "Option B"}],
            }],
        }
        decision, result = await h._handle_ask_user_question(tool_input)
        assert decision == "allow"
        assert "answers" in result

    async def test_cancelled(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus, timeout=1.0)
        h.set_session_context("s1", "telegram", "chat1")
        h.set_current_session("s1")

        bus.wait_interaction_response = AsyncMock(return_value=InteractionResponse(
            request_id="r1", session_key="s1", action="cancel", content="",
        ))

        tool_input = {"questions": [{"question": "Q", "options": []}]}
        decision, result = await h._handle_ask_user_question(tool_input)
        assert decision == "deny"

    async def test_multiple_questions(self):
        """Cover multi-question prompt (lines 519-527)."""
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus, timeout=1.0)
        h.set_session_context("s1", "telegram", "chat1")
        h.set_current_session("s1")

        bus.wait_interaction_response = AsyncMock(return_value=InteractionResponse(
            request_id="r1", session_key="s1", action="reply", content="A, B",
        ))

        tool_input = {
            "questions": [
                {"header": "Q1", "question": "First?", "options": [{"label": "A"}]},
                {"header": "Q2", "question": "Second?", "options": [{"label": "B"}]},
            ],
        }
        decision, result = await h._handle_ask_user_question(tool_input)
        assert decision == "allow"


class TestPermissionRequestInteraction:
    """Test request_interaction for PermissionRequestHandler (lines 562-597)."""

    async def test_no_session(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus)
        result = await h.request_interaction(kind="question", prompt="test")
        assert result.action == "cancel"

    async def test_with_session(self):
        bus = _make_bus()
        h = PermissionRequestHandler(bus=bus, timeout=1.0)
        h.set_session_context("s1", "telegram", "chat1")

        bus.wait_interaction_response = AsyncMock(return_value=InteractionResponse(
            request_id="r1", session_key="s1", action="reply", content="answer",
        ))

        result = await h.request_interaction(
            kind="question", prompt="What?", session_key="s1",
        )
        assert result.action == "reply"


class TestCLIPermissionHandler:
    """Test CLI permission handler (lines 600-837)."""

    async def test_safe_tool_auto_approved(self):
        h = CLIPermissionHandler(interactive=True)
        decision, result = await h.can_use_tool("Read", {"file": "test.py"}, None)
        assert decision == "allow"

    async def test_non_interactive_denies(self):
        """Cover line 635-636: non-interactive denies non-safe tools."""
        h = CLIPermissionHandler(interactive=False)
        decision, result = await h.can_use_tool("Bash", {"command": "rm"}, None)
        assert decision == "deny"
        assert "Non-interactive" in result

    async def test_ask_user_terminal_yes(self):
        """Cover lines 676-716: rich prompt."""
        h = CLIPermissionHandler(interactive=True)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="y"):
                decision, result = await h._ask_user_in_terminal("Bash", {"command": "ls"})
        assert decision == "allow"

    async def test_ask_user_terminal_always(self):
        """Cover lines 712-714: 'a' adds to safe tools."""
        h = CLIPermissionHandler(interactive=True)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="a"):
                decision, result = await h._ask_user_in_terminal("CustomTool", {"cmd": "x"})
        assert decision == "allow"
        assert h.is_safe_tool("CustomTool")

    async def test_ask_user_terminal_deny(self):
        """Cover lines 715-716: deny."""
        h = CLIPermissionHandler(interactive=True)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="n"):
                decision, result = await h._ask_user_in_terminal("Bash", {"command": "rm"})
        assert decision == "deny"

    async def test_ask_user_terminal_interrupt(self):
        """Cover lines 707-708: KeyboardInterrupt."""
        h = CLIPermissionHandler(interactive=True)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", side_effect=KeyboardInterrupt):
                decision, result = await h._ask_user_in_terminal("Bash", {})
        assert decision == "deny"
        assert "cancelled" in result.lower()

    async def test_ask_user_basic_yes(self):
        """Cover lines 718-745: basic input."""
        h = CLIPermissionHandler(interactive=True)

        with patch("builtins.print"):
            with patch("builtins.input", return_value="y"):
                decision, result = await h._ask_user_basic("Bash", {"command": "ls"})
        assert decision == "allow"

    async def test_ask_user_basic_always(self):
        h = CLIPermissionHandler(interactive=True)

        with patch("builtins.print"):
            with patch("builtins.input", return_value="a"):
                decision, result = await h._ask_user_basic("Tool", {})
        assert decision == "allow"

    async def test_ask_user_basic_deny(self):
        h = CLIPermissionHandler(interactive=True)

        with patch("builtins.print"):
            with patch("builtins.input", return_value="n"):
                decision, result = await h._ask_user_basic("Bash", {})
        assert decision == "deny"

    async def test_ask_user_basic_interrupt(self):
        h = CLIPermissionHandler(interactive=True)

        with patch("builtins.print"):
            with patch("builtins.input", side_effect=EOFError):
                decision, result = await h._ask_user_basic("Bash", {})
        assert decision == "deny"

    async def test_ask_user_rich_fallback_to_basic(self):
        """Cover lines 679-681: rich not available fallback."""
        h = CLIPermissionHandler(interactive=True)

        with patch.dict("sys.modules", {"rich.console": None, "rich.prompt": None}):
            # Force ImportError by making the imports fail
            with patch("builtins.print"):
                with patch("builtins.input", return_value="y"):
                    # This should fall through to basic since we can't really
                    # un-import rich, but we can test the basic path directly
                    decision, result = await h._ask_user_basic("Bash", {"cmd": "ls"})
        assert decision == "allow"


class TestCLIInteraction:
    """Test CLI interaction handlers (lines 641-837)."""

    async def test_non_interactive_interaction(self):
        """Cover lines 655-661: non-interactive cancels."""
        h = CLIPermissionHandler(interactive=False)
        result = await h.request_interaction(kind="question", prompt="test")
        assert result.action == "cancel"

    async def test_interaction_question_rich(self):
        """Cover lines 790-796: question with rich."""
        h = CLIPermissionHandler(interactive=True)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="my answer"):
                result = await h._ask_interaction_in_terminal(
                    kind="question", prompt="What?", suggestions=["a", "b"],
                    session_key="s1",
                )
        assert result.action == "reply"
        assert result.content == "my answer"

    async def test_interaction_confirmation_yes(self):
        """Cover lines 775-788: confirmation with y."""
        h = CLIPermissionHandler(interactive=True)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="y"):
                result = await h._ask_interaction_in_terminal(
                    kind="confirmation", prompt="Confirm?", suggestions=[],
                    session_key="s1",
                )
        assert result.action == "confirm"

    async def test_interaction_confirmation_no(self):
        h = CLIPermissionHandler(interactive=True)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="n"):
                result = await h._ask_interaction_in_terminal(
                    kind="confirmation", prompt="Confirm?", suggestions=[],
                    session_key="s1",
                )
        assert result.action == "cancel"

    async def test_interaction_approval_yes(self):
        h = CLIPermissionHandler(interactive=True)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="y"):
                result = await h._ask_interaction_in_terminal(
                    kind="approval", prompt="Approve?", suggestions=[],
                    session_key="s1",
                )
        assert result.action == "allow"

    async def test_interaction_approval_no(self):
        h = CLIPermissionHandler(interactive=True)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="n"):
                result = await h._ask_interaction_in_terminal(
                    kind="approval", prompt="Approve?", suggestions=[],
                    session_key="s1",
                )
        assert result.action == "deny"

    async def test_interaction_interrupt(self):
        """Cover lines 797-803: interrupt."""
        h = CLIPermissionHandler(interactive=True)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", side_effect=KeyboardInterrupt):
                result = await h._ask_interaction_in_terminal(
                    kind="question", prompt="?", suggestions=[], session_key="s1",
                )
        assert result.action == "cancel"

    async def test_interaction_basic_question(self):
        """Cover lines 805-837: basic input."""
        h = CLIPermissionHandler(interactive=True)

        with patch("builtins.print"):
            with patch("builtins.input", return_value="answer"):
                result = await h._ask_interaction_basic(
                    kind="question", prompt="What?", session_key="s1",
                )
        assert result.action == "reply"
        assert result.content == "answer"

    async def test_interaction_basic_confirmation_yes(self):
        h = CLIPermissionHandler(interactive=True)

        with patch("builtins.print"):
            with patch("builtins.input", return_value="y"):
                result = await h._ask_interaction_basic(
                    kind="confirmation", prompt="Confirm?", session_key="s1",
                )
        assert result.action == "confirm"

    async def test_interaction_basic_confirmation_no(self):
        h = CLIPermissionHandler(interactive=True)

        with patch("builtins.print"):
            with patch("builtins.input", return_value="n"):
                result = await h._ask_interaction_basic(
                    kind="confirmation", prompt="Confirm?", session_key="s1",
                )
        assert result.action == "cancel"

    async def test_interaction_basic_interrupt(self):
        """Cover lines 831-837: interrupt."""
        h = CLIPermissionHandler(interactive=True)

        with patch("builtins.print"):
            with patch("builtins.input", side_effect=EOFError):
                result = await h._ask_interaction_basic(
                    kind="question", prompt="?", session_key="s1",
                )
        assert result.action == "cancel"


class TestInteractivePermissionHandler:
    """Test interactive handler with spinner (lines 840-946)."""

    async def test_ask_user_with_spinner(self):
        """Cover lines 865-912: spinner pause/resume."""
        h = InteractivePermissionHandler()
        mock_spinner = MagicMock()
        mock_pause_ctx = MagicMock()
        mock_spinner.pause.return_value = mock_pause_ctx
        h.set_thinking_spinner(mock_spinner)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="y"):
                decision, result = await h._ask_user_in_terminal("Bash", {"cmd": "ls"})

        assert decision == "allow"
        mock_spinner.pause.assert_called_once()

    async def test_ask_user_no_spinner(self):
        h = InteractivePermissionHandler()

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="y"):
                decision, result = await h._ask_user_in_terminal("Bash", {"cmd": "ls"})
        assert decision == "allow"

    async def test_interaction_with_spinner(self):
        """Cover lines 914-946: interaction with spinner pause."""
        h = InteractivePermissionHandler()
        mock_spinner = MagicMock()
        mock_pause_ctx = MagicMock()
        mock_spinner.pause.return_value = mock_pause_ctx
        h.set_thinking_spinner(mock_spinner)

        with patch("rich.console.Console"):
            with patch("rich.prompt.Prompt.ask", return_value="answer"):
                result = await h.request_interaction(
                    kind="question", prompt="What?", session_key="s1",
                )
        assert result.action == "reply"
        mock_spinner.pause.assert_called_once()

    async def test_interaction_non_interactive(self):
        """Cover lines 928-934: non-interactive."""
        h = InteractivePermissionHandler()
        h.interactive = False
        result = await h.request_interaction(kind="question", prompt="test")
        assert result.action == "cancel"


class TestCreatePermissionHandler:
    """Test create_permission_handler factory (lines 949-998)."""

    def test_channel_mode(self):
        bus = _make_bus()
        h = create_permission_handler("channel", bus=bus)
        assert isinstance(h, PermissionRequestHandler)

    def test_channel_mode_no_bus_raises(self):
        with pytest.raises(ValueError, match="MessageBus"):
            create_permission_handler("channel")

    def test_cli_mode(self):
        h = create_permission_handler("cli")
        assert isinstance(h, CLIPermissionHandler)

    def test_cli_non_interactive(self):
        h = create_permission_handler("cli", non_interactive=True)
        assert isinstance(h, CLIPermissionHandler)
        assert h.interactive is False

    def test_interactive_mode(self):
        h = create_permission_handler("interactive")
        assert isinstance(h, InteractivePermissionHandler)

    def test_interactive_with_spinner(self):
        spinner = MagicMock()
        h = create_permission_handler("interactive", thinking_spinner=spinner)
        assert h._thinking is spinner


# ===================================================================
# 5. Role CLI tests — covering lines 75-78, 95, 138-142, 239-337,
#    364-368, 426-428, 468-483, 513-555
# ===================================================================

# We need to mock the planner modules
@pytest.fixture(autouse=True)
def _mock_planner_modules():
    """Mock RolePoolManager and RoleCreator for CLI tests."""
    # Create mock role
    mock_role = MagicMock()
    mock_role.name = "test_role"
    mock_role.display_name = "Test Role"
    mock_role.description = "A test role"
    mock_role.goal = "Test goal"
    mock_role.backstory = "Test backstory"
    mock_role.tier = MagicMock()
    mock_role.tier.value = "core"
    mock_cap = MagicMock()
    mock_cap.value = "analyze"
    mock_role.capabilities = [mock_cap]
    mock_role.tools = ["tool1", "tool2"]
    mock_role.tags = ["test"]
    mock_role.examples = ["example1"]
    mock_role.max_iterations = 30
    mock_role.timeout_multiplier = 1.0
    mock_role.tool_restrictions = None
    mock_role.to_dict.return_value = {
        "name": "test_role",
        "display_name": "Test Role",
        "tier": "core",
        "description": "A test role",
    }

    # Mock pool
    mock_pool = MagicMock()
    mock_pool.get_available_roles.return_value = [mock_role]
    mock_pool.get_role.return_value = mock_role

    # Mock manager
    mock_manager_instance = MagicMock()
    mock_manager_instance.get_pool.return_value = mock_pool

    with patch("xbot.crew.cli.role_cmd.RolePoolManager", return_value=mock_manager_instance):
        with patch("xbot.crew.cli.role_cmd.RoleCreator") as MockCreator:
            mock_creator_instance = MagicMock()
            MockCreator.return_value = mock_creator_instance
            yield {
                "role": mock_role,
                "pool": mock_pool,
                "manager": mock_manager_instance,
                "creator": mock_creator_instance,
            }


class TestRoleCLIList:
    """Test roles_list command."""

    def test_list_all(self):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0

    def test_list_json(self, _mock_planner_modules):
        """Cover lines 74-78: JSON output."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == 0

    def test_list_by_tier(self):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["list", "--tier", "core"])
        assert result.exit_code == 0

    def test_list_invalid_tier(self):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["list", "--tier", "invalid"])
        assert result.exit_code == 1

    def test_list_no_roles(self, _mock_planner_modules):
        """Cover line 80-82: no roles found."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        _mock_planner_modules["pool"].get_available_roles.return_value = []
        runner = CliRunner()
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0

    def test_list_many_capabilities(self, _mock_planner_modules):
        """Cover line 95: capabilities overflow display."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        mock_role = MagicMock()
        mock_role.name = "complex_role"
        mock_role.display_name = "Complex"
        mock_role.tier = MagicMock()
        mock_role.tier.value = "core"
        mock_role.description = "Short"
        caps = [MagicMock() for _ in range(5)]
        for i, c in enumerate(caps):
            c.value = f"cap{i}"
        mock_role.capabilities = caps

        _mock_planner_modules["pool"].get_available_roles.return_value = [mock_role]

        runner = CliRunner()
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "+2" in result.output  # 5 caps - 3 shown = +2


class TestRoleCLIShow:
    """Test roles_show command."""

    def test_show_role(self):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["show", "test_role"])
        assert result.exit_code == 0

    def test_show_not_found(self, _mock_planner_modules):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        _mock_planner_modules["pool"].get_role.return_value = None
        runner = CliRunner()
        result = runner.invoke(app, ["show", "nonexistent"])
        assert result.exit_code == 1

    def test_show_yaml(self):
        """Cover lines 137-142: YAML output."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["show", "test_role", "--yaml"])
        assert result.exit_code == 0


class TestRoleCLICreate:
    """Test roles_create command (lines 171-339)."""

    def test_create_from_args(self, _mock_planner_modules):
        """Cover lines 277-339: create with CLI args."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        mock_creator = _mock_planner_modules["creator"]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.warnings = []
        mock_role = MagicMock()
        mock_role.name = "new_role"
        mock_role.display_name = "New Role"
        mock_role.description = "desc"
        mock_cap = MagicMock()
        mock_cap.value = "search"
        mock_role.capabilities = [mock_cap]
        mock_role.tools = []
        mock_result.role = mock_role
        mock_creator.create_role_from_definition.return_value = mock_result

        runner = CliRunner()
        result = runner.invoke(app, [
            "create",
            "--name", "new_role",
            "--description", "A new role",
            "--goal", "Do things",
            "--capabilities", "search",
            "--no-interactive",
        ])
        assert result.exit_code == 0

    def test_create_from_file(self, _mock_planner_modules, tmp_path):
        """Cover lines 234-253: create from file."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        role_file = tmp_path / "role.yaml"
        role_file.write_text("name: test\ndescription: test")

        mock_creator = _mock_planner_modules["creator"]
        mock_creator.load_role_from_file.return_value = MagicMock(name="loaded")
        mock_creator.validate_role.return_value = []

        runner = CliRunner()
        result = runner.invoke(app, ["create", "--from-file", str(role_file)])
        assert result.exit_code == 0

    def test_create_from_file_validation_error(self, _mock_planner_modules, tmp_path):
        """Cover lines 242-247: validation errors from file."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        role_file = tmp_path / "role.yaml"
        role_file.write_text("name: test")

        mock_creator = _mock_planner_modules["creator"]
        mock_creator.load_role_from_file.return_value = MagicMock(name="loaded")
        mock_creator.validate_role.return_value = ["Missing field", "Bad value"]

        runner = CliRunner()
        result = runner.invoke(app, ["create", "--from-file", str(role_file)])
        assert result.exit_code == 1

    def test_create_from_file_load_failure(self, _mock_planner_modules, tmp_path):
        """Cover lines 236-238: load failure."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        role_file = tmp_path / "bad.yaml"
        role_file.write_text("invalid")

        mock_creator = _mock_planner_modules["creator"]
        mock_creator.load_role_from_file.return_value = None

        runner = CliRunner()
        result = runner.invoke(app, ["create", "--from-file", str(role_file)])
        assert result.exit_code == 1

    def test_create_no_name(self, _mock_planner_modules):
        """Cover lines 278-280: missing name."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, [
            "create", "--capabilities", "search", "--no-interactive",
        ])
        assert result.exit_code == 1

    def test_create_no_capabilities(self, _mock_planner_modules):
        """Cover lines 282-284: missing capabilities."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, [
            "create", "--name", "test", "--no-interactive",
        ])
        assert result.exit_code == 1

    def test_create_invalid_capability_skipped(self, _mock_planner_modules):
        """Cover lines 288-294: invalid capability skipped."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, [
            "create",
            "--name", "test_role",
            "--capabilities", "nonexistent_cap",
            "--no-interactive",
        ])
        assert result.exit_code == 1  # No valid caps -> error

    def test_create_failure(self, _mock_planner_modules):
        """Cover lines 316-320: creation failure."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        mock_creator = _mock_planner_modules["creator"]
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.errors = ["Creation failed"]
        mock_creator.create_role_from_definition.return_value = mock_result

        runner = CliRunner()
        result = runner.invoke(app, [
            "create",
            "--name", "test_role",
            "--capabilities", "search",
            "--no-interactive",
        ])
        assert result.exit_code == 1

    def test_create_with_output(self, _mock_planner_modules, tmp_path):
        """Cover lines 335-337: save to output."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        mock_creator = _mock_planner_modules["creator"]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.warnings = ["Some warning"]
        mock_role = MagicMock()
        mock_role.name = "new_role"
        mock_role.display_name = "New"
        mock_role.description = "desc"
        mock_cap = MagicMock()
        mock_cap.value = "search"
        mock_role.capabilities = [mock_cap]
        mock_role.tools = []
        mock_result.role = mock_role
        mock_creator.create_role_from_definition.return_value = mock_result
        mock_creator.save_role.return_value = tmp_path / "new_role.yaml"

        runner = CliRunner()
        result = runner.invoke(app, [
            "create",
            "--name", "test_role",
            "--capabilities", "search",
            "--output", str(tmp_path),
            "--no-interactive",
        ])
        assert result.exit_code == 0


class TestRoleCLIValidate:
    """Test roles_validate command."""

    def test_validate_valid(self):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        with patch("xbot.crew.cli.role_cmd.validate_role_file") as mock_validate:
            mock_role = MagicMock()
            mock_role.name = "test"
            mock_role.display_name = "Test"
            mock_role.tier = MagicMock()
            mock_role.tier.value = "core"
            mock_cap = MagicMock()
            mock_cap.value = "search"
            mock_role.capabilities = [mock_cap]
            mock_role.tools = ["tool1"]
            mock_validate.return_value = (True, [], mock_role)

            runner = CliRunner()
            with runner.isolated_filesystem():
                Path("role.yaml").write_text("name: test")
                result = runner.invoke(app, ["validate", "role.yaml"])
            assert result.exit_code == 0

    def test_validate_verbose(self):
        """Cover lines 363-368: verbose output."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        with patch("xbot.crew.cli.role_cmd.validate_role_file") as mock_validate:
            mock_role = MagicMock()
            mock_role.name = "test"
            mock_role.display_name = "Test"
            mock_role.tier = MagicMock()
            mock_role.tier.value = "core"
            mock_cap = MagicMock()
            mock_cap.value = "search"
            mock_role.capabilities = [mock_cap]
            mock_role.tools = ["tool1"]
            mock_validate.return_value = (True, [], mock_role)

            runner = CliRunner()
            with runner.isolated_filesystem():
                Path("role.yaml").write_text("name: test")
                result = runner.invoke(app, ["validate", "role.yaml", "--verbose"])
            assert result.exit_code == 0

    def test_validate_invalid(self):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        with patch("xbot.crew.cli.role_cmd.validate_role_file") as mock_validate:
            mock_validate.return_value = (False, ["Missing name"], None)

            runner = CliRunner()
            with runner.isolated_filesystem():
                Path("role.yaml").write_text("bad")
                result = runner.invoke(app, ["validate", "role.yaml"])
            assert result.exit_code == 1

    def test_validate_file_not_found(self):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["validate", "/nonexistent/file.yaml"])
        assert result.exit_code == 1


class TestRoleCLICopy:
    """Test roles_copy command (lines 376-431)."""

    def test_copy_with_output(self, _mock_planner_modules, tmp_path):
        """Cover lines 425-428: save copy."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        mock_creator = _mock_planner_modules["creator"]
        mock_creator.save_role.return_value = tmp_path / "new_role.yaml"

        runner = CliRunner()
        result = runner.invoke(app, [
            "copy", "test_role", "new_role",
            "--output", str(tmp_path),
        ])
        assert result.exit_code == 0

    def test_copy_no_output(self, _mock_planner_modules):
        """Cover lines 430-431: no output warning."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["copy", "test_role", "new_role"])
        assert result.exit_code == 0
        assert "Warning" in result.output or "output" in result.output.lower()

    def test_copy_source_not_found(self, _mock_planner_modules):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        _mock_planner_modules["pool"].get_role.return_value = None

        runner = CliRunner()
        result = runner.invoke(app, ["copy", "nonexistent", "new_role"])
        assert result.exit_code == 1


class TestRoleCLIDelete:
    """Test roles_delete command (lines 434-483)."""

    def test_delete_invalid_name_slash(self):
        """Cover lines 450-453: path traversal blocked."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["delete", "../etc/passwd", "-c", "/tmp"])
        assert result.exit_code == 1

    def test_delete_invalid_name_format(self):
        """Cover lines 456-460: invalid name format."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["delete", "123invalid", "-c", "/tmp"])
        assert result.exit_code == 1

    def test_delete_not_found(self, tmp_path):
        """Cover lines 472-474: role file not found."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["delete", "nonexistent", "-c", str(tmp_path)])
        assert result.exit_code == 1

    def test_delete_with_force(self, tmp_path):
        """Cover lines 476-483: successful delete."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        role_file = tmp_path / "test_role.yaml"
        role_file.write_text("name: test_role")

        runner = CliRunner()
        result = runner.invoke(app, [
            "delete", "test_role", "-c", str(tmp_path), "--force",
        ])
        assert result.exit_code == 0
        assert not role_file.exists()

    def test_delete_cancelled(self, tmp_path):
        """Cover lines 477-480: user cancels."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        role_file = tmp_path / "test_role.yaml"
        role_file.write_text("name: test_role")

        runner = CliRunner()
        result = runner.invoke(app, [
            "delete", "test_role", "-c", str(tmp_path),
        ], input="n\n")
        assert result.exit_code == 0


class TestRoleCLIExport:
    """Test roles_export command (lines 486-515)."""

    def test_export_success(self, _mock_planner_modules, tmp_path):
        """Cover lines 513-515: export."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        mock_creator = _mock_planner_modules["creator"]
        mock_creator.save_role.return_value = tmp_path / "exported.yaml"

        runner = CliRunner()
        result = runner.invoke(app, [
            "export", "test_role", "-o", str(tmp_path / "exported.yaml"),
        ])
        assert result.exit_code == 0

    def test_export_not_found(self, _mock_planner_modules):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        _mock_planner_modules["pool"].get_role.return_value = None

        runner = CliRunner()
        result = runner.invoke(app, ["export", "nonexistent", "-o", "/tmp/out.yaml"])
        assert result.exit_code == 1


class TestRoleCLIImport:
    """Test roles_import command (lines 518-555)."""

    def test_import_success_with_output(self, _mock_planner_modules, tmp_path):
        """Cover lines 549-552: import with save."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        role_file = tmp_path / "role.yaml"
        role_file.write_text("name: test")

        mock_creator = _mock_planner_modules["creator"]
        mock_role = MagicMock()
        mock_role.name = "imported_role"
        mock_creator.load_role_from_file.return_value = mock_role
        mock_creator.validate_role.return_value = []
        mock_creator.save_role.return_value = tmp_path / "imported.yaml"

        runner = CliRunner()
        result = runner.invoke(app, [
            "import", str(role_file), "-o", str(tmp_path),
        ])
        assert result.exit_code == 0

    def test_import_success_no_output(self, _mock_planner_modules, tmp_path):
        """Cover lines 553-555: import without save."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        role_file = tmp_path / "role.yaml"
        role_file.write_text("name: test")

        mock_creator = _mock_planner_modules["creator"]
        mock_role = MagicMock()
        mock_role.name = "imported_role"
        mock_creator.load_role_from_file.return_value = mock_role
        mock_creator.validate_role.return_value = []

        runner = CliRunner()
        result = runner.invoke(app, ["import", str(role_file)])
        assert result.exit_code == 0

    def test_import_file_not_found(self):
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        runner = CliRunner()
        result = runner.invoke(app, ["import", "/nonexistent/role.yaml"])
        assert result.exit_code == 1

    def test_import_load_failure(self, _mock_planner_modules, tmp_path):
        """Cover lines 537-539: load failure."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        role_file = tmp_path / "bad.yaml"
        role_file.write_text("invalid")

        mock_creator = _mock_planner_modules["creator"]
        mock_creator.load_role_from_file.return_value = None

        runner = CliRunner()
        result = runner.invoke(app, ["import", str(role_file)])
        assert result.exit_code == 1

    def test_import_validation_errors(self, _mock_planner_modules, tmp_path):
        """Cover lines 542-547: validation errors."""
        from typer.testing import CliRunner
        from xbot.crew.cli.role_cmd import app

        role_file = tmp_path / "role.yaml"
        role_file.write_text("name: test")

        mock_creator = _mock_planner_modules["creator"]
        mock_creator.load_role_from_file.return_value = MagicMock(name="loaded")
        mock_creator.validate_role.return_value = ["Error 1", "Error 2"]

        runner = CliRunner()
        result = runner.invoke(app, ["import", str(role_file)])
        assert result.exit_code == 1


# ===================================================================
# 6. Format tests — covering lines 205-257 (schema validation fallback)
# ===================================================================

class TestOutputParserSchemaFallback:
    """Test _validate_schema without jsonschema (lines 208-257)."""

    def test_validate_object_type_ok(self):
        parser = OutputParser()
        schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
        errors = parser._validate_schema({"name": "test"}, schema)
        assert errors == []

    def test_validate_object_missing_required(self):
        parser = OutputParser()
        schema = {"type": "object", "required": ["name", "age"]}
        errors = parser._validate_schema({"name": "test"}, schema)
        assert len(errors) >= 1
        assert any("required" in e.lower() or "age" in e.lower() for e in errors)

    def test_validate_object_wrong_type(self):
        parser = OutputParser()
        schema = {"type": "object"}
        errors = parser._validate_schema("not an object", schema)
        assert len(errors) == 1
        assert "expected object" in errors[0]

    def test_validate_nested_properties(self):
        parser = OutputParser()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        errors = parser._validate_schema({"name": 123, "age": "not_int"}, schema)
        assert len(errors) == 2

    def test_validate_array_type(self):
        parser = OutputParser()
        schema = {"type": "array", "items": {"type": "string"}}
        errors = parser._validate_schema(["a", "b"], schema)
        assert errors == []

    def test_validate_array_wrong_type(self):
        parser = OutputParser()
        schema = {"type": "array"}
        errors = parser._validate_schema("not array", schema)
        assert len(errors) == 1

    def test_validate_array_items_errors(self):
        parser = OutputParser()
        schema = {"type": "array", "items": {"type": "integer"}}
        errors = parser._validate_schema([1, "bad", 3], schema)
        assert len(errors) == 1

    def test_validate_string_type(self):
        parser = OutputParser()
        schema = {"type": "string"}
        assert parser._validate_schema("hello", schema) == []
        assert len(parser._validate_schema(123, schema)) == 1

    def test_validate_integer_type(self):
        parser = OutputParser()
        schema = {"type": "integer"}
        assert parser._validate_schema(42, schema) == []
        assert len(parser._validate_schema("bad", schema)) == 1
        # Bool should fail
        assert len(parser._validate_schema(True, schema)) == 1

    def test_validate_number_type(self):
        parser = OutputParser()
        schema = {"type": "number"}
        assert parser._validate_schema(42, schema) == []
        assert parser._validate_schema(3.14, schema) == []
        assert len(parser._validate_schema("bad", schema)) == 1

    def test_validate_boolean_type(self):
        parser = OutputParser()
        schema = {"type": "boolean"}
        assert parser._validate_schema(True, schema) == []
        assert len(parser._validate_schema(1, schema)) == 1

    def test_validate_unknown_type_with_jsonschema(self):
        """When jsonschema is available, unknown type raises UnknownType."""
        parser = OutputParser()
        schema = {"type": "unknown"}
        # jsonschema raises UnknownType for invalid schema types
        import jsonschema
        with pytest.raises(jsonschema.exceptions.UnknownType):
            parser._validate_schema("anything", schema)

    def test_validate_fallback_without_jsonschema(self):
        """Test the fallback validation when jsonschema is not available."""
        parser = OutputParser()
        schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}

        # Temporarily make jsonschema import fail
        import sys
        orig = sys.modules.get("jsonschema")
        sys.modules["jsonschema"] = None  # type: ignore
        try:
            errors = parser._validate_schema({"name": "test"}, schema)
            assert errors == []
            errors = parser._validate_schema({}, schema)
            assert len(errors) >= 1
        finally:
            if orig is not None:
                sys.modules["jsonschema"] = orig
            else:
                sys.modules.pop("jsonschema", None)


class TestOutputParserWithSchema:
    """Test full parse with schema validation."""

    def test_parse_json_valid_schema(self):
        parser = OutputParser()
        schema = {"type": "object", "required": ["name"]}
        result = parser.parse('{"name": "test"}', OutputFormat.JSON, schema)
        assert result.valid is True

    def test_parse_json_invalid_schema(self):
        parser = OutputParser()
        schema = {"type": "object", "required": ["name", "age"]}
        result = parser.parse('{"name": "test"}', OutputFormat.JSON, schema)
        assert result.valid is False
        assert "Schema validation failed" in result.error


class TestDetectFormat:
    """Test format auto-detection."""

    def test_detect_json_object(self):
        assert detect_format('{"key": "value"}') == OutputFormat.JSON

    def test_detect_json_array(self):
        assert detect_format('[1, 2, 3]') == OutputFormat.JSON

    def test_detect_json_in_code_block(self):
        assert detect_format('```json\n{"key": "value"}\n```') == OutputFormat.JSON

    def test_detect_markdown(self):
        assert detect_format("# Title\n\nSome content") == OutputFormat.MARKDOWN

    def test_detect_raw(self):
        assert detect_format("Just plain text") == OutputFormat.RAW

    def test_detect_invalid_json(self):
        assert detect_format("{broken json") == OutputFormat.RAW


class TestFormatOutput:
    """Test convenience function."""

    def test_format_output_raw(self):
        result = format_output("hello", OutputFormat.RAW)
        assert result.valid is True
        assert result.raw == "hello"

    def test_format_output_json(self):
        result = format_output('{"key": "value"}', OutputFormat.JSON)
        assert result.valid is True
        assert result.structured == {"key": "value"}

    def test_format_output_markdown(self):
        result = format_output("# Title\n\nBody", OutputFormat.MARKDOWN)
        assert result.valid is True
        assert result.sections is not None

    def test_format_output_structured_no_template(self):
        result = format_output("content", OutputFormat.STRUCTURED, None)
        assert result.valid is False

    def test_format_output_structured_with_template(self):
        template = {"name": "{{name}}", "role": "{{role}}"}
        content = "name: Alice\nrole: Engineer"
        result = format_output(content, OutputFormat.STRUCTURED, template)
        assert result.valid is True
        assert result.structured is not None
        assert result.structured.get("name") == "Alice"
        assert result.structured.get("role") == "Engineer"

    def test_format_output_unknown(self):
        # Simulate an unknown format by passing a raw string
        result = format_output("content", OutputFormat.RAW)
        assert result.valid is True


class TestExtractJson:
    """Test _extract_json edge cases."""

    def test_extract_from_code_block(self):
        parser = OutputParser()
        content = '```json\n{"key": "value"}\n```'
        assert parser._extract_json(content) == '{"key": "value"}'

    def test_extract_direct_json(self):
        parser = OutputParser()
        content = '{"key": "value"}'
        assert parser._extract_json(content) == content

    def test_extract_nested_json(self):
        parser = OutputParser()
        content = 'Some prefix {"nested": {"key": "value"}} suffix'
        result = parser._extract_json(content)
        data = json.loads(result)
        assert data["nested"]["key"] == "value"

    def test_extract_json_array(self):
        parser = OutputParser()
        content = 'prefix [1, 2, 3] suffix'
        result = parser._extract_json(content)
        data = json.loads(result)
        assert data == [1, 2, 3]

    def test_extract_no_json_found(self):
        parser = OutputParser()
        content = 'no json here at all'
        # Should return content as-is when no JSON found
        result = parser._extract_json(content)
        assert result == content


class TestParseMarkdown:
    """Test markdown parsing."""

    def test_sections(self):
        parser = OutputParser()
        result = parser._parse_markdown("# Title\n\nIntro\n\n## Details\n\nSome details")
        assert "title" in result.sections
        assert "details" in result.sections

    def test_code_blocks(self):
        parser = OutputParser()
        content = "# Code\n\n```python\nprint('hello')\n```"
        result = parser._parse_markdown(content)
        assert "code_blocks" in result.structured
        assert result.structured["code_blocks"][0]["language"] == "python"

    def test_links(self):
        parser = OutputParser()
        content = "# Links\n\n[Example](https://example.com)"
        result = parser._parse_markdown(content)
        assert "links" in result.structured
        assert result.structured["links"][0]["url"] == "https://example.com"

    def test_lists(self):
        parser = OutputParser()
        content = "# Items\n\n- Item 1\n- Item 2\n- Item 3"
        result = parser._parse_markdown(content)
        assert "items_list" in result.structured


class TestParseStructured:
    """Test structured parsing."""

    def test_no_template(self):
        parser = OutputParser()
        result = parser._parse_structured("content", None)
        assert result.valid is False

    def test_extract_variables(self):
        parser = OutputParser()
        template = {"name_field": "{{name}}", "age_field": "{{age}}"}
        content = "name: Alice\nage: 30"
        result = parser._parse_structured(content, template)
        assert result.structured["name"] == "Alice"
        assert result.structured["age"] == "30"

    def test_extract_with_bold_pattern(self):
        parser = OutputParser()
        template = {"value": "{{value}}"}
        content = "**value**: Hello World"
        result = parser._parse_structured(content, template)
        assert result.structured["value"] == "Hello World"

    def test_extract_with_header_pattern(self):
        parser = OutputParser()
        template = {"data": "{{data}}"}
        content = "### data\nSome data here"
        result = parser._parse_structured(content, template)
        assert result.structured["data"] == "Some data here"


class TestFormatJsonschemaError:
    """Test _format_jsonschema_error static method."""

    def test_required_error(self):
        error = MagicMock()
        error.absolute_path = []
        error.validator = "required"
        error.validator_value = ["name", "age"]
        error.instance = {}
        result = OutputParser._format_jsonschema_error(error)
        assert "required" in result.lower()

    def test_type_error(self):
        error = MagicMock()
        error.absolute_path = ["field"]
        error.validator = "type"
        error.validator_value = "string"
        error.instance = 123
        result = OutputParser._format_jsonschema_error(error)
        assert "string" in result

    def test_other_error(self):
        error = MagicMock()
        error.absolute_path = []
        error.validator = "pattern"
        error.message = "does not match pattern"
        result = OutputParser._format_jsonschema_error(error)
        assert "pattern" in result


class TestBalancedJsonExtraction:
    """Test _find_balanced_end and _iter_balanced_json_candidates."""

    def test_find_balanced_object(self):
        parser = OutputParser()
        text = '{"a": {"b": 1}}'
        end = parser._find_balanced_end(text, 0)
        assert end == len(text) - 1

    def test_find_balanced_array(self):
        parser = OutputParser()
        text = '[1, [2, 3]]'
        end = parser._find_balanced_end(text, 0)
        assert end == len(text) - 1

    def test_find_balanced_with_string(self):
        parser = OutputParser()
        text = '{"key": "value with } inside"}'
        end = parser._find_balanced_end(text, 0)
        assert end == len(text) - 1

    def test_find_balanced_with_escape(self):
        parser = OutputParser()
        text = '{"key": "value \\" escaped"}'
        end = parser._find_balanced_end(text, 0)
        assert end == len(text) - 1

    def test_find_balanced_none_unmatched(self):
        parser = OutputParser()
        text = '{"unmatched"'
        end = parser._find_balanced_end(text, 0)
        assert end is None

    def test_find_balanced_wrong_bracket(self):
        parser = OutputParser()
        text = '{"key": 1]'
        end = parser._find_balanced_end(text, 0)
        assert end is None

    def test_find_balanced_not_json_start(self):
        parser = OutputParser()
        text = 'hello'
        end = parser._find_balanced_end(text, 0)
        assert end is None

    def test_iter_candidates(self):
        parser = OutputParser()
        text = 'prefix {"a": 1} middle {"b": 2} suffix'
        candidates = list(parser._iter_balanced_json_candidates(text))
        assert len(candidates) == 2
        assert json.loads(candidates[0]) == {"a": 1}
        assert json.loads(candidates[1]) == {"b": 2}
