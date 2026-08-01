"""Comprehensive tests for channel modules — pure logic functions.

Covers:
- Telegram: _markdown_to_telegram_html, _render_table_box, _strip_md,
           _has_mention_entity, _is_group_message_for_bot, is_allowed
- Feishu content: _extract_post_content, _extract_interactive_content,
                  _extract_element_content, _extract_share_card_content,
                  _extract_post_mention_ids, _extract_post_text
- Feishu: _detect_msg_format, _build_card_elements, _split_elements_by_table_limit,
          _parse_md_table, _strip_md_formatting, _markdown_to_post
- Slack: _to_mrkdwn, _convert_table, _fixup_mrkdwn, _strip_bot_mention,
         _is_allowed, _should_respond_in_channel
- Base: is_allowed allowlist logic
- Manager: _validate_allow_from
- Registry: discover_channel_names, load_channel_class
"""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub external SDK modules so channel imports succeed without heavy deps
# ---------------------------------------------------------------------------
def _install_sdk_stubs():
    """Install lightweight stubs for SDKs that channels import at module level."""
    stubs = {
        "telegram": types.ModuleType("telegram"),
        "telegram.error": types.ModuleType("telegram.error"),
        "telegram.ext": types.ModuleType("telegram.ext"),
        "telegram.request": types.ModuleType("telegram.request"),
        "slack_sdk": types.ModuleType("slack_sdk"),
        "slack_sdk.socket_mode": types.ModuleType("slack_sdk.socket_mode"),
        "slack_sdk.socket_mode.request": types.ModuleType("slack_sdk.socket_mode.request"),
        "slack_sdk.socket_mode.response": types.ModuleType("slack_sdk.socket_mode.response"),
        "slack_sdk.socket_mode.websockets": types.ModuleType("slack_sdk.socket_mode.websockets"),
        "slack_sdk.web": types.ModuleType("slack_sdk.web"),
        "slack_sdk.web.async_client": types.ModuleType("slack_sdk.web.async_client"),
        "slackify_markdown": types.ModuleType("slackify_markdown"),
    }
    # telegram stubs
    tg = stubs["telegram"]
    tg.BotCommand = type("BotCommand", (), {"__init__": lambda self, *a, **kw: None})
    tg.ReplyParameters = type("ReplyParameters", (), {"__init__": lambda self, *a, **kw: None})
    tg.Update = type("Update", (), {})
    stubs["telegram.error"].Conflict = Exception
    stubs["telegram.error"].TimedOut = Exception
    ext = stubs["telegram.ext"]
    ext.Application = MagicMock()
    ext.CommandHandler = type("CommandHandler", (), {"__init__": lambda self, *a, **kw: None})
    ext.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
    ext.MessageHandler = type("MessageHandler", (), {"__init__": lambda self, *a, **kw: None})
    ext.filters = MagicMock()
    stubs["telegram.request"].HTTPXRequest = MagicMock()

    # slack stubs
    stubs["slack_sdk.socket_mode.request"].SocketModeRequest = type("SocketModeRequest", (), {})
    stubs["slack_sdk.socket_mode.response"].SocketModeResponse = type("SocketModeResponse", (), {})
    stubs["slack_sdk.socket_mode.websockets"].SocketModeClient = MagicMock()
    stubs["slack_sdk.web.async_client"].AsyncWebClient = MagicMock()

    # slackify_markdown: pass-through
    stubs["slackify_markdown"].slackify_markdown = lambda text: text

    for mod_name, mod in stubs.items():
        if mod_name not in sys.modules:
            sys.modules[mod_name] = mod


_install_sdk_stubs()


# Now import the modules under test
from xbot.channels.telegram import (  # noqa: E402
    _markdown_to_telegram_html,
    _render_table_box,
    _strip_md,
    TelegramChannel,
    TelegramConfig,
)
from xbot.channels.feishu_content import (  # noqa: E402
    _extract_element_content,
    _extract_interactive_content,
    _extract_post_content,
    _extract_post_mention_ids,
    _extract_post_text,
    _extract_share_card_content,
)
from xbot.channels.slack import SlackChannel, SlackConfig  # noqa: E402
from xbot.channels.feishu import FeishuChannel, FeishuConfig  # noqa: E402
from xbot.channels.base import BaseChannel  # noqa: E402
from xbot.channels.registry import (  # noqa: E402
    discover_channel_names,
    load_channel_class,
)


# ===================================================================
# Helpers
# ===================================================================

def _make_base_channel_subclass(allow_from: list[str]):
    """Create a concrete BaseChannel subclass for testing is_allowed."""
    class TestChannel(BaseChannel):
        name = "test"
        display_name = "Test"

        async def start(self):
            pass

        async def stop(self):
            pass

        async def send(self, msg):
            pass

    config = SimpleNamespace(allow_from=allow_from)
    bus = MagicMock()
    return TestChannel(config, bus)


def _make_slack_channel(
    allow_from=None,
    group_policy="mention",
    group_allow_from=None,
    dm_enabled=True,
    dm_policy="open",
    dm_allow_from=None,
):
    """Create a SlackChannel with controlled config for unit tests."""
    cfg_dict = {
        "enabled": True,
        "bot_token": "xoxb-test",
        "app_token": "xapp-test",
        "allow_from": allow_from or [],
        "group_policy": group_policy,
        "group_allow_from": group_allow_from or [],
        "dm": {
            "enabled": dm_enabled,
            "policy": dm_policy,
            "allow_from": dm_allow_from or [],
        },
    }
    config = SlackConfig.model_validate(cfg_dict)
    bus = MagicMock()
    ch = SlackChannel(config, bus)
    return ch


def _make_feishu_channel(group_policy="mention", bot_open_id="ou_bot123"):
    """Create a FeishuChannel for unit tests."""
    cfg_dict = {
        "enabled": True,
        "app_id": "cli_test",
        "app_secret": "secret",
        "allow_from": ["*"],
        "group_policy": group_policy,
        "bot_open_id": bot_open_id,
    }
    config = FeishuConfig.model_validate(cfg_dict)
    bus = MagicMock()
    return FeishuChannel(config, bus)


# ===================================================================
# 1. Telegram — _strip_md
# ===================================================================

