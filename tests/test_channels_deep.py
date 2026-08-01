"""Deep tests for channel modules with low coverage.

Covers:
- Mochat: start, stop, send, socket client, subscribe, event processing,
          refresh, HTTP helpers, dedup, delay/flush, notify handlers, cursor
- Discord: gateway loop, heartbeat, send, reconnect, message handling
- DingTalk: access token, upload media, send, on_message, download file
- Feishu: send paths, card rendering edge cases, media upload/download,
          reply, tool hint, message handling
- WeCom: process_message, download_media, send, remember_chat_frame
- Feishu WS worker: lifecycle, normalize_message_event
- Slack: send retry, rate limit handling, reaction update
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.machinery
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub external SDK modules so channel imports succeed without heavy deps
# ---------------------------------------------------------------------------
def _install_sdk_stubs():
    stubs = {
        "socketio": types.ModuleType("socketio"),
        "socketio.AsyncClient": type("AsyncClient", (), {}),
        "msgpack": types.ModuleType("msgpack"),
        "websockets": types.ModuleType("websockets"),
        "websockets.connect": MagicMock(),
        "dingtalk_stream": types.ModuleType("dingtalk_stream"),
        "dingtalk_stream.chatbot": types.ModuleType("dingtalk_stream.chatbot"),
        "lark_oapi": types.ModuleType("lark_oapi"),
        "lark_oapi.api": types.ModuleType("lark_oapi.api"),
        "lark_oapi.api.im": types.ModuleType("lark_oapi.api.im"),
        "lark_oapi.api.im.v1": types.ModuleType("lark_oapi.api.im.v1"),
        "lark_oapi.ws": types.ModuleType("lark_oapi.ws"),
        "lark_oapi.ws.client": types.ModuleType("lark_oapi.ws.client"),
        "wecom_aibot_sdk": types.ModuleType("wecom_aibot_sdk"),
        "slack_sdk": types.ModuleType("slack_sdk"),
        "slack_sdk.socket_mode": types.ModuleType("slack_sdk.socket_mode"),
        "slack_sdk.socket_mode.request": types.ModuleType("slack_sdk.socket_mode.request"),
        "slack_sdk.socket_mode.response": types.ModuleType("slack_sdk.socket_mode.response"),
        "slack_sdk.socket_mode.websockets": types.ModuleType("slack_sdk.socket_mode.websockets"),
        "slack_sdk.web": types.ModuleType("slack_sdk.web"),
        "slack_sdk.web.async_client": types.ModuleType("slack_sdk.web.async_client"),
        "slackify_markdown": types.ModuleType("slackify_markdown"),
        "telegram": types.ModuleType("telegram"),
        "telegram.error": types.ModuleType("telegram.error"),
        "telegram.ext": types.ModuleType("telegram.ext"),
        "telegram.request": types.ModuleType("telegram.request"),
    }

    # socketio stubs
    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            self.events = {}
        def event(self, fn=None):
            if fn is not None:
                self.events.setdefault(fn.__name__, fn)
                return fn
            def wrapper(f):
                self.events.setdefault(f.__name__, f)
                return f
            return wrapper
        def on(self, event, handler=None):
            self.events[event] = handler
        async def connect(self, *a, **kw):
            pass
        async def call(self, *a, **kw):
            return {}
        async def disconnect(self, *a, **kw):
            pass
    stubs["socketio"].AsyncClient = _FakeAsyncClient
    stubs["socketio"].SUPPORTED = True

    # websockets stubs
    stubs["websockets"].connect = MagicMock()

    # dingtalk_stream stubs
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
            msg.sender_id = data.get("senderId", "")
            msg.sender_staff_id = data.get("senderStaffId", "")
            msg.sender_nick = data.get("senderNick", "")
            return msg

    stubs["dingtalk_stream"].AckMessage = _FakeAckMessage
    stubs["dingtalk_stream"].CallbackHandler = _FakeCallbackHandler
    stubs["dingtalk_stream"].CallbackMessage = _FakeCallbackMessage
    stubs["dingtalk_stream"].Credential = _FakeCredential
    stubs["dingtalk_stream"].DingTalkStreamClient = _FakeDingTalkStreamClient
    stubs["dingtalk_stream"].ChatbotMessage = _FakeChatbotMessage
    # Make dingtalk_stream.chatbot the SAME module as dingtalk_stream
    # so `from dingtalk_stream.chatbot import ChatbotMessage` works via sys.modules
    stubs["dingtalk_stream"].chatbot = stubs["dingtalk_stream"]
    stubs["dingtalk_stream.chatbot"] = stubs["dingtalk_stream"]

    # lark_oapi stubs
    class _FakeLogLevel:
        INFO = 1

    lark_mod = stubs["lark_oapi"]
    try:
        import importlib.machinery
        lark_mod.__spec__ = importlib.machinery.ModuleSpec("lark_oapi", None)
    except Exception:
        pass
    class _FakeBuilder:
        def __init__(self):
            pass
        def app_id(self, *a, **kw):
            return self
        def app_secret(self, *a, **kw):
            return self
        def log_level(self, *a, **kw):
            return self
        def build(self):
            return MagicMock()
        def register_p2_im_message_receive_v1(self, *a, **kw):
            return self
        def register_p2_im_message_reaction_created_v1(self, *a, **kw):
            return self
        def register_p2_im_message_message_read_v1(self, *a, **kw):
            return self
        def register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self, *a, **kw):
            return self

    stubs["lark_oapi"].LogLevel = _FakeLogLevel
    stubs["lark_oapi"].Client = MagicMock()
    stubs["lark_oapi"].Client.builder = staticmethod(lambda: _FakeBuilder())
    stubs["lark_oapi"].EventDispatcherHandler = MagicMock()
    stubs["lark_oapi"].EventDispatcherHandler.builder = staticmethod(lambda *a, **kw: _FakeBuilder())
    stubs["lark_oapi"].ws = stubs["lark_oapi.ws"]
    stubs["lark_oapi"].ws.Client = MagicMock()

    # lark_oapi.api.im.v1 stubs
    for name in ("CreateMessageRequest", "CreateMessageRequestBody",
                 "CreateImageRequest", "CreateImageRequestBody",
                 "CreateFileRequest", "CreateFileRequestBody",
                 "GetMessageResourceRequest", "GetMessageRequest",
                 "ReplyMessageRequest", "ReplyMessageRequestBody",
                 "CreateMessageReactionRequest", "CreateMessageReactionRequestBody",
                 "Emoji"):
        cls = MagicMock()
        cls.builder = staticmethod(lambda: MagicMock())
        setattr(stubs["lark_oapi.api.im.v1"], name, cls)

    # wecom stubs
    wecom_mod = stubs["wecom_aibot_sdk"]
    wecom_mod.__spec__ = importlib.machinery.ModuleSpec("wecom_aibot_sdk", None)
    wecom_mod.WSClient = MagicMock()
    wecom_mod.generate_req_id = MagicMock(return_value="req_123")

    # slack stubs
    stubs["slack_sdk.socket_mode.request"].SocketModeRequest = type("SocketModeRequest", (), {})
    stubs["slack_sdk.socket_mode.response"].SocketModeResponse = type("SocketModeResponse", (), {"__init__": lambda self, *a, **kw: None})
    stubs["slack_sdk.socket_mode.websockets"].SocketModeClient = MagicMock()
    stubs["slack_sdk.web.async_client"].AsyncWebClient = MagicMock()
    stubs["slackify_markdown"].slackify_markdown = lambda text: text

    # telegram stubs
    stubs["telegram"].BotCommand = type("BotCommand", (), {"__init__": lambda self, *a, **kw: None})
    stubs["telegram"].ReplyParameters = type("ReplyParameters", (), {"__init__": lambda self, *a, **kw: None})
    stubs["telegram"].Update = type("Update", (), {})
    stubs["telegram.error"].Conflict = Exception
    stubs["telegram.error"].TimedOut = Exception
    stubs["telegram.ext"].Application = MagicMock()
    stubs["telegram.ext"].CommandHandler = type("CommandHandler", (), {"__init__": lambda self, *a, **kw: None})
    stubs["telegram.ext"].ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
    stubs["telegram.ext"].MessageHandler = type("MessageHandler", (), {"__init__": lambda self, *a, **kw: None})
    stubs["telegram.ext"].filters = MagicMock()
    stubs["telegram.request"].HTTPXRequest = MagicMock()

    for mod_name, mod in stubs.items():
        if mod_name not in sys.modules:
            sys.modules[mod_name] = mod

    # Patch slack_sdk.socket_mode.response.SocketModeResponse to accept kwargs
    # (the real SDK class may not accept envelope_id=... in tests)
    try:
        import slack_sdk.socket_mode.response as _smr
        _OrigSMR = _smr.SocketModeResponse
        # Only patch if the class doesn't accept kwargs
        try:
            _OrigSMR(envelope_id="test")
        except TypeError:
            _smr.SocketModeResponse = type(
                "SocketModeResponse", (), {"__init__": lambda self, *a, **kw: None}
            )
            # Also patch in the already-imported slack module
            try:
                import xbot.channels.slack as _slack_mod
                _slack_mod.SocketModeResponse = _smr.SocketModeResponse
            except Exception:
                pass
    except ImportError:
        pass


_install_sdk_stubs()


# Now import the modules under test
from xbot.channels.mochat import (  # noqa: E402
    MochatChannel, MochatConfig, MochatMentionConfig, MochatGroupRule,
    MochatBufferedEntry, DelayState, MochatTarget,
    _safe_dict, _str_field, _make_synthetic_event, normalize_mochat_content,
    resolve_mochat_target, extract_mention_ids, resolve_was_mentioned,
    resolve_require_mention, build_buffered_body, parse_timestamp,
)
from xbot.channels.discord import (  # noqa: E402
    DiscordChannel, DiscordConfig, DISCORD_API_BASE,
)
from xbot.channels.dingtalk import (  # noqa: E402
    DingTalkChannel, DingTalkConfig, XbotDingTalkHandler,
)
from xbot.channels.feishu import FeishuChannel, FeishuConfig  # noqa: E402
from xbot.channels.wecom import WecomChannel, WecomConfig  # noqa: E402
from xbot.channels.feishu_ws_worker import (  # noqa: E402
    _getattr_chain, _normalize_message_event, run_feishu_ws_worker,
)
from xbot.channels.slack import SlackChannel, SlackConfig  # noqa: E402
from xbot.platform.bus.events import OutboundMessage  # noqa: E402


# ===================================================================
# Helpers
# ===================================================================

def _make_mochat_channel(**overrides):
    cfg_dict = {
        "enabled": True,
        "base_url": "https://mochat.test",
        "claw_token": "test-token",
        "agent_user_id": "agent1",
        "allow_from": ["*"],
    }
    cfg_dict.update(overrides)
    config = MochatConfig.model_validate(cfg_dict)
    bus = MagicMock()
    return MochatChannel(config, bus)


def _make_discord_channel(**overrides):
    cfg_dict = {
        "enabled": True,
        "token": "test-bot-token",
        "allow_from": ["*"],
        "group_policy": "mention",
    }
    cfg_dict.update(overrides)
    config = DiscordConfig.model_validate(cfg_dict)
    bus = MagicMock()
    return DiscordChannel(config, bus)


def _make_dingtalk_channel(**overrides):
    cfg_dict = {
        "enabled": True,
        "client_id": "test-id",
        "client_secret": "test-secret",
        "allow_from": ["*"],
    }
    cfg_dict.update(overrides)
    config = DingTalkConfig.model_validate(cfg_dict)
    bus = MagicMock()
    return DingTalkChannel(config, bus)


def _make_feishu_channel(**overrides):
    cfg_dict = {
        "enabled": True,
        "app_id": "cli_test",
        "app_secret": "secret",
        "allow_from": ["*"],
        "group_policy": "mention",
        "bot_open_id": "ou_bot123",
    }
    cfg_dict.update(overrides)
    config = FeishuConfig.model_validate(cfg_dict)
    bus = MagicMock()
    return FeishuChannel(config, bus)


def _make_wecom_channel(**overrides):
    cfg_dict = {
        "enabled": True,
        "bot_id": "test-bot-id",
        "secret": "test-secret",
        "allow_from": ["*"],
    }
    cfg_dict.update(overrides)
    config = WecomConfig.model_validate(cfg_dict)
    bus = MagicMock()
    return WecomChannel(config, bus)


def _make_slack_channel(**overrides):
    cfg_dict = {
        "enabled": True,
        "bot_token": "xoxb-test",
        "app_token": "xapp-test",
        "allow_from": [],
        "group_policy": "mention",
        "group_allow_from": [],
        "dm": {"enabled": True, "policy": "open", "allow_from": []},
    }
    cfg_dict.update(overrides)
    config = SlackConfig.model_validate(cfg_dict)
    bus = MagicMock()
    ch = SlackChannel(config, bus)
    return ch


# ===================================================================
# 1. Mochat — Pure helper functions
# ===================================================================

class TestMochatPureHelpers:
    def test_safe_dict_with_dict(self):
        assert _safe_dict({"a": 1}) == {"a": 1}

    def test_safe_dict_with_non_dict(self):
        assert _safe_dict("hello") == {}
        assert _safe_dict(None) == {}
        assert _safe_dict(42) == {}

    def test_str_field_first_match(self):
        src = {"a": "  hello  ", "b": "world"}
        assert _str_field(src, "a") == "hello"

    def test_str_field_fallback(self):
        src = {"a": "", "b": "  world  "}
        assert _str_field(src, "a", "b") == "world"

    def test_str_field_all_empty(self):
        src = {"a": "", "b": None}
        assert _str_field(src, "a", "b") == ""

    def test_str_field_missing_keys(self):
        assert _str_field({}, "a", "b") == ""

    def test_make_synthetic_event(self):
        evt = _make_synthetic_event("msg1", "user1", "hello", {}, "g1", "c1")
        assert evt["type"] == "message.add"
        assert evt["payload"]["messageId"] == "msg1"
        assert evt["payload"]["author"] == "user1"
        assert evt["payload"]["content"] == "hello"

    def test_make_synthetic_event_with_author_info(self):
        evt = _make_synthetic_event("msg1", "user1", "hello", {}, "g1", "c1", author_info={"nickname": "Alice"})
        assert evt["payload"]["authorInfo"]["nickname"] == "Alice"

    def test_normalize_mochat_content_str(self):
        assert normalize_mochat_content("  hello  ") == "hello"

    def test_normalize_mochat_content_none(self):
        assert normalize_mochat_content(None) == ""

    def test_normalize_mochat_content_dict(self):
        result = normalize_mochat_content({"text": "hello"})
        assert "text" in result
        assert "hello" in result

    def test_resolve_mochat_target_empty(self):
        t = resolve_mochat_target("")
        assert t.id == ""
        assert t.is_panel is False

    def test_resolve_mochat_target_session(self):
        t = resolve_mochat_target("session_abc")
        assert t.id == "session_abc"
        assert t.is_panel is False

    def test_resolve_mochat_target_panel(self):
        t = resolve_mochat_target("panel123")
        assert t.id == "panel123"
        assert t.is_panel is True

    def test_resolve_mochat_target_with_prefix(self):
        t = resolve_mochat_target("group:panel_abc")
        assert t.id == "panel_abc"
        assert t.is_panel is True

    def test_resolve_mochat_target_mochat_prefix(self):
        t = resolve_mochat_target("mochat:session_x")
        assert t.id == "session_x"

    def test_extract_mention_ids_empty(self):
        assert extract_mention_ids(None) == []
        assert extract_mention_ids("not a list") == []

    def test_extract_mention_ids_strings(self):
        assert extract_mention_ids(["user1", "  user2  "]) == ["user1", "user2"]

    def test_extract_mention_ids_dicts(self):
        assert extract_mention_ids([{"id": "u1"}, {"userId": "u2"}, {"_id": "u3"}]) == ["u1", "u2", "u3"]

    def test_extract_mention_ids_mixed(self):
        assert extract_mention_ids(["u1", {"id": "u2"}]) == ["u1", "u2"]

    def test_resolve_was_mentioned_meta_flag(self):
        payload = {"meta": {"mentioned": True}}
        assert resolve_was_mentioned(payload, "agent1") is True

    def test_resolve_was_mentioned_meta_wasMentioned(self):
        payload = {"meta": {"wasMentioned": True}}
        assert resolve_was_mentioned(payload, "agent1") is True

    def test_resolve_was_mentioned_mention_ids(self):
        payload = {"meta": {"mentions": ["agent1"]}}
        assert resolve_was_mentioned(payload, "agent1") is True

    def test_resolve_was_mentioned_content_fallback(self):
        payload = {"meta": {}, "content": "hi <@agent1>"}
        assert resolve_was_mentioned(payload, "agent1") is True

    def test_resolve_was_mentioned_at_name(self):
        payload = {"meta": {}, "content": "hi @agent1"}
        assert resolve_was_mentioned(payload, "agent1") is True

    def test_resolve_was_mentioned_no_agent(self):
        payload = {"meta": {}}
        assert resolve_was_mentioned(payload, "") is False

    def test_build_buffered_body_empty(self):
        assert build_buffered_body([], False) == ""

    def test_build_buffered_body_single(self):
        entry = MochatBufferedEntry(raw_body="hello", author="u1")
        assert build_buffered_body([entry], False) == "hello"

    def test_build_buffered_body_group(self):
        e1 = MochatBufferedEntry(raw_body="hi", author="u1", sender_name="Alice")
        e2 = MochatBufferedEntry(raw_body="there", author="u2", sender_name="Bob")
        result = build_buffered_body([e1, e2], True)
        assert "Alice: hi" in result
        assert "Bob: there" in result

    def test_build_buffered_body_dm_multiple(self):
        e1 = MochatBufferedEntry(raw_body="hi", author="u1")
        e2 = MochatBufferedEntry(raw_body="there", author="u2")
        result = build_buffered_body([e1, e2], False)
        assert "hi" in result
        assert "there" in result

    def test_parse_timestamp_valid(self):
        ts = parse_timestamp("2024-01-15T10:30:00Z")
        assert ts is not None
        assert isinstance(ts, int)
        assert ts > 0

    def test_parse_timestamp_invalid(self):
        assert parse_timestamp("not-a-date") is None
        assert parse_timestamp(None) is None
        assert parse_timestamp("") is None
        assert parse_timestamp(42) is None

    def test_resolve_require_mention_default(self):
        config = MochatConfig.model_validate({"mention": {"require_in_groups": True}})
        assert resolve_require_mention(config, "session_1", "group_1") is True

    def test_resolve_require_mention_per_group(self):
        config = MochatConfig.model_validate({
            "mention": {"require_in_groups": False},
            "groups": {"group_1": {"require_mention": True}},
        })
        assert resolve_require_mention(config, "session_1", "group_1") is True
        assert resolve_require_mention(config, "session_1", "group_2") is False

    def test_resolve_require_mention_wildcard(self):
        config = MochatConfig.model_validate({
            "groups": {"*": {"require_mention": True}},
        })
        assert resolve_require_mention(config, "session_1", "group_x") is True


# ===================================================================
# 2. Mochat — Channel methods
# ===================================================================

class TestMochatChannel:
    def test_init_from_dict(self):
        cfg = {"enabled": True, "claw_token": "tok", "allow_from": ["*"]}
        bus = MagicMock()
        ch = MochatChannel(cfg, bus)
        assert isinstance(ch.config, MochatConfig)
        assert ch.config.claw_token == "tok"

    @pytest.mark.asyncio
    async def test_send_empty_content(self):
        ch = _make_mochat_channel()
        ch._http = MagicMock()
        msg = OutboundMessage(channel="mochat", chat_id="session_1", content="", media=[])
        await ch.send(msg)
        # Should skip without calling HTTP

    @pytest.mark.asyncio
    async def test_send_no_token(self):
        ch = _make_mochat_channel(claw_token="")
        msg = OutboundMessage(channel="mochat", chat_id="session_1", content="hi")
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_send_empty_target(self):
        ch = _make_mochat_channel()
        ch._http = AsyncMock()
        msg = OutboundMessage(channel="mochat", chat_id="", content="hi")
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_send_to_session(self):
        ch = _make_mochat_channel()
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"code": 200, "data": {}}
        mock_response.text = ""
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        msg = OutboundMessage(channel="mochat", chat_id="session_abc", content="hello")
        await ch.send(msg)
        ch._http.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_panel(self):
        ch = _make_mochat_channel()
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"code": 200, "data": {}}
        mock_response.text = ""
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        msg = OutboundMessage(channel="mochat", chat_id="panel_abc", content="hello")
        await ch.send(msg)
        ch._http.post.assert_called_once()
        url = ch._http.post.call_args[0][0]
        assert "panels/send" in url

    @pytest.mark.asyncio
    async def test_send_with_media(self):
        ch = _make_mochat_channel()
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"code": 200, "data": {}}
        mock_response.text = ""
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        msg = OutboundMessage(
            channel="mochat", chat_id="session_abc", content="hi",
            media=["http://example.com/img.png"],
        )
        await ch.send(msg)

    def test_remember_message_id_new(self):
        ch = _make_mochat_channel()
        assert ch._remember_message_id("key1", "msg1") is False
        assert "msg1" in ch._seen_set["key1"]

    def test_remember_message_id_duplicate(self):
        ch = _make_mochat_channel()
        ch._remember_message_id("key1", "msg1")
        assert ch._remember_message_id("key1", "msg1") is True

    def test_remember_message_id_eviction(self):
        ch = _make_mochat_channel()
        # Insert more than MAX_SEEN_MESSAGE_IDS
        for i in range(2001):
            ch._remember_message_id("key1", f"msg_{i}")
        # Oldest should be evicted
        assert "msg_0" not in ch._seen_set["key1"]
        assert "msg_2000" in ch._seen_set["key1"]

    def test_mark_session_cursor_new(self):
        ch = _make_mochat_channel()
        ch._create_tracked_task = MagicMock(return_value=MagicMock())
        ch._mark_session_cursor("s1", 10)
        assert ch._session_cursor["s1"] == 10

    def test_mark_session_cursor_negative_ignored(self):
        ch = _make_mochat_channel()
        ch._session_cursor["s1"] = 5
        ch._mark_session_cursor("s1", -1)
        assert ch._session_cursor["s1"] == 5

    def test_mark_session_cursor_old_value_ignored(self):
        ch = _make_mochat_channel()
        ch._session_cursor["s1"] = 10
        ch._mark_session_cursor("s1", 5)
        assert ch._session_cursor["s1"] == 10

    @pytest.mark.asyncio
    async def test_load_session_cursors_no_file(self):
        ch = _make_mochat_channel()
        ch._cursor_path = Path("/nonexistent/path/cursors.json")
        await ch._load_session_cursors()
        assert ch._session_cursor == {}

    @pytest.mark.asyncio
    async def test_load_session_cursors_from_file(self, tmp_path):
        ch = _make_mochat_channel()
        ch._state_dir = tmp_path
        ch._cursor_path = tmp_path / "cursors.json"
        ch._cursor_path.write_text(json.dumps({"cursors": {"s1": 42}}))
        await ch._load_session_cursors()
        assert ch._session_cursor["s1"] == 42

    @pytest.mark.asyncio
    async def test_save_session_cursors(self, tmp_path):
        ch = _make_mochat_channel()
        ch._state_dir = tmp_path
        ch._cursor_path = tmp_path / "cursors.json"
        ch._session_cursor = {"s1": 99}
        await ch._save_session_cursors()
        data = json.loads(ch._cursor_path.read_text("utf-8"))
        assert data["cursors"]["s1"] == 99

    @pytest.mark.asyncio
    async def test_post_json_no_http(self):
        ch = _make_mochat_channel()
        ch._http = None
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await ch._post_json("/test", {})

    @pytest.mark.asyncio
    async def test_post_json_success(self):
        ch = _make_mochat_channel()
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"code": 200, "data": {"result": "ok"}}
        mock_response.text = ""
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        result = await ch._post_json("/test/path", {"key": "val"})
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_post_json_http_error(self):
        ch = _make_mochat_channel()
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="HTTP 500"):
            await ch._post_json("/test", {})

    @pytest.mark.asyncio
    async def test_post_json_api_error_code(self):
        ch = _make_mochat_channel()
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"code": 401, "message": "Unauthorized"}
        mock_response.text = ""
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="Unauthorized"):
            await ch._post_json("/test", {})

    @pytest.mark.asyncio
    async def test_post_json_no_code_field(self):
        ch = _make_mochat_channel()
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"sessions": [{"id": "s1"}]}
        mock_response.text = ""
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        result = await ch._post_json("/test", {})
        assert "sessions" in result

    @pytest.mark.asyncio
    async def test_socket_call_no_socket(self):
        ch = _make_mochat_channel()
        ch._socket = None
        result = await ch._socket_call("test.event", {})
        assert result["result"] is False

    @pytest.mark.asyncio
    async def test_socket_call_success(self):
        ch = _make_mochat_channel()
        ch._socket = MagicMock()
        ch._socket.call = AsyncMock(return_value={"result": True, "data": []})
        result = await ch._socket_call("test.event", {})
        assert result["result"] is True

    @pytest.mark.asyncio
    async def test_socket_call_exception(self):
        ch = _make_mochat_channel()
        ch._socket = MagicMock()
        ch._socket.call = AsyncMock(side_effect=Exception("timeout"))
        result = await ch._socket_call("test.event", {})
        assert result["result"] is False
        assert "timeout" in result["message"]

    @pytest.mark.asyncio
    async def test_subscribe_sessions_empty(self):
        ch = _make_mochat_channel()
        result = await ch._subscribe_sessions([])
        assert result is True

    @pytest.mark.asyncio
    async def test_subscribe_sessions_success(self):
        ch = _make_mochat_channel()
        ch._session_set.add("s1")
        ch._session_set.add("s2")
        ch._socket = MagicMock()
        ch._socket.call = AsyncMock(return_value={"result": True, "data": []})
        result = await ch._subscribe_sessions(["s1", "s2"])
        assert result is True

    @pytest.mark.asyncio
    async def test_subscribe_sessions_failure(self):
        ch = _make_mochat_channel()
        ch._socket = MagicMock()
        ch._socket.call = AsyncMock(return_value={"result": False, "message": "bad"})
        result = await ch._subscribe_sessions(["s1"])
        assert result is False

    @pytest.mark.asyncio
    async def test_subscribe_panels_no_auto_discover(self):
        ch = _make_mochat_channel()
        result = await ch._subscribe_panels([])
        assert result is True

    @pytest.mark.asyncio
    async def test_subscribe_panels_success(self):
        ch = _make_mochat_channel()
        ch._socket = MagicMock()
        ch._socket.call = AsyncMock(return_value={"result": True})
        result = await ch._subscribe_panels(["p1"])
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_watch_payload_invalid(self):
        ch = _make_mochat_channel()
        # Non-dict should be ignored
        await ch._handle_watch_payload("not a dict", "session")
        await ch._handle_watch_payload(None, "session")

    @pytest.mark.asyncio
    async def test_handle_watch_payload_no_target_id(self):
        ch = _make_mochat_channel()
        await ch._handle_watch_payload({"events": []}, "session")
        # No crash, no target

    @pytest.mark.asyncio
    async def test_handle_watch_payload_cold_session(self):
        ch = _make_mochat_channel()
        ch._cold_sessions.add("s1")
        payload = {"sessionId": "s1", "cursor": 5, "events": [
            {"type": "message.add", "payload": {"author": "u1", "content": "hi", "messageId": "m1"}}
        ]}
        await ch._handle_watch_payload(payload, "session")
        # Cold session events should be skipped
        assert "s1" not in ch._cold_sessions

    @pytest.mark.asyncio
    async def test_handle_watch_payload_processes_events(self):
        ch = _make_mochat_channel()
        ch._handle_message = AsyncMock()
        payload = {
            "sessionId": "s1",
            "cursor": 10,
            "events": [{
                "type": "message.add",
                "seq": 10,
                "payload": {
                    "author": "user1",
                    "messageId": "msg1",
                    "content": "hello world",
                },
            }],
        }
        await ch._handle_watch_payload(payload, "session")
        ch._handle_message.assert_called_once()
        call_kwargs = ch._handle_message.call_args[1]
        assert call_kwargs["sender_id"] == "user1"
        assert "hello" in call_kwargs["content"]

    @pytest.mark.asyncio
    async def test_process_inbound_event_author_is_agent(self):
        ch = _make_mochat_channel()
        ch._handle_message = AsyncMock()
        event = {
            "payload": {"author": "agent1", "messageId": "m1", "content": "hi"},
        }
        await ch._process_inbound_event("s1", event, "session")
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_inbound_event_unauthorized(self):
        ch = _make_mochat_channel(allow_from=["other_user"])
        ch._handle_message = AsyncMock()
        event = {
            "payload": {"author": "blocked_user", "messageId": "m1", "content": "hi"},
        }
        await ch._process_inbound_event("s1", event, "session")
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_inbound_event_duplicate(self):
        ch = _make_mochat_channel()
        ch._handle_message = AsyncMock()
        ch._remember_message_id("session:s1", "m1")
        event = {
            "payload": {"author": "user1", "messageId": "m1", "content": "hi"},
        }
        await ch._process_inbound_event("s1", event, "session")
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_inbound_event_no_payload(self):
        ch = _make_mochat_channel()
        ch._handle_message = AsyncMock()
        await ch._process_inbound_event("s1", {}, "session")
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_entries_empty(self):
        ch = _make_mochat_channel()
        ch._handle_message = AsyncMock()
        await ch._dispatch_entries("s1", "session", [], False)
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_entries_with_entries(self):
        ch = _make_mochat_channel()
        ch._handle_message = AsyncMock()
        entries = [
            MochatBufferedEntry(raw_body="hello", author="u1", message_id="m1", group_id=""),
        ]
        await ch._dispatch_entries("s1", "session", entries, True)
        ch._handle_message.assert_called_once()
        kwargs = ch._handle_message.call_args[1]
        assert kwargs["sender_id"] == "u1"
        assert kwargs["content"] == "hello"
        assert kwargs["metadata"]["was_mentioned"] is True

    @pytest.mark.asyncio
    async def test_cancel_delay_timers(self):
        ch = _make_mochat_channel()
        state = DelayState()
        # Create a real async task that can be gathered
        async def _noop():
            await asyncio.sleep(100)
        state.timer = asyncio.create_task(_noop())
        ch._delay_states["key1"] = state
        await ch._cancel_delay_timers()
        assert ch._delay_states == {}

    @pytest.mark.asyncio
    async def test_handle_notify_chat_message_no_group(self):
        ch = _make_mochat_channel()
        ch._handle_message = AsyncMock()
        await ch._handle_notify_chat_message({"author": "u1"})
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_notify_chat_message_valid(self):
        ch = _make_mochat_channel(reply_delay_mode="always")
        ch._panel_set.add("p1")
        ch._handle_message = AsyncMock()
        payload = {
            "groupId": "g1",
            "converseId": "p1",
            "author": "u1",
            "content": "hello",
            "_id": "msg1",
        }
        await ch._handle_notify_chat_message(payload)
        ch._handle_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_notify_chat_message_not_in_panel_set(self):
        ch = _make_mochat_channel()
        ch._panel_set.add("p_other")
        ch._handle_message = AsyncMock()
        payload = {
            "groupId": "g1",
            "converseId": "p1",
            "author": "u1",
            "content": "hello",
        }
        await ch._handle_notify_chat_message(payload)
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_notify_inbox_append_non_message(self):
        ch = _make_mochat_channel()
        ch._handle_message = AsyncMock()
        await ch._handle_notify_inbox_append({"type": "other"})
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_notify_inbox_append_with_group(self):
        ch = _make_mochat_channel()
        ch._handle_message = AsyncMock()
        await ch._handle_notify_inbox_append({
            "type": "message",
            "payload": {"groupId": "g1", "converseId": "c1"},
        })
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_notify_inbox_append_unknown_converse(self):
        ch = _make_mochat_channel()
        ch._handle_message = AsyncMock()
        ch._post_json = AsyncMock(return_value={"sessions": []})
        await ch._handle_notify_inbox_append({
            "type": "message",
            "payload": {"converseId": "c_unknown", "messageId": "m1", "messageAuthor": "u1"},
        })
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_notify_inbox_append_known_converse(self):
        ch = _make_mochat_channel()
        ch._session_by_converse["c1"] = "s1"
        ch._handle_message = AsyncMock()
        await ch._handle_notify_inbox_append({
            "type": "message",
            "payload": {"converseId": "c1", "messageId": "m1", "messageAuthor": "u1", "messagePlainContent": "hi"},
        })
        ch._handle_message.assert_called_once()

    def test_seed_targets_from_config(self):
        ch = _make_mochat_channel(
            sessions=["s1", "s2", "*"],
            panels=["p1"],
        )
        ch._seed_targets_from_config()
        assert "s1" in ch._session_set
        assert "s2" in ch._session_set
        assert ch._auto_discover_sessions is True
        assert "p1" in ch._panel_set
        assert ch._auto_discover_panels is False

    def test_normalize_id_list(self):
        cleaned, auto = MochatChannel._normalize_id_list(["a", "  ", "b", "*"])
        assert "a" in cleaned
        assert "b" in cleaned
        assert auto is True

    def test_normalize_id_list_no_wildcard(self):
        cleaned, auto = MochatChannel._normalize_id_list(["a", "b"])
        assert auto is False

    @pytest.mark.asyncio
    async def test_refresh_sessions_directory(self):
        ch = _make_mochat_channel()
        ch._post_json = AsyncMock(return_value={
            "sessions": [
                {"sessionId": "s_new", "converseId": "c1"},
            ]
        })
        ch._subscribe_sessions = AsyncMock(return_value=True)
        ch._ws_ready = True
        await ch._refresh_sessions_directory(subscribe_new=True)
        assert "s_new" in ch._session_set
        assert ch._session_by_converse["c1"] == "s_new"

    @pytest.mark.asyncio
    async def test_refresh_panels(self):
        ch = _make_mochat_channel()
        ch._post_json = AsyncMock(return_value={
            "panels": [
                {"id": "p_new", "type": 0},
                {"id": "p_skip", "type": 1},
            ]
        })
        ch._subscribe_panels = AsyncMock(return_value=True)
        ch._ws_ready = True
        await ch._refresh_panels(subscribe_new=True)
        assert "p_new" in ch._panel_set
        assert "p_skip" not in ch._panel_set

    @pytest.mark.asyncio
    async def test_start_no_token(self):
        ch = _make_mochat_channel(claw_token="")
        await ch.start()
        # Should return without error

    @pytest.mark.asyncio
    async def test_stop(self):
        ch = _make_mochat_channel()
        ch._running = True
        ch._http = AsyncMock()
        mock_socket = MagicMock()
        mock_socket.disconnect = AsyncMock()
        ch._socket = mock_socket

        # Create a real task for _refresh_task
        async def _noop_refresh():
            await asyncio.sleep(100)
        ch._refresh_task = asyncio.create_task(_noop_refresh())
        await asyncio.sleep(0)

        await ch.stop()
        assert ch._running is False
        mock_socket.disconnect.assert_called_once()
        assert ch._socket is None
        assert ch._http is None

    @pytest.mark.asyncio
    async def test_api_send(self):
        ch = _make_mochat_channel()
        ch._post_json = AsyncMock(return_value={})
        result = await ch._api_send("/test", "sessionId", "s1", "hello", None)
        ch._post_json.assert_called_once()
        call_args = ch._post_json.call_args
        assert call_args[0][0] == "/test"
        body = call_args[0][1]
        assert body["sessionId"] == "s1"
        assert body["content"] == "hello"

    @pytest.mark.asyncio
    async def test_api_send_with_reply_and_group(self):
        ch = _make_mochat_channel()
        ch._post_json = AsyncMock(return_value={})
        await ch._api_send("/test", "panelId", "p1", "hi", "reply123", "g1")
        body = ch._post_json.call_args[0][1]
        assert body["replyTo"] == "reply123"
        assert body["groupId"] == "g1"

    def test_read_group_id(self):
        assert MochatChannel._read_group_id({"group_id": " g1 "}) == "g1"
        assert MochatChannel._read_group_id({"groupId": "g2"}) == "g2"
        assert MochatChannel._read_group_id({}) is None
        assert MochatChannel._read_group_id(None) is None

    @pytest.mark.asyncio
    async def test_start_socket_client_not_available(self):
        ch = _make_mochat_channel()
        import socketio as _sio
        orig = _sio.SUPPORTED if hasattr(_sio, "SUPPORTED") else True
        try:
            # Simulate socketio not available by monkey-patching the module-level flag
            import xbot.channels.mochat as mochat_mod
            orig_val = mochat_mod.SOCKETIO_AVAILABLE
            mochat_mod.SOCKETIO_AVAILABLE = False
            result = await ch._start_socket_client()
            assert result is False
            mochat_mod.SOCKETIO_AVAILABLE = orig_val
        finally:
            pass

    def test_build_notify_handler(self):
        ch = _make_mochat_channel()
        ch._handle_notify_chat_message = AsyncMock()
        ch._handle_notify_inbox_append = AsyncMock()

        handler = ch._build_notify_handler("notify:chat.message.add")
        assert handler is not None

        handler2 = ch._build_notify_handler("notify:chat.inbox.append")
        assert handler2 is not None


# ===================================================================
# 3. Discord — Channel methods
# ===================================================================

class TestDiscordChannel:
    def test_init_from_dict(self):
        cfg = {"enabled": True, "token": "tok", "allow_from": ["*"]}
        bus = MagicMock()
        ch = DiscordChannel(cfg, bus)
        assert isinstance(ch.config, DiscordConfig)

    @pytest.mark.asyncio
    async def test_is_duplicate_first_time(self):
        ch = _make_discord_channel()
        assert await ch._is_duplicate_message("msg1") is False

    @pytest.mark.asyncio
    async def test_is_duplicate_second_time(self):
        ch = _make_discord_channel()
        await ch._is_duplicate_message("msg1")
        assert await ch._is_duplicate_message("msg1") is True

    @pytest.mark.asyncio
    async def test_is_duplicate_expired(self):
        ch = _make_discord_channel()
        ch._processed_message_ids["old_msg"] = 0  # epoch 0 = very old
        # Cleanup should remove it
        result = await ch._is_duplicate_message("new_msg")
        assert result is False
        assert "old_msg" not in ch._processed_message_ids

    @pytest.mark.asyncio
    async def test_handle_message_create_bot_message(self):
        ch = _make_discord_channel()
        ch._handle_message = AsyncMock()
        payload = {
            "author": {"id": "u1", "bot": True},
            "channel_id": "c1",
            "content": "hi",
            "id": "msg1",
        }
        await ch._handle_message_create(payload)
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_message_create_allowed(self):
        ch = _make_discord_channel()
        ch._handle_message = AsyncMock()
        ch._start_typing = AsyncMock()
        ch._bot_user_id = "bot1"
        payload = {
            "author": {"id": "u1", "bot": False},
            "channel_id": "c1",
            "content": "<@bot1> hello",
            "id": "msg1",
            "guild_id": "g1",
            "attachments": [],
        }
        await ch._handle_message_create(payload)
        ch._handle_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_create_dm_no_guild(self):
        ch = _make_discord_channel()
        ch._handle_message = AsyncMock()
        ch._start_typing = AsyncMock()
        payload = {
            "author": {"id": "u1"},
            "channel_id": "c1",
            "content": "hello",
            "id": "msg1",
            "attachments": [],
        }
        await ch._handle_message_create(payload)
        ch._handle_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_create_empty(self):
        ch = _make_discord_channel()
        ch._handle_message = AsyncMock()
        ch._start_typing = AsyncMock()
        payload = {
            "author": {"id": ""},
            "channel_id": "c1",
            "content": "",
            "id": "msg1",
        }
        await ch._handle_message_create(payload)
        ch._handle_message.assert_not_called()

    def test_should_respond_in_group_open_policy(self):
        ch = _make_discord_channel(group_policy="open")
        assert ch._should_respond_in_group({}, "any text") is True

    def test_should_respond_in_group_mention_via_array(self):
        ch = _make_discord_channel(group_policy="mention")
        ch._bot_user_id = "bot1"
        payload = {"mentions": [{"id": "bot1"}], "channel_id": "c1"}
        assert ch._should_respond_in_group(payload, "hello") is True

    def test_should_respond_in_group_mention_via_content(self):
        ch = _make_discord_channel(group_policy="mention")
        ch._bot_user_id = "bot1"
        payload = {"mentions": [], "channel_id": "c1"}
        assert ch._should_respond_in_group(payload, "hi <@bot1>") is True

    def test_should_respond_in_group_mention_nickname(self):
        ch = _make_discord_channel(group_policy="mention")
        ch._bot_user_id = "bot1"
        payload = {"mentions": [], "channel_id": "c1"}
        assert ch._should_respond_in_group(payload, "hi <@!bot1>") is True

    def test_should_respond_in_group_mention_not_mentioned(self):
        ch = _make_discord_channel(group_policy="mention")
        ch._bot_user_id = "bot1"
        payload = {"mentions": [], "channel_id": "c1"}
        assert ch._should_respond_in_group(payload, "no mention") is False

    @pytest.mark.asyncio
    async def test_send_no_http(self):
        ch = _make_discord_channel()
        ch._http = None
        msg = OutboundMessage(channel="discord", chat_id="c1", content="hi")
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_send_text_only(self):
        ch = _make_discord_channel()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)
        ch._stop_typing = AsyncMock()

        msg = OutboundMessage(channel="discord", chat_id="c1", content="hello world")
        await ch.send(msg)
        ch._http.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_with_reply(self):
        ch = _make_discord_channel()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)
        ch._stop_typing = AsyncMock()

        msg = OutboundMessage(channel="discord", chat_id="c1", content="hi", reply_to="msg123")
        await ch.send(msg)
        call_kwargs = ch._http.post.call_args[1]
        payload = call_kwargs.get("json", {})
        assert "message_reference" in payload

    @pytest.mark.asyncio
    async def test_send_empty_content_no_media(self):
        ch = _make_discord_channel()
        ch._http = AsyncMock()
        ch._stop_typing = AsyncMock()
        msg = OutboundMessage(channel="discord", chat_id="c1", content="")
        await ch.send(msg)
        ch._http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_payload_rate_limit(self):
        ch = _make_discord_channel()
        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.json.return_value = {"retry_after": 0.01}

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()

        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=[rate_limit_response, success_response])

        result = await ch._send_payload("http://test", {}, {"content": "hi"})
        assert result is True

    @pytest.mark.asyncio
    async def test_send_payload_error(self):
        ch = _make_discord_channel()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=Exception("network error"))

        result = await ch._send_payload("http://test", {}, {"content": "hi"})
        assert result is False

    @pytest.mark.asyncio
    async def test_stop(self):
        ch = _make_discord_channel()
        ch._http = AsyncMock()
        ch._ws = MagicMock()
        ch._ws.close = AsyncMock()
        await ch.stop()
        assert ch._running is False
        assert ch._ws is None
        assert ch._http is None

    @pytest.mark.asyncio
    async def test_gateway_loop_no_ws(self):
        ch = _make_discord_channel()
        ch._ws = None
        await ch._gateway_loop()  # Should return without error


# ===================================================================
# 4. DingTalk — Channel methods
# ===================================================================

class TestDingTalkChannel:
    def test_init_from_dict(self):
        cfg = {"enabled": True, "client_id": "id", "client_secret": "sec", "allow_from": ["*"]}
        bus = MagicMock()
        ch = DingTalkChannel(cfg, bus)
        assert isinstance(ch.config, DingTalkConfig)

    @pytest.mark.asyncio
    async def test_get_access_token_cached(self):
        ch = _make_dingtalk_channel()
        ch._access_token = "cached_token"
        import time
        ch._token_expiry = time.time() + 1000
        result = await ch._get_access_token()
        assert result == "cached_token"

    @pytest.mark.asyncio
    async def test_get_access_token_refresh(self):
        ch = _make_dingtalk_channel()
        ch._access_token = None
        mock_response = MagicMock()
        mock_response.json.return_value = {"accessToken": "new_token", "expireIn": 7200}
        mock_response.raise_for_status = MagicMock()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        result = await ch._get_access_token()
        assert result == "new_token"

    @pytest.mark.asyncio
    async def test_get_access_token_no_http(self):
        ch = _make_dingtalk_channel()
        ch._http = None
        result = await ch._get_access_token()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_access_token_error(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=Exception("network error"))
        result = await ch._get_access_token()
        assert result is None

    def test_is_http_url(self):
        assert DingTalkChannel._is_http_url("https://example.com/img.jpg") is True
        assert DingTalkChannel._is_http_url("http://example.com/file") is True
        assert DingTalkChannel._is_http_url("/local/path/file.jpg") is False

    def test_guess_upload_type(self):
        ch = _make_dingtalk_channel()
        assert ch._guess_upload_type("image.png") == "image"
        assert ch._guess_upload_type("audio.mp3") == "voice"
        assert ch._guess_upload_type("video.mp4") == "video"
        assert ch._guess_upload_type("document.pdf") == "file"

    def test_guess_filename(self):
        ch = _make_dingtalk_channel()
        assert ch._guess_filename("https://example.com/photo.jpg", "image") == "photo.jpg"
        # When basename is empty (e.g. URL with no path), fall back to defaults
        assert ch._guess_filename("https://example.com/", "image") == "image.jpg"
        assert ch._guess_filename("https://example.com/", "voice") == "audio.amr"
        assert ch._guess_filename("https://example.com/", "video") == "video.mp4"
        assert ch._guess_filename("https://example.com/", "file") == "file.bin"

    @pytest.mark.asyncio
    async def test_upload_media_success(self):
        ch = _make_dingtalk_channel()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"media_id": "media_123"}
        mock_response.text = '{"media_id": "media_123"}'
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        result = await ch._upload_media("token", b"data", "image", "img.jpg", "image/jpeg")
        assert result == "media_123"

    @pytest.mark.asyncio
    async def test_upload_media_http_error(self):
        ch = _make_dingtalk_channel()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {}
        mock_response.text = "error"
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        result = await ch._upload_media("token", b"data", "image", "img.jpg", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_upload_media_api_error(self):
        ch = _make_dingtalk_channel()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"errcode": 40001}
        mock_response.text = "error"
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        result = await ch._upload_media("token", b"data", "image", "img.jpg", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_upload_media_no_http(self):
        ch = _make_dingtalk_channel()
        ch._http = None
        result = await ch._upload_media("token", b"data", "image", "img.jpg", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_send_no_token(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        ch._get_access_token = AsyncMock(return_value=None)
        msg = OutboundMessage(channel="dingtalk", chat_id="u1", content="hi")
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_send_text(self):
        ch = _make_dingtalk_channel()
        ch._get_access_token = AsyncMock(return_value="tok")
        ch._send_markdown_text = AsyncMock(return_value=True)
        msg = OutboundMessage(channel="dingtalk", chat_id="u1", content="hello")
        await ch.send(msg)
        ch._send_markdown_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_text_and_media(self):
        ch = _make_dingtalk_channel()
        ch._get_access_token = AsyncMock(return_value="tok")
        ch._send_markdown_text = AsyncMock(return_value=True)
        ch._send_media_ref = AsyncMock(return_value=True)
        msg = OutboundMessage(channel="dingtalk", chat_id="u1", content="hi", media=["file.jpg"])
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_send_media_failure_fallback(self):
        ch = _make_dingtalk_channel()
        ch._get_access_token = AsyncMock(return_value="tok")
        ch._send_markdown_text = AsyncMock(return_value=True)
        ch._send_media_ref = AsyncMock(return_value=False)
        msg = OutboundMessage(channel="dingtalk", chat_id="u1", content="hi", media=["file.jpg"])
        await ch.send(msg)
        # Should send fallback message
        assert ch._send_markdown_text.call_count == 2

    @pytest.mark.asyncio
    async def test_send_batch_message_private(self):
        ch = _make_dingtalk_channel()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.text = "{}"
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        result = await ch._send_batch_message("tok", "u1", "sampleMarkdown", {"text": "hi"})
        assert result is True
        url = ch._http.post.call_args[0][0]
        assert "oToMessages" in url

    @pytest.mark.asyncio
    async def test_send_batch_message_group(self):
        ch = _make_dingtalk_channel()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.text = "{}"
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        result = await ch._send_batch_message("tok", "group:conv1", "sampleMarkdown", {"text": "hi"})
        assert result is True
        url = ch._http.post.call_args[0][0]
        assert "groupMessages" in url

    @pytest.mark.asyncio
    async def test_send_batch_message_error(self):
        ch = _make_dingtalk_channel()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "error"
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(return_value=mock_response)

        result = await ch._send_batch_message("tok", "u1", "sampleMarkdown", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_on_message_group(self):
        ch = _make_dingtalk_channel()
        ch._handle_message = AsyncMock()
        await ch._on_message("hello", "u1", "Alice", "2", "conv1")
        ch._handle_message.assert_called_once()
        kwargs = ch._handle_message.call_args[1]
        assert kwargs["chat_id"] == "group:conv1"

    @pytest.mark.asyncio
    async def test_on_message_private(self):
        ch = _make_dingtalk_channel()
        ch._handle_message = AsyncMock()
        await ch._on_message("hello", "u1", "Alice", "1", None)
        kwargs = ch._handle_message.call_args[1]
        assert kwargs["chat_id"] == "u1"

    @pytest.mark.asyncio
    async def test_read_media_bytes_http(self):
        ch = _make_dingtalk_channel()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"image data"
        mock_response.headers = {"content-type": "image/jpeg"}
        ch._http = AsyncMock()
        ch._http.get = AsyncMock(return_value=mock_response)

        data, filename, ctype = await ch._read_media_bytes("https://example.com/photo.jpg")
        assert data == b"image data"
        assert filename == "photo.jpg"

    @pytest.mark.asyncio
    async def test_read_media_bytes_local(self, tmp_path):
        ch = _make_dingtalk_channel()
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello")
        data, filename, ctype = await ch._read_media_bytes(str(f))
        assert data == b"hello"
        assert filename == "test.txt"

    @pytest.mark.asyncio
    async def test_read_media_bytes_empty(self):
        ch = _make_dingtalk_channel()
        data, filename, ctype = await ch._read_media_bytes("")
        assert data is None

    @pytest.mark.asyncio
    async def test_read_media_bytes_not_found(self):
        ch = _make_dingtalk_channel()
        data, filename, ctype = await ch._read_media_bytes("/nonexistent/file.txt")
        assert data is None

    @pytest.mark.asyncio
    async def test_stop(self):
        ch = _make_dingtalk_channel()
        ch._http = AsyncMock()
        await ch.stop()
        assert ch._running is False
        assert ch._http is None


# ===================================================================
# 5. Feishu — Uncovered send/reply paths
# ===================================================================

class TestFeishuUncovered:
    @pytest.mark.asyncio
    async def test_send_no_client(self):
        ch = _make_feishu_channel()
        ch._client = None
        msg = OutboundMessage(channel="feishu", chat_id="oc_test", content="hi")
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_send_text_format(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)
        msg = OutboundMessage(channel="feishu", chat_id="oc_test", content="hi")
        await ch.send(msg)
        ch._send_message_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_with_reply(self):
        ch = _make_feishu_channel(reply_to_message=True)
        ch._client = MagicMock()
        ch._reply_message_sync = MagicMock(return_value=True)
        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="hi",
            metadata={"message_id": "parent_msg"},
        )
        await ch.send(msg)
        ch._reply_message_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_tool_hint(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)
        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="web_search(q)",
            metadata={"_tool_hint": True},
        )
        await ch.send(msg)
        ch._send_message_sync.assert_called_once()
        call_args = ch._send_message_sync.call_args
        assert call_args[0][2] == "interactive"

    @pytest.mark.asyncio
    async def test_send_interaction_request(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)
        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="Pick one:",
            metadata={
                "interaction_request": True,
                "interaction_kind": "question",
                "suggestions": ["A", "B"],
            },
        )
        await ch.send(msg)
        ch._send_message_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_interaction_no_suggestions(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)
        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="Confirm?",
            metadata={
                "interaction_request": True,
                "interaction_kind": "confirmation",
                "suggestions": [],
            },
        )
        await ch.send(msg)
        call_args = ch._send_message_sync.call_args
        assert call_args[0][2] == "text"

    def test_format_tool_hint_lines(self):
        result = FeishuChannel._format_tool_hint_lines('web_search("hello, world"), read_file("path")')
        assert "web_search" in result
        assert "read_file" in result
        lines = result.split("\n")
        assert len(lines) == 2

    def test_format_tool_hint_nested(self):
        result = FeishuChannel._format_tool_hint_lines('func(a, func2(b, c))')
        assert "func" in result

    def test_check_health_stopped(self):
        ch = _make_feishu_channel()
        ch._running = False
        ok, msg = ch.check_health()
        assert ok is False
        assert "stopped" in msg

    def test_check_health_no_process(self):
        ch = _make_feishu_channel()
        ch._running = True
        ch._ws_process = None
        ok, msg = ch.check_health()
        assert ok is False

    def test_check_health_ok(self):
        ch = _make_feishu_channel()
        ch._running = True
        ch._ws_process = MagicMock()
        ch._ws_process.is_alive.return_value = True
        ch._client = MagicMock()
        ok, msg = ch.check_health()
        assert ok is True

    @pytest.mark.asyncio
    async def test_on_reaction_created_noop(self):
        ch = _make_feishu_channel()
        ch._on_reaction_created({})  # Should not raise

    @pytest.mark.asyncio
    async def test_on_message_read_noop(self):
        ch = _make_feishu_channel()
        ch._on_message_read({})

    @pytest.mark.asyncio
    async def test_on_bot_p2p_chat_entered(self):
        ch = _make_feishu_channel()
        ch._on_bot_p2p_chat_entered({})

    def test_mark_message_seen(self):
        ch = _make_feishu_channel()
        assert ch._mark_message_seen("msg1") is True
        assert ch._mark_message_seen("msg1") is False
        assert ch._mark_message_seen(None) is True
        assert ch._mark_message_seen("") is True

    def test_unmark_message_seen(self):
        ch = _make_feishu_channel()
        ch._mark_message_seen("msg1")
        ch._unmark_message_seen("msg1")
        assert ch._mark_message_seen("msg1") is True

    def test_extract_message_id_from_event(self):
        ch = _make_feishu_channel()
        data = SimpleNamespace(
            event=SimpleNamespace(message=SimpleNamespace(message_id="mid1"))
        )
        assert ch._extract_message_id_from_event(data) == "mid1"

    def test_extract_message_id_bad_data(self):
        ch = _make_feishu_channel()
        assert ch._extract_message_id_from_event(None) is None
        assert ch._extract_message_id_from_event("string") is None

    def test_namespace_from_dict(self):
        result = FeishuChannel._namespace_from_dict({"a": 1, "b": {"c": 2}})
        assert result.a == 1
        assert result.b.c == 2

    def test_namespace_from_dict_list(self):
        result = FeishuChannel._namespace_from_dict([{"a": 1}])
        assert isinstance(result, list)
        assert result[0].a == 1

    def test_namespace_from_dict_scalar(self):
        assert FeishuChannel._namespace_from_dict("hello") == "hello"
        assert FeishuChannel._namespace_from_dict(42) == 42

    @pytest.mark.asyncio
    async def test_dispatch_worker_event_message(self):
        ch = _make_feishu_channel()
        ch._on_message = AsyncMock()
        event = {
            "type": "message",
            "payload": {
                "event": {
                    "message": {"message_id": "m1"},
                    "sender": {"sender_type": "user"},
                }
            },
        }
        await ch._dispatch_worker_event(event)
        ch._on_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_worker_event_error(self):
        ch = _make_feishu_channel()
        event = {"type": "error", "error": "something went wrong"}
        await ch._dispatch_worker_event(event)  # Should not raise

    @pytest.mark.asyncio
    async def test_dispatch_worker_event_unknown_type(self):
        ch = _make_feishu_channel()
        event = {"type": "unknown"}
        await ch._dispatch_worker_event(event)

    def test_card_payload_len(self):
        elements = [{"tag": "markdown", "content": "hi"}]
        length = FeishuChannel._card_payload_len(elements)
        assert length > 0

    def test_largest_fitting_prefix_empty(self):
        ch = _make_feishu_channel()
        el = {"tag": "markdown", "content": ""}
        assert ch._largest_fitting_prefix(el, "", 1000) == 0

    def test_split_markdown_element_to_fit_small(self):
        ch = _make_feishu_channel()
        el = {"tag": "markdown", "content": "short text"}
        result = ch._split_markdown_element_to_fit(el, 10000)
        assert len(result) == 1

    def test_split_table_element_to_fit_small(self):
        ch = _make_feishu_channel()
        el = {
            "tag": "table",
            "columns": [{"name": "c0", "display_name": "A", "width": "auto"}],
            "rows": [{"c0": "1"}],
            "page_size": 2,
        }
        result = ch._split_table_element_to_fit(el, 10000)
        assert len(result) == 1

    def test_split_oversized_element_unknown_tag(self):
        ch = _make_feishu_channel()
        el = {"tag": "div", "content": "x" * 5000}
        result = ch._split_oversized_element(el, 100)
        assert len(result) == 1  # Unknown tags not split

    @pytest.mark.asyncio
    async def test_send_progress_no_reply(self):
        ch = _make_feishu_channel(reply_to_message=True)
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)
        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="progress...",
            metadata={"_progress": True, "message_id": "parent"},
        )
        await ch.send(msg)
        # Progress messages should NOT use reply
        ch._send_message_sync.assert_called_once()


# ===================================================================
# 6. WeCom — Channel methods
# ===================================================================

class TestWecomChannel:
    def test_init_from_dict(self):
        cfg = {"enabled": True, "bot_id": "bid", "secret": "sec", "allow_from": ["*"]}
        bus = MagicMock()
        ch = WecomChannel(cfg, bus)
        assert isinstance(ch.config, WecomConfig)

    def test_remember_chat_frame(self):
        ch = _make_wecom_channel()
        ch._remember_chat_frame("key1", "frame1")
        assert ch._chat_frames["key1"] == "frame1"

    def test_remember_chat_frame_empty_key(self):
        ch = _make_wecom_channel()
        ch._remember_chat_frame("", "frame1")
        assert "" not in ch._chat_frames

    def test_remember_chat_frame_eviction(self):
        ch = _make_wecom_channel()
        for i in range(1001):
            ch._remember_chat_frame(f"key_{i}", f"frame_{i}")
        assert len(ch._chat_frames) == 1000
        assert "key_0" not in ch._chat_frames

    @pytest.mark.asyncio
    async def test_process_message_text(self):
        ch = _make_wecom_channel()
        ch._handle_message = AsyncMock()
        frame = {"body": {
            "msgid": "m1",
            "chatid": "c1",
            "from": {"userid": "u1"},
            "text": {"content": "hello"},
        }}
        await ch._process_message(frame, "text")
        ch._handle_message.assert_called_once()
        kwargs = ch._handle_message.call_args[1]
        assert kwargs["content"] == "hello"
        assert kwargs["sender_id"] == "u1"

    @pytest.mark.asyncio
    async def test_process_message_dedup(self):
        ch = _make_wecom_channel()
        ch._handle_message = AsyncMock()
        frame = {"body": {
            "msgid": "m1",
            "chatid": "c1",
            "from": {"userid": "u1"},
            "text": {"content": "hello"},
        }}
        await ch._process_message(frame, "text")
        await ch._process_message(frame, "text")
        assert ch._handle_message.call_count == 1

    @pytest.mark.asyncio
    async def test_process_message_voice(self):
        ch = _make_wecom_channel()
        ch._handle_message = AsyncMock()
        frame = {"body": {
            "msgid": "m1",
            "chatid": "c1",
            "from": {"userid": "u1"},
            "voice": {"content": "transcribed text"},
        }}
        await ch._process_message(frame, "voice")
        kwargs = ch._handle_message.call_args[1]
        assert "transcribed text" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_process_message_mixed(self):
        ch = _make_wecom_channel()
        ch._handle_message = AsyncMock()
        frame = {"body": {
            "msgid": "m1",
            "chatid": "c1",
            "from": {"userid": "u1"},
            "mixed": {
                "item": [
                    {"type": "text", "text": {"content": "hello"}},
                    {"type": "image"},
                ]
            },
        }}
        await ch._process_message(frame, "mixed")
        kwargs = ch._handle_message.call_args[1]
        assert "hello" in kwargs["content"]
        assert "[image]" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_process_message_no_content(self):
        ch = _make_wecom_channel()
        ch._handle_message = AsyncMock()
        frame = {"body": {
            "msgid": "m1",
            "chatid": "c1",
            "from": {"userid": "u1"},
            "text": {"content": ""},
        }}
        await ch._process_message(frame, "text")
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_message_invalid_body(self):
        ch = _make_wecom_channel()
        ch._handle_message = AsyncMock()
        await ch._process_message("not_a_dict", "text")
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_message_image_download(self):
        ch = _make_wecom_channel()
        ch._handle_message = AsyncMock()
        ch._download_and_save_media = AsyncMock(return_value="/tmp/img.jpg")
        frame = {"body": {
            "msgid": "m1",
            "chatid": "c1",
            "from": {"userid": "u1"},
            "image": {"url": "http://example.com/img", "aeskey": "key123"},
        }}
        await ch._process_message(frame, "image")
        kwargs = ch._handle_message.call_args[1]
        assert "img.jpg" in kwargs["content"]
        assert "/tmp/img.jpg" in kwargs["media"]

    @pytest.mark.asyncio
    async def test_process_message_image_download_fail(self):
        ch = _make_wecom_channel()
        ch._handle_message = AsyncMock()
        ch._download_and_save_media = AsyncMock(return_value=None)
        frame = {"body": {
            "msgid": "m1",
            "chatid": "c1",
            "from": {"userid": "u1"},
            "image": {"url": "http://example.com/img", "aeskey": "key123"},
        }}
        await ch._process_message(frame, "image")
        kwargs = ch._handle_message.call_args[1]
        assert "download failed" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_process_message_file_download(self):
        ch = _make_wecom_channel()
        ch._handle_message = AsyncMock()
        ch._download_and_save_media = AsyncMock(return_value="/tmp/doc.pdf")
        frame = {"body": {
            "msgid": "m1",
            "chatid": "c1",
            "from": {"userid": "u1"},
            "file": {"url": "http://example.com/doc", "aeskey": "key123", "name": "doc.pdf"},
        }}
        await ch._process_message(frame, "file")
        kwargs = ch._handle_message.call_args[1]
        assert "doc.pdf" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_download_and_save_media_success(self):
        ch = _make_wecom_channel()
        ch._client = MagicMock()
        ch._client.download_file = AsyncMock(return_value=(b"file data", "img.jpg"))

        with patch("xbot.channels.wecom.get_media_dir") as mock_dir:
            mock_media_dir = MagicMock()
            mock_dir.return_value = mock_media_dir
            mock_file_path = MagicMock()
            mock_media_dir.__truediv__ = MagicMock(return_value=mock_file_path)

            result = await ch._download_and_save_media("http://url", "key1", "image", "test.jpg")
            assert result is not None

    @pytest.mark.asyncio
    async def test_download_and_save_media_no_data(self):
        ch = _make_wecom_channel()
        ch._client = MagicMock()
        ch._client.download_file = AsyncMock(return_value=(None, None))

        result = await ch._download_and_save_media("http://url", "key1", "image")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_and_save_media_error(self):
        ch = _make_wecom_channel()
        ch._client = MagicMock()
        ch._client.download_file = AsyncMock(side_effect=Exception("download error"))

        result = await ch._download_and_save_media("http://url", "key1", "image")
        assert result is None

    @pytest.mark.asyncio
    async def test_send_no_client(self):
        ch = _make_wecom_channel()
        ch._client = None
        msg = OutboundMessage(channel="wecom", chat_id="c1", content="hi")
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_send_no_frame(self):
        ch = _make_wecom_channel()
        ch._client = MagicMock()
        msg = OutboundMessage(channel="wecom", chat_id="unknown_chat", content="hi")
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_send_no_generator(self):
        ch = _make_wecom_channel()
        ch._client = MagicMock()
        ch._generate_req_id = None
        ch._remember_chat_frame("c1", "frame1")
        msg = OutboundMessage(channel="wecom", chat_id="c1", content="hi")
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_send_success(self):
        ch = _make_wecom_channel()
        ch._client = MagicMock()
        ch._client.reply_stream = AsyncMock()
        ch._generate_req_id = MagicMock(return_value="stream_123")
        ch._remember_chat_frame("c1", "frame1")
        msg = OutboundMessage(channel="wecom", chat_id="c1", content="hello")
        await ch.send(msg)
        ch._client.reply_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_empty_content(self):
        ch = _make_wecom_channel()
        ch._client = MagicMock()
        ch._remember_chat_frame("c1", "frame1")
        ch._generate_req_id = MagicMock(return_value="s1")
        msg = OutboundMessage(channel="wecom", chat_id="c1", content="  ")
        await ch.send(msg)
        ch._client.reply_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_enter_chat(self):
        ch = _make_wecom_channel(welcome_message="Welcome!")
        ch._client = MagicMock()
        ch._client.reply_welcome = AsyncMock()
        frame = {"body": {"chatid": "c1"}}
        await ch._on_enter_chat(frame)
        ch._client.reply_welcome.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_enter_chat_no_welcome(self):
        ch = _make_wecom_channel(welcome_message="")
        ch._client = MagicMock()
        ch._client.reply_welcome = AsyncMock()
        frame = {"body": {"chatid": "c1"}}
        await ch._on_enter_chat(frame)
        ch._client.reply_welcome.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_text_message(self):
        ch = _make_wecom_channel()
        ch._process_message = AsyncMock()
        frame = {"body": {"text": {"content": "hi"}}}
        await ch._on_text_message(frame)
        ch._process_message.assert_called_once_with(frame, "text")

    @pytest.mark.asyncio
    async def test_on_image_message(self):
        ch = _make_wecom_channel()
        ch._process_message = AsyncMock()
        await ch._on_image_message({"body": {}})
        ch._process_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop(self):
        ch = _make_wecom_channel()
        ch._client = MagicMock()
        ch._client.disconnect = AsyncMock()
        await ch.stop()
        assert ch._running is False


# ===================================================================
# 7. Feishu WS Worker — Pure functions
# ===================================================================

class TestFeishuWsWorker:
    def test_getattr_chain_found(self):
        obj = SimpleNamespace(a=SimpleNamespace(b=SimpleNamespace(c=42)))
        assert _getattr_chain(obj, "a", "b", "c") == 42

    def test_getattr_chain_missing(self):
        obj = SimpleNamespace(a=SimpleNamespace())
        assert _getattr_chain(obj, "a", "b", "c") is None

    def test_getattr_chain_none(self):
        assert _getattr_chain(None, "a") is None

    def test_normalize_message_event(self):
        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="mid1",
                    chat_id="cid1",
                    chat_type="p2p",
                    message_type="text",
                    content='{"text": "hello"}',
                    parent_id=None,
                    root_id=None,
                    mentions=None,
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_user1"),
                ),
            )
        )
        result = _normalize_message_event(data)
        assert result["event"]["message"]["message_id"] == "mid1"
        assert result["event"]["message"]["chat_id"] == "cid1"
        assert result["event"]["sender"]["sender_type"] == "user"
        assert result["event"]["sender"]["sender_id"]["open_id"] == "ou_user1"

    def test_normalize_message_event_with_mentions(self):
        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="mid1",
                    chat_id="cid1",
                    chat_type="group",
                    message_type="text",
                    content="",
                    parent_id=None,
                    root_id=None,
                    mentions=[
                        SimpleNamespace(id=SimpleNamespace(open_id="ou_bot", user_id="uid_bot")),
                    ],
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_user1"),
                ),
            )
        )
        result = _normalize_message_event(data)
        assert len(result["event"]["message"]["mentions"]) == 1
        assert result["event"]["message"]["mentions"][0]["id"]["open_id"] == "ou_bot"

    def test_normalize_message_event_no_sender_id(self):
        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="mid1", chat_id="cid1", chat_type="p2p",
                    message_type="text", content="", parent_id=None, root_id=None,
                    mentions=None,
                ),
                sender=SimpleNamespace(sender_type="user", sender_id=None),
            )
        )
        result = _normalize_message_event(data)
        assert result["event"]["sender"]["sender_id"] is None


# ===================================================================
# 8. Slack — Retry and reaction paths
# ===================================================================

class TestSlackUncovered:
    @pytest.mark.asyncio
    async def test_send_with_retry_success(self):
        ch = _make_slack_channel()
        mock_api = AsyncMock(return_value={"ok": True})
        result = await ch._send_with_retry(mock_api, channel="c1", text="hi")
        assert result == {"ok": True}
        mock_api.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_with_retry_rate_limit(self):
        ch = _make_slack_channel()
        error = Exception("rate limited 429")
        error.response = None
        mock_api = AsyncMock(side_effect=[error, {"ok": True}])
        result = await ch._send_with_retry(mock_api, channel="c1", text="hi")
        assert result == {"ok": True}
        assert mock_api.call_count == 2

    @pytest.mark.asyncio
    async def test_send_with_retry_auth_error(self):
        ch = _make_slack_channel()
        error = Exception("invalid_auth 401")
        mock_api = AsyncMock(side_effect=error)
        with pytest.raises(Exception, match="invalid_auth"):
            await ch._send_with_retry(mock_api, channel="c1", text="hi")

    @pytest.mark.asyncio
    async def test_send_with_retry_transient(self):
        ch = _make_slack_channel()
        error = Exception("connection timeout")
        mock_api = AsyncMock(side_effect=[error, error, {"ok": True}])
        result = await ch._send_with_retry(mock_api, channel="c1", text="hi")
        assert result == {"ok": True}
        assert mock_api.call_count == 3

    @pytest.mark.asyncio
    async def test_send_with_retry_all_fail(self):
        ch = _make_slack_channel()
        error = Exception("persistent error")
        mock_api = AsyncMock(side_effect=error)
        with pytest.raises(Exception, match="persistent error"):
            await ch._send_with_retry(mock_api, channel="c1", text="hi")

    @pytest.mark.asyncio
    async def test_update_react_emoji_no_client(self):
        ch = _make_slack_channel()
        ch._web_client = None
        await ch._update_react_emoji("c1", "ts1")

    @pytest.mark.asyncio
    async def test_update_react_emoji_no_ts(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        await ch._update_react_emoji("c1", None)
        ch._web_client.reactions_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_react_emoji_success(self):
        ch = _make_slack_channel(done_emoji="white_check_mark")
        ch._web_client = MagicMock()
        ch._web_client.reactions_remove = AsyncMock()
        ch._web_client.reactions_add = AsyncMock()
        await ch._update_react_emoji("c1", "ts1")
        ch._web_client.reactions_remove.assert_called_once()
        ch._web_client.reactions_add.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_react_emoji_no_reaction_error(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        error = Exception("no_reaction")
        ch._web_client.reactions_remove = AsyncMock(side_effect=error)
        ch._web_client.reactions_add = AsyncMock()
        await ch._update_react_emoji("c1", "ts1")
        # Should break out of retry loop on no_reaction

    @pytest.mark.asyncio
    async def test_notify_processing_error_no_client(self):
        ch = _make_slack_channel()
        ch._web_client = None
        await ch._notify_processing_error("c1", "ts1", "channel")

    @pytest.mark.asyncio
    async def test_notify_processing_error(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        ch._send_with_retry = AsyncMock()
        await ch._notify_processing_error("c1", "ts1", "channel")
        ch._send_with_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_notify_processing_error_dm(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        ch._send_with_retry = AsyncMock()
        await ch._notify_processing_error("c1", None, "im")
        call_kwargs = ch._send_with_retry.call_args[1]
        assert call_kwargs.get("thread_ts") is None

    @pytest.mark.asyncio
    async def test_send_with_media(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        ch._web_client.chat_postMessage = AsyncMock()
        ch._web_client.files_upload_v2 = AsyncMock()
        ch._send_with_retry = AsyncMock()
        msg = OutboundMessage(
            channel="slack", chat_id="c1", content="hi",
            media=["/path/to/file.png"],
            metadata={"slack": {"event": {"ts": "ts1"}, "channel_type": "im"}},
        )
        await ch.send(msg)
        # Should call send_with_retry for text and file
        assert ch._send_with_retry.call_count == 2

    @pytest.mark.asyncio
    async def test_send_no_content_media_only(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        ch._send_with_retry = AsyncMock()
        msg = OutboundMessage(
            channel="slack", chat_id="c1", content="",
            media=["/path/to/file.png"],
            metadata={"slack": {"event": {}, "channel_type": "im"}},
        )
        await ch.send(msg)
        # Should only upload file, no text message
        assert ch._send_with_retry.call_count == 1

    @pytest.mark.asyncio
    async def test_send_progress_no_reaction(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        ch._send_with_retry = AsyncMock()
        ch._update_react_emoji = AsyncMock()
        msg = OutboundMessage(
            channel="slack", chat_id="c1", content="progress...",
            metadata={
                "_progress": True,
                "slack": {"event": {"ts": "ts1"}, "channel_type": "im"},
            },
        )
        await ch.send(msg)
        ch._update_react_emoji.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_socket_request_non_event(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        req = MagicMock()
        req.type = "not_events_api"
        client = MagicMock()
        await ch._on_socket_request(client, req)
        client.send_socket_mode_response.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_socket_request_bot_message(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        ch._bot_user_id = "U_BOT"
        ch._handle_message = AsyncMock()
        client = MagicMock()
        client.send_socket_mode_response = AsyncMock()
        req = MagicMock()
        req.type = "events_api"
        req.envelope_id = "env1"
        req.payload = {
            "event": {
                "type": "message",
                "user": "U_BOT",
                "channel": "C1",
                "text": "hi",
                "ts": "ts1",
            }
        }
        await ch._on_socket_request(client, req)
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_socket_request_subtype_ignored(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        ch._handle_message = AsyncMock()
        client = MagicMock()
        client.send_socket_mode_response = AsyncMock()
        req = MagicMock()
        req.type = "events_api"
        req.envelope_id = "env1"
        req.payload = {
            "event": {
                "type": "message",
                "user": "U1",
                "channel": "C1",
                "subtype": "message_changed",
                "text": "hi",
            }
        }
        await ch._on_socket_request(client, req)
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_socket_request_app_mention_dedup(self):
        ch = _make_slack_channel()
        ch._web_client = MagicMock()
        ch._bot_user_id = "U_BOT"
        ch._handle_message = AsyncMock()
        client = MagicMock()
        client.send_socket_mode_response = AsyncMock()
        req = MagicMock()
        req.type = "events_api"
        req.envelope_id = "env1"
        req.payload = {
            "event": {
                "type": "message",
                "user": "U1",
                "channel": "C1",
                "text": "hi <@U_BOT>",
                "channel_type": "channel",
                "ts": "ts1",
            }
        }
        await ch._on_socket_request(client, req)
        # message with bot mention in text should be skipped (prefer app_mention)
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop(self):
        ch = _make_slack_channel()
        ch._socket_client = MagicMock()
        ch._socket_client.close = AsyncMock()
        await ch.stop()
        assert ch._running is False
        assert ch._socket_client is None


# ===================================================================
# 9. DingTalk Handler
# ===================================================================

class TestDingTalkHandler:
    @pytest.mark.asyncio
    async def test_handler_process_text(self):
        ch = _make_dingtalk_channel()
        ch._schedule_inbound_message = MagicMock()
        handler = XbotDingTalkHandler(ch)

        msg_data = {
            "text": {"content": "hello"},
            "senderId": "u1",
            "senderNick": "Alice",
            "conversationType": "1",
            "messageType": "text",
        }
        message = MagicMock()
        message.data = msg_data

        result = await handler.process(message)
        assert result[0] == 200
        ch._schedule_inbound_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handler_process_empty(self):
        ch = _make_dingtalk_channel()
        ch._schedule_inbound_message = MagicMock()
        handler = XbotDingTalkHandler(ch)

        msg_data = {
            "text": {"content": ""},
            "messageType": "unsupported",
        }
        message = MagicMock()
        message.data = msg_data

        result = await handler.process(message)
        assert result[0] == 200
        ch._schedule_inbound_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_process_exception(self):
        ch = _make_dingtalk_channel()
        handler = XbotDingTalkHandler(ch)
        message = MagicMock()
        message.data = None  # Will cause an exception

        result = await handler.process(message)
        assert result[0] == 200  # Still returns OK to avoid retry


# ===================================================================
# 10. Mochat — Config models
# ===================================================================

class TestMochatConfigModels:
    def test_mochat_config_defaults(self):
        cfg = MochatConfig()
        assert cfg.enabled is False
        assert cfg.claw_token == ""
        assert cfg.sessions == []
        assert cfg.panels == []
        assert cfg.mention.require_in_groups is False
        assert cfg.groups == {}
        assert cfg.reply_delay_mode == "non-mention"

    def test_mochat_mention_config(self):
        cfg = MochatMentionConfig(require_in_groups=True)
        assert cfg.require_in_groups is True

    def test_mochat_group_rule(self):
        cfg = MochatGroupRule(require_mention=True)
        assert cfg.require_mention is True

    def test_mochat_target_dataclass(self):
        t = MochatTarget(id="s1", is_panel=False)
        assert t.id == "s1"
        assert t.is_panel is False

    def test_delay_state(self):
        ds = DelayState()
        assert ds.entries == []
        assert ds.timer is None

    def test_buffered_entry(self):
        entry = MochatBufferedEntry(raw_body="hello", author="u1", sender_name="Alice")
        assert entry.raw_body == "hello"
        assert entry.sender_name == "Alice"

    def test_default_config(self):
        result = MochatChannel.default_config()
        assert isinstance(result, dict)
        assert "enabled" in result
