from xbot_codex.channels.telegram import TELEGRAM_MAX_MESSAGE_LEN, split_telegram_text


def test_split_telegram_text_splits_long_messages() -> None:
    text = "a" * (TELEGRAM_MAX_MESSAGE_LEN + 10)

    parts = split_telegram_text(text)

    assert len(parts) == 2
    assert len(parts[0]) == TELEGRAM_MAX_MESSAGE_LEN
    assert parts[0] + parts[1] == text