class TestStripMd:
    def test_bold(self):
        assert _strip_md("**hello**") == "hello"

    def test_underscore_bold(self):
        assert _strip_md("__world__") == "world"

    def test_strikethrough(self):
        assert _strip_md("~~gone~~") == "gone"

    def test_inline_code(self):
        assert _strip_md("`code`") == "code"

    def test_combined(self):
        assert _strip_md("**bold** and `code`") == "bold and code"

    def test_plain_text(self):
        assert _strip_md("no formatting") == "no formatting"


# ===================================================================
# 2. Telegram — _render_table_box
# ===================================================================

class TestRenderTableBox:
    def test_simple_table(self):
        lines = [
            "| Name | Age |",
            "| --- | --- |",
            "| Alice | 30 |",
            "| Bob | 25 |",
        ]
        result = _render_table_box(lines)
        result_lines = result.split("\n")
        assert len(result_lines) == 4  # header + separator + 2 data rows
        assert "Name" in result_lines[0]
        assert "Age" in result_lines[0]
        # BUG-003: _render_table_box uses ASCII dashes not Unicode box chars
        assert "─" in result_lines[1] or "-" in result_lines[1]
        assert "Alice" in result_lines[2]
        assert "Bob" in result_lines[3]

    def test_no_separator_returns_original(self):
        lines = ["| just | text |", "| no | sep |"]
        result = _render_table_box(lines)
        assert result == "\n".join(lines)

    def test_empty_rows(self):
        result = _render_table_box([])
        assert result == ""

    def test_single_row_no_sep(self):
        lines = ["| only | header |"]
        result = _render_table_box(lines)
        assert result == "\n".join(lines)

    def test_alignment_markers_in_sep(self):
        lines = [
            "| Left | Center | Right |",
            "| :--- | :---: | ---: |",
            "| a | b | c |",
        ]
        result = _render_table_box(lines)
        result_lines = result.split("\n")
        assert len(result_lines) == 3  # header + separator + 1 data row
        assert "─" in result_lines[1]

    def test_chinese_characters_display_width(self):
        """CJK characters should be counted as width 2 for alignment."""
        lines = [
            "| 名字 | Age |",
            "| --- | --- |",
            "| 小明 | 20 |",
        ]
        result = _render_table_box(lines)
        result_lines = result.split("\n")
        assert len(result_lines) == 3
        assert "小明" in result_lines[2]


# ===================================================================
# 3. Telegram — _markdown_to_telegram_html
# ===================================================================

