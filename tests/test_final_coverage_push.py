"""Final coverage push tests — target ~121 uncovered lines across 8 modules.

Modules targeted:
- xbot/crew/cli/plan_cmd.py (69 missed)
- xbot/channels/qq.py (53 missed)
- xbot/platform/utils/helpers.py (47 missed)
- xbot/tools/web.py (50 missed)
- xbot/runtime/session/conversation_store.py (40 missed)
- xbot/channels/discord.py (47 missed)
- xbot/channels/dingtalk.py (45 missed)
- xbot/interfaces/cli/goal.py (61 missed)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
import time
from pathlib import Path
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
        "start": AsyncMock(),
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
            msg.sender_staff_id = data.get("senderStaffId")
            msg.sender_nick = data.get("senderNick", "")
            return msg

    chatbot_mod.ChatbotMessage = _FakeChatbotMessage
    mod.chatbot = chatbot_mod
    sys.modules["dingtalk_stream"] = mod
    sys.modules["dingtalk_stream.chatbot"] = chatbot_mod


def _install_botpy_stubs():
    if "botpy" in sys.modules:
        return
    mod = types.ModuleType("botpy")

    class _FakeIntents:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeClient:
        def __init__(self, *a, **kw):
            self.api = MagicMock()
            self.api.post_c2c_message = AsyncMock()
            self.api.post_group_message = AsyncMock()
            self.robot = MagicMock()
            self.robot.name = "test-bot"

        async def start(self, **kw):
            await asyncio.sleep(0)

        async def close(self):
            pass

    mod.Intents = _FakeIntents
    mod.Client = _FakeClient
    sys.modules["botpy"] = mod

    msg_mod = types.ModuleType("botpy.message")

    class _C2CMessage:
        pass

    class _GroupMessage:
        pass

    msg_mod.C2CMessage = _C2CMessage
    msg_mod.GroupMessage = _GroupMessage
    sys.modules["botpy.message"] = msg_mod


_install_dingtalk_stubs()
_install_botpy_stubs()


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_msg_bus():
    from xbot.platform.bus.queue import MessageBus
    bus = MagicMock(spec=MessageBus)
    bus.publish = AsyncMock()
    return bus


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. xbot/crew/cli/plan_cmd.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestPlanCmd:
    def _runner(self):
        from typer.testing import CliRunner
        from xbot.crew.cli.plan_cmd import app
        return CliRunner(), app

    def test_plan_empty_goal(self):
        runner, app = self._runner()
        result = runner.invoke(app, ["plan", ""])
        assert result.exit_code != 0
        assert "empty" in result.output.lower() or "error" in result.output.lower()

    def test_plan_too_long_goal(self):
        runner, app = self._runner()
        result = runner.invoke(app, ["plan", "x" * 11000])
        assert result.exit_code != 0
        assert "long" in result.output.lower() or "error" in result.output.lower()

    def test_plan_invalid_workspace(self):
        runner, app = self._runner()
        result = runner.invoke(app, ["plan", "test goal", "--workspace", "/no/such/path/xyz"])
        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_plan_invalid_tier(self, tmp_path):
        runner, app = self._runner()
        result = runner.invoke(app, ["plan", "test goal", "--workspace", str(tmp_path), "--tier", "bogus"])
        assert result.exit_code != 0
        assert "invalid tier" in result.output.lower()

    def test_plan_success_preview_save(self, tmp_path):
        runner, app = self._runner()
        with patch("xbot.crew.cli.plan_cmd.CrewPlanner") as MockPlanner:
            inst = MockPlanner.return_value
            plan = MagicMock()
            plan.name = "test-crew"
            plan.process = "sequential"
            role = MagicMock()
            role.name = "researcher"
            plan.roles = [role]
            plan.tasks = [MagicMock(), MagicMock()]
            plan.confidence = 0.9
            plan.planning_time = 0.5
            plan.to_dict.return_value = {"name": "test-crew"}
            inst.plan.return_value = plan
            inst.preview.return_value = "preview text"
            inst.generate_config.return_value = "name: test-crew\n"
            inst.save_config = MagicMock()

            result = runner.invoke(app, [
                "plan", "Build something",
                "--workspace", str(tmp_path),
                "--tier", "core",
                "--preview",
                "--output", str(tmp_path / "out.yaml"),
            ])
            assert result.exit_code == 0
            assert "Summary" in result.output

    def test_plan_json_output(self, tmp_path):
        runner, app = self._runner()
        with patch("xbot.crew.cli.plan_cmd.CrewPlanner") as MockPlanner:
            inst = MockPlanner.return_value
            plan = MagicMock()
            plan.to_dict.return_value = {"name": "crew"}
            inst.plan.return_value = plan

            result = runner.invoke(app, [
                "plan", "Goal", "--workspace", str(tmp_path), "--json",
            ])
            assert result.exit_code == 0

    def test_plan_save_to_temp(self, tmp_path):
        runner, app = self._runner()
        with patch("xbot.crew.cli.plan_cmd.CrewPlanner") as MockPlanner:
            inst = MockPlanner.return_value
            plan = MagicMock()
            plan.name = "crew"
            plan.process = "seq"
            plan.roles = []
            plan.tasks = []
            plan.confidence = 0.5
            plan.planning_time = 0.1
            inst.plan.return_value = plan
            inst.generate_config.return_value = "yaml: content\n"

            result = runner.invoke(app, [
                "plan", "Goal", "--workspace", str(tmp_path), "--save",
            ])
            assert result.exit_code == 0
            assert "Saved config" in result.output

    def test_plan_tier_all(self, tmp_path):
        runner, app = self._runner()
        with patch("xbot.crew.cli.plan_cmd.CrewPlanner") as MockPlanner:
            inst = MockPlanner.return_value
            plan = MagicMock()
            plan.name = "c"
            plan.process = "s"
            plan.roles = []
            plan.tasks = []
            plan.confidence = 0.5
            plan.planning_time = 0.1
            inst.plan.return_value = plan
            inst.generate_config.return_value = "x: y\n"

            result = runner.invoke(app, [
                "plan", "Goal", "--workspace", str(tmp_path), "--tier", "all",
            ])
            assert result.exit_code == 0

    def test_plan_planning_fails(self, tmp_path):
        runner, app = self._runner()
        with patch("xbot.crew.cli.plan_cmd.CrewPlanner") as MockPlanner:
            inst = MockPlanner.return_value
            inst.plan.side_effect = RuntimeError("plan boom")

            result = runner.invoke(app, ["plan", "Goal", "--workspace", str(tmp_path)])
            assert result.exit_code != 0
            assert "planning failed" in result.output.lower()

    def test_run_dynamic_empty_goal(self):
        runner, app = self._runner()
        result = runner.invoke(app, ["run-dynamic", ""])
        assert result.exit_code != 0

    def test_run_dynamic_invalid_workspace(self):
        runner, app = self._runner()
        result = runner.invoke(app, ["run-dynamic", "goal", "--workspace", "/no/such/path"])
        assert result.exit_code != 0

    def test_run_dynamic_invalid_tier(self, tmp_path):
        runner, app = self._runner()
        result = runner.invoke(app, [
            "run-dynamic", "goal", "--workspace", str(tmp_path), "--tier", "bogus",
        ])
        assert result.exit_code != 0

    def test_run_dynamic_dry_run(self, tmp_path):
        runner, app = self._runner()
        with patch("xbot.crew.cli.plan_cmd.CrewPlanner") as MockPlanner:
            inst = MockPlanner.return_value
            plan = MagicMock()
            plan.name = "c"
            role = MagicMock()
            role.name = "researcher"
            plan.roles = [role]
            plan.tasks = [MagicMock()]
            plan.confidence = 0.5
            inst.plan.return_value = plan
            inst.generate_config.return_value = "yaml: yes\n"

            result = runner.invoke(app, [
                "run-dynamic", "Goal", "--workspace", str(tmp_path), "--dry-run",
            ])
            assert result.exit_code == 0
            assert "Dry Run" in result.output

    def test_run_dynamic_planning_fails(self, tmp_path):
        runner, app = self._runner()
        with patch("xbot.crew.cli.plan_cmd.CrewPlanner") as MockPlanner:
            inst = MockPlanner.return_value
            inst.plan.side_effect = RuntimeError("boom")

            result = runner.invoke(app, [
                "run-dynamic", "Goal", "--workspace", str(tmp_path),
            ])
            assert "planning failed" in result.output.lower()

    def test_print_crew_result_success(self):
        from xbot.crew.cli.plan_cmd import _print_crew_result
        result = MagicMock()
        result.status = "completed"
        result.total_time = 1.5
        result.summary = "all good"
        task_result = MagicMock()
        task_result.status = "success"
        task_result.task_name = "task1"
        task_result.started_at = None
        task_result.finished_at = None
        result.task_results = [task_result]

        _print_crew_result(result)  # Should not raise

    def test_print_crew_result_failure(self):
        from xbot.crew.cli.plan_cmd import _print_crew_result
        result = MagicMock()
        result.status = "failed"
        result.total_time = 0
        result.summary = "error"
        result.task_results = []
        _print_crew_result(result)

    def test_print_crew_result_with_timestamps(self):
        from xbot.crew.cli.plan_cmd import _print_crew_result
        from datetime import datetime, timedelta
        result = MagicMock()
        result.status = "completed"
        result.total_time = 2.0
        result.summary = ""
        tr = MagicMock()
        tr.status = "completed"
        tr.task_name = "t"
        now = datetime.now()
        tr.started_at = now
        tr.finished_at = now + timedelta(seconds=3)
        result.task_results = [tr]
        _print_crew_result(result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. xbot/channels/qq.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestQQChannel:
    def _make_channel(self, **kwargs):
        from xbot.channels.qq import QQChannel, QQConfig
        defaults = {
            "enabled": True, "app_id": "aid", "secret": "sec",
            "allow_from": ["*"], "msg_format": "plain",
        }
        defaults.update(kwargs)
        cfg = QQConfig.model_validate(defaults)
        return QQChannel(cfg, _make_msg_bus())

    def test_default_config(self):
        from xbot.channels.qq import QQChannel
        assert isinstance(QQChannel.default_config(), dict)

    def test_cleanup_expired(self):
        ch = self._make_channel()
        old = time.monotonic() - 100_000
        ch._processed_ids = {"old1": old, "old2": old, "new1": time.monotonic()}
        ch._cleanup_processed_ids(time.monotonic())
        assert "new1" in ch._processed_ids
        assert "old1" not in ch._processed_ids

    def test_cleanup_overflow(self):
        ch = self._make_channel()
        now = time.monotonic()
        # Force overflow: PROCESSED_ID_MAX_ENTRIES=10000, so add 10001 entries
        from xbot.channels.qq import PROCESSED_ID_MAX_ENTRIES
        for i in range(PROCESSED_ID_MAX_ENTRIES + 5):
            ch._processed_ids[f"k{i}"] = now + i * 0.001
        ch._cleanup_processed_ids(now + 1000)
        assert len(ch._processed_ids) <= PROCESSED_ID_MAX_ENTRIES

    def test_mark_processed_dedup(self):
        ch = self._make_channel()
        assert ch._mark_processed("m1") is True
        assert ch._mark_processed("m1") is False

    @pytest.mark.asyncio
    async def test_send_no_client(self):
        from xbot.platform.bus.events import OutboundMessage
        ch = self._make_channel()
        ch._client = None
        msg = OutboundMessage(channel="test", chat_id="c", content="hi")
        await ch.send(msg)  # Should warn, not raise

    @pytest.mark.asyncio
    async def test_send_c2c(self):
        from xbot.platform.bus.events import OutboundMessage
        ch = self._make_channel()
        ch._client = MagicMock()
        ch._client.api.post_c2c_message = AsyncMock()
        msg = OutboundMessage(channel="test", chat_id="u1", content="hi", metadata={"message_id": "m1"})
        ch._chat_type_cache["u1"] = "c2c"
        await ch.send(msg)
        ch._client.api.post_c2c_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_group(self):
        from xbot.platform.bus.events import OutboundMessage
        ch = self._make_channel()
        ch._client = MagicMock()
        ch._client.api.post_group_message = AsyncMock()
        msg = OutboundMessage(channel="test", chat_id="g1", content="hi", metadata={"message_id": "m1"})
        ch._chat_type_cache["g1"] = "group"
        await ch.send(msg)
        ch._client.api.post_group_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_markdown(self):
        from xbot.platform.bus.events import OutboundMessage
        ch = self._make_channel(msg_format="markdown")
        ch._client = MagicMock()
        ch._client.api.post_c2c_message = AsyncMock()
        msg = OutboundMessage(channel="test", chat_id="u", content="**bold**", metadata={"message_id": "m"})
        await ch.send(msg)
        call_kwargs = ch._client.api.post_c2c_message.call_args.kwargs
        assert "markdown" in call_kwargs

    @pytest.mark.asyncio
    async def test_send_error_caught(self):
        from xbot.platform.bus.events import OutboundMessage
        ch = self._make_channel()
        ch._client = MagicMock()
        ch._client.api.post_c2c_message = AsyncMock(side_effect=RuntimeError("boom"))
        msg = OutboundMessage(channel="test", chat_id="u", content="hi", metadata={"message_id": "m"})
        await ch.send(msg)  # Should not raise

    @pytest.mark.asyncio
    async def test_on_message_group(self):
        ch = self._make_channel()
        ch._handle_message = AsyncMock()
        msg = MagicMock()
        msg.id = "123"
        msg.content = "  hello  "
        msg.group_openid = "g1"
        msg.author.member_openid = "user1"
        await ch._on_message(msg, is_group=True)
        ch._handle_message.assert_awaited_once()
        assert ch._chat_type_cache["g1"] == "group"

    @pytest.mark.asyncio
    async def test_on_message_c2c_with_user_openid(self):
        ch = self._make_channel()
        ch._handle_message = AsyncMock()
        msg = MagicMock()
        msg.id = "abc"
        msg.content = "hello"
        msg.author = SimpleNamespace(user_openid="u1")
        await ch._on_message(msg, is_group=False)
        ch._handle_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_message_dedup(self):
        ch = self._make_channel()
        ch._handle_message = AsyncMock()
        msg = MagicMock()
        msg.id = "dup"
        msg.content = "hello"
        msg.author = SimpleNamespace(user_openid="u1")
        await ch._on_message(msg, is_group=False)
        await ch._on_message(msg, is_group=False)
        assert ch._handle_message.await_count == 1

    @pytest.mark.asyncio
    async def test_on_message_empty_content(self):
        ch = self._make_channel()
        ch._handle_message = AsyncMock()
        msg = MagicMock()
        msg.id = "e1"
        msg.content = "   "
        await ch._on_message(msg, is_group=False)
        ch._handle_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_message_handler_exception(self):
        ch = self._make_channel()
        ch._handle_message = AsyncMock(side_effect=RuntimeError("boom"))
        msg = MagicMock()
        msg.id = "x1"
        msg.content = "hi"
        msg.author = SimpleNamespace(user_openid="u")
        await ch._on_message(msg, is_group=False)  # Should log, not raise

    @pytest.mark.asyncio
    async def test_start_no_sdk(self):
        from xbot.channels.qq import QQChannel, QQConfig
        ch = self._make_channel()
        with patch("xbot.channels.qq.QQ_AVAILABLE", False):
            await ch.start()

    @pytest.mark.asyncio
    async def test_start_no_credentials(self):
        from xbot.channels.qq import QQChannel, QQConfig
        cfg = QQConfig.model_validate({"enabled": True, "app_id": "", "secret": ""})
        ch = QQChannel(cfg, _make_msg_bus())
        await ch.start()

    @pytest.mark.asyncio
    async def test_stop(self):
        ch = self._make_channel()
        ch._client = MagicMock()
        ch._client.close = AsyncMock()
        ch._running = True
        await ch.stop()
        assert ch._running is False
        ch._client.close.assert_awaited_once()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. xbot/platform/utils/helpers.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSyncWorkspaceTemplates:
    def test_no_source_root(self, tmp_path):
        from xbot.platform.utils.helpers import sync_workspace_templates
        with patch("xbot.platform.utils.helpers.pkg_files") as mock_pkg:
            # Simulate missing templates
            mock_pkg.side_effect = Exception("no package")
            result = sync_workspace_templates(tmp_path)
            assert result == []

    def test_sync_creates_missing(self, tmp_path):
        from xbot.platform.utils.helpers import sync_workspace_templates

        # Build a fake traversable templates directory on disk
        # pkg_files("xbot") / "templates" / "workspace"
        src = tmp_path / "src"
        ws_root = src / "templates" / "workspace"
        ws_root.mkdir(parents=True)
        (ws_root / "README.md").write_text("hello")
        (ws_root / "subdir").mkdir()
        (ws_root / "subdir" / "inner.txt").write_text("inner")
        # Hidden file should be skipped
        (ws_root / ".hidden").write_text("skip")

        workspace = tmp_path / "workspace_out"
        workspace.mkdir()

        def _fake_pkg(name):
            return src

        with patch("xbot.platform.utils.helpers.pkg_files", side_effect=_fake_pkg):
            added = sync_workspace_templates(workspace, silent=True)

        assert "README.md" in added
        assert any("subdir" in a for a in added)
        assert (workspace / "README.md").read_text() == "hello"
        assert (workspace / ".claude" / "skills").is_dir()
        assert (workspace / "commands").is_dir()

    def test_sync_does_not_overwrite(self, tmp_path):
        from xbot.platform.utils.helpers import sync_workspace_templates

        src = tmp_path / "src"
        ws_root = src / "templates" / "workspace"
        ws_root.mkdir(parents=True)
        (ws_root / "existing.txt").write_text("src-content")

        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "existing.txt").write_text("user-content")

        def _fake_pkg(name):
            return src

        with patch("xbot.platform.utils.helpers.pkg_files", side_effect=_fake_pkg):
            added = sync_workspace_templates(workspace, silent=True)

        assert "existing.txt" not in added
        assert (workspace / "existing.txt").read_text() == "user-content"


class TestCopyTraversableDir:
    def test_copy(self, tmp_path):
        from xbot.platform.utils.helpers import _copy_traversable_dir

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_bytes(b"A")
        (src / "nested").mkdir()
        (src / "nested" / "b.txt").write_bytes(b"B")

        dest = tmp_path / "dest"
        _copy_traversable_dir(src, dest)

        assert (dest / "a.txt").read_bytes() == b"A"
        assert (dest / "nested" / "b.txt").read_bytes() == b"B"


class TestLoadInitPack:
    def test_missing_pack(self, tmp_path):
        from xbot.platform.utils.helpers import load_init_pack

        def _fake_pkg(name):
            return tmp_path

        with patch("xbot.platform.utils.helpers.pkg_files", side_effect=_fake_pkg):
            with pytest.raises(FileNotFoundError):
                load_init_pack("missing")

    def test_load_pack(self, tmp_path):
        from xbot.platform.utils.helpers import load_init_pack

        packs_dir = tmp_path / "templates" / "packs"
        packs_dir.mkdir(parents=True)
        (packs_dir / "default.json").write_text(json.dumps({"commands": []}))

        def _fake_pkg(name):
            return tmp_path

        with patch("xbot.platform.utils.helpers.pkg_files", side_effect=_fake_pkg):
            data = load_init_pack("default")
        assert data == {"commands": []}


class TestSyncWorkspaceCommandPack:
    def test_empty_commands(self, tmp_path):
        from xbot.platform.utils.helpers import sync_workspace_command_pack

        packs_dir = tmp_path / "templates" / "packs"
        packs_dir.mkdir(parents=True)
        (packs_dir / "default.json").write_text(json.dumps({"commands": []}))

        def _fake_pkg(name):
            return tmp_path

        with patch("xbot.platform.utils.helpers.pkg_files", side_effect=_fake_pkg):
            added = sync_workspace_command_pack(tmp_path / "ws", "default")
        assert added == []

    def test_copies_file_command(self, tmp_path):
        from xbot.platform.utils.helpers import sync_workspace_command_pack

        packs_dir = tmp_path / "templates" / "packs"
        packs_dir.mkdir(parents=True)
        (packs_dir / "default.json").write_text(json.dumps({"commands": ["hello"]}))

        cmds_dir = tmp_path / "templates" / "commands"
        cmds_dir.mkdir(parents=True)
        (cmds_dir / "hello.md").write_text("# hello")

        workspace = tmp_path / "ws"
        workspace.mkdir()

        def _fake_pkg(name):
            return tmp_path

        with patch("xbot.platform.utils.helpers.pkg_files", side_effect=_fake_pkg):
            added = sync_workspace_command_pack(workspace, "default")
        assert len(added) == 1
        assert (workspace / "commands" / "hello.md").exists()

    def test_skips_existing(self, tmp_path):
        from xbot.platform.utils.helpers import sync_workspace_command_pack

        packs_dir = tmp_path / "templates" / "packs"
        packs_dir.mkdir(parents=True)
        (packs_dir / "default.json").write_text(json.dumps({"commands": ["hello"]}))

        cmds_dir = tmp_path / "templates" / "commands"
        cmds_dir.mkdir(parents=True)
        (cmds_dir / "hello.md").write_text("# hello")

        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "commands").mkdir()
        (workspace / "commands" / "hello.md").write_text("existing")

        def _fake_pkg(name):
            return tmp_path

        with patch("xbot.platform.utils.helpers.pkg_files", side_effect=_fake_pkg):
            added = sync_workspace_command_pack(workspace, "default")
        assert added == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. xbot/tools/web.py — WebFetchTool
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestWebFetchTool:
    def _make_tool(self, **kwargs):
        from xbot.tools.web import WebFetchTool
        defaults = {
            "max_chars": 1000,
            "proxy": None,
            "web_config": SimpleNamespace(
                disable_security_checks=True,
                web_fetch_use_jina=True,
            ),
            "timeout": 5.0,
        }
        defaults.update(kwargs)
        return WebFetchTool(**defaults)

    @pytest.mark.asyncio
    async def test_jina_success(self):
        tool = self._make_tool()
        jina_response = MagicMock()
        jina_response.status_code = 200
        jina_response.json.return_value = {
            "data": {"title": "T", "content": "hello world", "url": "https://example.com"},
        }

        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=jina_response)
            client.__aenter__.return_value = client
            MockClient.return_value = client

            result = await tool.execute("https://example.com")
        data = json.loads(result)
        assert data["extractor"] == "jina"
        assert "hello world" in data["text"]

    @pytest.mark.asyncio
    async def test_jina_rate_limit_falls_back(self, tmp_path):
        tool = self._make_tool()

        jina_resp = MagicMock()
        jina_resp.status_code = 429

        redir_resp = MagicMock()
        redir_resp.status_code = 200
        redir_resp.headers = {"content-type": "text/html"}
        redir_resp.text = "<html><body>Hello</body></html>"
        redir_resp.url = "https://example.com"

        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            # First call: Jina (rate limited). Second call: readability fallback.
            client.get = AsyncMock(side_effect=[jina_resp, redir_resp])
            client.__aenter__.return_value = client
            MockClient.return_value = client

            with patch("xbot.tools.web._validate_and_pin_url", new=AsyncMock(return_value=(True, "", {}))):
                with patch("xbot.tools.web._PinnedAsyncHTTPTransport"):
                    result = await tool.execute("https://example.com")
        data = json.loads(result)
        assert "Hello" in data["text"] or data.get("extractor") in ("readability", "raw")

    @pytest.mark.asyncio
    async def test_jina_failure_falls_back(self):
        tool = self._make_tool()

        fallback_resp = MagicMock()
        fallback_resp.status_code = 200
        fallback_resp.headers = {"content-type": "application/json"}
        fallback_resp.json.return_value = {"key": "value"}
        fallback_resp.url = "https://example.com"

        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=[RuntimeError("jina down"), fallback_resp])
            client.__aenter__.return_value = client
            MockClient.return_value = client

            with patch("xbot.tools.web._validate_and_pin_url", new=AsyncMock(return_value=(True, "", {}))):
                with patch("xbot.tools.web._PinnedAsyncHTTPTransport"):
                    result = await tool.execute("https://example.com")
        data = json.loads(result)
        assert data["extractor"] == "json"

    @pytest.mark.asyncio
    async def test_proxy_fallback_blocked_when_security_enabled(self):
        tool = self._make_tool(
            proxy="http://proxy:8080",
            web_config=SimpleNamespace(
                disable_security_checks=False,
                web_fetch_use_jina=False,
            ),
        )
        with patch("xbot.tools.web._validate_url_safe", new=AsyncMock(return_value=(True, ""))):
            result = await tool.execute("https://example.com")
        data = json.loads(result)
        assert "error" in data
        assert "proxy" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_url_validation_failure(self):
        tool = self._make_tool(
            web_config=SimpleNamespace(
                disable_security_checks=False,
                web_fetch_use_jina=False,
            ),
        )
        with patch("xbot.tools.web._validate_url_safe", new=AsyncMock(return_value=(False, "bad scheme"))):
            result = await tool.execute("ftp://example.com")
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_readability_proxy_error(self):
        import httpx as _httpx
        tool = self._make_tool(
            proxy="http://proxy:8080",
            web_config=SimpleNamespace(
                disable_security_checks=True,
                web_fetch_use_jina=False,
            ),
        )
        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=_httpx.ProxyError("proxy boom"))
            client.__aenter__.return_value = client
            MockClient.return_value = client
            result = await tool.execute("https://example.com")
        data = json.loads(result)
        assert "proxy" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_readability_html_content(self):
        tool = self._make_tool(
            web_config=SimpleNamespace(
                disable_security_checks=True,
                web_fetch_use_jina=False,
            ),
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "text/html"}
        resp.text = "<html><head><title>T</title></head><body><p>Hello</p></body></html>"
        resp.url = "https://example.com"

        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=resp)
            client.__aenter__.return_value = client
            MockClient.return_value = client

            with patch("xbot.tools.web._validate_and_pin_url", new=AsyncMock(return_value=(True, "", {}))):
                with patch("xbot.tools.web._PinnedAsyncHTTPTransport"):
                    with patch("readability.Document") as MockDoc:
                        doc = MagicMock()
                        doc.summary.return_value = "<p>Hello</p>"
                        doc.title.return_value = "My Title"
                        MockDoc.return_value = doc
                        result = await tool.execute("https://example.com")

        data = json.loads(result)
        assert data["extractor"] == "readability"
        assert "My Title" in data["text"]

    @pytest.mark.asyncio
    async def test_jina_text_mode_strips_markdown(self):
        tool = self._make_tool()
        jina_response = MagicMock()
        jina_response.status_code = 200
        jina_response.json.return_value = {
            "data": {"title": "T", "content": "**bold**", "url": "https://example.com"},
        }
        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=jina_response)
            client.__aenter__.return_value = client
            MockClient.return_value = client
            result = await tool.execute("https://example.com", extract_mode="text")
        data = json.loads(result)
        assert "**" not in data["text"]

    @pytest.mark.asyncio
    async def test_camelCase_backward_compat(self):
        tool = self._make_tool()
        jina_response = MagicMock()
        jina_response.status_code = 200
        jina_response.json.return_value = {
            "data": {"title": "", "content": "body", "url": "https://x.com"},
        }
        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=jina_response)
            client.__aenter__.return_value = client
            MockClient.return_value = client
            result = await tool.execute(
                "https://x.com", extractMode="text", maxChars=500,
            )
        data = json.loads(result)
        assert "body" in data["text"]

    @pytest.mark.asyncio
    async def test_jina_int_extract_mode(self):
        tool = self._make_tool()
        jina_response = MagicMock()
        jina_response.status_code = 200
        jina_response.json.return_value = {
            "data": {"title": "", "content": "x" * 2000, "url": "https://x.com"},
        }
        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=jina_response)
            client.__aenter__.return_value = client
            MockClient.return_value = client
            result = await tool.execute("https://x.com", extract_mode=500)
        data = json.loads(result)
        assert data["truncated"] is True

    def _mock_async_client_with(self, side_effect):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=side_effect)
        client.__aenter__.return_value = client
        return client


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. xbot/runtime/session/conversation_store.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestConversationStoreCoverage:
    def _make_store(self, tmp_path, max_cache=500):
        from xbot.runtime.session.conversation_store import ConversationStore
        with patch("xbot.runtime.session.conversation_store.get_legacy_sessions_dir", return_value=tmp_path / "legacy"):
            (tmp_path / "legacy").mkdir(exist_ok=True)
            return ConversationStore(tmp_path, max_cache_size=max_cache)

    def test_save_append_mode(self, tmp_path):
        from xbot.runtime.session.conversation_store import ConversationSession
        store = self._make_store(tmp_path)
        sess = store.get_or_create("k1")
        sess.add_message("user", "hi")
        store.save(sess)  # First save: full
        sess.add_message("user", "again")
        store.save(sess)  # Second save: append
        reloaded = self._make_store(tmp_path).get("k1")
        assert reloaded is not None
        assert len(reloaded.messages) == 2

    def test_save_metadata_dirty_forces_full(self, tmp_path):
        from xbot.runtime.session.conversation_store import ConversationSession
        store = self._make_store(tmp_path)
        sess = store.get_or_create("k2")
        sess.add_message("user", "m")
        store.save(sess)
        sess.metadata["note"] = "changed"
        sess.mark_metadata_dirty()
        store.save(sess)
        reloaded = self._make_store(tmp_path).get("k2")
        assert reloaded.metadata.get("note") == "changed"

    def test_get_returns_none_when_missing(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.get("nope") is None

    def test_delete_message(self, tmp_path):
        from xbot.runtime.session.conversation_store import ConversationSession
        store = self._make_store(tmp_path)
        sess = store.get_or_create("k3")
        sess.add_message("user", "a")
        sess.add_message("assistant", "b")
        store.save(sess)
        assert store.delete_message(sess, 0) is True
        assert len(sess.messages) == 1
        assert store.delete_message(sess, 99) is False

    def test_delete_message_adjusts_consolidated(self, tmp_path):
        store = self._make_store(tmp_path)
        sess = store.get_or_create("k4")
        sess.add_message("user", "a")
        sess.add_message("user", "b")
        sess.last_consolidated = 2
        store.save(sess)
        store.delete_message(sess, 0)
        assert sess.last_consolidated == 1

    def test_compact(self, tmp_path):
        store = self._make_store(tmp_path)
        sess = store.get_or_create("k5")
        sess.add_message("user", "m")
        store.save(sess)
        store.compact(sess)
        path = store._get_session_path("k5")
        assert path.exists()

    def test_delete_nonexistent(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.delete("nope") is False

    def test_delete_existing(self, tmp_path):
        store = self._make_store(tmp_path)
        sess = store.get_or_create("k6")
        sess.add_message("user", "m")
        store.save(sess)
        assert store.delete("k6") is True
        assert store.get("k6") is None

    def test_eviction(self, tmp_path):
        store = self._make_store(tmp_path, max_cache=2)
        s1 = store.get_or_create("a")
        s1.add_message("user", "x")
        store.save(s1)
        s2 = store.get_or_create("b")
        s2.add_message("user", "y")
        store.save(s2)
        # Force older updated_at
        s1.updated_at = s1.updated_at.replace(year=2000)
        s3 = store.get_or_create("c")
        s3.add_message("user", "z")
        store.save(s3)
        # a should have been evicted
        assert "a" not in store._cache

    def test_corrupt_jsonl_skipped(self, tmp_path):
        from xbot.runtime.session.conversation_store import ConversationStore
        store = self._make_store(tmp_path)
        path = store._get_session_path("k7")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"_type":"metadata","key":"k7","created_at":"2024-01-01T00:00:00","updated_at":"2024-01-01T00:00:00","metadata":{},"last_consolidated":0}\n'
            'this is not json\n'
            '{"role":"user","content":"hi"}\n'
        )
        sess = store.get("k7")
        assert sess is not None
        assert len(sess.messages) == 1

    def test_empty_session_returns_none(self, tmp_path):
        from xbot.runtime.session.conversation_store import ConversationStore
        store = self._make_store(tmp_path)
        path = store._get_session_path("k8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("   \n\n")
        assert store.get("k8") is None

    def test_message_preview(self):
        from xbot.runtime.session.conversation_store import ConversationStore
        assert ConversationStore._message_preview(None) == ""
        assert ConversationStore._message_preview("short") == "short"
        long_msg = "x" * 200
        preview = ConversationStore._message_preview(long_msg, max_chars=10)
        assert preview.endswith("...")
        assert len(preview) <= 13


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. xbot/channels/discord.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDiscordChannel:
    def _make_channel(self, **kwargs):
        from xbot.channels.discord import DiscordChannel, DiscordConfig
        defaults = {
            "enabled": True, "token": "tok", "allow_from": ["*"],
            "group_policy": "mention",
        }
        defaults.update(kwargs)
        cfg = DiscordConfig.model_validate(defaults)
        return DiscordChannel(cfg, _make_msg_bus())

    @pytest.mark.asyncio
    async def test_gateway_loop_invalid_json(self):
        ch = self._make_channel()

        events = ["not json", json.dumps({"op": 9, "d": None})]

        class _FakeWS:
            def __init__(self, items):
                self._items = list(items)
                self._i = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._i >= len(self._items):
                    raise StopAsyncIteration
                item = self._items[self._i]
                self._i += 1
                return item

        ch._ws = _FakeWS(events)
        ch._heartbeat_failed = asyncio.Event()
        await ch._gateway_loop()

    @pytest.mark.asyncio
    async def test_gateway_loop_heartbeat_failure(self):
        ch = self._make_channel()

        class _FakeWS:
            def __init__(self, ch):
                self.ch = ch
                self._done = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._done:
                    raise StopAsyncIteration
                self.ch._heartbeat_failed.set()
                self._done = True
                return json.dumps({"op": 0, "t": "PING", "d": None})

        ch._ws = _FakeWS(ch)
        await ch._gateway_loop()
        assert not ch._heartbeat_failed.is_set()

    @pytest.mark.asyncio
    async def test_gateway_loop_hello_starts_heartbeat(self):
        ch = self._make_channel()
        fake_ws = MagicMock()
        fake_ws.send = AsyncMock()

        events = [
            json.dumps({"op": 10, "d": {"heartbeat_interval": 100}}),
            json.dumps({"op": 7, "d": None}),
        ]

        class _FakeWS:
            def __init__(self, items):
                self._items = list(items)
                self._i = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._i >= len(self._items):
                    raise StopAsyncIteration
                item = self._items[self._i]
                self._i += 1
                return item

        ws = _FakeWS(events)
        # Need to also carry `send` attribute
        ws.send = fake_ws.send
        ch._ws = ws
        ch._running = True
        ch._heartbeat_failed = asyncio.Event()
        await ch._gateway_loop()
        fake_ws.send.assert_awaited()

    @pytest.mark.asyncio
    async def test_gateway_loop_ready_captures_bot_id(self):
        ch = self._make_channel()
        fake_ws = MagicMock()
        fake_ws.send = AsyncMock()

        events = [
            json.dumps({"op": 0, "t": "READY", "s": 1, "d": {"user": {"id": "12345"}}}),
            json.dumps({"op": 9, "d": None}),
        ]

        class _FakeWS:
            def __init__(self, items):
                self._items = list(items)
                self._i = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._i >= len(self._items):
                    raise StopAsyncIteration
                item = self._items[self._i]
                self._i += 1
                return item

        ws = _FakeWS(events)
        ws.send = fake_ws.send
        ch._ws = ws
        ch._heartbeat_failed = asyncio.Event()
        await ch._gateway_loop()
        assert ch._bot_user_id == "12345"
        assert ch._seq == 1

    @pytest.mark.asyncio
    async def test_identify_no_ws(self):
        ch = self._make_channel()
        ch._ws = None
        await ch._identify()  # Should return silently

    @pytest.mark.asyncio
    async def test_start_heartbeat_cancels_existing(self):
        ch = self._make_channel()
        ch._running = True
        fake_ws = MagicMock()
        fake_ws.send = AsyncMock()
        ch._ws = fake_ws

        await ch._start_heartbeat(0.05)
        task1 = ch._heartbeat_task
        assert task1 is not None
        await ch._start_heartbeat(0.05)
        assert ch._heartbeat_task is not task1
        # Cleanup
        ch._running = False
        await asyncio.sleep(0.1)
        if ch._heartbeat_task:
            ch._heartbeat_task.cancel()
            with patch("contextlib.suppress"):
                try:
                    await ch._heartbeat_task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_start_no_token(self):
        ch = self._make_channel()
        ch.config.token = ""
        await ch.start()  # Should return without error

    @pytest.mark.asyncio
    async def test_send_no_http(self):
        from xbot.platform.bus.events import OutboundMessage
        ch = self._make_channel()
        ch._http = None
        msg = OutboundMessage(channel="test", chat_id="c", content="hi")
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_send_payload_success(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        ch._http.post = AsyncMock(return_value=resp)
        ok = await ch._send_payload("url", {}, {"content": "hi"})
        assert ok is True

    @pytest.mark.asyncio
    async def test_send_payload_rate_limit(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.json.return_value = {"retry_after": 0.01}
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ch._http.post = AsyncMock(side_effect=[rate_resp, ok_resp])
        ok = await ch._send_payload("url", {}, {"content": "hi"})
        assert ok is True

    @pytest.mark.asyncio
    async def test_send_payload_failure(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=RuntimeError("boom"))
        ok = await ch._send_payload("url", {}, {"content": "hi"})
        assert ok is False

    def test_should_respond_in_group_open(self):
        ch = self._make_channel(group_policy="open")
        assert ch._should_respond_in_group({}, "hi") is True

    def test_should_respond_in_group_mention_match(self):
        ch = self._make_channel(group_policy="mention")
        ch._bot_user_id = "99"
        payload = {"mentions": [{"id": "99"}], "channel_id": "c"}
        assert ch._should_respond_in_group(payload, "hi") is True

    def test_should_respond_in_group_mention_content(self):
        ch = self._make_channel(group_policy="mention")
        ch._bot_user_id = "99"
        assert ch._should_respond_in_group({"channel_id": "c"}, "<@99> hi") is True
        assert ch._should_respond_in_group({"channel_id": "c"}, "<@!99> hi") is True

    def test_should_respond_in_group_no_mention(self):
        ch = self._make_channel(group_policy="mention")
        ch._bot_user_id = "99"
        assert ch._should_respond_in_group({"channel_id": "c"}, "hi") is False

    @pytest.mark.asyncio
    async def test_is_duplicate_message(self):
        ch = self._make_channel()
        assert await ch._is_duplicate_message("m1") is False
        assert await ch._is_duplicate_message("m1") is True

    @pytest.mark.asyncio
    async def test_handle_message_create_bot_ignored(self):
        ch = self._make_channel()
        ch._handle_message = AsyncMock()
        await ch._handle_message_create({"author": {"bot": True, "id": "x"}, "channel_id": "c"})
        ch._handle_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop(self):
        ch = self._make_channel()
        ch._running = True
        ch._ws = MagicMock()
        ch._ws.close = AsyncMock()
        ch._http = AsyncMock()
        ch._http.aclose = AsyncMock()
        await ch.stop()
        assert ch._running is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. xbot/channels/dingtalk.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDingTalkChannel:
    def _make_channel(self):
        from xbot.channels.dingtalk import DingTalkChannel, DingTalkConfig
        cfg = DingTalkConfig.model_validate({
            "enabled": True, "client_id": "id", "client_secret": "sec",
            "allow_from": ["*"],
        })
        return DingTalkChannel(cfg, _make_msg_bus())

    def test_default_config(self):
        from xbot.channels.dingtalk import DingTalkChannel
        assert isinstance(DingTalkChannel.default_config(), dict)

    @pytest.mark.asyncio
    async def test_start_no_sdk(self):
        ch = self._make_channel()
        with patch("xbot.channels.dingtalk.DINGTALK_AVAILABLE", False):
            await ch.start()

    @pytest.mark.asyncio
    async def test_start_no_credentials(self):
        from xbot.channels.dingtalk import DingTalkChannel, DingTalkConfig
        cfg = DingTalkConfig.model_validate({"enabled": True, "client_id": "", "client_secret": ""})
        ch = DingTalkChannel(cfg, _make_msg_bus())
        await ch.start()

    @pytest.mark.asyncio
    async def test_start_success(self):
        ch = self._make_channel()
        ch._running = False
        fake_client = MagicMock()
        fake_client.start = AsyncMock(side_effect=[None, RuntimeError("stop")])

        async def _fake_sleep(*a, **kw):
            ch._running = False

        with patch("xbot.channels.dingtalk.DingTalkStreamClient", return_value=fake_client):
            with patch("xbot.channels.dingtalk.Credential"):
                with patch("xbot.channels.dingtalk.asyncio.sleep", new=AsyncMock(side_effect=_fake_sleep)):
                    await ch.start()

    @pytest.mark.asyncio
    async def test_stop(self):
        ch = self._make_channel()
        ch._running = True
        ch._http = AsyncMock()
        ch._http.aclose = AsyncMock()
        await ch.stop()
        assert ch._running is False

    def test_schedule_inbound_same_loop(self):
        ch = self._make_channel()

        async def _noop():
            pass

        coro = _noop()
        ch._loop = asyncio.new_event_loop()
        try:
            # Different loop, not running → falls back to _create_tracked_task path
            ch._create_tracked_task = MagicMock(return_value=MagicMock())
            ch._schedule_inbound_message(coro, "test")
        finally:
            ch._loop.close()
            ch._loop = None

    def test_schedule_inbound_different_loop(self):
        ch = self._make_channel()

        async def _noop():
            pass

        coro = _noop()
        # Simulate a running event loop that's different from stored
        running_loop = MagicMock()
        running_loop.is_running.return_value = True
        ch._loop = running_loop
        ch._create_tracked_task = MagicMock()
        ch._loop.call_soon_threadsafe = MagicMock()

        # The check "current_loop is not self._loop" requires us to NOT be in the stored loop
        ch._schedule_inbound_message(coro, "test")
        ch._loop.call_soon_threadsafe.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_access_token_cached(self):
        ch = self._make_channel()
        ch._access_token = "tok"
        ch._token_expiry = time.time() + 1000
        result = await ch._get_access_token()
        assert result == "tok"

    @pytest.mark.asyncio
    async def test_get_access_token_refresh(self):
        ch = self._make_channel()
        ch._access_token = None
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.json.return_value = {"accessToken": "new", "expireIn": 7200}
        resp.raise_for_status = MagicMock()
        ch._http.post = AsyncMock(return_value=resp)
        result = await ch._get_access_token()
        assert result == "new"

    @pytest.mark.asyncio
    async def test_get_access_token_no_http(self):
        ch = self._make_channel()
        ch._http = None
        result = await ch._get_access_token()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_access_token_error(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=RuntimeError("boom"))
        result = await ch._get_access_token()
        assert result is None

    def test_is_http_url(self):
        from xbot.channels.dingtalk import DingTalkChannel
        assert DingTalkChannel._is_http_url("https://x.com") is True
        assert DingTalkChannel._is_http_url("/local/path") is False

    def test_guess_upload_type(self):
        ch = self._make_channel()
        assert ch._guess_upload_type("/path/image.jpg") == "image"
        assert ch._guess_upload_type("/path/audio.mp3") == "voice"
        assert ch._guess_upload_type("/path/video.mp4") == "video"
        assert ch._guess_upload_type("/path/data.bin") == "file"

    def test_guess_filename(self):
        ch = self._make_channel()
        assert ch._guess_filename("https://x.com/foo.jpg", "image") == "foo.jpg"
        assert ch._guess_filename("", "image") == "image.jpg"
        assert ch._guess_filename("", "voice") == "audio.amr"
        assert ch._guess_filename("", "file") == "file.bin"

    @pytest.mark.asyncio
    async def test_read_media_http(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"BYTES"
        resp.headers = {"content-type": "image/jpeg"}
        ch._http.get = AsyncMock(return_value=resp)
        data, name, ctype = await ch._read_media_bytes("https://x.com/foo.jpg")
        assert data == b"BYTES"
        assert name == "foo.jpg"
        assert ctype == "image/jpeg"

    @pytest.mark.asyncio
    async def test_read_media_local(self, tmp_path):
        ch = self._make_channel()
        f = tmp_path / "a.txt"
        f.write_bytes(b"hello")
        data, name, ctype = await ch._read_media_bytes(str(f))
        assert data == b"hello"
        assert name == "a.txt"

    @pytest.mark.asyncio
    async def test_read_media_missing(self):
        ch = self._make_channel()
        data, name, ctype = await ch._read_media_bytes("/no/such/file.txt")
        assert data is None

    @pytest.mark.asyncio
    async def test_read_media_empty(self):
        ch = self._make_channel()
        data, name, ctype = await ch._read_media_bytes("")
        assert data is None

    @pytest.mark.asyncio
    async def test_send_no_token(self):
        from xbot.platform.bus.events import OutboundMessage
        ch = self._make_channel()
        ch._access_token = None
        ch._token_expiry = 0
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=RuntimeError("no token"))
        msg = OutboundMessage(channel="test", chat_id="u", content="hi")
        await ch.send(msg)  # Should return early

    @pytest.mark.asyncio
    async def test_send_batch_private(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {}
        ch._http.post = AsyncMock(return_value=resp)
        ok = await ch._send_batch_message("tok", "user1", "sampleText", {"text": "hi"})
        assert ok is True

    @pytest.mark.asyncio
    async def test_send_batch_group(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {}
        ch._http.post = AsyncMock(return_value=resp)
        ok = await ch._send_batch_message("tok", "group:conv1", "sampleText", {"text": "hi"})
        assert ok is True

    @pytest.mark.asyncio
    async def test_send_batch_failure(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "error"
        ch._http.post = AsyncMock(return_value=resp)
        ok = await ch._send_batch_message("tok", "u", "k", {})
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_batch_no_http(self):
        ch = self._make_channel()
        ch._http = None
        ok = await ch._send_batch_message("tok", "u", "k", {})
        assert ok is False

    @pytest.mark.asyncio
    async def test_on_message_group(self):
        ch = self._make_channel()
        ch._handle_message = AsyncMock()
        await ch._on_message("hi", "user1", "Alice", conversation_type="2", conversation_id="conv1")
        call = ch._handle_message.call_args.kwargs
        assert call["chat_id"] == "group:conv1"

    @pytest.mark.asyncio
    async def test_on_message_private(self):
        ch = self._make_channel()
        ch._handle_message = AsyncMock()
        await ch._on_message("hi", "user1", "Alice")
        call = ch._handle_message.call_args.kwargs
        assert call["chat_id"] == "user1"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. xbot/interfaces/cli/goal.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGoalCLI:
    def _runner(self):
        from typer.testing import CliRunner
        from xbot.interfaces.cli.goal import goal_app
        return CliRunner(), goal_app

    def test_init(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        result = runner.invoke(app, ["init", "my objective", "--verify", "pytest", "--max-loops", "5"])
        assert result.exit_code == 0
        assert "Goal created" in result.output

    def test_list_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No goals" in result.output

    def test_list_with_goals(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        runner.invoke(app, ["init", "obj1"])
        result = runner.invoke(app, ["list"])
        assert "obj1" in result.output

    def test_show(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        runner.invoke(app, ["init", "show obj"])
        result = runner.invoke(app, ["show"])
        assert "show obj" in result.output

    def test_show_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        result = runner.invoke(app, ["show", "nope"])
        assert result.exit_code != 0

    def test_modify(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        runner.invoke(app, ["init", "obj"])
        result = runner.invoke(app, ["modify", "--verify", "make test", "--max-loops", "20"])
        assert result.exit_code == 0
        assert "Goal updated" in result.output

    def test_modify_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        runner.invoke(app, ["init", "obj"])
        result = runner.invoke(app, ["modify"])
        assert "Nothing to modify" in result.output

    def test_modify_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        result = runner.invoke(app, ["modify"])
        assert result.exit_code != 0

    def test_clear_no_confirm(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        runner.invoke(app, ["init", "obj"])
        result = runner.invoke(app, ["clear", "--force"])
        assert "Goal cleared" in result.output

    def test_clear_active_requires_force(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        runner.invoke(app, ["init", "obj"])
        from xbot.runtime.session.goal_store import GoalStore, GoalStatus
        store = GoalStore(tmp_path)
        goal = store.find_active()
        goal.status = GoalStatus.ACTIVE
        store.save(goal)
        result = runner.invoke(app, ["clear"])
        assert "active" in result.output.lower()

    def test_clear_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        result = runner.invoke(app, ["clear", "nope"])
        assert result.exit_code != 0

    def test_run_no_active(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        result = runner.invoke(app, ["run"])
        assert "no active goal" in result.output.lower()

    def test_run_already_achieved(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        result_init = runner.invoke(app, ["init", "obj"])
        from xbot.runtime.session.goal_store import GoalStore, GoalStatus
        store = GoalStore(tmp_path)
        goal = store.find_active()
        goal.status = GoalStatus.ACHIEVED
        store.save(goal)
        result = runner.invoke(app, ["run", goal.goal_id])
        assert "already achieved" in result.output.lower()

    def test_run_exhausted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner, app = self._runner()
        runner.invoke(app, ["init", "obj", "--max-loops", "2"])
        from xbot.runtime.session.goal_store import GoalStore, GoalStatus
        store = GoalStore(tmp_path)
        goal = store.find_active()
        goal.status = GoalStatus.UNMET
        goal.loop_count = 2
        store.save(goal)
        result = runner.invoke(app, ["run", goal.goal_id])
        assert "exhausted" in result.output.lower()


class TestGoalRunner:
    def _make_goal(self, tmp_path):
        from xbot.runtime.session.goal_store import GoalState, GoalStore
        store = GoalStore(tmp_path)
        goal = GoalState(
            goal_id="g1",
            objective="do thing",
            workspace=str(tmp_path),
            session_key="goal:g1",
            verify_cmd="echo ok",
            max_loops=2,
        )
        store.save(goal)
        return goal, store

    @pytest.mark.asyncio
    async def test_verify_with_cmd_success(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(return_value="")
        result = await runner._verify()
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_with_cmd_failure(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        goal.verify_cmd = "exit 1"
        store.save(goal)
        runner = GoalRunner(goal, store)
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(return_value="")
        result = await runner._verify()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_with_cmd_error(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        goal.verify_cmd = "nonexistent_command_xyz"
        store.save(goal)
        runner = GoalRunner(goal, store)
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(return_value="")
        # Should not raise, just return False
        result = await runner._verify()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_no_cmd_agent_pass(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        goal.verify_cmd = None
        store.save(goal)
        runner = GoalRunner(goal, store)
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(return_value="[GOAL_ACHIEVED] yes")
        result = await runner._verify()
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_no_cmd_agent_fail(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        goal.verify_cmd = None
        store.save(goal)
        runner = GoalRunner(goal, store)
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(return_value="[GOAL_NOT_MET] missing")
        result = await runner._verify()
        assert result is False

    @pytest.mark.asyncio
    async def test_run_cmd_success(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        code, out = await runner._run_cmd("echo hello")
        assert code == 0
        assert "hello" in out

    @pytest.mark.asyncio
    async def test_run_cmd_failure(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        code, out = await runner._run_cmd("exit 42")
        assert code != 0

    def test_build_first_prompt(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        prompt = runner._build_first_prompt()
        assert "do thing" in prompt
        assert "echo ok" in prompt

    def test_build_retry_prompt(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        prompt = runner._build_retry_prompt()
        assert "Previous attempt" in prompt

    def test_persist_log(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        runner._persist_log("plan", "content here")
        log_file = tmp_path / ".goal" / "logs" / f"loop-{goal.loop_count}-plan.md"
        assert log_file.exists()
        assert "content here" in log_file.read_text()

    def test_check_git_preconditions_no_repo(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        # tmp_path is not a git repo
        result = runner._check_git_preconditions(tmp_path)
        assert result is False

    @pytest.mark.asyncio
    async def test_on_progress_streaming(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        await runner._on_progress("hello", event_type="content_delta")
        assert runner._streaming_active is True
        await runner._on_progress("", event_type="result", event_data={"terminal_reason": "done"})
        assert runner._terminal_reason == "done"
        assert runner._streaming_active is False

    @pytest.mark.asyncio
    async def test_on_progress_thinking(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        await runner._on_progress("Thinking: x", event_type="thinking")
        assert runner._streaming_active is True

    @pytest.mark.asyncio
    async def test_call_agent_success(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(return_value="result text")
        out = await runner._call_agent("hi")
        assert out == "result text"

    @pytest.mark.asyncio
    async def test_call_agent_failure(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        goal, store = self._make_goal(tmp_path)
        runner = GoalRunner(goal, store)
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(side_effect=RuntimeError("boom"))
        out = await runner._call_agent("hi")
        assert out == ""


class TestGoalPermissionHandler:
    def test_is_safe_tool(self):
        from xbot.interfaces.cli.goal import GoalPermissionHandler
        h = GoalPermissionHandler()
        assert h.is_safe_tool("anything") is True

    @pytest.mark.asyncio
    async def test_can_use_tool(self):
        from xbot.interfaces.cli.goal import GoalPermissionHandler
        h = GoalPermissionHandler()
        decision, data = await h.can_use_tool("tool", {"a": 1}, None)
        assert decision == "allow"

    def test_noop_methods(self):
        from xbot.interfaces.cli.goal import GoalPermissionHandler
        h = GoalPermissionHandler()
        h.set_session_context()
        h.clear_session_context()
        h.set_current_session()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Extra coverage — second pass
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestQQExtraCoverage:
    def _make_channel(self, **kwargs):
        from xbot.channels.qq import QQChannel, QQConfig
        defaults = {
            "enabled": True, "app_id": "aid", "secret": "sec",
            "allow_from": ["*"], "msg_format": "plain",
        }
        defaults.update(kwargs)
        cfg = QQConfig.model_validate(defaults)
        return QQChannel(cfg, _make_msg_bus())

    def test_init_from_dict(self):
        """QQChannel can be initialized from a dict config."""
        from xbot.channels.qq import QQChannel
        ch = QQChannel({"app_id": "aid", "secret": "sec"}, _make_msg_bus())
        assert ch.config.app_id == "aid"

    @pytest.mark.asyncio
    async def test_start_happy_path(self):
        """Test start() creates client and runs bot."""
        ch = self._make_channel()

        async def fake_run_bot():
            ch._running = False  # Exit loop

        ch._run_bot = fake_run_bot
        await ch.start()
        assert ch._client is not None
        assert ch._running is False

    @pytest.mark.asyncio
    async def test_run_bot_reconnects(self):
        """Test _run_bot reconnects on error."""
        ch = self._make_channel()
        call_count = 0

        class FakeClient:
            async def start(self, **kw):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("first fail")
                ch._running = False  # Stop after second attempt

        ch._client = FakeClient()
        ch._running = True
        with patch("xbot.channels.qq.asyncio.sleep", new=AsyncMock()):
            await ch._run_bot()
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_stop_with_close_error(self):
        """Test stop() handles client close error gracefully."""
        ch = self._make_channel()
        ch._client = MagicMock()
        ch._client.close = AsyncMock(side_effect=RuntimeError("close fail"))
        ch._running = True
        await ch.stop()  # Should not raise
        assert ch._running is False

    @pytest.mark.asyncio
    async def test_send_error_logs(self):
        """Test send() logs error on API failure."""
        from xbot.platform.bus.events import OutboundMessage
        ch = self._make_channel()
        ch._client = MagicMock()
        ch._client.api.post_c2c_message = AsyncMock(side_effect=RuntimeError("api fail"))
        msg = OutboundMessage(channel="qq", chat_id="u", content="hi", metadata={"message_id": "m"})
        await ch.send(msg)  # Should log, not raise

    @pytest.mark.asyncio
    async def test_on_message_with_author_id(self):
        """Test _on_message when author has id attribute."""
        ch = self._make_channel()
        ch._handle_message = AsyncMock()
        msg = MagicMock()
        msg.id = "msg1"
        msg.content = "hello"
        # Author with id attribute (not user_openid)
        msg.author = SimpleNamespace(id="author_id_123")
        await ch._on_message(msg, is_group=False)
        call_kwargs = ch._handle_message.call_args.kwargs
        assert call_kwargs["sender_id"] == "author_id_123"


class TestGoalExtraCoverage:
    def test_permission_handler_build_callback(self):
        """Test build_can_use_tool_callback returns a callable."""
        from xbot.interfaces.cli.goal import GoalPermissionHandler
        h = GoalPermissionHandler()
        # Patch claude_agent_sdk to avoid ImportError
        sdk_mod = types.ModuleType("claude_agent_sdk")
        types_mod = types.ModuleType("claude_agent_sdk.types")

        class PermissionResultAllow:
            pass

        class ToolPermissionContext:
            pass

        types_mod.PermissionResultAllow = PermissionResultAllow
        types_mod.ToolPermissionContext = ToolPermissionContext
        sdk_mod.types = types_mod

        with patch.dict(sys.modules, {
            "claude_agent_sdk": sdk_mod,
            "claude_agent_sdk.types": types_mod,
        }):
            cb = h.build_can_use_tool_callback()
        assert callable(cb)

    def test_build_first_prompt_no_verify(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState, GoalStore
        store = GoalStore(tmp_path)
        goal = GoalState(
            goal_id="g1",
            objective="do thing",
            workspace=str(tmp_path),
            session_key="goal:g1",
            verify_cmd=None,  # No verify command
            max_loops=2,
        )
        store.save(goal)
        runner = GoalRunner(goal, store)
        prompt = runner._build_first_prompt()
        assert "do thing" in prompt
        assert "Validation" not in prompt

    def test_build_retry_prompt_with_verify(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState, GoalStore
        store = GoalStore(tmp_path)
        goal = GoalState(
            goal_id="g1",
            objective="do thing",
            workspace=str(tmp_path),
            session_key="goal:g1",
            verify_cmd="pytest",
            max_loops=2,
        )
        store.save(goal)
        runner = GoalRunner(goal, store)
        prompt = runner._build_retry_prompt()
        assert "pytest" in prompt

    def test_git_rev_parse_no_git(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState, GoalStore
        store = GoalStore(tmp_path)
        goal = GoalState(
            goal_id="g1", objective="o", workspace=str(tmp_path),
            session_key="goal:g1", max_loops=2,
        )
        store.save(goal)
        runner = GoalRunner(goal, store)
        # No git repo in tmp_path
        result = runner._git_rev_parse(tmp_path)
        assert result == ""

    def test_setup_signals(self, tmp_path):
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState, GoalStore
        store = GoalStore(tmp_path)
        goal = GoalState(
            goal_id="g1", objective="o", workspace=str(tmp_path),
            session_key="goal:g1", max_loops=2,
        )
        store.save(goal)
        runner = GoalRunner(goal, store)
        runner._setup_signals()
        # Should not raise

    def test_goal_resume_alias(self, tmp_path, monkeypatch):
        """Test goal resume is an alias for goal run."""
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from xbot.interfaces.cli.goal import goal_app
        runner = CliRunner()
        runner.invoke(goal_app, ["init", "obj"])
        # Mock asyncio.run to prevent the blocking runner.run() call
        with patch("xbot.interfaces.cli.goal.asyncio.run"):
            result = runner.invoke(goal_app, ["resume"])
        # Verify resume delegates to run: should print goal details
        assert result.exit_code == 0
        assert "obj" in result.output


class TestConversationStoreExtraCoverage:
    def _make_store(self, tmp_path, max_cache=500):
        from xbot.runtime.session.conversation_store import ConversationStore
        with patch("xbot.runtime.session.conversation_store.get_legacy_sessions_dir", return_value=tmp_path / "legacy"):
            (tmp_path / "legacy").mkdir(exist_ok=True)
            return ConversationStore(tmp_path, max_cache_size=max_cache)

    def test_hashed_session_filename_deterministic(self):
        from xbot.runtime.session.conversation_store import ConversationStore
        h1 = ConversationStore._hashed_session_filename("test:key")
        h2 = ConversationStore._hashed_session_filename("test:key")
        assert h1 == h2
        assert h1.endswith(".jsonl")

    def test_safe_session_filename(self):
        from xbot.runtime.session.conversation_store import ConversationStore
        name = ConversationStore._safe_session_filename("test:chat_id")
        assert ":" not in name
        assert name.endswith(".jsonl")

    def test_get_or_create_returns_existing(self, tmp_path):
        store = self._make_store(tmp_path)
        s1 = store.get_or_create("k")
        s2 = store.get_or_create("k")
        assert s1 is s2

    def test_invalidate(self, tmp_path):
        store = self._make_store(tmp_path)
        store.get_or_create("k")
        assert "k" in store._cache
        store.invalidate("k")
        assert "k" not in store._cache

    def test_session_paths_for_read_with_im_prefix(self, tmp_path):
        store = self._make_store(tmp_path)
        paths = store._session_paths_for_read("im:telegram:chat123")
        # Should include legacy paths without the "im:" prefix too
        assert len(paths) > 4

    def test_file_lock_context_manager(self, tmp_path):
        store = self._make_store(tmp_path)
        path = tmp_path / "test.lock"
        with store._file_lock(path, exclusive=True):
            pass  # Should not raise

    def test_save_empty_session(self, tmp_path):
        """Test saving a session with no messages."""
        from xbot.runtime.session.conversation_store import ConversationSession
        store = self._make_store(tmp_path)
        sess = ConversationSession(key="empty")
        store.save(sess)
        path = store._get_session_path("empty")
        assert path.exists()

    def test_list_sessions_empty(self, tmp_path):
        store = self._make_store(tmp_path)
        sessions = store.list_sessions()
        assert sessions == []

    def test_list_sessions_with_data(self, tmp_path):
        store = self._make_store(tmp_path)
        sess = store.get_or_create("telegram:chat1")
        sess.add_message("user", "hello")
        sess.add_message("assistant", "hi")
        store.save(sess)
        sessions = store.list_sessions()
        assert len(sessions) >= 1

    def test_session_add_message(self):
        from xbot.runtime.session.conversation_store import ConversationSession
        sess = ConversationSession(key="k")
        sess.add_message("user", "hello", extra="data")
        assert len(sess.messages) == 1
        assert sess.messages[0]["role"] == "user"
        assert sess.messages[0]["content"] == "hello"
        assert sess.messages[0]["extra"] == "data"

    def test_session_clear(self):
        from xbot.runtime.session.conversation_store import ConversationSession
        sess = ConversationSession(key="k")
        sess.add_message("user", "m")
        sess.last_consolidated = 1
        sess.clear()
        assert len(sess.messages) == 0
        assert sess.last_consolidated == 0
        assert sess._metadata_dirty is True

    def test_get_history_drops_leading_non_user(self):
        from xbot.runtime.session.conversation_store import ConversationSession
        sess = ConversationSession(key="k")
        sess.add_message("assistant", "first")
        sess.add_message("user", "second")
        sess.add_message("assistant", "third")
        hist = sess.get_history()
        assert hist[0]["role"] == "user"

    def test_filter_orphan_tool_results(self):
        from xbot.runtime.session.conversation_store import ConversationSession
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1"}]},
            {"role": "tool", "content": "result", "tool_call_id": "tc1"},
            {"role": "tool", "content": "orphan", "tool_call_id": "missing"},
        ]
        filtered = ConversationSession._filter_orphan_tool_results(msgs)
        assert len(filtered) == 3
        assert all(m.get("tool_call_id") != "missing" for m in filtered)


class TestWebSearchExtraCoverage:
    @pytest.mark.asyncio
    async def test_execute_unknown_provider(self):
        from xbot.tools.web import WebSearchTool
        from xbot.platform.config.schema import WebSearchConfig
        cfg = WebSearchConfig(provider="unknown_provider")
        tool = WebSearchTool(config=cfg)
        result = await tool.execute("test query")
        assert "unknown" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_jina_no_key_fallback(self):
        from xbot.tools.web import WebSearchTool
        from xbot.platform.config.schema import WebSearchConfig
        cfg = WebSearchConfig(provider="jina")
        tool = WebSearchTool(config=cfg)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JINA_API_KEY", None)
            with patch.object(tool, "_get_api_key", return_value=""):
                with patch("xbot.tools.web.WebSearchTool._search_duckduckgo", new=AsyncMock(return_value="ddg result")):
                    result = await tool.execute("test")
        assert result == "ddg result"

    def test_get_api_key_with_secret_str(self):
        from xbot.tools.web import WebSearchTool
        from xbot.platform.config.schema import WebSearchConfig
        cfg = WebSearchConfig()
        tool = WebSearchTool(config=cfg)
        # If api_key is a SecretStr it would have get_secret_value
        key = tool._get_api_key()
        assert isinstance(key, str)

    def test_get_api_key_none(self):
        from xbot.tools.web import WebSearchTool
        from xbot.platform.config.schema import WebSearchConfig
        cfg = WebSearchConfig()
        tool = WebSearchTool(config=cfg)
        # Simulate api_key being None at runtime
        tool.config.api_key = None
        key = tool._get_api_key()
        assert key == ""


class TestHelpersExtraCoverage:
    def test_detect_image_mime_png(self):
        from xbot.platform.utils.helpers import detect_image_mime
        assert detect_image_mime(b"\x89PNG\r\n\x1a\nrest") == "image/png"

    def test_detect_image_mime_jpeg(self):
        from xbot.platform.utils.helpers import detect_image_mime
        assert detect_image_mime(b"\xff\xd8\xffrest") == "image/jpeg"

    def test_detect_image_mime_gif(self):
        from xbot.platform.utils.helpers import detect_image_mime
        assert detect_image_mime(b"GIF89a") == "image/gif"

    def test_detect_image_mime_webp(self):
        from xbot.platform.utils.helpers import detect_image_mime
        assert detect_image_mime(b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"

    def test_detect_image_mime_unknown(self):
        from xbot.platform.utils.helpers import detect_image_mime
        assert detect_image_mime(b"unknown") is None

    def test_detect_audio_mime_mp3_id3(self):
        from xbot.platform.utils.helpers import detect_audio_mime
        assert detect_audio_mime(b"ID3rest") == "audio/mp3"

    def test_detect_audio_mime_mp3_frame_sync(self):
        from xbot.platform.utils.helpers import detect_audio_mime
        assert detect_audio_mime(b"\xff\xfb" + b"\x00" * 10) == "audio/mp3"

    def test_detect_audio_mime_wav(self):
        from xbot.platform.utils.helpers import detect_audio_mime
        assert detect_audio_mime(b"RIFF\x00\x00\x00\x00WAVE") == "audio/wav"

    def test_detect_audio_mime_ogg(self):
        from xbot.platform.utils.helpers import detect_audio_mime
        assert detect_audio_mime(b"OggS" + b"\x00" * 10) == "audio/ogg"

    def test_detect_audio_mime_flac(self):
        from xbot.platform.utils.helpers import detect_audio_mime
        assert detect_audio_mime(b"fLaC" + b"\x00" * 10) == "audio/flac"

    def test_detect_audio_mime_m4a(self):
        from xbot.platform.utils.helpers import detect_audio_mime
        data = b"\x00\x00\x00\x00ftypM4A " + b"\x00" * 10
        assert detect_audio_mime(data) == "audio/mp4"

    def test_detect_audio_mime_too_short(self):
        from xbot.platform.utils.helpers import detect_audio_mime
        assert detect_audio_mime(b"\x00") is None

    def test_detect_audio_mime_unknown(self):
        from xbot.platform.utils.helpers import detect_audio_mime
        assert detect_audio_mime(b"unknown data!!") is None

    def test_ensure_dir(self, tmp_path):
        from xbot.platform.utils.helpers import ensure_dir
        d = tmp_path / "a" / "b"
        result = ensure_dir(d)
        assert result == d
        assert d.is_dir()

    def test_timestamp(self):
        from xbot.platform.utils.helpers import timestamp
        ts = timestamp()
        assert "T" in ts

    def test_current_time_str(self):
        from xbot.platform.utils.helpers import current_time_str
        s = current_time_str()
        assert "(" in s

    def test_safe_filename(self):
        from xbot.platform.utils.helpers import safe_filename
        assert safe_filename('file:name/with<bad>chars') == "file_name_with_bad_chars"

    def test_sanitize_download_filename(self):
        from xbot.platform.utils.helpers import sanitize_download_filename
        assert sanitize_download_filename("file.txt", "fb.txt") == "file.txt"
        assert sanitize_download_filename("", "fallback.txt") == "fallback.txt"
        # "../etc" last path segment is "etc"
        assert sanitize_download_filename("../etc", "fb.txt") == "etc"
        # ".." normalizes to ".." which is in the banned set → uses fallback
        assert sanitize_download_filename("..", "fb.txt") == "fb.txt"
        assert sanitize_download_filename("path/to/file.txt", "fb") == "file.txt"
        assert sanitize_download_filename("path\\to\\file.txt", "fb") == "file.txt"

    def test_split_message_empty(self):
        from xbot.platform.utils.helpers import split_message
        assert split_message("") == []

    def test_split_message_short(self):
        from xbot.platform.utils.helpers import split_message
        assert split_message("hello") == ["hello"]

    def test_split_message_long(self):
        from xbot.platform.utils.helpers import split_message
        content = "a" * 5000
        chunks = split_message(content, max_len=2000)
        assert len(chunks) >= 3
        assert all(len(c) <= 2000 for c in chunks)

    def test_split_message_with_newlines(self):
        from xbot.platform.utils.helpers import split_message
        content = "a" * 100 + "\n" + "b" * 100
        chunks = split_message(content, max_len=150)
        assert len(chunks) == 2

    def test_split_message_invalid_max_len(self):
        from xbot.platform.utils.helpers import split_message
        with pytest.raises(ValueError):
            split_message("hello", max_len=0)

    def test_build_assistant_message(self):
        from xbot.platform.utils.helpers import build_assistant_message
        msg = build_assistant_message("hi")
        assert msg["role"] == "assistant"
        assert msg["content"] == "hi"

        msg2 = build_assistant_message("hi", tool_calls=[{"id": "tc"}], reasoning_content="think", thinking_blocks=[{"t": 1}])
        assert msg2["tool_calls"] == [{"id": "tc"}]
        assert msg2["reasoning_content"] == "think"
        assert msg2["thinking_blocks"] == [{"t": 1}]

    def test_estimate_prompt_tokens(self):
        from xbot.platform.utils.helpers import estimate_prompt_tokens
        messages = [{"role": "user", "content": "hello world"}]
        tokens = estimate_prompt_tokens(messages)
        assert tokens > 0

    def test_estimate_prompt_tokens_with_tools(self):
        from xbot.platform.utils.helpers import estimate_prompt_tokens
        messages = [{"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "test", "parameters": {}}}]
        tokens = estimate_prompt_tokens(messages, tools=tools)
        assert tokens > 0

    def test_estimate_prompt_tokens_with_list_content(self):
        from xbot.platform.utils.helpers import estimate_prompt_tokens
        messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        tokens = estimate_prompt_tokens(messages)
        assert tokens > 0

    def test_estimate_message_tokens(self):
        from xbot.platform.utils.helpers import estimate_message_tokens
        msg = {"role": "user", "content": "hello"}
        tokens = estimate_message_tokens(msg)
        assert tokens > 0

    def test_estimate_message_tokens_empty(self):
        from xbot.platform.utils.helpers import estimate_message_tokens
        msg = {"role": "user", "content": ""}
        tokens = estimate_message_tokens(msg)
        assert tokens == 1

    def test_estimate_message_tokens_with_tool_calls(self):
        from xbot.platform.utils.helpers import estimate_message_tokens
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc1", "function": {"name": "f"}}],
        }
        tokens = estimate_message_tokens(msg)
        assert tokens > 0

    def test_estimate_prompt_tokens_chain_with_provider(self):
        from xbot.platform.utils.helpers import estimate_prompt_tokens_chain
        provider = MagicMock()
        provider.estimate_prompt_tokens = MagicMock(return_value=(100, "provider"))
        tokens, source = estimate_prompt_tokens_chain(provider, "model", [])
        assert tokens == 100
        assert source == "provider"

    def test_estimate_prompt_tokens_chain_fallback(self):
        from xbot.platform.utils.helpers import estimate_prompt_tokens_chain
        provider = MagicMock(spec=[])
        messages = [{"role": "user", "content": "hello"}]
        tokens, source = estimate_prompt_tokens_chain(provider, "model", messages)
        assert tokens > 0
        assert source == "tiktoken"


class TestWebFetchExtraCoverage:
    @pytest.mark.asyncio
    async def test_fetch_jina_with_title_markdown(self):
        from xbot.tools.web import WebFetchTool
        tool = WebFetchTool(
            max_chars=1000,
            web_config=SimpleNamespace(disable_security_checks=True, web_fetch_use_jina=True),
        )
        jina_resp = MagicMock()
        jina_resp.status_code = 200
        jina_resp.json.return_value = {
            "data": {"title": "My Title", "content": "content", "url": "https://x.com"},
        }
        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=jina_resp)
            client.__aenter__.return_value = client
            MockClient.return_value = client
            result = await tool.execute("https://x.com", extract_mode="markdown")
        data = json.loads(result)
        assert "# My Title" in data["text"]

    @pytest.mark.asyncio
    async def test_fetch_jina_empty_content_fallback(self):
        from xbot.tools.web import WebFetchTool
        tool = WebFetchTool(
            max_chars=1000,
            web_config=SimpleNamespace(disable_security_checks=True, web_fetch_use_jina=True),
        )
        jina_resp = MagicMock()
        jina_resp.status_code = 200
        jina_resp.json.return_value = {"data": {"title": "", "content": "", "url": ""}}

        fallback_resp = MagicMock()
        fallback_resp.status_code = 200
        fallback_resp.headers = {"content-type": "text/plain"}
        fallback_resp.text = "fallback content"
        fallback_resp.url = "https://x.com"

        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=[jina_resp, fallback_resp])
            client.__aenter__.return_value = client
            MockClient.return_value = client
            with patch("xbot.tools.web._validate_and_pin_url", new=AsyncMock(return_value=(True, "", {}))):
                with patch("xbot.tools.web._PinnedAsyncHTTPTransport"):
                    result = await tool.execute("https://x.com")
        data = json.loads(result)
        assert "fallback content" in data["text"]

    @pytest.mark.asyncio
    async def test_fetch_readability_raw_content(self):
        from xbot.tools.web import WebFetchTool
        tool = WebFetchTool(
            max_chars=1000,
            web_config=SimpleNamespace(disable_security_checks=True, web_fetch_use_jina=False),
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "text/plain"}
        resp.text = "plain text content"
        resp.url = "https://x.com"

        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=resp)
            client.__aenter__.return_value = client
            MockClient.return_value = client
            with patch("xbot.tools.web._validate_and_pin_url", new=AsyncMock(return_value=(True, "", {}))):
                with patch("xbot.tools.web._PinnedAsyncHTTPTransport"):
                    result = await tool.execute("https://x.com")
        data = json.loads(result)
        assert data["extractor"] == "raw"

    @pytest.mark.asyncio
    async def test_fetch_readability_redirect(self):
        from xbot.tools.web import WebFetchTool
        tool = WebFetchTool(
            max_chars=1000,
            web_config=SimpleNamespace(disable_security_checks=True, web_fetch_use_jina=False),
        )
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.headers = {"location": "https://x.com/final"}

        final_resp = MagicMock()
        final_resp.status_code = 200
        final_resp.headers = {"content-type": "text/plain"}
        final_resp.text = "final content"
        final_resp.url = "https://x.com/final"

        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=[redirect_resp, final_resp])
            client.__aenter__.return_value = client
            MockClient.return_value = client
            with patch("xbot.tools.web._validate_and_pin_url", new=AsyncMock(return_value=(True, "", {}))):
                with patch("xbot.tools.web._PinnedAsyncHTTPTransport"):
                    with patch("xbot.tools.web._validate_url_safe", new=AsyncMock(return_value=(True, ""))):
                        result = await tool.execute("https://x.com")
        data = json.loads(result)
        assert "final content" in data["text"]

    @pytest.mark.asyncio
    async def test_fetch_readability_too_many_redirects(self):
        from xbot.tools.web import WebFetchTool
        tool = WebFetchTool(
            max_chars=1000,
            web_config=SimpleNamespace(disable_security_checks=True, web_fetch_use_jina=False),
        )
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.headers = {"location": "https://x.com/next"}

        with patch("xbot.tools.web.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=redirect_resp)
            client.__aenter__.return_value = client
            MockClient.return_value = client
            with patch("xbot.tools.web._validate_and_pin_url", new=AsyncMock(return_value=(True, "", {}))):
                with patch("xbot.tools.web._PinnedAsyncHTTPTransport"):
                    result = await tool.execute("https://x.com")
        data = json.loads(result)
        assert "redirect" in data["error"].lower()

    def test_to_markdown(self):
        from xbot.tools.web import WebFetchTool
        tool = WebFetchTool(web_config=SimpleNamespace(disable_security_checks=True))
        html_content = '<h1>Title</h1><p>Hello <a href="http://x.com">link</a></p><ul><li>item1</li></ul>'
        md = tool._to_markdown(html_content)
        assert "Title" in md
        assert "[link]" in md
        assert "item1" in md


class TestDingTalkExtraCoverage:
    def _make_channel(self):
        from xbot.channels.dingtalk import DingTalkChannel, DingTalkConfig
        cfg = DingTalkConfig.model_validate({
            "enabled": True, "client_id": "id", "client_secret": "sec",
            "allow_from": ["*"],
        })
        return DingTalkChannel(cfg, _make_msg_bus())

    @pytest.mark.asyncio
    async def test_send_markdown_text(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {}
        ch._http.post = AsyncMock(return_value=resp)
        ok = await ch._send_markdown_text("tok", "u", "content")
        assert ok is True

    @pytest.mark.asyncio
    async def test_upload_media_no_http(self):
        ch = self._make_channel()
        ch._http = None
        result = await ch._upload_media("tok", b"data", "image", "f.jpg", "image/jpeg")
        assert result is None

    @pytest.mark.asyncio
    async def test_send_media_ref_empty(self):
        ch = self._make_channel()
        ok = await ch._send_media_ref("tok", "u", "")
        assert ok is True

    @pytest.mark.asyncio
    async def test_send_media_ref_http_image(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {}
        ch._http.post = AsyncMock(return_value=resp)
        ok = await ch._send_media_ref("tok", "u", "https://x.com/img.jpg")
        assert ok is True

    @pytest.mark.asyncio
    async def test_on_message_error_handling(self):
        ch = self._make_channel()
        ch._handle_message = AsyncMock(side_effect=RuntimeError("boom"))
        await ch._on_message("hi", "u", "n")  # Should log, not raise

    @pytest.mark.asyncio
    async def test_download_dingtalk_file_no_token(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        ch._access_token = None
        ch._token_expiry = 0
        ch._http.post = AsyncMock(side_effect=RuntimeError("no token"))
        result = await ch._download_dingtalk_file("code", "f.txt", "sender")
        assert result is None


class TestDiscordExtraCoverage:
    def _make_channel(self, **kwargs):
        from xbot.channels.discord import DiscordChannel, DiscordConfig
        defaults = {
            "enabled": True, "token": "tok", "allow_from": ["*"],
        }
        defaults.update(kwargs)
        cfg = DiscordConfig.model_validate(defaults)
        return DiscordChannel(cfg, _make_msg_bus())

    def test_init_from_dict(self):
        from xbot.channels.discord import DiscordChannel
        ch = DiscordChannel({"token": "t"}, _make_msg_bus())
        assert ch.config.token == "t"

    @pytest.mark.asyncio
    async def test_start_typing_and_stop(self):
        ch = self._make_channel()
        ch._running = True
        ch._http = AsyncMock()
        ch._http.post = AsyncMock()
        await ch._start_typing("chan1")
        assert "chan1" in ch._typing_tasks
        await ch._stop_typing("chan1")
        assert "chan1" not in ch._typing_tasks

    @pytest.mark.asyncio
    async def test_stop_typing_no_task(self):
        ch = self._make_channel()
        await ch._stop_typing("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_send_payload_exception(self):
        ch = self._make_channel()
        ch._http = AsyncMock()
        ch._http.post = AsyncMock(side_effect=[RuntimeError("e"), RuntimeError("e"), RuntimeError("e")])
        ok = await ch._send_payload("url", {}, {"content": "hi"})
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_file_not_found(self, tmp_path):
        ch = self._make_channel()
        ch._http = AsyncMock()
        ok = await ch._send_file("url", {}, str(tmp_path / "missing.txt"))
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_with_reply(self):
        from xbot.platform.bus.events import OutboundMessage
        ch = self._make_channel()
        ch._http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        ch._http.post = AsyncMock(return_value=resp)
        msg = OutboundMessage(channel="discord", chat_id="c", content="hi", reply_to="m1")
        await ch.send(msg)
        call_kwargs = ch._http.post.call_args.kwargs
        payload = call_kwargs["json"]
        assert payload.get("message_reference") == {"message_id": "m1"}


class TestPlanCmdExtraCoverage:
    def _runner(self):
        from typer.testing import CliRunner
        from xbot.crew.cli.plan_cmd import app
        return CliRunner(), app

    def test_run_dynamic_too_long_goal(self):
        runner, app = self._runner()
        result = runner.invoke(app, ["run-dynamic", "x" * 11000])
        assert result.exit_code != 0
        assert "long" in result.output.lower()

    def test_run_dynamic_empty_goal(self):
        runner, app = self._runner()
        result = runner.invoke(app, ["run-dynamic", ""])
        assert result.exit_code != 0

    def test_run_dynamic_tier_all(self, tmp_path):
        runner, app = self._runner()
        with patch("xbot.crew.cli.plan_cmd.CrewPlanner") as MockPlanner:
            inst = MockPlanner.return_value
            plan = MagicMock()
            plan.name = "c"
            role = MagicMock()
            role.name = "r"
            plan.roles = [role]
            plan.tasks = []
            plan.confidence = 0.5
            inst.plan.return_value = plan
            inst.generate_config.return_value = "yaml: yes\n"
            result = runner.invoke(app, [
                "run-dynamic", "Goal", "--workspace", str(tmp_path), "--tier", "all", "--dry-run",
            ])
            assert result.exit_code == 0
