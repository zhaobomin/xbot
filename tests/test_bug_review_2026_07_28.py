from pathlib import Path

import pytest

from xbot.crew.planner.utils import LLMResponseParser
from xbot.memory.reme import ReMeMemoryStore
from xbot.memory.store import MemoryStore
from xbot.platform.config.loader import set_config_path
from xbot.platform.config.paths import get_media_dir
from xbot.platform.utils.helpers import detect_audio_mime


def test_detect_audio_mime_recognizes_id3_mp3() -> None:
    data = b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb"

    assert detect_audio_mime(data) == "audio/mp3"


@pytest.mark.parametrize(
    "channel",
    [
        "../escape",
        "/absolute",
        ".",
        "..",
        "nested/name",
        r"nested\name",
    ],
)
def test_get_media_dir_rejects_unsafe_channel_namespace(
    tmp_path: Path,
    channel: str,
) -> None:
    config_path = tmp_path / "config.json"
    set_config_path(config_path)
    try:
        with pytest.raises(ValueError, match="media channel"):
            get_media_dir(channel)
    finally:
        set_config_path(None)

    assert not (tmp_path / "media").exists()


@pytest.mark.parametrize(
    "formatter",
    [MemoryStore._format_messages, ReMeMemoryStore._format_messages],
)
def test_memory_formatter_handles_none_timestamp(formatter) -> None:
    output = formatter(
        [{"role": "user", "content": "hello", "timestamp": None}]
    )

    assert output == "[?] USER: hello"


@pytest.mark.parametrize(
    "line",
    [
        "https://example.com:8443/path",
        "12:30",
    ],
)
def test_string_list_fallback_preserves_non_description_colons(line: str) -> None:
    assert LLMResponseParser.parse_string_list(f"- {line}") == [line]
