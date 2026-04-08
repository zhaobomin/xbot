"""Tests for Feishu message reply (quote) feature."""
import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.bus.events import OutboundMessage
from xbot.bus.queue import MessageBus
from xbot.channels.feishu import FeishuChannel, FeishuConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feishu_channel(reply_to_message: bool = False) -> FeishuChannel:
    config = FeishuConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        allow_from=["*"],
        reply_to_message=reply_to_message,
    )
    channel = FeishuChannel(config, MessageBus())
    channel._client = MagicMock()
    # _loop is only used by the WebSocket thread bridge; not needed for unit tests
    channel._loop = None
    return channel


def _make_feishu_event(
    *,
    message_id: str = "om_001",
    chat_id: str = "oc_abc",
    chat_type: str = "p2p",
    msg_type: str = "text",
    content: str = '{"text": "hello"}',
    sender_open_id: str = "ou_alice",
    parent_id: str | None = None,
    root_id: str | None = None,
):
    message = SimpleNamespace(
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        message_type=msg_type,
        content=content,
        parent_id=parent_id,
        root_id=root_id,
        mentions=[],
    )
    sender = SimpleNamespace(
        sender_type="user",
        sender_id=SimpleNamespace(open_id=sender_open_id),
    )
    return SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))


def _make_feishu_worker_payload(
    *,
    message_id: str = "om_001",
    chat_id: str = "oc_abc",
    chat_type: str = "p2p",
    msg_type: str = "text",
    content: str = '{"text": "hello"}',
    sender_open_id: str = "ou_alice",
    parent_id: str | None = None,
    root_id: str | None = None,
):
    return {
        "event": {
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": chat_type,
                "message_type": msg_type,
                "content": content,
                "parent_id": parent_id,
                "root_id": root_id,
                "mentions": [],
            },
            "sender": {
                "sender_type": "user",
                "sender_id": {"open_id": sender_open_id},
            },
        }
    }


def _make_get_message_response(text: str, msg_type: str = "text", success: bool = True):
    """Build a fake im.v1.message.get response object."""
    body = SimpleNamespace(content=json.dumps({"text": text}))
    item = SimpleNamespace(msg_type=msg_type, body=body)
    data = SimpleNamespace(items=[item])
    resp = MagicMock()
    resp.success.return_value = success
    resp.data = data
    resp.code = 0
    resp.msg = "ok"
    return resp


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

def test_feishu_config_reply_to_message_defaults_false() -> None:
    assert FeishuConfig().reply_to_message is False


def test_feishu_config_reply_to_message_can_be_enabled() -> None:
    config = FeishuConfig(reply_to_message=True)
    assert config.reply_to_message is True


# ---------------------------------------------------------------------------
# _get_message_content_sync tests
# ---------------------------------------------------------------------------

def test_get_message_content_sync_returns_reply_prefix() -> None:
    channel = _make_feishu_channel()
    channel._client.im.v1.message.get.return_value = _make_get_message_response("what time is it?")

    result = channel._get_message_content_sync("om_parent")

    assert result == "[Reply to: what time is it?]"


def test_get_message_content_sync_truncates_long_text() -> None:
    channel = _make_feishu_channel()
    long_text = "x" * (FeishuChannel._REPLY_CONTEXT_MAX_LEN + 50)
    channel._client.im.v1.message.get.return_value = _make_get_message_response(long_text)

    result = channel._get_message_content_sync("om_parent")

    assert result is not None
    assert result.endswith("...]")
    inner = result[len("[Reply to: ") : -1]
    assert len(inner) == FeishuChannel._REPLY_CONTEXT_MAX_LEN + len("...")


def test_get_message_content_sync_returns_none_on_api_failure() -> None:
    channel = _make_feishu_channel()
    resp = MagicMock()
    resp.success.return_value = False
    resp.code = 230002
    resp.msg = "bot not in group"
    channel._client.im.v1.message.get.return_value = resp

    result = channel._get_message_content_sync("om_parent")

    assert result is None


def test_get_message_content_sync_returns_none_for_non_text_type() -> None:
    channel = _make_feishu_channel()
    body = SimpleNamespace(content=json.dumps({"image_key": "img_1"}))
    item = SimpleNamespace(msg_type="image", body=body)
    data = SimpleNamespace(items=[item])
    resp = MagicMock()
    resp.success.return_value = True
    resp.data = data
    channel._client.im.v1.message.get.return_value = resp

    result = channel._get_message_content_sync("om_parent")

    assert result is None


def test_get_message_content_sync_returns_none_when_empty_text() -> None:
    channel = _make_feishu_channel()
    channel._client.im.v1.message.get.return_value = _make_get_message_response("   ")

    result = channel._get_message_content_sync("om_parent")

    assert result is None


# ---------------------------------------------------------------------------
# _reply_message_sync tests
# ---------------------------------------------------------------------------

