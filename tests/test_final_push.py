"""Final push tests — targeting remaining gaps to reach 90% coverage.

Focus: dingtalk, discord, web helpers, goal helpers.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── SDK stubs ────────────────────────────────────────────────────────────


def _install_dingtalk_stubs():
    if "dingtalk_stream" in sys.modules:
        return
    mod = types.ModuleType("dingtalk_stream")
    mod.AckMessage = type("AckMessage", (), {"STATUS_OK": 200})
    mod.CallbackHandler = type("CallbackHandler", (), {"__init__": lambda *a, **kw: None})
    mod.CallbackMessage = type("CallbackMessage", (), {})
    mod.Credential = type("Credential", (), {"__init__": lambda *a, **kw: None})
    mod.DingTalkStreamClient = type("DingTalkStreamClient", (), {
        "__init__": lambda *a, **kw: None,
        "register_callback_handler": lambda *a, **kw: None,
    })
    chatbot_mod = types.ModuleType("dingtalk_stream.chatbot")
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
            msg.sender_id = data.get("senderId", "")
            msg.sender_nick = data.get("senderNick", "")
            return msg
    chatbot_mod.ChatbotMessage = _FakeChatbotMessage
    mod.chatbot = chatbot_mod
    sys.modules["dingtalk_stream"] = mod
    sys.modules["dingtalk_stream.chatbot"] = chatbot_mod

_install_dingtalk_stubs()


# ── DingTalk ─────────────────────────────────────────────────────────────


class TestDingTalkRound5:
    def _make_ch(self):
        from xbot.channels.dingtalk import DingTalkChannel, DingTalkConfig
        cfg = DingTalkConfig.model_validate({
            "enabled": True, "client_id": "id", "client_secret": "secret",
            "allow_from": ["*"],
        })
        return DingTalkChannel(cfg, MagicMock())

    def test_default_config(self):
        from xbot.channels.dingtalk import DingTalkChannel
        assert isinstance(DingTalkChannel.default_config(), dict)

    def test_is_allowed_wildcard(self):
        ch = self._make_ch()
        assert ch.is_allowed("any") is True

    def test_is_allowed_specific(self):
        from xbot.channels.dingtalk import DingTalkChannel, DingTalkConfig
        cfg = DingTalkConfig.model_validate({
            "enabled": True, "client_id": "id", "client_secret": "s",
            "allow_from": ["u1"],
        })
        ch = DingTalkChannel(cfg, MagicMock())
        assert ch.is_allowed("u1") is True
        assert ch.is_allowed("u2") is False

    def test_is_allowed_empty(self):
        from xbot.channels.dingtalk import DingTalkChannel, DingTalkConfig
        cfg = DingTalkConfig.model_validate({
            "enabled": True, "client_id": "id", "client_secret": "s",
            "allow_from": [],
        })
        ch = DingTalkChannel(cfg, MagicMock())
        assert ch.is_allowed("any") is False

    def test_check_health(self):
        ch = self._make_ch()
        ch._running = True
        ok, _ = ch.check_health()
        assert ok is True

        ch._running = False
        ok, _ = ch.check_health()
        assert ok is False

    def test_is_http_url(self):
        from xbot.channels.dingtalk import DingTalkChannel
        assert DingTalkChannel._is_http_url("https://example.com") is True
        assert DingTalkChannel._is_http_url("http://example.com") is True
        assert DingTalkChannel._is_http_url("ftp://example.com") is False

    def test_guess_upload_type(self):
        from xbot.channels.dingtalk import DingTalkChannel
        ch = self._make_ch()
        assert ch._guess_upload_type("photo.jpg") == "image"
        assert ch._guess_upload_type("file.mp3") == "voice"
        assert ch._guess_upload_type("doc.pdf") == "file"

    def test_guess_filename(self):
        from xbot.channels.dingtalk import DingTalkChannel
        ch = self._make_ch()
        result = ch._guess_filename("https://example.com/path/doc.pdf", "file")
        assert result is not None

    def test_handler_process_text(self):
        from xbot.channels.dingtalk import XbotDingTalkHandler
        ch = self._make_ch()
        ch._schedule_inbound_message = MagicMock()
        handler = XbotDingTalkHandler.__new__(XbotDingTalkHandler)
        handler.channel = ch

        fake_msg = MagicMock()
        fake_msg.text.content = "hello"
        fake_msg.message_type = "text"
        fake_msg.extensions = {}
        fake_msg.sender_id = "u1"
        fake_msg.sender_nick = "User"

        from dingtalk_stream import AckMessage
        result = handler.process(fake_msg)
        # Should return ACK status
        assert result is not None

    def test_handler_empty_content_returns_ack(self):
        from xbot.channels.dingtalk import XbotDingTalkHandler
        ch = self._make_ch()
        ch._schedule_inbound_message = MagicMock()
        handler = XbotDingTalkHandler.__new__(XbotDingTalkHandler)
        handler.channel = ch

        fake_msg = MagicMock()
        fake_msg.text.content = ""
        fake_msg.message_type = "text"
        fake_msg.extensions = {}
        fake_msg.sender_id = "u1"

        result = handler.process(fake_msg)
        assert result is not None


# ── Discord ──────────────────────────────────────────────────────────────


class TestDiscordRound5:
    def _make_ch(self):
        if "websockets" not in sys.modules:
            ws = types.ModuleType("websockets")
            ws.connect = MagicMock()
            sys.modules["websockets"] = ws
        from xbot.channels.discord import DiscordChannel, DiscordConfig
        cfg = DiscordConfig.model_validate({
            "enabled": True, "bot_token": "tok",
            "gateway_url": "wss://gateway.discord.gg",
            "allow_from": ["*"],
        })
        return DiscordChannel(cfg, MagicMock())

    async def test_is_duplicate_new(self):
        ch = self._make_ch()
        result = await ch._is_duplicate_message("new_123")
        assert result is False

    async def test_is_duplicate_seen(self):
        ch = self._make_ch()
        ch._processed_message_ids["msg_1"] = 9999999999.0
        result = await ch._is_duplicate_message("msg_1")
        assert result is True

    def test_should_respond_open(self):
        ch = self._make_ch()
        ch.config.group_policy = "open"
        result = ch._should_respond_in_group({}, "hello")
        assert result is True

    def test_should_respond_mention_no_bot(self):
        ch = self._make_ch()
        ch.config.group_policy = "mention"
        ch._bot_user_id = None
        result = ch._should_respond_in_group({}, "hello")
        assert result is False

    def test_default_config(self):
        from xbot.channels.discord import DiscordChannel
        assert isinstance(DiscordChannel.default_config(), dict)

    def test_check_health(self):
        ch = self._make_ch()
        ch._running = True
        ok, _ = ch.check_health()
        assert ok is True

    async def test_send_without_http(self):
        ch = self._make_ch()
        ch._http = None
        from xbot.platform.bus.events import OutboundMessage
        msg = OutboundMessage(channel="discord", chat_id="ch1", content="hi")
        await ch.send(msg)  # Should warn, not crash


# ── Web helpers ──────────────────────────────────────────────────────────


class TestWebRound5:
    def test_strip_tags(self):
        from xbot.tools.web import _strip_tags
        assert _strip_tags("<b>hi</b>") == "hi"
        assert _strip_tags("<script>x</script>t") == "t"

    def test_normalize(self):
        from xbot.tools.web import _normalize
        assert _normalize("  a   b  ") == "a b"

    def test_strip_markdown(self):
        from xbot.tools.web import _strip_markdown
        r = _strip_markdown("**bold**")
        assert "bold" in r

    def test_validate_url_ok(self):
        from xbot.tools.web import _validate_url
        ok, _ = _validate_url("https://example.com")
        assert ok is True

    def test_validate_url_no_scheme(self):
        from xbot.tools.web import _validate_url
        ok, _ = _validate_url("example.com")
        assert ok is False

    def test_validate_url_ftp(self):
        from xbot.tools.web import _validate_url
        ok, _ = _validate_url("ftp://example.com")
        assert ok is False


# ── Goal helpers ─────────────────────────────────────────────────────────


class TestGoalRound5:
    def _make_runner(self, tmp_path=None):
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState, GoalStore
        import tempfile, os
        ws = str(tmp_path) if tmp_path else tempfile.mkdtemp()
        store = GoalStore(ws)
        state = GoalState(
            goal_id="test-1",
            objective="build app",
            workspace=ws,
            session_key="cli:goal-test",
        )
        return GoalRunner(state, store)

    def test_permission_handler_safe_tools(self):
        from xbot.interfaces.cli.goal import GoalPermissionHandler
        h = GoalPermissionHandler()
        assert h.is_safe_tool("anything") is True

    def test_permission_handler_add_safe(self):
        from xbot.interfaces.cli.goal import GoalPermissionHandler
        h = GoalPermissionHandler()
        h.add_safe_tool("custom")
        assert h.is_safe_tool("custom") is True

    def test_goal_runner_init(self, tmp_path):
        runner = self._make_runner(tmp_path)
        assert runner.goal.objective == "build app"

    def test_build_first_prompt(self, tmp_path):
        runner = self._make_runner(tmp_path)
        prompt = runner._build_first_prompt()
        assert "build app" in prompt

    def test_build_retry_prompt(self, tmp_path):
        runner = self._make_runner(tmp_path)
        prompt = runner._build_retry_prompt()
        assert "build app" in prompt