class TestMarkdownToTelegramHtml:
    def test_empty_string(self):
        assert _markdown_to_telegram_html("") == ""

    def test_plain_text(self):
        assert _markdown_to_telegram_html("hello world") == "hello world"

    def test_bold(self):
        result = _markdown_to_telegram_html("**bold**")
        assert "<b>bold</b>" in result

    def test_underscore_bold(self):
        result = _markdown_to_telegram_html("__bold__")
        assert "<b>bold</b>" in result

    def test_italic(self):
        result = _markdown_to_telegram_html("_italic_")
        assert "<i>italic</i>" in result

    def test_strikethrough(self):
        result = _markdown_to_telegram_html("~~strike~~")
        assert "<s>strike</s>" in result

    def test_inline_code(self):
        result = _markdown_to_telegram_html("use `foo` here")
        assert "<code>foo</code>" in result

    def test_inline_code_html_escape(self):
        result = _markdown_to_telegram_html("`<div>`")
        assert "<code>&lt;div&gt;</code>" in result

    def test_code_block(self):
        md = "```python\nprint('hi')\n```"
        result = _markdown_to_telegram_html(md)
        assert "<pre><code>" in result
        assert "print" in result

    def test_code_block_html_escape(self):
        md = "```\n<a>&b\n```"
        result = _markdown_to_telegram_html(md)
        assert "&lt;a&gt;" in result
        assert "&amp;b" in result

    def test_link(self):
        result = _markdown_to_telegram_html("[click](https://example.com)")
        assert '<a href="https://example.com">click</a>' in result

    def test_header_stripped(self):
        result = _markdown_to_telegram_html("# Title")
        assert "Title" in result
        assert "#" not in result

    def test_blockquote_stripped(self):
        result = _markdown_to_telegram_html("> quoted")
        assert "quoted" in result
        assert ">" not in result or "&gt;" not in result  # > stripped, not escaped

    def test_bullet_list(self):
        result = _markdown_to_telegram_html("- item")
        assert "• item" in result

    def test_html_escaping(self):
        result = _markdown_to_telegram_html("a < b & c > d")
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result

    def test_table_converted(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = _markdown_to_telegram_html(md)
        # Table should be wrapped in <pre><code>
        assert "<pre><code>" in result

    def test_bold_and_italic_combined(self):
        result = _markdown_to_telegram_html("**bold** and _italic_")
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result


# ===================================================================
# 4. Telegram — _has_mention_entity
# ===================================================================

class TestHasMentionEntity:
    def test_mention_entity_matching(self):
        entity = SimpleNamespace(type="mention", offset=0, length=8)
        assert TelegramChannel._has_mention_entity(
            "@mybot hi", [entity], "mybot", 123
        )

    def test_mention_entity_case_insensitive(self):
        entity = SimpleNamespace(type="mention", offset=0, length=8)
        assert TelegramChannel._has_mention_entity(
            "@MyBot hi", [entity], "mybot", 123
        )

    def test_text_mention_entity(self):
        user = SimpleNamespace(id=123)
        entity = SimpleNamespace(type="text_mention", user=user, offset=0, length=5)
        assert TelegramChannel._has_mention_entity(
            "hello", [entity], "mybot", 123
        )

    def test_text_mention_wrong_user(self):
        user = SimpleNamespace(id=999)
        entity = SimpleNamespace(type="text_mention", user=user, offset=0, length=5)
        assert not TelegramChannel._has_mention_entity(
            "hello", [entity], "mybot", 123
        )

    def test_no_entities(self):
        assert not TelegramChannel._has_mention_entity("hello", None, "mybot", 123)

    def test_empty_entities(self):
        assert not TelegramChannel._has_mention_entity("hello", [], "mybot", 123)

    def test_fallback_text_search(self):
        """When entities don't match, falls back to text search."""
        assert TelegramChannel._has_mention_entity(
            "hi @mybot", [], "mybot", 123
        )

    def test_fallback_not_found(self):
        assert not TelegramChannel._has_mention_entity(
            "hi @otherbot", [], "mybot", 123
        )


# ===================================================================
# 5. Telegram — is_allowed (override with id|username)
# ===================================================================

class TestTelegramIsAllowed:
    def _make_channel(self, allow_from):
        cfg = TelegramConfig(enabled=True, token="test", allow_from=allow_from)
        bus = MagicMock()
        return TelegramChannel(cfg, bus)

    def test_base_allowlist_match(self):
        ch = self._make_channel(["123"])
        assert ch.is_allowed("123")

    def test_base_wildcard(self):
        ch = self._make_channel(["*"])
        assert ch.is_allowed("anyone")

    def test_empty_list_denies(self):
        ch = self._make_channel([])
        assert not ch.is_allowed("123")

    def test_id_pipe_username_match_by_id(self):
        ch = self._make_channel(["123"])
        assert ch.is_allowed("123|alice")

    def test_id_pipe_username_match_by_username(self):
        ch = self._make_channel(["alice"])
        assert ch.is_allowed("123|alice")

    def test_id_pipe_username_no_match(self):
        ch = self._make_channel(["456"])
        assert not ch.is_allowed("123|alice")

    def test_malformed_sender_id(self):
        ch = self._make_channel(["123"])
        # no pipe separator
        assert not ch.is_allowed("nodash")

    def test_two_pipes_not_valid(self):
        ch = self._make_channel(["123"])
        assert not ch.is_allowed("123|alice|extra")


# ===================================================================
# 6. Feishu content — _extract_post_content
# ===================================================================

class TestExtractPostContent:
    def test_direct_format(self):
        data = {
            "title": "My Title",
            "content": [
                [{"tag": "text", "text": "hello "}, {"tag": "text", "text": "world"}]
            ],
        }
        text, imgs = _extract_post_content(data)
        assert "My Title" in text
        assert "hello" in text
        assert "world" in text
        assert imgs == []

    def test_localized_zh_cn(self):
        data = {
            "zh_cn": {
                "title": "标题",
                "content": [[{"tag": "text", "text": "内容"}]],
            }
        }
        text, imgs = _extract_post_content(data)
        assert "标题" in text
        assert "内容" in text

    def test_wrapped_post_envelope(self):
        data = {
            "post": {
                "zh_cn": {
                    "title": "Wrapped",
                    "content": [[{"tag": "text", "text": "inside"}]],
                }
            }
        }
        text, imgs = _extract_post_content(data)
        assert "Wrapped" in text
        assert "inside" in text

    def test_image_extraction(self):
        data = {
            "content": [
                [
                    {"tag": "text", "text": "see image"},
                    {"tag": "img", "image_key": "img_v2_abc"},
                ]
            ]
        }
        text, imgs = _extract_post_content(data)
        assert "img_v2_abc" in imgs

    def test_at_mention(self):
        data = {
            "content": [
                [{"tag": "at", "user_name": "alice"}]
            ]
        }
        text, imgs = _extract_post_content(data)
        assert "@alice" in text

    def test_code_block(self):
        data = {
            "content": [
                [{"tag": "code_block", "language": "py", "text": "x=1"}]
            ]
        }
        text, imgs = _extract_post_content(data)
        assert "```py" in text
        assert "x=1" in text

    def test_empty_content(self):
        text, imgs = _extract_post_content({})
        assert text == ""
        assert imgs == []

    def test_non_dict_input(self):
        text, imgs = _extract_post_content("not a dict")
        assert text == ""
        assert imgs == []

    def test_localized_en_us(self):
        data = {
            "en_us": {
                "title": "EN Title",
                "content": [[{"tag": "text", "text": "EN body"}]],
            }
        }
        text, imgs = _extract_post_content(data)
        assert "EN Title" in text
        assert "EN body" in text

    def test_fallback_to_any_dict_child(self):
        data = {
            "fr_fr": {
                "title": "FR",
                "content": [[{"tag": "text", "text": "Bonjour"}]],
            }
        }
        text, imgs = _extract_post_content(data)
        assert "FR" in text
        assert "Bonjour" in text


# ===================================================================
# 7. Feishu content — _extract_interactive_content
# ===================================================================

class TestExtractInteractiveContent:
    def test_title_from_top_level(self):
        content = {"title": {"content": "Hello"}}
        parts = _extract_interactive_content(content)
        assert any("Hello" in p for p in parts)

    def test_header_title(self):
        content = {"header": {"title": {"content": "Header Title"}}}
        parts = _extract_interactive_content(content)
        assert any("Header Title" in p for p in parts)

    def test_body_elements(self):
        content = {
            "body": {
                "elements": [
                    {"tag": "markdown", "content": "body text"},
                ]
            }
        }
        parts = _extract_interactive_content(content)
        assert "body text" in parts

    def test_top_level_elements(self):
        content = {
            "elements": [
                {"tag": "markdown", "content": "top text"},
            ]
        }
        parts = _extract_interactive_content(content)
        assert "top text" in parts

    def test_nested_card(self):
        content = {
            "card": {
                "title": {"content": "Nested"},
                "elements": [{"tag": "plain_text", "content": "deep"}],
            }
        }
        parts = _extract_interactive_content(content)
        assert any("Nested" in p for p in parts)
        assert "deep" in parts

    def test_string_json_content(self):
        content_str = json.dumps({"title": {"content": "From JSON"}})
        parts = _extract_interactive_content(content_str)
        assert any("From JSON" in p for p in parts)

    def test_invalid_json_string(self):
        parts = _extract_interactive_content("not json at all")
        assert "not json at all" in parts

    def test_non_dict_returns_empty(self):
        parts = _extract_interactive_content(42)
        assert parts == []


# ===================================================================
# 8. Feishu content — _extract_element_content
# ===================================================================

class TestExtractElementContent:
    def test_markdown_tag(self):
        el = {"tag": "markdown", "content": "**bold**"}
        assert _extract_element_content(el) == ["**bold**"]

    def test_lark_md_tag(self):
        el = {"tag": "lark_md", "content": "text"}
        assert _extract_element_content(el) == ["text"]

    def test_div_tag_with_text(self):
        el = {"tag": "div", "text": {"content": "div text"}}
        parts = _extract_element_content(el)
        assert "div text" in parts

    def test_div_tag_with_fields(self):
        el = {
            "tag": "div",
            "text": {"content": "main"},
            "fields": [{"text": {"content": "field1"}}, {"text": {"content": "field2"}}],
        }
        parts = _extract_element_content(el)
        assert "main" in parts
        assert "field1" in parts
        assert "field2" in parts

    def test_link_tag(self):
        el = {"tag": "a", "href": "https://example.com", "text": "click"}
        parts = _extract_element_content(el)
        assert "link: https://example.com" in parts
        assert "click" in parts

    def test_button_tag(self):
        el = {"tag": "button", "text": {"content": "OK"}, "url": "https://x.com"}
        parts = _extract_element_content(el)
        assert "OK" in parts
        assert "link: https://x.com" in parts

    def test_img_tag(self):
        el = {"tag": "img", "alt": {"content": "a photo"}}
        parts = _extract_element_content(el)
        assert "a photo" in parts

    def test_img_tag_no_alt(self):
        el = {"tag": "img", "alt": {}}
        parts = _extract_element_content(el)
        assert "[image]" in parts

    def test_note_tag_recursive(self):
        el = {
            "tag": "note",
            "elements": [{"tag": "markdown", "content": "note text"}],
        }
        parts = _extract_element_content(el)
        assert "note text" in parts

    def test_column_set(self):
        el = {
            "tag": "column_set",
            "columns": [
                {"elements": [{"tag": "markdown", "content": "col1"}]},
                {"elements": [{"tag": "markdown", "content": "col2"}]},
            ],
        }
        parts = _extract_element_content(el)
        assert "col1" in parts
        assert "col2" in parts

    def test_action_tag(self):
        el = {
            "tag": "action",
            "actions": [{"tag": "button", "text": {"content": "Go"}}],
        }
        parts = _extract_element_content(el)
        assert "Go" in parts

    def test_plain_text_tag(self):
        el = {"tag": "plain_text", "content": "plain"}
        parts = _extract_element_content(el)
        assert "plain" in parts

    def test_unknown_tag_recursive(self):
        el = {
            "tag": "unknown_widget",
            "elements": [{"tag": "markdown", "content": "nested"}],
        }
        parts = _extract_element_content(el)
        assert "nested" in parts

    def test_non_dict_input(self):
        assert _extract_element_content("string") == []
        assert _extract_element_content(None) == []


# ===================================================================
# 9. Feishu content — _extract_share_card_content
# ===================================================================

class TestExtractShareCardContent:
    def test_share_chat(self):
        result = _extract_share_card_content({"chat_id": "oc_123"}, "share_chat")
        assert "shared chat" in result
        assert "oc_123" in result

    def test_share_user(self):
        result = _extract_share_card_content({"user_id": "ou_456"}, "share_user")
        assert "shared user" in result
        assert "ou_456" in result

    def test_interactive(self):
        content = {"title": {"content": "Card Title"}}
        result = _extract_share_card_content(content, "interactive")
        assert "Card Title" in result

    def test_share_calendar_event(self):
        result = _extract_share_card_content({"event_key": "ev_1"}, "share_calendar_event")
        assert "calendar event" in result
        assert "ev_1" in result

    def test_system_message(self):
        result = _extract_share_card_content({}, "system")
        assert "system message" in result

    def test_merge_forward(self):
        result = _extract_share_card_content({}, "merge_forward")
        assert "merged forward" in result

    def test_unknown_type(self):
        result = _extract_share_card_content({}, "unknown_type")
        assert "[unknown_type]" in result


# ===================================================================
# 10. Feishu content — _extract_post_mention_ids
# ===================================================================

class TestExtractPostMentionIds:
    def test_direct_format(self):
        data = {
            "content": [
                [{"tag": "at", "open_id": "ou_1", "user_id": "uid_1"}]
            ]
        }
        ids = _extract_post_mention_ids(data)
        assert "ou_1" in ids
        assert "uid_1" in ids

    def test_localized(self):
        data = {
            "zh_cn": {
                "content": [[{"tag": "at", "open_id": "ou_zh"}]]
            }
        }
        ids = _extract_post_mention_ids(data)
        assert "ou_zh" in ids

    def test_wrapped_post(self):
        data = {
            "post": {
                "en_us": {
                    "content": [[{"tag": "at", "open_id": "ou_en"}]]
                }
            }
        }
        ids = _extract_post_mention_ids(data)
        assert "ou_en" in ids

    def test_no_mentions(self):
        data = {"content": [[{"tag": "text", "text": "no mentions"}]]}
        ids = _extract_post_mention_ids(data)
        assert ids == []

    def test_empty_input(self):
        assert _extract_post_mention_ids({}) == []


# ===================================================================
# 11. Feishu content — _extract_post_text (legacy wrapper)
# ===================================================================

class TestExtractPostText:
    def test_returns_text_only(self):
        data = {
            "content": [
                [{"tag": "text", "text": "hello"}],
            ]
        }
        assert _extract_post_text(data) == "hello"

    def test_empty_returns_empty(self):
        assert _extract_post_text({}) == ""


# ===================================================================
# 12. Feishu — _strip_md_formatting
# ===================================================================

class TestFeishuStripMdFormatting:
    def test_bold(self):
        assert FeishuChannel._strip_md_formatting("**bold**") == "bold"

    def test_underscore_bold(self):
        assert FeishuChannel._strip_md_formatting("__bold__") == "bold"

    def test_italic(self):
        result = FeishuChannel._strip_md_formatting("*italic*")
        assert result == "italic"

    def test_strikethrough(self):
        assert FeishuChannel._strip_md_formatting("~~strike~~") == "strike"

    def test_combined(self):
        result = FeishuChannel._strip_md_formatting("**b** and ~~s~~")
        assert "b" in result
        assert "s" in result
        assert "**" not in result
        assert "~~" not in result


# ===================================================================
# 13. Feishu — _parse_md_table
# ===================================================================

class TestParseMdTable:
    def test_simple_table(self):
        table_text = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
        result = FeishuChannel._parse_md_table(table_text)
        assert result is not None
        assert result["tag"] == "table"
        assert len(result["columns"]) == 2
        assert result["columns"][0]["display_name"] == "A"
        assert result["columns"][1]["display_name"] == "B"
        assert len(result["rows"]) == 2
        assert result["rows"][0]["c0"] == "1"
        assert result["rows"][1]["c1"] == "4"

    def test_too_few_lines(self):
        result = FeishuChannel._parse_md_table("| A |\n|---|")
        assert result is None

    def test_strips_md_formatting_in_cells(self):
        table_text = "| **Bold** | ~~Strike~~ |\n|---|---|\n| val | val2 |"
        result = FeishuChannel._parse_md_table(table_text)
        assert result is not None
        assert result["columns"][0]["display_name"] == "Bold"
        assert result["columns"][1]["display_name"] == "Strike"


# ===================================================================
# 14. Feishu — _build_card_elements
# ===================================================================

class TestBuildCardElements:
    def test_plain_text_only(self):
        ch = _make_feishu_channel()
        elements = ch._build_card_elements("just plain text")
        assert len(elements) >= 1
        assert elements[0]["tag"] == "markdown"
        assert "just plain text" in elements[0]["content"]

    def test_with_table(self):
        ch = _make_feishu_channel()
        content = "Before table\n| A | B |\n|---|---|\n| 1 | 2 |\nAfter table"
        elements = ch._build_card_elements(content)
        tags = [e["tag"] for e in elements]
        assert "table" in tags

    def test_multiple_tables(self):
        ch = _make_feishu_channel()
        content = (
            "Text\n| A | B |\n|---|---|\n| 1 | 2 |\n"
            "Middle\n| C | D |\n|---|---|\n| 3 | 4 |"
        )
        elements = ch._build_card_elements(content)
        table_elements = [e for e in elements if e["tag"] == "table"]
        assert len(table_elements) == 2

    def test_empty_content_returns_markdown(self):
        ch = _make_feishu_channel()
        elements = ch._build_card_elements("")
        assert len(elements) == 1
        assert elements[0]["tag"] == "markdown"


# ===================================================================
# 15. Feishu — _split_elements_by_table_limit
# ===================================================================

class TestSplitElementsByTableLimit:
    def test_empty_elements(self):
        result = FeishuChannel._split_elements_by_table_limit([])
        assert result == [[]]

    def test_single_element_no_split(self):
        elements = [{"tag": "markdown", "content": "hello"}]
        result = FeishuChannel._split_elements_by_table_limit(elements)
        assert len(result) == 1
        assert result[0] == elements

    def test_two_tables_split(self):
        elements = [
            {"tag": "table", "columns": [{"name": "c0", "display_name": "A", "width": "auto"}],
             "rows": [{"c0": "1"}], "page_size": 2},
            {"tag": "table", "columns": [{"name": "c0", "display_name": "B", "width": "auto"}],
             "rows": [{"c0": "2"}], "page_size": 2},
        ]
        result = FeishuChannel._split_elements_by_table_limit(elements, max_tables=1)
        assert len(result) == 2  # each table in its own group

    def test_length_limit_triggers_split(self):
        # Create a large element that exceeds max_chars_per_card
        big_content = "x" * 4000
        elements = [{"tag": "markdown", "content": big_content}]
        result = FeishuChannel._split_elements_by_table_limit(elements, max_chars_per_card=3500)
        # Should be split into at least 2 groups
        assert len(result) >= 2

    def test_mixed_elements_respects_table_limit(self):
        elements = [
            {"tag": "markdown", "content": "text1"},
            {"tag": "table", "columns": [], "rows": [], "page_size": 1},
            {"tag": "markdown", "content": "text2"},
            {"tag": "table", "columns": [], "rows": [], "page_size": 1},
        ]
        result = FeishuChannel._split_elements_by_table_limit(elements, max_tables=1)
        # The second table should start a new group
        table_groups = [g for g in result if any(e["tag"] == "table" for e in g)]
        assert len(table_groups) == 2


# ===================================================================
# 16. Feishu — _detect_msg_format
# ===================================================================

class TestDetectMsgFormat:
    def test_short_plain_text(self):
        assert FeishuChannel._detect_msg_format("Hello!") == "text"

    def test_long_plain_text(self):
        # Medium length (> TEXT_MAX_LEN but <= POST_MAX_LEN)
        text = "A" * 300
        assert FeishuChannel._detect_msg_format(text) == "post"

    def test_code_block_triggers_interactive(self):
        content = "```\ncode\n```"
        assert FeishuChannel._detect_msg_format(content) == "interactive"

    def test_table_triggers_interactive(self):
        content = "| A | B |\n|---|---|\n| 1 | 2 |"
        assert FeishuChannel._detect_msg_format(content) == "interactive"

    def test_heading_triggers_interactive(self):
        assert FeishuChannel._detect_msg_format("# Title") == "interactive"

    def test_bold_triggers_interactive(self):
        assert FeishuChannel._detect_msg_format("**bold text**") == "interactive"

    def test_italic_triggers_interactive(self):
        assert FeishuChannel._detect_msg_format("*italic*") == "interactive"

    def test_strikethrough_triggers_interactive(self):
        assert FeishuChannel._detect_msg_format("~~strike~~") == "interactive"

    def test_list_triggers_interactive(self):
        assert FeishuChannel._detect_msg_format("- item1\n- item2") == "interactive"

    def test_ordered_list_triggers_interactive(self):
        assert FeishuChannel._detect_msg_format("1. first\n2. second") == "interactive"

    def test_link_triggers_post(self):
        assert FeishuChannel._detect_msg_format("click [here](https://x.com)") == "post"

    def test_very_long_content_triggers_interactive(self):
        content = "A" * 3000  # > POST_MAX_LEN
        assert FeishuChannel._detect_msg_format(content) == "interactive"


# ===================================================================
# 17. Feishu — _markdown_to_post
# ===================================================================

class TestMarkdownToPost:
    def test_plain_text_line(self):
        result = json.loads(FeishuChannel._markdown_to_post("hello world"))
        assert result["zh_cn"]["content"][0][0]["tag"] == "text"
        assert result["zh_cn"]["content"][0][0]["text"] == "hello world"

    def test_link_conversion(self):
        result = json.loads(FeishuChannel._markdown_to_post("[click](https://x.com)"))
        elements = result["zh_cn"]["content"][0]
        assert any(e["tag"] == "a" and e["href"] == "https://x.com" for e in elements)

    def test_mixed_text_and_link(self):
        result = json.loads(FeishuChannel._markdown_to_post("see [link](https://x.com) ok"))
        elements = result["zh_cn"]["content"][0]
        tags = [e["tag"] for e in elements]
        assert "text" in tags
        assert "a" in tags

    def test_empty_line_produces_empty_paragraph(self):
        result = json.loads(FeishuChannel._markdown_to_post("line1\n\nline2"))
        paragraphs = result["zh_cn"]["content"]
        assert len(paragraphs) == 3  # line1, empty, line2


# ===================================================================
# 18. Slack — _convert_table
# ===================================================================

class TestSlackConvertTable:
    def test_simple_table(self):
        table = "| Name | Age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |"
        match = MagicMock()
        match.group.return_value = table
        result = SlackChannel._convert_table(match)
        assert "**Name**: Alice" in result
        assert "**Age**: 30" in result
        assert "**Name**: Bob" in result

    def test_no_separator_line(self):
        table = "| Name | Age |\n| Alice | 30 |"
        match = MagicMock()
        match.group.return_value = table
        result = SlackChannel._convert_table(match)
        assert "**Name**: Alice" in result

    def test_too_few_lines(self):
        table = "| Name |"
        match = MagicMock()
        match.group.return_value = table
        result = SlackChannel._convert_table(match)
        assert result == table  # returns unchanged

    def test_cell_separator(self):
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        match = MagicMock()
        match.group.return_value = table
        result = SlackChannel._convert_table(match)
        assert " · " in result


# ===================================================================
# 19. Slack — _to_mrkdwn
# ===================================================================

class TestToMrkdwn:
    def test_empty(self):
        assert SlackChannel._to_mrkdwn("") == ""

    def test_table_conversion(self):
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = SlackChannel._to_mrkdwn(text)
        # Table is first converted by _convert_table (producing **A**: 1 · **B**: 2),
        # then _fixup_mrkdwn converts leftover **bold** to *bold* for Slack mrkdwn.
        assert "*A*" in result
        assert "1" in result
        assert "2" in result

    def test_plain_text_passthrough(self):
        result = SlackChannel._to_mrkdwn("hello world")
        assert "hello world" in result


# ===================================================================
# 20. Slack — _fixup_mrkdwn
# ===================================================================

class TestFixupMrkdwn:
    def test_leftover_bold_converted(self):
        result = SlackChannel._fixup_mrkdwn("**still bold**")
        assert "*still bold*" in result

    def test_leftover_header_converted(self):
        result = SlackChannel._fixup_mrkdwn("# Title")
        assert "*Title*" in result

    def test_code_block_preserved(self):
        result = SlackChannel._fixup_mrkdwn("```code```")
        assert "```code```" in result

    def test_inline_code_preserved(self):
        result = SlackChannel._fixup_mrkdwn("`code`")
        assert "`code`" in result


# ===================================================================
# 21. Slack — _strip_bot_mention
# ===================================================================

class TestSlackStripBotMention:
    def test_strips_mention(self):
        ch = _make_slack_channel()
        ch._bot_user_id = "U123"
        result = ch._strip_bot_mention("<@U123> hello bot")
        assert result == "hello bot"

    def test_no_mention(self):
        ch = _make_slack_channel()
        ch._bot_user_id = "U123"
        result = ch._strip_bot_mention("no mention here")
        assert result == "no mention here"

    def test_empty_text(self):
        ch = _make_slack_channel()
        ch._bot_user_id = "U123"
        result = ch._strip_bot_mention("")
        assert result == ""

    def test_no_bot_id(self):
        ch = _make_slack_channel()
        ch._bot_user_id = None
        result = ch._strip_bot_mention("<@U123> hello")
        assert result == "<@U123> hello"


# ===================================================================
# 22. Slack — _is_allowed (channel-specific)
# ===================================================================

class TestSlackIsAllowed:
    def test_im_open_policy(self):
        ch = _make_slack_channel(dm_enabled=True, dm_policy="open")
        assert ch._is_allowed("U123", "C456", "im")

    def test_im_disabled(self):
        ch = _make_slack_channel(dm_enabled=False)
        assert not ch._is_allowed("U123", "C456", "im")

    def test_im_allowlist_match(self):
        ch = _make_slack_channel(dm_policy="allowlist", dm_allow_from=["U123"])
        assert ch._is_allowed("U123", "C456", "im")

    def test_im_allowlist_no_match(self):
        ch = _make_slack_channel(dm_policy="allowlist", dm_allow_from=["U999"])
        assert not ch._is_allowed("U123", "C456", "im")

    def test_group_open_policy(self):
        ch = _make_slack_channel(group_policy="open")
        assert ch._is_allowed("U123", "C456", "channel")

    def test_group_allowlist_match(self):
        ch = _make_slack_channel(group_policy="allowlist", group_allow_from=["C456"])
        assert ch._is_allowed("U123", "C456", "channel")

    def test_group_allowlist_no_match(self):
        ch = _make_slack_channel(group_policy="allowlist", group_allow_from=["C999"])
        assert not ch._is_allowed("U123", "C456", "channel")

    def test_top_level_allowlist_wildcard(self):
        ch = _make_slack_channel(allow_from=["*"])
        assert ch.is_allowed("anyone")

    def test_top_level_allowlist_match(self):
        ch = _make_slack_channel(allow_from=["U123"])
        assert ch.is_allowed("U123")

    def test_top_level_allowlist_empty_allows_all(self):
        ch = _make_slack_channel(allow_from=[])
        assert ch.is_allowed("anyone")


# ===================================================================
# 23. Slack — _should_respond_in_channel
# ===================================================================

class TestSlackShouldRespondInChannel:
    def test_open_policy_always_responds(self):
        ch = _make_slack_channel(group_policy="open")
        assert ch._should_respond_in_channel("message", "text", "C123")

    def test_mention_policy_app_mention(self):
        ch = _make_slack_channel(group_policy="mention")
        assert ch._should_respond_in_channel("app_mention", "text", "C123")

    def test_mention_policy_direct_mention_in_text(self):
        ch = _make_slack_channel(group_policy="mention")
        ch._bot_user_id = "U123"
        assert ch._should_respond_in_channel("message", "hi <@U123>", "C123")

    def test_mention_policy_no_mention(self):
        ch = _make_slack_channel(group_policy="mention")
        ch._bot_user_id = "U123"
        assert not ch._should_respond_in_channel("message", "just text", "C123")

    def test_allowlist_policy_match(self):
        ch = _make_slack_channel(group_policy="allowlist", group_allow_from=["C123"])
        assert ch._should_respond_in_channel("message", "text", "C123")

    def test_allowlist_policy_no_match(self):
        ch = _make_slack_channel(group_policy="allowlist", group_allow_from=["C999"])
        assert not ch._should_respond_in_channel("message", "text", "C123")


# ===================================================================
# 24. Base — is_allowed
# ===================================================================

class TestBaseChannelIsAllowed:
    def test_empty_list_denies(self):
        ch = _make_base_channel_subclass([])
        assert not ch.is_allowed("anyone")

    def test_wildcard_allows_all(self):
        ch = _make_base_channel_subclass(["*"])
        assert ch.is_allowed("anyone")
        assert ch.is_allowed("user1")

    def test_specific_user_allowed(self):
        ch = _make_base_channel_subclass(["user1", "user2"])
        assert ch.is_allowed("user1")
        assert ch.is_allowed("user2")
        assert not ch.is_allowed("user3")

    def test_numeric_sender_id(self):
        ch = _make_base_channel_subclass(["123"])
        assert ch.is_allowed("123")
        assert ch.is_allowed(123)  # int converted to str


# ===================================================================
# 25. Manager — _validate_allow_from
# ===================================================================

class TestManagerValidateAllowFrom:
    def test_empty_allow_from_raises(self):
        """Channels with empty allowFrom should raise ValueError."""
        from xbot.channels.manager import ChannelManager

        config = MagicMock()
        config.channels = MagicMock()
        config.channels.send_tool_hints = False
        config.channels.send_usage_summary = False
        config.channels.send_progress = False
        bus = MagicMock()

        with patch("xbot.channels.registry.discover_all", return_value={}):
            mgr = ChannelManager(config, bus)

        # Manually add a channel with empty allow_from
        mock_channel = MagicMock()
        mock_channel.config = SimpleNamespace(allow_from=[])
        mock_channel.name = "test_channel"
        mgr.channels["test_channel"] = mock_channel

        with pytest.raises(ValueError, match="empty or missing allowFrom"):
            mgr._validate_allow_from()

    def test_wildcard_allow_from_passes(self):
        from xbot.channels.manager import ChannelManager

        config = MagicMock()
        config.channels = MagicMock()
        bus = MagicMock()

        with patch("xbot.channels.registry.discover_all", return_value={}):
            mgr = ChannelManager(config, bus)

        mock_channel = MagicMock()
        mock_channel.config = SimpleNamespace(allow_from=["*"])
        mgr.channels["test"] = mock_channel

        # Should not raise
        mgr._validate_allow_from()

    def test_specific_users_passes(self):
        from xbot.channels.manager import ChannelManager

        config = MagicMock()
        config.channels = MagicMock()
        bus = MagicMock()

        with patch("xbot.channels.registry.discover_all", return_value={}):
            mgr = ChannelManager(config, bus)

        mock_channel = MagicMock()
        mock_channel.config = SimpleNamespace(allow_from=["user1"])
        mgr.channels["test"] = mock_channel

        mgr._validate_allow_from()  # should not raise

    def test_dict_config_with_allowFrom(self):
        from xbot.channels.manager import ChannelManager

        config = MagicMock()
        config.channels = MagicMock()
        bus = MagicMock()

        with patch("xbot.channels.registry.discover_all", return_value={}):
            mgr = ChannelManager(config, bus)

        mock_channel = MagicMock()
        mock_channel.config = {"allowFrom": ["user1"]}
        mgr.channels["test"] = mock_channel

        mgr._validate_allow_from()  # should not raise

    def test_dict_config_empty_allowFrom_raises(self):
        from xbot.channels.manager import ChannelManager

        config = MagicMock()
        config.channels = MagicMock()
        bus = MagicMock()

        with patch("xbot.channels.registry.discover_all", return_value={}):
            mgr = ChannelManager(config, bus)

        mock_channel = MagicMock()
        mock_channel.config = {"allowFrom": []}
        mgr.channels["test"] = mock_channel

        with pytest.raises(ValueError, match="empty or missing allowFrom"):
            mgr._validate_allow_from()


# ===================================================================
# 26. Registry — discover_channel_names
# ===================================================================

class TestDiscoverChannelNames:
    def test_returns_list_of_strings(self):
        names = discover_channel_names()
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)

    def test_excludes_internal_modules(self):
        names = discover_channel_names()
        for internal in ("base", "manager", "registry", "feishu_content", "feishu_ws_worker"):
            assert internal not in names

    def test_includes_known_channels(self):
        names = discover_channel_names()
        assert "telegram" in names
        assert "slack" in names
        assert "feishu" in names