def test_reply_message_sync_returns_true_on_success() -> None:
    channel = _make_feishu_channel()
    resp = MagicMock()
    resp.success.return_value = True
    channel._client.im.v1.message.reply.return_value = resp

    ok = channel._reply_message_sync("om_parent", "text", '{"text":"hi"}')

    assert ok is True
    channel._client.im.v1.message.reply.assert_called_once()


def test_reply_message_sync_returns_false_on_api_error() -> None:
    channel = _make_feishu_channel()
    resp = MagicMock()
    resp.success.return_value = False
    resp.code = 400
    resp.msg = "bad request"
    resp.get_log_id.return_value = "log_x"
    channel._client.im.v1.message.reply.return_value = resp

    ok = channel._reply_message_sync("om_parent", "text", '{"text":"hi"}')

    assert ok is False


def test_reply_message_sync_returns_false_on_exception() -> None:
    channel = _make_feishu_channel()
    channel._client.im.v1.message.reply.side_effect = RuntimeError("network error")

    ok = channel._reply_message_sync("om_parent", "text", '{"text":"hi"}')

    assert ok is False


@pytest.mark.asyncio
async def test_on_message_falls_back_to_unknown_sender_when_open_id_missing() -> None:
    channel = _make_feishu_channel()
    bus = channel.bus
    data = _make_feishu_event(sender_open_id=None)

    await channel._on_message(data)

    msg = await bus.consume_inbound()
    assert msg.sender_id == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "expected_msg_type"),
    [
        ("voice.opus", "audio"),
        ("clip.mp4", "video"),
        ("report.pdf", "file"),
    ],
)
async def test_send_uses_expected_feishu_msg_type_for_uploaded_files(
    tmp_path: Path, filename: str, expected_msg_type: str
) -> None:
    file_path = tmp_path / filename
    file_path.write_bytes(b"demo")

    send_calls: list[tuple[str, str, str, str]] = []

    def _record_send(receive_id_type: str, receive_id: str, msg_type: str, content: str) -> None:
        send_calls.append((receive_id_type, receive_id, msg_type, content))


@pytest.mark.asyncio
async def test_on_message_does_not_block_event_loop_when_dedup_lock_is_held() -> None:
    channel = _make_feishu_channel()
    channel._dedup_lock.acquire()

    data = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id="om_blocked",
                chat_id="oc_abc",
                chat_type="p2p",
                message_type="text",
                content='{"text":"hello"}',
                parent_id=None,
                root_id=None,
                mentions=[],
            ),
            sender=SimpleNamespace(
                sender_type="bot",
                sender_id=SimpleNamespace(open_id="ou_bot"),
            ),
        )
    )

    threading.Timer(0.2, channel._dedup_lock.release).start()
    task = asyncio.create_task(channel._on_message(data))
    start = time.perf_counter()
    await asyncio.sleep(0.02)
    elapsed = time.perf_counter() - start
    await asyncio.wait_for(task, timeout=1.0)

    assert elapsed < 0.1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "expected_name"),
    [
        ("../../escape.txt", "escape.txt"),
        ("..\\\\..\\\\escape.txt", "escape.txt"),
        ("report.pdf", "report.pdf"),
    ],
)
async def test_download_and_save_media_sanitizes_filename(
    tmp_path: Path, filename: str, expected_name: str
) -> None:
    channel = _make_feishu_channel()

    with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
        channel._download_file_sync = MagicMock(return_value=(b"demo", filename))
        file_path, content_text = await channel._download_and_save_media(
            "file",
            {"file_key": "file-key"},
            message_id="om_001",
        )

    assert file_path == str(tmp_path / expected_name)
    assert (tmp_path / expected_name).read_bytes() == b"demo"
    assert content_text == f"[file: {expected_name}]"


# ---------------------------------------------------------------------------
# send() — reply routing tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_uses_reply_api_when_configured() -> None:
    channel = _make_feishu_channel(reply_to_message=True)

    reply_resp = MagicMock()
    reply_resp.success.return_value = True
    channel._client.im.v1.message.reply.return_value = reply_resp

    await channel.send(OutboundMessage(
        channel="feishu",
        chat_id="oc_abc",
        content="hello",
        metadata={"message_id": "om_001"},
    ))

    channel._client.im.v1.message.reply.assert_called_once()
    channel._client.im.v1.message.create.assert_not_called()


@pytest.mark.asyncio
async def test_send_uses_create_api_when_reply_disabled() -> None:
    channel = _make_feishu_channel(reply_to_message=False)

    create_resp = MagicMock()
    create_resp.success.return_value = True
    channel._client.im.v1.message.create.return_value = create_resp

    await channel.send(OutboundMessage(
        channel="feishu",
        chat_id="oc_abc",
        content="hello",
        metadata={"message_id": "om_001"},
    ))

    channel._client.im.v1.message.create.assert_called_once()
    channel._client.im.v1.message.reply.assert_not_called()


