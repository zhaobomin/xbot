from __future__ import annotations

from xbot_codex.channels.feishu import FeishuChannel
from xbot_codex.channels.telegram import TelegramChannel
from xbot_codex.config import FeishuConfig, TelegramConfig


def test_telegram_group_policy_requires_mention() -> None:
    channel = TelegramChannel(TelegramConfig(enabled=True, token="t", allow_from=["*"], group_policy="mention"))

    assert channel.should_accept_text(chat_type="private", text="hello") is True
    assert channel.should_accept_text(chat_type="group", text="hello") is False
    assert channel.should_accept_text(chat_type="group", text="@mybot hello") is True


def test_feishu_group_policy_requires_mention() -> None:
    channel = FeishuChannel(FeishuConfig(enabled=True, app_id="id", app_secret="secret", allow_from=["*"], group_policy="mention"))

    assert channel.should_accept_text(is_group=False, mentioned=False) is True
    assert channel.should_accept_text(is_group=True, mentioned=False) is False
    assert channel.should_accept_text(is_group=True, mentioned=True) is True