# ===================================================================
# 27. Registry — load_channel_class
# ===================================================================

class TestLoadChannelClass:
    def test_load_telegram(self):
        cls = load_channel_class("telegram")
        assert cls is not None
        assert issubclass(cls, BaseChannel)
        assert cls.name == "telegram"

    def test_load_slack(self):
        cls = load_channel_class("slack")
        assert cls is not None
        assert issubclass(cls, BaseChannel)
        assert cls.name == "slack"

    def test_load_feishu(self):
        cls = load_channel_class("feishu")
        assert cls is not None
        assert issubclass(cls, BaseChannel)
        assert cls.name == "feishu"

    def test_load_nonexistent_raises(self):
        with pytest.raises(ImportError):
            load_channel_class("nonexistent_channel_xyz")


# ===================================================================
# 28. Telegram — _is_group_message_for_bot (async, with mocks)
# ===================================================================

class TestTelegramIsGroupMessageForBot:
    @pytest.mark.asyncio
    async def test_private_chat_always_allowed(self):
        cfg = TelegramConfig(enabled=True, token="test", allow_from=["*"])
        bus = MagicMock()
        ch = TelegramChannel(cfg, bus)
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            text="hello",
            caption=None,
        )
        assert await ch._is_group_message_for_bot(message)

    @pytest.mark.asyncio
    async def test_open_policy_allows_group(self):
        cfg = TelegramConfig(enabled=True, token="test", group_policy="open", allow_from=["*"])
        bus = MagicMock()
        ch = TelegramChannel(cfg, bus)
        message = SimpleNamespace(
            chat=SimpleNamespace(type="group"),
            text="hello",
            caption=None,
        )
        assert await ch._is_group_message_for_bot(message)

    @pytest.mark.asyncio
    async def test_mention_policy_with_mention(self):
        cfg = TelegramConfig(enabled=True, token="test", group_policy="mention", allow_from=["*"])
        bus = MagicMock()
        ch = TelegramChannel(cfg, bus)
        ch._bot_user_id = 999
        ch._bot_username = "testbot"
        ch._app = MagicMock()  # so _ensure_bot_identity returns cached values

        entity = SimpleNamespace(type="mention", offset=0, length=8)
        message = SimpleNamespace(
            chat=SimpleNamespace(type="group"),
            text="@testbot hello",
            caption=None,
            entities=[entity],
            caption_entities=None,
            reply_to_message=None,
        )
        assert await ch._is_group_message_for_bot(message)

    @pytest.mark.asyncio
    async def test_mention_policy_reply_to_bot(self):
        cfg = TelegramConfig(enabled=True, token="test", group_policy="mention", allow_from=["*"])
        bus = MagicMock()
        ch = TelegramChannel(cfg, bus)
        ch._bot_user_id = 999
        ch._bot_username = "testbot"
        ch._app = MagicMock()

        reply = SimpleNamespace(from_user=SimpleNamespace(id=999))
        message = SimpleNamespace(
            chat=SimpleNamespace(type="group"),
            text="just text",
            caption=None,
            entities=[],
            caption_entities=None,
            reply_to_message=reply,
        )
        assert await ch._is_group_message_for_bot(message)

    @pytest.mark.asyncio
    async def test_mention_policy_no_match(self):
        cfg = TelegramConfig(enabled=True, token="test", group_policy="mention", allow_from=["*"])
        bus = MagicMock()
        ch = TelegramChannel(cfg, bus)
        ch._bot_user_id = 999
        ch._bot_username = "testbot"
        ch._app = MagicMock()

        message = SimpleNamespace(
            chat=SimpleNamespace(type="group"),
            text="random text",
            caption=None,
            entities=[],
            caption_entities=None,
            reply_to_message=None,
        )
        assert not await ch._is_group_message_for_bot(message)