@pytest.mark.asyncio
async def test_send_uses_create_api_when_no_message_id() -> None:
    channel = _make_feishu_channel(reply_to_message=True)

    create_resp = MagicMock()
    create_resp.success.return_value = True
    channel._client.im.v1.message.create.return_value = create_resp

    await channel.send(OutboundMessage(
        channel="feishu",
        chat_id="oc_abc",
        content="hello",
        metadata={},
    ))

    channel._client.im.v1.message.create.assert_called_once()
    channel._client.im.v1.message.reply.assert_not_called()


@pytest.mark.asyncio
async def test_send_skips_reply_for_progress_messages() -> None:
    channel = _make_feishu_channel(reply_to_message=True)

    create_resp = MagicMock()
    create_resp.success.return_value = True
    channel._client.im.v1.message.create.return_value = create_resp

    await channel.send(OutboundMessage(
        channel="feishu",
        chat_id="oc_abc",
        content="thinking...",
        metadata={"message_id": "om_001", "_progress": True},
    ))

    channel._client.im.v1.message.create.assert_called_once()
    channel._client.im.v1.message.reply.assert_not_called()


@pytest.mark.asyncio
async def test_send_fallback_to_create_when_reply_fails() -> None:
    channel = _make_feishu_channel(reply_to_message=True)

    reply_resp = MagicMock()
    reply_resp.success.return_value = False
    reply_resp.code = 400
    reply_resp.msg = "error"
    reply_resp.get_log_id.return_value = "log_x"
    channel._client.im.v1.message.reply.return_value = reply_resp

    create_resp = MagicMock()
    create_resp.success.return_value = True
    channel._client.im.v1.message.create.return_value = create_resp

    await channel.send(OutboundMessage(
        channel="feishu",
        chat_id="oc_abc",
        content="hello",
        metadata={"message_id": "om_001"},
    ))

    # reply attempted first, then falls back to create
    channel._client.im.v1.message.reply.assert_called_once()
    channel._client.im.v1.message.create.assert_called_once()


# ---------------------------------------------------------------------------
# _on_message — parent_id / root_id metadata tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_message_captures_parent_and_root_id_in_metadata() -> None:
    channel = _make_feishu_channel()
    channel._processed_message_ids.clear()
    channel._client.im.v1.message.react.return_value = MagicMock(success=lambda: True)

    captured = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    channel._handle_message = _capture

    with patch.object(channel, "_add_reaction", return_value=None):
        await channel._on_message(
            _make_feishu_event(
                parent_id="om_parent",
                root_id="om_root",
            )
        )

    assert len(captured) == 1
    meta = captured[0]["metadata"]
    assert meta["parent_id"] == "om_parent"
    assert meta["root_id"] == "om_root"
    assert meta["message_id"] == "om_001"


@pytest.mark.asyncio
async def test_on_message_parent_and_root_id_none_when_absent() -> None:
    channel = _make_feishu_channel()
    channel._processed_message_ids.clear()

    captured = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    channel._handle_message = _capture

    with patch.object(channel, "_add_reaction", return_value=None):
        await channel._on_message(_make_feishu_event())

    assert len(captured) == 1
    meta = captured[0]["metadata"]
    assert meta["parent_id"] is None
    assert meta["root_id"] is None


@pytest.mark.asyncio
async def test_on_message_prepends_reply_context_when_parent_id_present() -> None:
    channel = _make_feishu_channel()
    channel._processed_message_ids.clear()
    channel._client.im.v1.message.get.return_value = _make_get_message_response("original question")

    captured = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    channel._handle_message = _capture

    with patch.object(channel, "_add_reaction", return_value=None):
        await channel._on_message(
            _make_feishu_event(
                content='{"text": "my answer"}',
                parent_id="om_parent",
            )
        )

    assert len(captured) == 1
    content = captured[0]["content"]
    assert content.startswith("[Reply to: original question]")
    assert "my answer" in content


@pytest.mark.asyncio
async def test_on_message_no_extra_api_call_when_no_parent_id() -> None:
    channel = _make_feishu_channel()
    channel._processed_message_ids.clear()

    captured = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    channel._handle_message = _capture

    with patch.object(channel, "_add_reaction", return_value=None):
        await channel._on_message(_make_feishu_event())

    channel._client.im.v1.message.get.assert_not_called()
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_dispatch_worker_event_forwards_message_payload() -> None:
    channel = _make_feishu_channel()
    channel._processed_message_ids.clear()
    channel._on_message = AsyncMock()

    await channel._dispatch_worker_event(
        {"type": "message", "payload": _make_feishu_worker_payload()}
    )

    channel._on_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_worker_event_deduplicates_by_message_id() -> None:
    channel = _make_feishu_channel()
    channel._processed_message_ids.clear()
    channel._on_message = AsyncMock()

    event = {"type": "message", "payload": _make_feishu_worker_payload(message_id="om_dup")}

    await channel._dispatch_worker_event(event)
    await channel._dispatch_worker_event(event)

    channel._on_message.assert_awaited_once()
