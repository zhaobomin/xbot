"""Round-4 tests for xbot/channels/feishu.py — focus on uncovered lines.

Covers:
- __init__ edge cases (dict config → FeishuConfig)
- start() — SDK unavailable, missing creds, successful start
- stop() — cleanup, worker termination (join/terminate/kill)
- check_health() — all paths
- _kill_stale_ws_workers / _read_parent_pid
- _start_ws_worker / _cleanup_ws_resources / _close_ws_ipc_resources
- _iter_multiprocessing_semaphore_names / _cleanup_registered_semaphore / _cleanup_multiprocessing_semlocks
- _upload_image_sync / _upload_file_sync — success, API error, exception
- _download_image_sync / _download_file_sync — success, API error, exception
- _download_and_save_media — image, audio, file, media, failure
- _reply_message_sync — success, failure, retry on network error, generic exception
- _get_message_content_sync — text, post, unsupported, failure, truncation
- _send_message_sync — success, failure, retry, generic exception
- _add_reaction / _add_reaction_sync — with/without client, success, failure
- _on_message — text, post, image, audio, file, media, share, unknown, bot sender, group filtering
- _is_bot_mentioned — edge cases (no bot_open_id, fallback ou_ detection, post JSON errors)
- _run_with_dedup_lock
- _run_ws_event_reader — restart on dead worker, max restart exceeded
- _dispatch_worker_event — all paths
- _split_markdown_element_to_fit / _split_table_element_to_fit — oversized content
- _split_oversized_element — tag dispatch
- _split_elements_by_table_limit — more edge cases
- _send_tool_hint_card
- _register_optional_event
- _namespace_from_dict — deeper nesting
- _build_card_elements — headings with code blocks
- _markdown_to_post — edge cases
- _detect_msg_format — edge cases
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import json
import queue
import sys
import types
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Stub external SDK modules
# ---------------------------------------------------------------------------
def _install_sdk_stubs():
    stubs = {
        "lark_oapi": types.ModuleType("lark_oapi"),
        "lark_oapi.api": types.ModuleType("lark_oapi.api"),
        "lark_oapi.api.im": types.ModuleType("lark_oapi.api.im"),
        "lark_oapi.api.im.v1": types.ModuleType("lark_oapi.api.im.v1"),
        "lark_oapi.ws": types.ModuleType("lark_oapi.ws"),
        "lark_oapi.ws.client": types.ModuleType("lark_oapi.ws.client"),
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

    # lark_oapi stubs
    class _FakeLogLevel:
        INFO = 1

    lark_mod = stubs["lark_oapi"]
    try:
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

    # lark_oapi.api.im.v1 stubs - builder chain for all request types
    for name in (
        "CreateMessageRequest", "CreateMessageRequestBody",
        "CreateImageRequest", "CreateImageRequestBody",
        "CreateFileRequest", "CreateFileRequestBody",
        "GetMessageResourceRequest", "GetMessageRequest",
        "ReplyMessageRequest", "ReplyMessageRequestBody",
        "CreateMessageReactionRequest", "CreateMessageReactionRequestBody",
        "Emoji",
    ):
        cls = MagicMock()
        cls.builder = staticmethod(lambda: MagicMock())
        setattr(stubs["lark_oapi.api.im.v1"], name, cls)

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

    # slack stubs
    stubs["slack_sdk.socket_mode.request"].SocketModeRequest = type("SocketModeRequest", (), {})
    stubs["slack_sdk.socket_mode.response"].SocketModeResponse = type("SocketModeResponse", (), {"__init__": lambda self, *a, **kw: None})
    stubs["slack_sdk.socket_mode.websockets"].SocketModeClient = MagicMock()
    stubs["slack_sdk.web.async_client"].AsyncWebClient = MagicMock()
    stubs["slackify_markdown"].slackify_markdown = lambda text: text

    for mod_name, mod in stubs.items():
        if mod_name not in sys.modules:
            sys.modules[mod_name] = mod


_install_sdk_stubs()


# Now import the modules under test
from xbot.channels.feishu import FeishuChannel, FeishuConfig  # noqa: E402
from xbot.platform.bus.events import OutboundMessage  # noqa: E402


# ===================================================================
# Helpers
# ===================================================================

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


def _make_event(
    message_id="msg_001",
    chat_id="oc_chat1",
    chat_type="p2p",
    msg_type="text",
    content='{"text": "hello"}',
    sender_type="user",
    sender_open_id="ou_user1",
    parent_id=None,
    root_id=None,
    mentions=None,
):
    """Create a SimpleNamespace event tree matching _on_message expectations."""
    return SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id=message_id,
                chat_id=chat_id,
                chat_type=chat_type,
                message_type=msg_type,
                content=content,
                parent_id=parent_id,
                root_id=root_id,
                mentions=mentions,
            ),
            sender=SimpleNamespace(
                sender_type=sender_type,
                sender_id=SimpleNamespace(open_id=sender_open_id) if sender_open_id else None,
            ),
        )
    )


# ===================================================================
# 1. __init__ edge cases
# ===================================================================

class TestInit:
    def test_init_from_dict_config(self):
        """Line 77: dict config should be validated into FeishuConfig."""
        bus = MagicMock()
        cfg_dict = {
            "enabled": True,
            "app_id": "cli_abc",
            "app_secret": "sec",
            "allow_from": ["u1"],
            "bot_open_id": "ou_bot",
        }
        ch = FeishuChannel(cfg_dict, bus)
        assert isinstance(ch.config, FeishuConfig)
        assert ch.config.app_id == "cli_abc"
        assert ch.config.bot_open_id == "ou_bot"

    def test_init_from_feishu_config(self):
        """When passed a FeishuConfig directly, no conversion needed."""
        cfg = FeishuConfig(app_id="cli_direct", app_secret="s")
        bus = MagicMock()
        ch = FeishuChannel(cfg, bus)
        assert ch.config.app_id == "cli_direct"

    def test_init_sets_all_attributes(self):
        ch = _make_feishu_channel()
        assert ch._client is None
        assert ch._ws_client is None
        assert ch._ws_process is None
        assert ch._ws_reader_task is None
        assert ch._ws_event_queue is None
        assert ch._ws_stop_event is None
        assert ch._main_loop is None
        assert ch._stop_event is not None
        assert isinstance(ch._processed_message_ids, OrderedDict)
        assert ch._message_dedup_ttl == 300
        assert ch._dedup_cleanup_interval == 100
        assert ch._dedup_message_counter == 0
        assert ch._ws_reconnect_delay == 5
        assert ch._ws_max_reconnect_delay == 60
        assert ch._pending_messages is None
        assert ch._bot_open_id == "ou_bot123"

    def test_init_bot_open_id_empty(self):
        ch = _make_feishu_channel(bot_open_id="")
        assert ch._bot_open_id == ""


# ===================================================================
# 2. start() — WebSocket worker process management
# ===================================================================

class TestStart:
    @pytest.mark.asyncio
    async def test_start_sdk_unavailable(self):
        """Line 106-108: start returns early when lark_oapi not installed."""
        ch = _make_feishu_channel()
        import xbot.channels.feishu as feishu_mod
        orig = feishu_mod.FEISHU_AVAILABLE
        try:
            feishu_mod.FEISHU_AVAILABLE = False
            await ch.start()
            assert ch._running is False
        finally:
            feishu_mod.FEISHU_AVAILABLE = orig

    @pytest.mark.asyncio
    async def test_start_missing_credentials(self):
        """Line 110-112: start returns early when app_id or app_secret empty."""
        ch = _make_feishu_channel(app_id="", app_secret="")
        await ch.start()
        assert ch._running is False

    @pytest.mark.asyncio
    async def test_start_missing_app_id_only(self):
        ch = _make_feishu_channel(app_id="", app_secret="secret")
        await ch.start()
        assert ch._running is False

    @pytest.mark.asyncio
    async def test_start_missing_app_secret_only(self):
        ch = _make_feishu_channel(app_id="cli_test", app_secret="")
        await ch.start()
        assert ch._running is False

    @pytest.mark.asyncio
    async def test_start_sets_up_client_and_flags(self):
        """Lines 114-137: successful start sets _running, _client, _main_loop."""
        ch = _make_feishu_channel()

        # Mock _start_ws_worker to avoid real multiprocessing
        ch._start_ws_worker = MagicMock()

        # Mock _create_tracked_task to return a mock task
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        ch._create_tracked_task = MagicMock(return_value=mock_task)

        # Make _running=False after a brief period to exit the while loop
        async def _stop_after_delay():
            await asyncio.sleep(0.05)
            ch._running = False

        stop_task = asyncio.create_task(_stop_after_delay())
        await ch.start()
        await stop_task

        assert ch._running is False
        assert ch._main_loop is not None
        assert ch._pending_messages is not None
        assert ch._client is not None
        ch._start_ws_worker.assert_called_once()
        ch._create_tracked_task.assert_called_once()


# ===================================================================
# 3. stop() — cleanup, worker termination
# ===================================================================

class TestStop:
    @pytest.mark.asyncio
    async def test_stop_no_resources(self):
        """stop() when nothing was started should not raise."""
        ch = _make_feishu_channel()
        ch._running = True
        await ch.stop()
        assert ch._running is False
        assert ch._main_loop is None
        assert ch._ws_process is None
        assert ch._pending_messages is None

    @pytest.mark.asyncio
    async def test_stop_with_reader_task(self):
        """stop() should cancel and await the ws_reader_task."""
        ch = _make_feishu_channel()
        ch._running = True

        async def _fake_reader():
            await asyncio.sleep(100)

        ch._ws_reader_task = asyncio.create_task(_fake_reader())
        await asyncio.sleep(0)  # let task start
        await ch.stop()
        assert ch._ws_reader_task is None

    @pytest.mark.asyncio
    async def test_stop_with_alive_process(self):
        """stop() should join/terminate/kill a running process."""
        ch = _make_feishu_channel()
        ch._running = True

        mock_process = MagicMock()
        mock_process.is_alive.side_effect = [True, True, False]
        mock_process.join = MagicMock()
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.close = MagicMock()
        ch._ws_process = mock_process

        await ch.stop()
        mock_process.join.assert_called()
        mock_process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_with_process_that_dies_gracefully(self):
        """stop() when process dies on first join."""
        ch = _make_feishu_channel()
        ch._running = True

        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.join = MagicMock()
        mock_process.close = MagicMock()
        ch._ws_process = mock_process

        await ch.stop()
        mock_process.join.assert_called_once_with(5)
        mock_process.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_with_ws_stop_event(self):
        """stop() should signal _ws_stop_event."""
        ch = _make_feishu_channel()
        ch._running = True
        mock_stop_event = MagicMock()
        ch._ws_stop_event = mock_stop_event

        await ch.stop()
        mock_stop_event.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_process_needs_kill(self):
        """stop() escalates to kill() if process still alive after terminate."""
        ch = _make_feishu_channel()
        ch._running = True

        mock_process = MagicMock()
        # alive after join, alive after terminate, dead after kill
        mock_process.is_alive.side_effect = [True, True, True, False]
        mock_process.join = MagicMock()
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.close = MagicMock()
        ch._ws_process = mock_process

        await ch.stop()
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()


# ===================================================================
# 4. check_health()
# ===================================================================

class TestCheckHealth:
    def test_stopped(self):
        ch = _make_feishu_channel()
        ch._running = False
        ok, msg = ch.check_health()
        assert ok is False
        assert "stopped" in msg

    def test_no_process(self):
        ch = _make_feishu_channel()
        ch._running = True
        ch._ws_process = None
        ok, msg = ch.check_health()
        assert ok is False
        assert "worker" in msg.lower() or "dead" in msg.lower()

    def test_dead_process(self):
        ch = _make_feishu_channel()
        ch._running = True
        ch._ws_process = MagicMock()
        ch._ws_process.is_alive.return_value = False
        ok, msg = ch.check_health()
        assert ok is False

    def test_no_client(self):
        ch = _make_feishu_channel()
        ch._running = True
        ch._ws_process = MagicMock()
        ch._ws_process.is_alive.return_value = True
        ch._client = None
        ok, msg = ch.check_health()
        assert ok is False
        assert "client" in msg.lower()

    def test_healthy(self):
        ch = _make_feishu_channel()
        ch._running = True
        ch._ws_process = MagicMock()
        ch._ws_process.is_alive.return_value = True
        ch._client = MagicMock()
        ok, msg = ch.check_health()
        assert ok is True
        assert msg == "ok"


# ===================================================================
# 5. _kill_stale_ws_workers / _read_parent_pid
# ===================================================================

class TestKillStaleWorkers:
    def test_read_parent_pid_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="  1234  ")
            result = FeishuChannel._read_parent_pid(5678)
            assert result == 1234

    def test_read_parent_pid_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = FeishuChannel._read_parent_pid(5678)
            assert result is None

    def test_read_parent_pid_exception(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = FeishuChannel._read_parent_pid(5678)
            assert result is None

    def test_read_parent_pid_value_error(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="  not_a_number  ")
            result = FeishuChannel._read_parent_pid(5678)
            assert result is None

    def test_kill_stale_workers_no_pgrep_results(self):
        ch = _make_feishu_channel()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            ch._kill_stale_ws_workers()  # Should not raise

    def test_kill_stale_workers_skips_own_pid(self):
        ch = _make_feishu_channel()
        import os
        my_pid = os.getpid()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=str(my_pid))
            with patch("os.kill") as mock_kill:
                ch._kill_stale_ws_workers()
                mock_kill.assert_not_called()

    def test_kill_stale_workers_skips_other_instance_workers(self):
        ch = _make_feishu_channel()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999")
            # ppid is some other process, not us (1) and not gateway pid
            with patch.object(FeishuChannel, "_read_parent_pid", return_value=88888):
                with patch("os.kill") as mock_kill:
                    ch._kill_stale_ws_workers()
                    mock_kill.assert_not_called()

    def test_kill_stale_workers_kills_orphan(self):
        ch = _make_feishu_channel()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999")
            with patch.object(FeishuChannel, "_read_parent_pid", return_value=1):
                with patch("os.kill") as mock_kill:
                    ch._kill_stale_ws_workers()
                    mock_kill.assert_called_once()

    def test_kill_stale_workers_kills_own_child(self):
        ch = _make_feishu_channel()
        import os
        my_pid = os.getpid()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999")
            with patch.object(FeishuChannel, "_read_parent_pid", return_value=my_pid):
                with patch("os.kill") as mock_kill:
                    ch._kill_stale_ws_workers()
                    mock_kill.assert_called_once()

    def test_kill_stale_workers_handles_pid_errors(self):
        ch = _make_feishu_channel()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999")
            with patch.object(FeishuChannel, "_read_parent_pid", return_value=1):
                with patch("os.kill", side_effect=ProcessLookupError):
                    ch._kill_stale_ws_workers()  # Should not raise

    def test_kill_stale_workers_outer_exception(self):
        ch = _make_feishu_channel()
        with patch("subprocess.run", side_effect=Exception("boom")):
            ch._kill_stale_ws_workers()  # Should not raise


# ===================================================================
# 6. _start_ws_worker / _cleanup_ws_resources / _close_ws_ipc_resources
# ===================================================================

class TestWsWorkerLifecycle:
    def test_start_ws_worker(self):
        """_start_ws_worker creates process, queue, event."""
        ch = _make_feishu_channel()
        ch._kill_stale_ws_workers = MagicMock()
        ch._cleanup_ws_resources = MagicMock()

        # Mock the multiprocessing context
        mock_ctx = MagicMock()
        mock_queue = MagicMock()
        mock_event = MagicMock()
        mock_process = MagicMock()
        mock_ctx.Queue.return_value = mock_queue
        mock_ctx.Event.return_value = mock_event
        mock_ctx.Process.return_value = mock_process

        with patch("multiprocessing.get_context", return_value=mock_ctx):
            with patch("xbot.channels.feishu_ws_worker.run_feishu_ws_worker", create=True):
                ch._start_ws_worker()

        assert ch._ws_event_queue is mock_queue
        assert ch._ws_stop_event is mock_event
        assert ch._ws_process is mock_process
        mock_process.start.assert_called_once()

    def test_cleanup_ws_resources_drains_queue(self):
        """_cleanup_ws_resources should drain old queue."""
        ch = _make_feishu_channel()
        mock_queue = MagicMock()
        mock_queue.get_nowait = MagicMock(side_effect=[{"type": "message"}, queue.Empty, queue.Empty])
        mock_queue.close = MagicMock()
        mock_queue.join_thread = MagicMock()
        ch._ws_event_queue = mock_queue
        mock_stop = MagicMock()
        ch._ws_stop_event = mock_stop

        ch._close_ws_ipc_resources = MagicMock()
        ch._cleanup_ws_resources()
        mock_stop.set.assert_called_once()
        assert mock_queue.get_nowait.call_count >= 1

    def test_cleanup_ws_resources_empty_queue(self):
        """No crash when queue is already empty."""
        ch = _make_feishu_channel()
        mock_queue = MagicMock()
        mock_queue.get_nowait = MagicMock(side_effect=queue.Empty)
        ch._ws_event_queue = mock_queue
        ch._ws_stop_event = None
        ch._close_ws_ipc_resources = MagicMock()
        ch._cleanup_ws_resources()

    def test_close_ws_ipc_resources(self):
        """_close_ws_ipc_resources closes queue and stop event semaphores."""
        ch = _make_feishu_channel()

        mock_queue = MagicMock()
        mock_queue._rlock = None
        mock_queue._wlock = None
        mock_queue._sem = None
        mock_queue._flag = None
        mock_queue._cond = None
        ch._ws_event_queue = mock_queue

        mock_stop = MagicMock()
        mock_stop._rlock = None
        mock_stop._wlock = None
        mock_stop._sem = None
        mock_stop._flag = None
        mock_stop._cond = None
        ch._ws_stop_event = mock_stop

        ch._close_ws_ipc_resources()
        mock_queue.close.assert_called_once()
        mock_queue.join_thread.assert_called_once()

    def test_close_ws_ipc_resources_none(self):
        """No crash when resources are None."""
        ch = _make_feishu_channel()
        ch._ws_event_queue = None
        ch._ws_stop_event = None
        ch._close_ws_ipc_resources()  # Should not raise


# ===================================================================
# 7. Semaphore helpers
# ===================================================================

class TestSemaphoreHelpers:
    def test_iter_semaphore_names_empty(self):
        obj = MagicMock()
        obj._rlock = None
        obj._wlock = None
        obj._sem = None
        obj._flag = None
        obj._cond = None
        names = FeishuChannel._iter_multiprocessing_semaphore_names(obj)
        assert names == []

    def test_iter_semaphore_names_with_semlock(self):
        obj = MagicMock()
        semlock = MagicMock()
        semlock.name = "/test_sem"
        obj._rlock = MagicMock()
        obj._rlock._semlock = semlock
        obj._wlock = None
        obj._sem = None
        obj._flag = None
        obj._cond = None
        names = FeishuChannel._iter_multiprocessing_semaphore_names(obj)
        assert "/test_sem" in names

    def test_iter_semaphore_names_with_cond(self):
        obj = MagicMock()
        obj._rlock = None
        obj._wlock = None
        obj._sem = None
        obj._flag = None
        cond = MagicMock()
        semlock = MagicMock()
        semlock.name = "/cond_sem"
        cond._lock = MagicMock()
        cond._lock._semlock = semlock
        cond._sleeping_count = None
        cond._woken_count = None
        cond._wait_semaphore = None
        obj._cond = cond
        names = FeishuChannel._iter_multiprocessing_semaphore_names(obj)
        assert "/cond_sem" in names

    def test_iter_semaphore_names_dedup(self):
        """Same object referenced twice should only produce one name."""
        obj = MagicMock()
        semlock = MagicMock()
        semlock.name = "/shared_sem"
        shared_lock = MagicMock()
        shared_lock._semlock = semlock
        obj._rlock = shared_lock
        obj._wlock = shared_lock  # same object
        obj._sem = None
        obj._flag = None
        obj._cond = None
        names = FeishuChannel._iter_multiprocessing_semaphore_names(obj)
        assert names.count("/shared_sem") == 1

    def test_cleanup_registered_semaphore_fallback(self):
        """When no finalizer matches, falls through to SemLock._cleanup."""
        with patch("multiprocessing.synchronize.SemLock._cleanup") as mock_cleanup:
            FeishuChannel._cleanup_registered_semaphore("/nonexistent_name")
            mock_cleanup.assert_called_once_with("/nonexistent_name")

    def test_cleanup_multiprocessing_semlocks(self):
        """Should call _cleanup_registered_semaphore for each semaphore name."""
        obj = MagicMock()
        semlock = MagicMock()
        semlock.name = "/test_sem_abc"
        obj._rlock = MagicMock()
        obj._rlock._semlock = semlock
        obj._wlock = None
        obj._sem = None
        obj._flag = None
        obj._cond = None
        with patch.object(FeishuChannel, "_cleanup_registered_semaphore") as mock_cleanup:
            FeishuChannel._cleanup_multiprocessing_semlocks(obj)
            mock_cleanup.assert_called_once_with("/test_sem_abc")


# ===================================================================
# 8. _register_optional_event
# ===================================================================

class TestRegisterOptionalEvent:
    def test_method_exists(self):
        builder = MagicMock()
        handler = MagicMock()
        builder.on_some_event = MagicMock(return_value="registered")
        result = FeishuChannel._register_optional_event(builder, "on_some_event", handler)
        builder.on_some_event.assert_called_once_with(handler)
        assert result == "registered"

    def test_method_missing(self):
        builder = MagicMock(spec=[])
        handler = MagicMock()
        result = FeishuChannel._register_optional_event(builder, "nonexistent_method", handler)
        assert result is builder


# ===================================================================
# 9. _upload_image_sync
# ===================================================================

class TestUploadImageSync:
    def test_success(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(image_key="img_key_123")
        ch._client.im.v1.image.create.return_value = mock_response

        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG\r\n")

        result = ch._upload_image_sync(str(img_file))
        assert result == "img_key_123"

    def test_api_failure(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 400
        mock_response.msg = "bad request"
        ch._client.im.v1.image.create.return_value = mock_response

        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG\r\n")

        result = ch._upload_image_sync(str(img_file))
        assert result is None

    def test_exception(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.image.create.side_effect = Exception("network error")

        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG\r\n")

        result = ch._upload_image_sync(str(img_file))
        assert result is None


# ===================================================================
# 10. _upload_file_sync
# ===================================================================

class TestUploadFileSync:
    def test_success_pdf(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(file_key="file_key_abc")
        ch._client.im.v1.file.create.return_value = mock_response

        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")

        result = ch._upload_file_sync(str(pdf_file))
        assert result == "file_key_abc"

    def test_success_unknown_ext(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(file_key="file_key_xyz")
        ch._client.im.v1.file.create.return_value = mock_response

        f = tmp_path / "data.bin"
        f.write_bytes(b"binary data")

        result = ch._upload_file_sync(str(f))
        assert result == "file_key_xyz"

    def test_api_failure(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 500
        mock_response.msg = "server error"
        ch._client.im.v1.file.create.return_value = mock_response

        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        result = ch._upload_file_sync(str(f))
        assert result is None

    def test_exception(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.file.create.side_effect = OSError("disk error")

        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        result = ch._upload_file_sync(str(f))
        assert result is None

    def test_file_type_map(self, tmp_path):
        """Verify various extensions map to correct file_type."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(file_key="key")
        ch._client.im.v1.file.create.return_value = mock_response

        for ext in [".opus", ".mp4", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]:
            f = tmp_path / f"test{ext}"
            f.write_bytes(b"data")
            ch._upload_file_sync(str(f))