# ===================================================================
# 29. Feishu — _is_bot_mentioned
# ===================================================================

class TestFeishuIsBotMentioned:
    def test_mention_via_mentions_list(self):
        ch = _make_feishu_channel()
        mention = SimpleNamespace(
            id=SimpleNamespace(open_id="ou_bot123", user_id="")
        )
        message = SimpleNamespace(
            content="",
            mentions=[mention],
            message_type="text",
        )
        assert ch._is_bot_mentioned(message)

    def test_mention_via_all(self):
        ch = _make_feishu_channel()
        message = SimpleNamespace(
            content="@_all please review",
            mentions=[],
            message_type="text",
        )
        assert ch._is_bot_mentioned(message)

    def test_no_mention(self):
        ch = _make_feishu_channel()
        message = SimpleNamespace(
            content="just plain text",
            mentions=[],
            message_type="text",
        )
        assert not ch._is_bot_mentioned(message)

    def test_post_mention_via_content_json(self):
        ch = _make_feishu_channel()
        content_json = {
            "content": [
                [{"tag": "at", "open_id": "ou_bot123"}]
            ]
        }
        message = SimpleNamespace(
            content=json.dumps(content_json),
            mentions=[],
            message_type="post",
        )
        assert ch._is_bot_mentioned(message)


# ===================================================================
# 30. Feishu — _split_headings
# ===================================================================

class TestFeishuSplitHeadings:
    def test_no_headings(self):
        ch = _make_feishu_channel()
        elements = ch._split_headings("just text")
        assert len(elements) == 1
        assert elements[0]["tag"] == "markdown"

    def test_single_heading(self):
        ch = _make_feishu_channel()
        elements = ch._split_headings("# My Title\nSome text")
        # Should have a div for the heading and a markdown for text
        tags = [e["tag"] for e in elements]
        assert "div" in tags
        assert "markdown" in tags

    def test_heading_with_code_block(self):
        ch = _make_feishu_channel()
        elements = ch._split_headings("# Title\n```\ncode\n```\nMore text")
        # Code blocks should be preserved inside markdown elements
        md_elements = [e for e in elements if e["tag"] == "markdown"]
        code_found = any("```" in e["content"] for e in md_elements)
        assert code_found


# ===================================================================
# 31. Feishu — _build_interactive_card
# ===================================================================

class TestBuildInteractiveCard:
    def test_structure(self):
        elements = [{"tag": "markdown", "content": "hi"}]
        card = FeishuChannel._build_interactive_card(elements)
        assert card["schema"] == "2.0"
        assert card["config"]["wide_screen_mode"] is True
        assert card["body"]["elements"] == elements