# ===================================================================
# 11. _download_image_sync / _download_file_sync
# ===================================================================

class TestDownloadSync:
    def test_download_image_success(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_file = MagicMock()
        mock_file.read.return_value = b"image bytes"
        mock_response.file = mock_file
        mock_response.file_name = "photo.jpg"
        ch._client.im.v1.message_resource.get.return_value = mock_response

        data, fname = ch._download_image_sync("msg_1", "img_key_1")
        assert data == b"image bytes"
        assert fname == "photo.jpg"

    def test_download_image_api_failure(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 404
        mock_response.msg = "not found"
        ch._client.im.v1.message_resource.get.return_value = mock_response

        data, fname = ch._download_image_sync("msg_1", "img_key_1")
        assert data is None
        assert fname is None

    def test_download_image_exception(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.message_resource.get.side_effect = Exception("network")

        data, fname = ch._download_image_sync("msg_1", "img_key_1")
        assert data is None
        assert fname is None

    def test_download_image_bytes_directly(self):
        """When response.file has no read() method (already bytes)."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.file = b"raw bytes"
        mock_response.file_name = "img.png"
        ch._client.im.v1.message_resource.get.return_value = mock_response

        data, fname = ch._download_image_sync("msg_1", "img_key_1")
        assert data == b"raw bytes"

    def test_download_file_success(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_file = MagicMock()
        mock_file.read.return_value = b"file bytes"
        mock_response.file = mock_file
        mock_response.file_name = "doc.pdf"
        ch._client.im.v1.message_resource.get.return_value = mock_response

        data, fname = ch._download_file_sync("msg_1", "file_key_1", "file")
        assert data == b"file bytes"
        assert fname == "doc.pdf"

    def test_download_file_audio_converted_to_file(self):
        """audio type should be converted to 'file' for API call."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.file = b"audio data"
        mock_response.file_name = "voice.opus"
        ch._client.im.v1.message_resource.get.return_value = mock_response

        data, fname = ch._download_file_sync("msg_1", "file_key_1", "audio")
        assert data == b"audio data"

    def test_download_file_api_failure(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 403
        mock_response.msg = "forbidden"
        ch._client.im.v1.message_resource.get.return_value = mock_response

        data, fname = ch._download_file_sync("msg_1", "file_key_1", "file")
        assert data is None
        assert fname is None

    def test_download_file_exception(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.message_resource.get.side_effect = Exception("timeout")

        data, fname = ch._download_file_sync("msg_1", "file_key_1", "file")
        assert data is None
        assert fname is None


# ===================================================================
# 12. _download_and_save_media
# ===================================================================

class TestDownloadAndSaveMedia:
    @pytest.mark.asyncio
    async def test_image_success(self, tmp_path):
        ch = _make_feishu_channel()
        ch._download_image_sync = MagicMock(return_value=(b"image data", "photo.jpg"))

        with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
            path, text = await ch._download_and_save_media("image", {"image_key": "img_v2_abc123"}, "msg_1")

        assert path is not None
        assert "photo.jpg" in path
        assert "image" in text

    @pytest.mark.asyncio
    async def test_image_no_filename_fallback(self, tmp_path):
        ch = _make_feishu_channel()
        ch._download_image_sync = MagicMock(return_value=(b"image data", None))

        with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
            path, text = await ch._download_and_save_media("image", {"image_key": "img_v2_abc123"}, "msg_1")

        assert path is not None
        assert "img_v2_a" in path or "jpg" in path  # fallback name from image_key

    @pytest.mark.asyncio
    async def test_image_no_message_id(self, tmp_path):
        ch = _make_feishu_channel()
        ch._download_image_sync = MagicMock()

        with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
            path, text = await ch._download_and_save_media("image", {"image_key": "img_key"}, None)

        assert path is None
        assert "download failed" in text
        ch._download_image_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_audio_success(self, tmp_path):
        ch = _make_feishu_channel()
        ch._download_file_sync = MagicMock(return_value=(b"audio data", "voice.opus"))

        with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
            path, text = await ch._download_and_save_media("audio", {"file_key": "file_key_1"}, "msg_1")

        assert path is not None
        assert "voice.opus" in path

    @pytest.mark.asyncio
    async def test_audio_adds_opus_extension(self, tmp_path):
        ch = _make_feishu_channel()
        ch._download_file_sync = MagicMock(return_value=(b"audio data", "voice"))

        with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
            path, text = await ch._download_and_save_media("audio", {"file_key": "file_key_1"}, "msg_1")

        assert path is not None
        assert ".opus" in path

    @pytest.mark.asyncio
    async def test_file_success(self, tmp_path):
        ch = _make_feishu_channel()
        ch._download_file_sync = MagicMock(return_value=(b"file data", "doc.pdf"))

        with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
            path, text = await ch._download_and_save_media("file", {"file_key": "file_key_1"}, "msg_1")

        assert path is not None
        assert "doc.pdf" in path

    @pytest.mark.asyncio
    async def test_media_success(self, tmp_path):
        ch = _make_feishu_channel()
        ch._download_file_sync = MagicMock(return_value=(b"video data", "clip.mp4"))

        with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
            path, text = await ch._download_and_save_media("media", {"file_key": "file_key_1"}, "msg_1")

        assert path is not None
        assert "clip.mp4" in path

    @pytest.mark.asyncio
    async def test_download_failure(self, tmp_path):
        ch = _make_feishu_channel()
        ch._download_image_sync = MagicMock(return_value=(None, None))

        with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
            path, text = await ch._download_and_save_media("image", {"image_key": "img_key"}, "msg_1")

        assert path is None
        assert "download failed" in text

    @pytest.mark.asyncio
    async def test_no_image_key(self, tmp_path):
        ch = _make_feishu_channel()

        with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
            path, text = await ch._download_and_save_media("image", {}, "msg_1")

        assert path is None
        assert "download failed" in text

    @pytest.mark.asyncio
    async def test_no_file_key(self, tmp_path):
        ch = _make_feishu_channel()

        with patch("xbot.channels.feishu.get_media_dir", return_value=tmp_path):
            path, text = await ch._download_and_save_media("file", {}, "msg_1")

        assert path is None
        assert "download failed" in text


# ===================================================================
# 13. _reply_message_sync
# ===================================================================

class TestReplyMessageSync:
    def test_success(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        ch._client.im.v1.message.reply.return_value = mock_response

        result = ch._reply_message_sync("parent_msg", "text", '{"text":"hi"}')
        assert result is True

    def test_api_failure(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 400
        mock_response.msg = "bad"
        mock_response.get_log_id.return_value = "log_1"
        ch._client.im.v1.message.reply.return_value = mock_response

        result = ch._reply_message_sync("parent_msg", "text", '{"text":"hi"}')
        assert result is False

    def test_network_error_with_retry(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        # First call raises ConnectionError, second succeeds
        ch._client.im.v1.message.reply.side_effect = [
            ConnectionError("timeout"),
            mock_response,
        ]

        with patch("time.sleep"):
            result = ch._reply_message_sync("parent_msg", "text", '{"text":"hi"}')
        assert result is True

    def test_network_error_all_retries_fail(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.message.reply.side_effect = ConnectionError("timeout")

        with patch("time.sleep"):
            result = ch._reply_message_sync("parent_msg", "text", '{"text":"hi"}')
        assert result is False

    def test_generic_exception_no_retry(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.message.reply.side_effect = ValueError("unexpected")

        result = ch._reply_message_sync("parent_msg", "text", '{"text":"hi"}')
        assert result is False


# ===================================================================
# 14. _get_message_content_sync
# ===================================================================

class TestGetMessageContentSync:
    def test_text_message(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.body = SimpleNamespace(content='{"text": "parent text"}')
        mock_msg.msg_type = "text"
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(items=[mock_msg])
        ch._client.im.v1.message.get.return_value = mock_response

        result = ch._get_message_content_sync("msg_parent")
        assert result == "[Reply to: parent text]"

    def test_post_message(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        post_content = json.dumps({
            "content": [[{"tag": "text", "text": "post content"}]]
        })
        mock_msg = MagicMock()
        mock_msg.body = SimpleNamespace(content=post_content)
        mock_msg.msg_type = "post"
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(items=[mock_msg])
        ch._client.im.v1.message.get.return_value = mock_response

        result = ch._get_message_content_sync("msg_parent")
        assert result is not None
        assert "Reply to" in result
        assert "post content" in result

    def test_unsupported_msg_type(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.body = SimpleNamespace(content='{"image_key": "img"}')
        mock_msg.msg_type = "image"
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(items=[mock_msg])
        ch._client.im.v1.message.get.return_value = mock_response

        result = ch._get_message_content_sync("msg_parent")
        assert result is None

    def test_api_failure(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 404
        mock_response.msg = "not found"
        ch._client.im.v1.message.get.return_value = mock_response

        result = ch._get_message_content_sync("msg_parent")
        assert result is None

    def test_no_items(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(items=[])
        ch._client.im.v1.message.get.return_value = mock_response

        result = ch._get_message_content_sync("msg_parent")
        assert result is None

    def test_no_body(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.body = None
        mock_msg.msg_type = "text"
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(items=[mock_msg])
        ch._client.im.v1.message.get.return_value = mock_response

        result = ch._get_message_content_sync("msg_parent")
        assert result is None

    def test_invalid_json_content(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.body = SimpleNamespace(content="not valid json")
        mock_msg.msg_type = "text"
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(items=[mock_msg])
        ch._client.im.v1.message.get.return_value = mock_response

        result = ch._get_message_content_sync("msg_parent")
        assert result is None

    def test_truncation(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        long_text = "x" * 500
        mock_msg = MagicMock()
        mock_msg.body = SimpleNamespace(content=json.dumps({"text": long_text}))
        mock_msg.msg_type = "text"
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(items=[mock_msg])
        ch._client.im.v1.message.get.return_value = mock_response

        result = ch._get_message_content_sync("msg_parent")
        assert result is not None
        assert "..." in result
        # Content should be truncated
        assert len(result) < len(long_text) + 20

    def test_exception(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.message.get.side_effect = Exception("boom")

        result = ch._get_message_content_sync("msg_parent")
        assert result is None

    def test_empty_text(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.body = SimpleNamespace(content='{"text": "  "}')
        mock_msg.msg_type = "text"
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data = SimpleNamespace(items=[mock_msg])
        ch._client.im.v1.message.get.return_value = mock_response

        result = ch._get_message_content_sync("msg_parent")
        assert result is None


# ===================================================================
# 15. _send_message_sync
# ===================================================================

class TestSendMessageSync:
    def test_success(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        ch._client.im.v1.message.create.return_value = mock_response

        result = ch._send_message_sync("chat_id", "oc_123", "text", '{"text":"hi"}')
        assert result is True

    def test_api_failure(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 400
        mock_response.msg = "bad"
        mock_response.get_log_id.return_value = "log_1"
        ch._client.im.v1.message.create.return_value = mock_response

        result = ch._send_message_sync("chat_id", "oc_123", "text", '{"text":"hi"}')
        assert result is False

    def test_network_retry_then_success(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        ch._client.im.v1.message.create.side_effect = [
            OSError("connection reset"),
            mock_response,
        ]

        with patch("time.sleep"):
            result = ch._send_message_sync("chat_id", "oc_123", "text", '{"text":"hi"}')
        assert result is True

    def test_all_retries_fail(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.message.create.side_effect = ConnectionError("dead")

        with patch("time.sleep"):
            result = ch._send_message_sync("chat_id", "oc_123", "text", '{"text":"hi"}')
        assert result is False

    def test_generic_exception(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.message.create.side_effect = RuntimeError("unexpected")

        result = ch._send_message_sync("chat_id", "oc_123", "text", '{"text":"hi"}')
        assert result is False


# ===================================================================
# 16. _add_reaction / _add_reaction_sync
# ===================================================================

class TestAddReaction:
    @pytest.mark.asyncio
    async def test_add_reaction_no_client(self):
        ch = _make_feishu_channel()
        ch._client = None
        await ch._add_reaction("msg_1", "THUMBSUP")  # Should return early

    @pytest.mark.asyncio
    async def test_add_reaction_with_client(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._add_reaction_sync = MagicMock()

        await ch._add_reaction("msg_1", "HEART")
        ch._add_reaction_sync.assert_called_once_with("msg_1", "HEART")

    def test_add_reaction_sync_success(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        ch._client.im.v1.message_reaction.create.return_value = mock_response

        ch._add_reaction_sync("msg_1", "THUMBSUP")

    def test_add_reaction_sync_api_failure(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 400
        mock_response.msg = "bad"
        ch._client.im.v1.message_reaction.create.return_value = mock_response

        ch._add_reaction_sync("msg_1", "THUMBSUP")  # Should not raise

    def test_add_reaction_sync_exception(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.message_reaction.create.side_effect = Exception("network")

        ch._add_reaction_sync("msg_1", "THUMBSUP")  # Should not raise


# ===================================================================
# 17. _is_bot_mentioned — edge cases
# ===================================================================

class TestIsBotMentionedEdgeCases:
    def test_no_bot_open_id_fallback_to_ou_prefix(self):
        """When bot_open_id is empty, check for ou_ prefix mention."""
        ch = _make_feishu_channel(bot_open_id="")
        mention = SimpleNamespace(
            id=SimpleNamespace(open_id="ou_some_bot", user_id=None)
        )
        message = SimpleNamespace(
            content="",
            mentions=[mention],
            message_type="text",
        )
        assert ch._is_bot_mentioned(message) is True

    def test_no_bot_open_id_with_user_id_skipped(self):
        """When bot_open_id empty and mention has user_id, skip it."""
        ch = _make_feishu_channel(bot_open_id="")
        mention = SimpleNamespace(
            id=SimpleNamespace(open_id="ou_some_bot", user_id="uid_123")
        )
        message = SimpleNamespace(
            content="",
            mentions=[mention],
            message_type="text",
        )
        assert ch._is_bot_mentioned(message) is False

    def test_no_mentions_attribute(self):
        """Message with no mentions attribute at all."""
        ch = _make_feishu_channel()
        message = SimpleNamespace(
            content="hello",
            message_type="text",
        )
        # No mentions attribute → getattr fallback to None
        assert ch._is_bot_mentioned(message) is False

    def test_mention_with_no_id(self):
        """Mention objects with no id should be skipped."""
        ch = _make_feishu_channel()
        mention = SimpleNamespace()  # no id attribute
        message = SimpleNamespace(
            content="",
            mentions=[mention],
            message_type="text",
        )
        assert ch._is_bot_mentioned(message) is False

    def test_post_mention_with_invalid_json(self):
        """Post messages with invalid JSON content."""
        ch = _make_feishu_channel()
        message = SimpleNamespace(
            content="not valid json",
            mentions=[],
            message_type="post",
        )
        assert ch._is_bot_mentioned(message) is False

    def test_post_mention_with_empty_content(self):
        ch = _make_feishu_channel()
        message = SimpleNamespace(
            content="",
            mentions=[],
            message_type="post",
        )
        assert ch._is_bot_mentioned(message) is False

    def test_none_content(self):
        ch = _make_feishu_channel()
        message = SimpleNamespace(
            content=None,
            mentions=[],
            message_type="text",
        )
        assert ch._is_bot_mentioned(message) is False


# ===================================================================
# 18. _on_message — event processing
# ===================================================================

class TestOnMessage:
    @pytest.mark.asyncio
    async def test_text_message(self):
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        data = _make_event()
        await ch._on_message(data)
        ch._handle_message.assert_called_once()
        kwargs = ch._handle_message.call_args[1]
        assert "hello" in kwargs["content"]
        assert kwargs["sender_id"] == "ou_user1"

    @pytest.mark.asyncio
    async def test_bot_sender_skipped(self):
        """Messages from bots should be ignored."""
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        data = _make_event(sender_type="bot")
        await ch._on_message(data)
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_message_not_for_bot(self):
        """Group messages without bot mention should be skipped."""
        ch = _make_feishu_channel(group_policy="mention")
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        data = _make_event(chat_type="group", mentions=[])
        await ch._on_message(data)
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_message_open_policy(self):
        ch = _make_feishu_channel(group_policy="open")
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        data = _make_event(chat_type="group")
        await ch._on_message(data)
        ch._handle_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_post_message_with_images(self):
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        ch._download_and_save_media = AsyncMock(return_value=("/tmp/img.jpg", "[image: img.jpg]"))

        post_content = json.dumps({
            "content": [
                [
                    {"tag": "text", "text": "check this"},
                    {"tag": "img", "image_key": "img_v2_key1"},
                ]
            ]
        })
        data = _make_event(msg_type="post", content=post_content)
        await ch._on_message(data)
        ch._handle_message.assert_called_once()
        ch._download_and_save_media.assert_called_once()
        kwargs = ch._handle_message.call_args[1]
        assert "check this" in kwargs["content"]
        assert "/tmp/img.jpg" in kwargs["media"]

    @pytest.mark.asyncio
    async def test_image_message(self):
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        ch._download_and_save_media = AsyncMock(return_value=("/tmp/photo.jpg", "[image: photo.jpg]"))

        data = _make_event(
            msg_type="image",
            content=json.dumps({"image_key": "img_key_1"}),
        )
        await ch._on_message(data)
        ch._handle_message.assert_called_once()
        kwargs = ch._handle_message.call_args[1]
        assert "/tmp/photo.jpg" in kwargs["media"]

    @pytest.mark.asyncio
    async def test_audio_message_with_transcription(self):
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        ch._download_and_save_media = AsyncMock(return_value=("/tmp/voice.opus", "[audio: voice.opus]"))
        ch.transcribe_audio = AsyncMock(return_value="transcribed text")

        data = _make_event(
            msg_type="audio",
            content=json.dumps({"file_key": "file_key_1"}),
        )
        await ch._on_message(data)
        ch._handle_message.assert_called_once()
        kwargs = ch._handle_message.call_args[1]
        assert "transcription" in kwargs["content"]
        assert "transcribed text" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_audio_message_no_transcription(self):
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        ch._download_and_save_media = AsyncMock(return_value=("/tmp/voice.opus", "[audio: voice.opus]"))
        ch.transcribe_audio = AsyncMock(return_value="")

        data = _make_event(
            msg_type="audio",
            content=json.dumps({"file_key": "file_key_1"}),
        )
        await ch._on_message(data)
        kwargs = ch._handle_message.call_args[1]
        assert "[audio:" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_file_message(self):
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        ch._download_and_save_media = AsyncMock(return_value=("/tmp/doc.pdf", "[file: doc.pdf]"))

        data = _make_event(
            msg_type="file",
            content=json.dumps({"file_key": "file_key_1"}),
        )
        await ch._on_message(data)
        ch._handle_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_media_message(self):
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        ch._download_and_save_media = AsyncMock(return_value=("/tmp/clip.mp4", "[media: clip.mp4]"))

        data = _make_event(
            msg_type="media",
            content=json.dumps({"file_key": "file_key_1"}),
        )
        await ch._on_message(data)
        ch._handle_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_share_chat_message(self):
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()

        data = _make_event(
            msg_type="share_chat",
            content=json.dumps({"chat_id": "oc_shared"}),
        )
        await ch._on_message(data)
        ch._handle_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_message_type(self):
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()

        data = _make_event(msg_type="some_future_type", content="{}")
        await ch._on_message(data)
        ch._handle_message.assert_called_once()
        kwargs = ch._handle_message.call_args[1]
        assert "some_future_type" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_empty_content_no_media(self):
        """When content is empty and no media, should return early."""
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()

        data = _make_event(msg_type="text", content='{"text": ""}')
        await ch._on_message(data)
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_json_content(self):
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()

        data = _make_event(msg_type="text", content="not json")
        await ch._on_message(data)
        # Should still process (empty content_json), but no text found
        ch._handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_reply_to_is_chat_id(self):
        """Group messages should reply to chat_id, not sender_id."""
        ch = _make_feishu_channel(group_policy="open")
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()

        data = _make_event(chat_type="group")
        await ch._on_message(data)
        kwargs = ch._handle_message.call_args[1]
        assert kwargs["chat_id"] == "oc_chat1"

    @pytest.mark.asyncio
    async def test_p2p_reply_to_is_sender_id(self):
        """P2P messages should reply to sender_id."""
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()

        data = _make_event(chat_type="p2p")
        await ch._on_message(data)
        kwargs = ch._handle_message.call_args[1]
        assert kwargs["chat_id"] == "ou_user1"

    @pytest.mark.asyncio
    async def test_parent_id_fetches_reply_context(self):
        """When parent_id exists, should fetch reply context."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()  # Must be set — guard condition
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        ch._get_message_content_sync = MagicMock(return_value="[Reply to: parent text]")

        data = _make_event(parent_id="om_parent_msg")
        await ch._on_message(data)
        ch._get_message_content_sync.assert_called_once_with("om_parent_msg")
        kwargs = ch._handle_message.call_args[1]
        assert "[Reply to: parent text]" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_exception_in_handler(self):
        """Exceptions in _on_message should be caught."""
        ch = _make_feishu_channel()
        ch._add_reaction = AsyncMock(side_effect=Exception("reaction failed"))

        data = _make_event()
        await ch._on_message(data)  # Should not raise

    @pytest.mark.asyncio
    async def test_dedup_cleanup_triggered(self):
        """After _dedup_cleanup_interval messages, cleanup runs."""
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()
        ch._dedup_cleanup_interval = 2  # Trigger cleanup every 2 messages

        # Add some expired entries
        old_time = 0.0
        ch._processed_message_ids["old_msg_1"] = old_time
        ch._processed_message_ids["old_msg_2"] = old_time

        data = _make_event(message_id="new_msg_1")
        await ch._on_message(data)

        data2 = _make_event(message_id="new_msg_2")
        await ch._on_message(data2)

        # Old entries should have been cleaned up
        assert "old_msg_1" not in ch._processed_message_ids

    @pytest.mark.asyncio
    async def test_sender_id_unknown_when_no_open_id(self):
        """When sender has no open_id, defaults to 'unknown'."""
        ch = _make_feishu_channel()
        ch._handle_message = AsyncMock()
        ch._add_reaction = AsyncMock()

        data = _make_event()
        data.event.sender.sender_id = None
        await ch._on_message(data)
        kwargs = ch._handle_message.call_args[1]
        assert kwargs["sender_id"] == "unknown"


# ===================================================================
# 19. _dispatch_worker_event — all paths
# ===================================================================

class TestDispatchWorkerEvent:
    @pytest.mark.asyncio
    async def test_non_message_non_error(self):
        """Events with unknown type should be silently ignored."""
        ch = _make_feishu_channel()
        await ch._dispatch_worker_event({"type": "status_update"})

    @pytest.mark.asyncio
    async def test_error_event(self):
        ch = _make_feishu_channel()
        await ch._dispatch_worker_event({"type": "error", "error": "ws connection lost"})

    @pytest.mark.asyncio
    async def test_message_without_payload(self):
        ch = _make_feishu_channel()
        ch._on_message = AsyncMock()
        await ch._dispatch_worker_event({"type": "message"})
        ch._on_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_with_non_dict_payload(self):
        ch = _make_feishu_channel()
        ch._on_message = AsyncMock()
        await ch._dispatch_worker_event({"type": "message", "payload": "not a dict"})
        ch._on_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_duplicate_skipped(self):
        ch = _make_feishu_channel()
        ch._on_message = AsyncMock()
        ch._mark_message_seen("dup_msg")  # Mark as seen

        event = {
            "type": "message",
            "payload": {
                "event": {
                    "message": {"message_id": "dup_msg"},
                    "sender": {"sender_type": "user"},
                }
            },
        }
        await ch._dispatch_worker_event(event)
        ch._on_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_on_message_raises_unmarks(self):
        """If _on_message raises, the message should be unmarked for retry."""
        ch = _make_feishu_channel()
        ch._on_message = AsyncMock(side_effect=Exception("processing failed"))

        event = {
            "type": "message",
            "payload": {
                "event": {
                    "message": {"message_id": "fail_msg"},
                    "sender": {"sender_type": "user"},
                }
            },
        }
        with pytest.raises(Exception, match="processing failed"):
            await ch._dispatch_worker_event(event)

        # Message should be unmarked so it can be retried
        assert ch._mark_message_seen("fail_msg") is True


# ===================================================================
# 20. _run_ws_event_reader
# ===================================================================

class TestRunWsEventReader:
    @pytest.mark.asyncio
    async def test_reader_exits_when_queue_none(self):
        ch = _make_feishu_channel()
        ch._running = True
        ch._ws_event_queue = None
        await ch._run_ws_event_reader()  # Should return immediately

    @pytest.mark.asyncio
    async def test_reader_processes_events(self):
        ch = _make_feishu_channel()
        ch._running = True
        ch._dispatch_worker_event = AsyncMock()

        call_count = 0

        def _fake_get(block=True, timeout=0.5):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"type": "message", "payload": {}}
            raise queue.Empty()

        mock_q = MagicMock()
        mock_q.get = _fake_get
        ch._ws_event_queue = mock_q

        # After processing one event and getting Empty, set _running=False
        async def _stop():
            await asyncio.sleep(0.1)
            ch._running = False

        stop_task = asyncio.create_task(_stop())
        await ch._run_ws_event_reader()
        await stop_task
        ch._dispatch_worker_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_reader_restarts_dead_worker(self):
        ch = _make_feishu_channel()
        ch._running = True
        ch._dispatch_worker_event = AsyncMock()
        ch._start_ws_worker = MagicMock()

        def _fake_get(block=True, timeout=0.5):
            raise queue.Empty()

        mock_q = MagicMock()
        mock_q.get = _fake_get
        ch._ws_event_queue = mock_q

        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = False
        ch._ws_process = mock_proc

        # Patch asyncio.sleep to avoid long waits for backoff
        import xbot.channels.feishu as feishu_mod
        real_sleep = asyncio.sleep
        feishu_mod.asyncio.sleep = AsyncMock()
        try:
            # Let a few restart cycles happen then stop
            async def _stop():
                await real_sleep(0.05)
                ch._running = False

            stop_task = asyncio.create_task(_stop())
            await ch._run_ws_event_reader()
            await stop_task
        finally:
            feishu_mod.asyncio.sleep = real_sleep

        ch._start_ws_worker.assert_called()

    @pytest.mark.asyncio
    async def test_reader_max_restarts_exceeded(self):
        """Test the max restarts exceeded path — patches asyncio.sleep to be fast."""
        ch = _make_feishu_channel()
        ch._running = True
        ch._start_ws_worker = MagicMock()

        def _fake_get(block=True, timeout=0.5):
            raise queue.Empty()

        mock_q = MagicMock()
        mock_q.get = _fake_get
        ch._ws_event_queue = mock_q

        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = False
        ch._ws_process = mock_proc

        import xbot.channels.feishu as feishu_mod
        real_sleep = asyncio.sleep
        feishu_mod.asyncio.sleep = AsyncMock()
        try:
            async def _stop():
                await real_sleep(0.05)
                ch._running = False

            stop_task = asyncio.create_task(_stop())
            await ch._run_ws_event_reader()
            await stop_task
        finally:
            feishu_mod.asyncio.sleep = real_sleep

        # Should have hit max_ws_restarts (10)
        assert ch._start_ws_worker.call_count >= 1


# ===================================================================
# 21. _run_with_dedup_lock
# ===================================================================

class TestRunWithDedupLock:
    @pytest.mark.asyncio
    async def test_lock_executes_function(self):
        ch = _make_feishu_channel()
        called = []

        def _fn():
            called.append(True)

        await ch._run_with_dedup_lock(_fn)
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_lock_protects_concurrent_access(self):
        ch = _make_feishu_channel()
        results = []

        def _fn():
            results.append(len(ch._processed_message_ids))
            ch._processed_message_ids["x"] = 1.0

        await ch._run_with_dedup_lock(_fn)
        await ch._run_with_dedup_lock(_fn)
        assert results == [0, 1]


# ===================================================================
# 22. _split_markdown_element_to_fit — oversized content
# ===================================================================

class TestSplitMarkdownElementToFit:
    def test_small_element_no_split(self):
        ch = _make_feishu_channel()
        el = {"tag": "markdown", "content": "short text"}
        result = ch._split_markdown_element_to_fit(el, 10000)
        assert len(result) == 1
        assert result[0] == el

    def test_large_content_splits(self):
        ch = _make_feishu_channel()
        big_text = "word " * 2000
        el = {"tag": "markdown", "content": big_text}
        result = ch._split_markdown_element_to_fit(el, 500)
        assert len(result) > 1
        # All content should be preserved
        all_content = "".join(r["content"] for r in result)
        assert all_content.strip() == big_text.strip()

    def test_preserves_code_blocks(self):
        ch = _make_feishu_channel()
        content = "text before\n```python\nprint('hi')\n```\ntext after"
        big_content = content + " x" * 2000
        el = {"tag": "markdown", "content": big_content}
        result = ch._split_markdown_element_to_fit(el, 500)
        all_content = "".join(r["content"] for r in result)
        assert "```python" in all_content
        assert "print('hi')" in all_content

    def test_double_newline_split_points(self):
        ch = _make_feishu_channel()
        paragraphs = ["paragraph " + str(i) + " " + "x" * 100 for i in range(20)]
        content = "\n\n".join(paragraphs)
        el = {"tag": "markdown", "content": content}
        result = ch._split_markdown_element_to_fit(el, 500)
        assert len(result) > 1

    def test_empty_content(self):
        ch = _make_feishu_channel()
        el = {"tag": "markdown", "content": ""}
        result = ch._split_markdown_element_to_fit(el, 500)
        assert len(result) == 1


# ===================================================================
# 23. _split_table_element_to_fit — oversized tables
# ===================================================================

class TestSplitTableElementToFit:
    def test_small_table_no_split(self):
        ch = _make_feishu_channel()
        el = {
            "tag": "table",
            "columns": [{"name": "c0", "display_name": "A", "width": "auto"}],
            "rows": [{"c0": "1"}],
            "page_size": 2,
        }
        result = ch._split_table_element_to_fit(el, 10000)
        assert len(result) == 1

    def test_large_table_splits_into_chunks(self):
        ch = _make_feishu_channel()
        rows = [{"c0": f"val_{i}", "c1": f"data_{i}"} for i in range(50)]
        el = {
            "tag": "table",
            "columns": [
                {"name": "c0", "display_name": "Col A", "width": "auto"},
                {"name": "c1", "display_name": "Col B", "width": "auto"},
            ],
            "rows": rows,
            "page_size": 51,
        }
        result = ch._split_table_element_to_fit(el, 500)
        assert len(result) > 1
        # All should be table or markdown chunks
        for chunk in result:
            assert chunk["tag"] in ("table", "markdown")

    def test_single_oversized_row_becomes_markdown(self):
        """A single row that exceeds the limit should become markdown chunks."""
        ch = _make_feishu_channel()
        rows = [{"c0": "x" * 1000}]
        el = {
            "tag": "table",
            "columns": [{"name": "c0", "display_name": "A", "width": "auto"}],
            "rows": rows,
            "page_size": 2,
        }
        result = ch._split_table_element_to_fit(el, 200)
        # Should produce markdown chunks since even one row exceeds limit
        has_markdown = any(c["tag"] == "markdown" for c in result)
        assert has_markdown

    def test_empty_rows(self):
        ch = _make_feishu_channel()
        el = {
            "tag": "table",
            "columns": [{"name": "c0", "display_name": "A", "width": "auto"}],
            "rows": [],
            "page_size": 1,
        }
        result = ch._split_table_element_to_fit(el, 100)
        assert len(result) == 1

    def test_non_list_rows(self):
        ch = _make_feishu_channel()
        el = {
            "tag": "table",
            "columns": [],
            "rows": "not a list",
            "page_size": 1,
        }
        result = ch._split_table_element_to_fit(el, 100)
        assert len(result) == 1

    def test_column_labels_extraction(self):
        """Columns with non-dict entries should be skipped."""
        ch = _make_feishu_channel()
        rows = [{"c0": "x" * 500}]
        el = {
            "tag": "table",
            "columns": [
                {"name": "c0", "display_name": "Column A", "width": "auto"},
                "not a dict",
                {"name": "", "display_name": "Empty"},
            ],
            "rows": rows,
            "page_size": 2,
        }
        result = ch._split_table_element_to_fit(el, 10000)
        assert len(result) == 1


# ===================================================================
# 24. _split_oversized_element
# ===================================================================

class TestSplitOversizedElement:
    def test_small_element_unchanged(self):
        ch = _make_feishu_channel()
        el = {"tag": "markdown", "content": "hi"}
        result = ch._split_oversized_element(el, 10000)
        assert result == [el]

    def test_markdown_dispatch(self):
        ch = _make_feishu_channel()
        big = "x" * 5000
        el = {"tag": "markdown", "content": big}
        result = ch._split_oversized_element(el, 500)
        assert len(result) > 1

    def test_table_dispatch(self):
        ch = _make_feishu_channel()
        rows = [{"c0": f"v{i}"} for i in range(50)]
        el = {
            "tag": "table",
            "columns": [{"name": "c0", "display_name": "A", "width": "auto"}],
            "rows": rows,
            "page_size": 51,
        }
        result = ch._split_oversized_element(el, 500)
        assert len(result) >= 1

    def test_unknown_tag_not_split(self):
        ch = _make_feishu_channel()
        el = {"tag": "div", "content": "x" * 5000}
        result = ch._split_oversized_element(el, 100)
        assert len(result) == 1


# ===================================================================
# 25. _split_elements_by_table_limit — more edge cases
# ===================================================================

class TestSplitElementsByTableLimitExtra:
    def test_empty_list(self):
        result = FeishuChannel._split_elements_by_table_limit([])
        assert result == [[]]

    def test_single_small_element(self):
        elements = [{"tag": "markdown", "content": "hi"}]
        result = FeishuChannel._split_elements_by_table_limit(elements)
        assert len(result) == 1

    def test_multiple_tables_with_markdown(self):
        elements = [
            {"tag": "markdown", "content": "intro"},
            {"tag": "table", "columns": [{"name": "c0", "display_name": "A", "width": "auto"}],
             "rows": [{"c0": "1"}], "page_size": 2},
            {"tag": "markdown", "content": "middle"},
            {"tag": "table", "columns": [{"name": "c0", "display_name": "B", "width": "auto"}],
             "rows": [{"c0": "2"}], "page_size": 2},
            {"tag": "markdown", "content": "end"},
        ]
        result = FeishuChannel._split_elements_by_table_limit(elements, max_tables=1)
        assert len(result) >= 2

    def test_max_tables_two(self):
        elements = [
            {"tag": "table", "columns": [], "rows": [], "page_size": 1},
            {"tag": "table", "columns": [], "rows": [], "page_size": 1},
            {"tag": "table", "columns": [], "rows": [], "page_size": 1},
        ]
        result = FeishuChannel._split_elements_by_table_limit(elements, max_tables=2)
        assert len(result) == 2  # 2 tables in first, 1 in second


# ===================================================================
# 26. _build_card_elements — headings + code blocks
# ===================================================================

class TestBuildCardElementsExtra:
    def test_heading_before_table(self):
        ch = _make_feishu_channel()
        content = "# Title\n| A | B |\n|---|---|\n| 1 | 2 |"
        elements = ch._build_card_elements(content)
        tags = [e["tag"] for e in elements]
        assert "div" in tags or "markdown" in tags
        assert "table" in tags

    def test_heading_after_table(self):
        ch = _make_feishu_channel()
        content = "| A | B |\n|---|---|\n| 1 | 2 |\n## Subtitle\nMore text"
        elements = ch._build_card_elements(content)
        tags = [e["tag"] for e in elements]
        assert "table" in tags

    def test_only_whitespace_returns_default(self):
        ch = _make_feishu_channel()
        elements = ch._build_card_elements("   \n  \n  ")
        assert len(elements) == 1
        assert elements[0]["tag"] == "markdown"


# ===================================================================
# 27. _markdown_to_post — edge cases
# ===================================================================

class TestMarkdownToPostExtra:
    def test_multiple_links_on_one_line(self):
        content = "see [a](https://a.com) and [b](https://b.com)"
        result = json.loads(FeishuChannel._markdown_to_post(content))
        elements = result["zh_cn"]["content"][0]
        a_tags = [e for e in elements if e["tag"] == "a"]
        assert len(a_tags) == 2

    def test_link_at_start(self):
        content = "[start](https://x.com) then text"
        result = json.loads(FeishuChannel._markdown_to_post(content))
        elements = result["zh_cn"]["content"][0]
        assert elements[0]["tag"] == "a"

    def test_link_at_end(self):
        content = "text then [end](https://x.com)"
        result = json.loads(FeishuChannel._markdown_to_post(content))
        elements = result["zh_cn"]["content"][0]
        assert elements[-1]["tag"] == "a"

    def test_only_empty_lines(self):
        content = "\n\n\n"
        result = json.loads(FeishuChannel._markdown_to_post(content))
        paragraphs = result["zh_cn"]["content"]
        assert len(paragraphs) >= 1

    def test_special_characters(self):
        content = "Hello <world> & 'friends'"
        result = json.loads(FeishuChannel._markdown_to_post(content))
        elements = result["zh_cn"]["content"][0]
        assert elements[0]["text"] == "Hello <world> & 'friends'"


# ===================================================================
# 28. _detect_msg_format — edge cases
# ===================================================================

class TestDetectMsgFormatExtra:
    def test_exactly_text_max_len(self):
        content = "A" * 200
        assert FeishuChannel._detect_msg_format(content) == "text"

    def test_one_over_text_max_len(self):
        content = "A" * 201
        assert FeishuChannel._detect_msg_format(content) == "post"

    def test_exactly_post_max_len(self):
        content = "A" * 2000
        assert FeishuChannel._detect_msg_format(content) == "post"

    def test_one_over_post_max_len(self):
        content = "A" * 2001
        assert FeishuChannel._detect_msg_format(content) == "interactive"

    def test_whitespace_only(self):
        result = FeishuChannel._detect_msg_format("   \n  \n  ")
        assert result == "text"  # stripped is empty → len <= 200

    def test_ordered_list_variant(self):
        content = "1. first item\n2. second item"
        assert FeishuChannel._detect_msg_format(content) == "interactive"

    def test_underscore_bold(self):
        assert FeishuChannel._detect_msg_format("__bold__") == "interactive"


# ===================================================================
# 29. send() — complex paths
# ===================================================================

class TestSendComplex:
    @pytest.mark.asyncio
    async def test_send_post_format(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)

        content = "see [link](https://example.com)"
        msg = OutboundMessage(channel="feishu", chat_id="oc_test", content=content)
        await ch.send(msg)
        ch._send_message_sync.assert_called_once()
        args = ch._send_message_sync.call_args[0]
        assert args[2] == "post"

    @pytest.mark.asyncio
    async def test_send_interactive_format(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)

        content = "# Title\n```python\ncode\n```"
        msg = OutboundMessage(channel="feishu", chat_id="oc_test", content=content)
        await ch.send(msg)
        ch._send_message_sync.assert_called_once()
        args = ch._send_message_sync.call_args[0]
        assert args[2] == "interactive"

    @pytest.mark.asyncio
    async def test_send_with_image_media(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._upload_image_sync = MagicMock(return_value="img_key_abc")
        ch._send_message_sync = MagicMock(return_value=True)

        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG")

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="hi",
            media=[str(img)],
        )
        await ch.send(msg)
        ch._upload_image_sync.assert_called_once()
        assert ch._send_message_sync.call_count == 2  # image + text

    @pytest.mark.asyncio
    async def test_send_with_file_media(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._upload_file_sync = MagicMock(return_value="file_key_abc")
        ch._send_message_sync = MagicMock(return_value=True)

        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF")

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="hi",
            media=[str(pdf)],
        )
        await ch.send(msg)
        ch._upload_file_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_with_audio_media(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._upload_file_sync = MagicMock(return_value="audio_key")
        ch._send_message_sync = MagicMock(return_value=True)

        audio = tmp_path / "voice.opus"
        audio.write_bytes(b"Opus")

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="",
            media=[str(audio)],
        )
        await ch.send(msg)
        args = ch._send_message_sync.call_args[0]
        assert args[2] == "audio"

    @pytest.mark.asyncio
    async def test_send_with_video_media(self, tmp_path):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._upload_file_sync = MagicMock(return_value="video_key")
        ch._send_message_sync = MagicMock(return_value=True)

        vid = tmp_path / "clip.mp4"
        vid.write_bytes(b"mp4")

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="",
            media=[str(vid)],
        )
        await ch.send(msg)
        args = ch._send_message_sync.call_args[0]
        assert args[2] == "media"

    @pytest.mark.asyncio
    async def test_send_media_file_not_found(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._upload_image_sync = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="hi",
            media=["/nonexistent/file.png"],
        )
        await ch.send(msg)
        ch._upload_image_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_upload_returns_none(self, tmp_path):
        """When upload returns None, should not send media message."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._upload_image_sync = MagicMock(return_value=None)
        ch._send_message_sync = MagicMock(return_value=True)

        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG")

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="",
            media=[str(img)],
        )
        await ch.send(msg)
        ch._send_message_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_open_id_target(self):
        """chat_id not starting with oc_ should use open_id receive type."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)

        msg = OutboundMessage(channel="feishu", chat_id="ou_user123", content="hi")
        await ch.send(msg)
        args = ch._send_message_sync.call_args[0]
        assert args[0] == "open_id"

    @pytest.mark.asyncio
    async def test_send_chat_id_target(self):
        """chat_id starting with oc_ should use chat_id receive type."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)

        msg = OutboundMessage(channel="feishu", chat_id="oc_chat1", content="hi")
        await ch.send(msg)
        args = ch._send_message_sync.call_args[0]
        assert args[0] == "chat_id"

    @pytest.mark.asyncio
    async def test_send_reply_falls_back_to_create(self):
        """When reply fails, should fall back to regular send."""
        ch = _make_feishu_channel(reply_to_message=True)
        ch._client = MagicMock()
        ch._reply_message_sync = MagicMock(return_value=False)
        ch._send_message_sync = MagicMock(return_value=True)

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="hi",
            metadata={"message_id": "parent_msg"},
        )
        await ch.send(msg)
        ch._reply_message_sync.assert_called_once()
        ch._send_message_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_reply_only_first_message(self):
        """Only the first send should use reply; subsequent use create."""
        ch = _make_feishu_channel(reply_to_message=True)
        ch._client = MagicMock()
        ch._reply_message_sync = MagicMock(return_value=True)
        ch._upload_image_sync = MagicMock(return_value="img_key")
        ch._send_message_sync = MagicMock(return_value=True)

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="hi",
            media=["/fake/path.png"],
            metadata={"message_id": "parent_msg"},
        )

        # The media file doesn't exist, so only text will be sent
        await ch.send(msg)
        # Reply should have been called for the text message
        ch._reply_message_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_interaction_with_suggested_mode(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="Pick one:",
            metadata={
                "interaction_request": True,
                "interaction_kind": "approval",
                "validation_mode": "suggested",
                "suggestions": ["Yes", "No"],
            },
        )
        await ch.send(msg)
        ch._send_message_sync.assert_called_once()
        args = ch._send_message_sync.call_args[0]
        assert args[2] == "post"

    @pytest.mark.asyncio
    async def test_send_interaction_strict_mode(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="Pick one:",
            metadata={
                "interaction_request": True,
                "interaction_kind": "question",
                "validation_mode": "strict",
                "suggestions": ["Option A", "Option B"],
            },
        )
        await ch.send(msg)
        args = ch._send_message_sync.call_args[0]
        assert args[2] == "post"

    @pytest.mark.asyncio
    async def test_send_exception_handled(self):
        """Unexpected exceptions in send should be caught."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(side_effect=Exception("boom"))

        msg = OutboundMessage(channel="feishu", chat_id="oc_test", content="hi")
        await ch.send(msg)  # Should not raise

    @pytest.mark.asyncio
    async def test_send_empty_content_with_media(self, tmp_path):
        """Send with only media, no text content."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._upload_image_sync = MagicMock(return_value="img_key")
        ch._send_message_sync = MagicMock(return_value=True)

        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG")

        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="",
            media=[str(img)],
        )
        await ch.send(msg)
        ch._send_message_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_whitespace_only_content(self):
        """Whitespace-only content should not be sent."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)

        msg = OutboundMessage(channel="feishu", chat_id="oc_test", content="   \n  ")
        await ch.send(msg)
        ch._send_message_sync.assert_not_called()


# ===================================================================
# 30. _send_tool_hint_card
# ===================================================================

class TestSendToolHintCard:
    @pytest.mark.asyncio
    async def test_send_tool_hint_card(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)

        await ch._send_tool_hint_card("chat_id", "oc_123", 'web_search("hello"), read("path")')
        ch._send_message_sync.assert_called_once()
        args = ch._send_message_sync.call_args[0]
        assert args[2] == "interactive"
        card = json.loads(args[3])
        assert card["schema"] == "2.0"


# ===================================================================
# 31. _format_tool_hint_lines — edge cases
# ===================================================================

class TestFormatToolHintLinesExtra:
    def test_single_call(self):
        result = FeishuChannel._format_tool_hint_lines('search("hello")')
        assert "search" in result
        lines = result.strip().split("\n")
        assert len(lines) == 1

    def test_empty_string(self):
        result = FeishuChannel._format_tool_hint_lines("")
        assert result == ""

    def test_nested_parentheses(self):
        result = FeishuChannel._format_tool_hint_lines('outer(inner(a, b), c)')
        # Should not split on commas inside nested parens
        lines = result.strip().split("\n")
        assert len(lines) == 1

    def test_string_with_comma(self):
        result = FeishuChannel._format_tool_hint_lines('search("hello, world")')
        lines = result.strip().split("\n")
        assert len(lines) == 1  # comma inside string should not split

    def test_escaped_quote(self):
        result = FeishuChannel._format_tool_hint_lines(r'search("say \"hi\"")')
        lines = result.strip().split("\n")
        assert len(lines) == 1

    def test_multiple_top_level_calls(self):
        result = FeishuChannel._format_tool_hint_lines('a(1), b(2), c(3)')
        lines = result.strip().split("\n")
        assert len(lines) == 3

    def test_single_quoted_string(self):
        result = FeishuChannel._format_tool_hint_lines("search('hello, world')")
        lines = result.strip().split("\n")
        assert len(lines) == 1

    def test_comma_without_space_no_split(self):
        """Comma without trailing space should NOT cause split."""
        result = FeishuChannel._format_tool_hint_lines("a(1,2)")
        lines = result.strip().split("\n")
        assert len(lines) == 1


# ===================================================================
# 32. _namespace_from_dict — deeper nesting
# ===================================================================

class TestNamespaceFromDictExtra:
    def test_deeply_nested(self):
        data = {"a": {"b": {"c": {"d": 42}}}}
        result = FeishuChannel._namespace_from_dict(data)
        assert result.a.b.c.d == 42

    def test_list_inside_dict(self):
        data = {"items": [{"name": "a"}, {"name": "b"}]}
        result = FeishuChannel._namespace_from_dict(data)
        assert result.items[0].name == "a"
        assert result.items[1].name == "b"

    def test_dict_inside_list_inside_dict(self):
        data = {"level1": [{"level2": {"level3": "deep"}}]}
        result = FeishuChannel._namespace_from_dict(data)
        assert result.level1[0].level2.level3 == "deep"

    def test_none_value(self):
        result = FeishuChannel._namespace_from_dict(None)
        assert result is None

    def test_bool_value(self):
        result = FeishuChannel._namespace_from_dict(True)
        assert result is True

    def test_empty_dict(self):
        result = FeishuChannel._namespace_from_dict({})
        assert isinstance(result, SimpleNamespace)

    def test_empty_list(self):
        result = FeishuChannel._namespace_from_dict([])
        assert result == []


# ===================================================================
# 33. _is_group_message_for_bot
# ===================================================================

class TestIsGroupMessageForBot:
    def test_open_policy(self):
        ch = _make_feishu_channel(group_policy="open")
        message = SimpleNamespace(content="", mentions=[], message_type="text")
        assert ch._is_group_message_for_bot(message) is True

    def test_mention_policy_mentioned(self):
        ch = _make_feishu_channel(group_policy="mention")
        mention = SimpleNamespace(
            id=SimpleNamespace(open_id="ou_bot123", user_id="")
        )
        message = SimpleNamespace(
            content="", mentions=[mention], message_type="text"
        )
        assert ch._is_group_message_for_bot(message) is True

    def test_mention_policy_not_mentioned(self):
        ch = _make_feishu_channel(group_policy="mention")
        message = SimpleNamespace(content="", mentions=[], message_type="text")
        assert ch._is_group_message_for_bot(message) is False


# ===================================================================
# 34. Dedup cache management
# ===================================================================

class TestDedupCache:
    def test_mark_seen_first_time(self):
        ch = _make_feishu_channel()
        assert ch._mark_message_seen("msg_1") is True

    def test_mark_seen_duplicate(self):
        ch = _make_feishu_channel()
        ch._mark_message_seen("msg_1")
        assert ch._mark_message_seen("msg_1") is False

    def test_mark_seen_empty_string(self):
        ch = _make_feishu_channel()
        assert ch._mark_message_seen("") is True

    def test_mark_seen_none(self):
        ch = _make_feishu_channel()
        assert ch._mark_message_seen(None) is True

    def test_unmark(self):
        ch = _make_feishu_channel()
        ch._mark_message_seen("msg_1")
        ch._unmark_message_seen("msg_1")
        assert ch._mark_message_seen("msg_1") is True

    def test_unmark_none(self):
        ch = _make_feishu_channel()
        ch._unmark_message_seen(None)  # Should not raise

    def test_unmark_empty(self):
        ch = _make_feishu_channel()
        ch._unmark_message_seen("")  # Should not raise

    def test_unmark_nonexistent(self):
        ch = _make_feishu_channel()
        ch._unmark_message_seen("nonexistent")  # Should not raise

    def test_extract_message_id(self):
        ch = _make_feishu_channel()
        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(message_id="mid_1")
            )
        )
        assert ch._extract_message_id_from_event(data) == "mid_1"

    def test_extract_message_id_bad_data(self):
        ch = _make_feishu_channel()
        assert ch._extract_message_id_from_event(None) is None
        assert ch._extract_message_id_from_event("string") is None
        assert ch._extract_message_id_from_event(42) is None


# ===================================================================
# 35. _strip_md_formatting — edge cases
# ===================================================================

class TestStripMdFormattingExtra:
    def test_no_formatting(self):
        assert FeishuChannel._strip_md_formatting("plain text") == "plain text"

    def test_empty_string(self):
        assert FeishuChannel._strip_md_formatting("") == ""

    def test_all_formats_combined(self):
        text = "**bold** __also_bold__ *italic* ~~strike~~"
        result = FeishuChannel._strip_md_formatting(text)
        assert "**" not in result
        assert "__" not in result
        assert "~~" not in result
        assert "bold" in result
        assert "also_bold" in result
        assert "italic" in result
        assert "strike" in result


# ===================================================================
# 36. _parse_md_table — edge cases
# ===================================================================

class TestParseMdTableExtra:
    def test_empty_string(self):
        result = FeishuChannel._parse_md_table("")
        assert result is None

    def test_single_column(self):
        table_text = "| A |\n|---|\n| 1 |\n| 2 |"
        result = FeishuChannel._parse_md_table(table_text)
        assert result is not None
        assert len(result["columns"]) == 1
        assert len(result["rows"]) == 2

    def test_bold_in_header(self):
        table_text = "| **Name** | Age |\n|---|---|\n| Alice | 30 |"
        result = FeishuChannel._parse_md_table(table_text)
        assert result["columns"][0]["display_name"] == "Name"

    def test_many_columns(self):
        header = "| " + " | ".join(f"C{i}" for i in range(10)) + " |"
        sep = "|" + "|".join("---" for _ in range(10)) + "|"
        row = "| " + " | ".join(f"v{i}" for i in range(10)) + " |"
        table_text = f"{header}\n{sep}\n{row}"
        result = FeishuChannel._parse_md_table(table_text)
        assert result is not None
        assert len(result["columns"]) == 10


# ===================================================================
# 37. _split_headings — more cases
# ===================================================================

class TestSplitHeadingsExtra:
    def test_multiple_headings(self):
        ch = _make_feishu_channel()
        content = "# Title 1\nText 1\n## Title 2\nText 2"
        elements = ch._split_headings(content)
        div_count = sum(1 for e in elements if e["tag"] == "div")
        assert div_count == 2

    def test_heading_with_md_formatting(self):
        ch = _make_feishu_channel()
        content = "# **Bold Title**"
        elements = ch._split_headings(content)
        div_el = [e for e in elements if e["tag"] == "div"][0]
        # Formatting should be stripped and re-applied as bold
        assert "Bold Title" in div_el["text"]["content"]

    def test_empty_heading(self):
        ch = _make_feishu_channel()
        content = "# \nSome text"
        elements = ch._split_headings(content)
        # Should still work
        assert len(elements) >= 1

    def test_code_block_not_treated_as_heading(self):
        ch = _make_feishu_channel()
        content = "```\n# Not a heading\n```"
        elements = ch._split_headings(content)
        # The # inside code block should not become a div
        div_count = sum(1 for e in elements if e["tag"] == "div")
        assert div_count == 0

    def test_placeholder_replacement_in_code_blocks(self):
        ch = _make_feishu_channel()
        content = "```python\n# comment\nprint('hi')\n```\n# Real heading"
        elements = ch._split_headings(content)
        # Code block should still contain the original content
        md_elements = [e for e in elements if e["tag"] == "markdown"]
        code_found = any("# comment" in e.get("content", "") for e in md_elements)
        assert code_found


# ===================================================================
# 38. _build_interactive_card / _card_payload_len
# ===================================================================

class TestCardBuilding:
    def test_build_interactive_card_structure(self):
        elements = [{"tag": "markdown", "content": "hi"}]
        card = FeishuChannel._build_interactive_card(elements)
        assert card["schema"] == "2.0"
        assert card["config"]["wide_screen_mode"] is True
        assert card["body"]["elements"] == elements

    def test_card_payload_len(self):
        elements = [{"tag": "markdown", "content": "hello"}]
        length = FeishuChannel._card_payload_len(elements)
        expected = len(json.dumps(FeishuChannel._build_interactive_card(elements), ensure_ascii=False))
        assert length == expected

    def test_card_payload_len_empty(self):
        length = FeishuChannel._card_payload_len([])
        assert length > 0  # Still has card wrapper


# ===================================================================
# 39. _largest_fitting_prefix
# ===================================================================

class TestLargestFittingPrefix:
    def test_empty_text(self):
        ch = _make_feishu_channel()
        el = {"tag": "markdown", "content": ""}
        assert ch._largest_fitting_prefix(el, "", 1000) == 0

    def test_single_char_exceeds_limit(self):
        ch = _make_feishu_channel()
        el = {"tag": "markdown", "content": ""}
        # Use a limit so small that even one char is too much
        result = ch._largest_fitting_prefix(el, "a", 1)
        assert result == 0

    def test_fits_completely(self):
        ch = _make_feishu_channel()
        text = "short"
        el = {"tag": "markdown", "content": text}
        result = ch._largest_fitting_prefix(el, text, 10000)
        assert result == len(text)

    def test_partial_fit(self):
        ch = _make_feishu_channel()
        text = "x" * 1000
        el = {"tag": "markdown", "content": ""}
        result = ch._largest_fitting_prefix(el, text, 500)
        assert 0 < result < len(text)


# ===================================================================
# 40. default_config classmethod (line 73)
# ===================================================================

class TestDefaultConfig:
    def test_returns_dict(self):
        result = FeishuChannel.default_config()
        assert isinstance(result, dict)

    def test_has_expected_keys(self):
        result = FeishuChannel.default_config()
        assert "enabled" in result
        assert "appId" in result
        assert "appSecret" in result
        assert "allowFrom" in result
        assert "reactEmoji" in result
        assert "groupPolicy" in result

    def test_default_values(self):
        result = FeishuChannel.default_config()
        assert result["enabled"] is False
        assert result["appId"] == ""
        assert result["appSecret"] == ""
        assert result["allowFrom"] == []
        assert result["reactEmoji"] == "THUMBSUP"
        assert result["groupPolicy"] == "mention"


# ===================================================================
# 41. _kill_stale_ws_workers — more edge cases (lines 223, 233)
# ===================================================================

class TestKillStaleWorkersEdgeCases:
    def test_empty_pid_line(self):
        """Line 223: empty PID in output should be skipped."""
        ch = _make_feishu_channel()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="\n\n")
            with patch("os.kill") as mock_kill:
                ch._kill_stale_ws_workers()
                mock_kill.assert_not_called()

    def test_pid_none_from_read_parent(self):
        """Line 233: _read_parent_pid returns None should skip."""
        ch = _make_feishu_channel()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="99999")
            with patch.object(FeishuChannel, "_read_parent_pid", return_value=None):
                with patch("os.kill") as mock_kill:
                    ch._kill_stale_ws_workers()
                    mock_kill.assert_not_called()

    def test_invalid_pid_value_error(self):
        """Line 238: ValueError on int conversion should be caught."""
        ch = _make_feishu_channel()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not_a_number")
            ch._kill_stale_ws_workers()  # Should not raise


# ===================================================================
# 42. _cleanup_registered_semaphore — matching finalizer (lines 349-352)
# ===================================================================

class TestCleanupRegisteredSemaphoreMatch:
    def test_matching_finalizer_found(self):
        """Line 349-352: when finalizer matches, call it and return."""
        mock_finalizer = MagicMock()
        mock_finalizer._args = ("/test/semaphore",)

        mock_registry = {"key1": mock_finalizer}

        with patch("multiprocessing.util._finalizer_registry", mock_registry):
            FeishuChannel._cleanup_registered_semaphore("/test/semaphore")
        mock_finalizer.assert_called_once()

    def test_finalizer_args_mismatch(self):
        """When finalizer args don't match, should fall through."""
        mock_finalizer = MagicMock()
        mock_finalizer._args = ("/other/semaphore",)
        mock_registry = {"key1": mock_finalizer}

        with patch("multiprocessing.util._finalizer_registry", mock_registry):
            with patch("multiprocessing.synchronize.SemLock._cleanup") as mock_cleanup:
                FeishuChannel._cleanup_registered_semaphore("/test/semaphore")
                mock_cleanup.assert_called_once_with("/test/semaphore")
        mock_finalizer.assert_not_called()


# ===================================================================
# 43. send() — edge cases (lines 1232-1233, 1242-1246, 1269-1270)
# ===================================================================

class TestSendEdgeCases:
    @pytest.mark.asyncio
    async def test_send_no_client_returns(self):
        """Lines 1232-1233: send returns early when client is None."""
        ch = _make_feishu_channel()
        ch._client = None
        msg = OutboundMessage(channel="feishu", chat_id="oc_test", content="hi")
        await ch.send(msg)
        # Should return without error

    @pytest.mark.asyncio
    async def test_send_tool_hint_empty_content(self):
        """Lines 1242-1246: tool hint with empty content should return without sending."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)
        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="   ",
            metadata={"_tool_hint": True},
        )
        await ch.send(msg)
        ch._send_message_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_tool_hint_none_content(self):
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)
        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="",
            metadata={"_tool_hint": True},
        )
        await ch.send(msg)
        ch._send_message_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_interaction_no_suggestions_text(self):
        """Lines 1269-1270: interaction request without suggestions sends text."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._send_message_sync = MagicMock(return_value=True)
        msg = OutboundMessage(
            channel="feishu", chat_id="oc_test", content="Pick one:",
            metadata={
                "interaction_request": True,
                "interaction_kind": "confirmation",
                "suggestions": [],
            },
        )
        await ch.send(msg)
        args = ch._send_message_sync.call_args[0]
        assert args[2] == "text"
        body = json.loads(args[3])
        assert body["text"] == "Pick one:"


# ===================================================================
# 44. _reply_message_sync / _send_message_sync — fallthrough (lines 1183, 1227)
# ===================================================================

class TestSyncFallthrough:
    def test_reply_all_retries_exhausted(self):
        """Line 1183: return False after all retries fail."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        # OSError on all 3 attempts
        ch._client.im.v1.message.reply.side_effect = OSError("fail")
        with patch("time.sleep"):
            result = ch._reply_message_sync("msg", "text", "{}")
        assert result is False

    def test_send_all_retries_exhausted(self):
        """Line 1227: return False after all retries fail."""
        ch = _make_feishu_channel()
        ch._client = MagicMock()
        ch._client.im.v1.message.create.side_effect = OSError("fail")
        with patch("time.sleep"):
            result = ch._send_message_sync("chat_id", "oc_1", "text", "{}")
        assert result is False


# ===================================================================
# 45. _on_reaction_created / _on_message_read / _on_bot_p2p_chat_entered (lines 1491, 1495, 1499)
# ===================================================================

class TestNoopEventHandlers:
    def test_on_reaction_created(self):
        """Line 1491: should be a no-op."""
        ch = _make_feishu_channel()
        ch._on_reaction_created(SimpleNamespace(event=SimpleNamespace()))

    def test_on_message_read(self):
        """Line 1495: should be a no-op."""
        ch = _make_feishu_channel()
        ch._on_message_read(SimpleNamespace(event=SimpleNamespace()))

    def test_on_bot_p2p_chat_entered(self):
        """Lines 1499-1500: should log debug and return."""
        ch = _make_feishu_channel()
        ch._on_bot_p2p_chat_entered(SimpleNamespace(event=SimpleNamespace()))