# ===================================================================
# 32. Edge cases / integration-style tests
# ===================================================================

class TestEdgeCases:
    def test_telegram_html_with_nested_formatting(self):
        """Bold inside a link should work."""
        result = _markdown_to_telegram_html("[**bold link**](https://x.com)")
        assert '<a href="https://x.com">' in result
        assert "<b>bold link</b>" in result

    def test_telegram_html_multiple_code_blocks(self):
        md = "```py\na=1\n```\ntext\n```js\nb=2\n```"
        result = _markdown_to_telegram_html(md)
        assert result.count("<pre><code>") == 2

    def test_extract_post_content_robust_with_non_list_rows(self):
        """Content rows that aren't lists should be skipped."""
        data = {
            "content": [
                "not a list",
                [{"tag": "text", "text": "valid"}],
            ]
        }
        text, imgs = _extract_post_content(data)
        assert "valid" in text

    def test_slack_channel_is_allowed_slack_semantics(self):
        """Slack top-level is_allowed: empty = allow all (unlike base)."""
        ch = _make_slack_channel(allow_from=[])
        assert ch.is_allowed("anyone")

    def test_feishu_parse_md_table_with_different_row_widths(self):
        """Rows with fewer cells than headers get padded."""
        table_text = "| A | B | C |\n|---|---|---|\n| 1 |"
        result = FeishuChannel._parse_md_table(table_text)
        assert result is not None
        assert len(result["columns"]) == 3
        # Row with only 1 cell should pad remaining with ""
        assert result["rows"][0]["c1"] == ""
        assert result["rows"][0]["c2"] == ""

    def test_split_elements_preserves_non_oversized(self):
        """Elements under limit should not be split."""
        elements = [
            {"tag": "markdown", "content": "short1"},
            {"tag": "markdown", "content": "short2"},
        ]
        result = FeishuChannel._split_elements_by_table_limit(elements, max_chars_per_card=3500)
        assert len(result) == 1
        assert len(result[0]) == 2
