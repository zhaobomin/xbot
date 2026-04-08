"""Tests for permission handler functionality."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.agent.interaction.permission import (
    BasePermissionHandler,
    CLIPermissionHandler,
    InteractivePermissionHandler,
    PermissionRequestHandler,
    create_permission_handler,
)
from xbot.bus.queue import InteractionResponse, MessageBus


class TestBasePermissionHandler:
    """Tests for BasePermissionHandler."""

    def test_is_safe_tool_default(self):
        handler = BasePermissionHandler()
        assert handler.is_safe_tool("read_file") is True
        assert handler.is_safe_tool("list_dir") is True
        assert handler.is_safe_tool("web_search") is True
        assert handler.is_safe_tool("exec") is False
        assert handler.is_safe_tool("write_file") is False

    def test_is_safe_tool_custom(self):
        handler = BasePermissionHandler(safe_tools={"read_file", "custom_tool"})
        assert handler.is_safe_tool("read_file") is True
        assert handler.is_safe_tool("custom_tool") is True
        assert handler.is_safe_tool("list_dir") is False

    def test_add_safe_tool(self):
        handler = BasePermissionHandler()
        assert handler.is_safe_tool("new_tool") is False
        handler.add_safe_tool("new_tool")
        assert handler.is_safe_tool("new_tool") is True

    def test_summarize_input_empty(self):
        handler = BasePermissionHandler()
        assert handler.summarize_input({}) == ""

    def test_summarize_input_short(self):
        handler = BasePermissionHandler()
        result = handler.summarize_input({"path": "/home/user/file.txt"})
        assert result == '{"path": "/home/user/file.txt"}'

    def test_summarize_input_truncated(self):
        handler = BasePermissionHandler()
        long_value = "x" * 200
        result = handler.summarize_input({"content": long_value}, max_len=50)
        assert len(result) == 53  # 50 + "..."
        assert result.endswith("...")

    def test_format_permission_message(self):
        handler = BasePermissionHandler()
        msg = handler.format_permission_message("exec", {"command": "ls -la"})
        assert "exec" in msg
        assert "ls -la" in msg
        assert "允许" in msg or "拒绝" in msg


class TestCLIPermissionHandler:
    """Tests for CLIPermissionHandler."""

    def test_auto_approve_safe_tools(self):
        handler = CLIPermissionHandler(auto_approve_safe_tools=True)
        assert handler.auto_approve_safe_tools is True

    @pytest.mark.asyncio
    async def test_auto_approve_enabled(self):
        handler = CLIPermissionHandler(auto_approve_safe_tools=True)
        decision, result = await handler.can_use_tool("read_file", {"path": "/tmp"}, None)
        assert decision == "allow"
        assert result == {"path": "/tmp"}

    @pytest.mark.asyncio
    async def test_auto_approve_disabled_non_interactive(self):
        """When auto_approve is disabled and non-interactive, safe tools are still denied."""
        handler = CLIPermissionHandler(auto_approve_safe_tools=False, interactive=False)
        decision, result = await handler.can_use_tool("read_file", {"path": "/tmp"}, None)
        assert decision == "deny"

    @pytest.mark.asyncio
    async def test_non_interactive_deny(self):
        handler = CLIPermissionHandler(auto_approve_safe_tools=False, interactive=False)
        decision, result = await handler.can_use_tool("exec", {"command": "rm -rf /"}, None)
        assert decision == "deny"
        assert "Non-interactive mode" in result

    @pytest.mark.asyncio
    async def test_non_interactive_interaction_request_is_cancelled(self):
        handler = CLIPermissionHandler(auto_approve_safe_tools=False, interactive=False)
        response = await handler.request_interaction(
            kind="question",
            prompt="请输入下一步",
            session_key="cli:direct",
        )
        assert response.action == "cancel"
        assert "Non-interactive" in response.content


class TestInteractivePermissionHandler:
    """Tests for InteractivePermissionHandler."""

    def test_set_thinking_spinner(self):
        handler = InteractivePermissionHandler()
        mock_spinner = MagicMock()
        mock_spinner.pause = MagicMock(return_value=MagicMock())
        handler.set_thinking_spinner(mock_spinner)
        assert handler._thinking == mock_spinner

    @pytest.mark.asyncio
    async def test_request_interaction_pauses_spinner(self):
        class _PauseCtx:
            def __init__(self):
                self.entered = False
                self.exited = False

            def __enter__(self):
                self.entered = True
                return self

            def __exit__(self, exc_type, exc, tb):
                self.exited = True
                return False

        pause_ctx = _PauseCtx()
        spinner = MagicMock()
        spinner.pause.return_value = pause_ctx

        handler = InteractivePermissionHandler()
        handler.set_thinking_spinner(spinner)
        handler._ask_interaction_in_terminal = AsyncMock(
            return_value=InteractionResponse(
                request_id="",
                session_key="cli:direct",
                action="reply",
                content="继续",
            )
        )

        result = await handler.request_interaction(
            kind="question",
            prompt="请输入后续",
            session_key="cli:direct",
        )

        assert result.action == "reply"
        assert pause_ctx.entered is True
        assert pause_ctx.exited is True


class TestPermissionRequestHandler:
    """Tests for PermissionRequestHandler (Channel mode)."""

    @pytest.fixture
    def bus(self):
        return MessageBus()

    @pytest.fixture
    def handler(self, bus):
        return PermissionRequestHandler(bus=bus, timeout=1.0)

    def test_set_session_context(self, handler):
        handler.set_session_context("test:123", "telegram", "456", {"message_thread_id": 99})
        assert "test:123" in handler._session_context
        assert handler._session_context["test:123"]["channel"] == "telegram"
        assert handler._session_context["test:123"]["chat_id"] == "456"
        assert handler._session_context["test:123"]["metadata"]["message_thread_id"] == 99

    def test_clear_session_context(self, handler):
        handler.set_session_context("test:123", "telegram", "456")
        handler.clear_session_context("test:123")
        assert "test:123" not in handler._session_context

    def test_set_current_session(self, handler):
        handler.set_session_context("test:123", "telegram", "456")
        handler.set_session_context("test:456", "cli", "direct")
        handler.set_current_session("test:456")
        assert handler.get_current_session_key() == "test:456"

    @pytest.mark.asyncio
    async def test_current_session_is_task_local(self, bus):
        handler = PermissionRequestHandler(bus=bus, timeout=1.0)
        handler.set_session_context("test:123", "telegram", "123")
        handler.set_session_context("test:456", "telegram", "456")

        async def _worker(session_key: str, delay: float) -> str | None:
            handler.set_current_session(session_key)
            await asyncio.sleep(delay)
            return handler.get_current_session_key()

        first, second = await asyncio.gather(
            _worker("test:123", 0.05),
            _worker("test:456", 0.01),
        )

        assert first == "test:123"
        assert second == "test:456"

    def test_get_current_session_single(self, handler):
        handler.set_session_context("test:123", "telegram", "456")
        assert handler.get_current_session_key() == "test:123"

    @pytest.mark.asyncio
    async def test_auto_approve_safe_tool(self, handler):
        handler.set_session_context("test:123", "telegram", "456")
        decision, result = await handler.can_use_tool("read_file", {"path": "/tmp"}, None)
        assert decision == "allow"
        assert result == {"path": "/tmp"}

    @pytest.mark.asyncio
    async def test_deny_without_session_context(self):
        bus = MessageBus()
        handler = PermissionRequestHandler(bus=bus, timeout=1.0)
        # No session context set
        decision, result = await handler.can_use_tool("exec", {"command": "ls"}, None)
        assert decision == "deny"
        assert "No active session context" in result

    @pytest.mark.asyncio
    async def test_permission_request_flow(self, bus):
        handler = PermissionRequestHandler(bus=bus, timeout=0.5)
        handler.set_session_context("test:123", "telegram", "456")

        # Start permission request in background
        async def request_permission():
            return await handler.can_use_tool("exec", {"command": "ls"}, None)

        # Create task
        asyncio.create_task(request_permission())

        # Wait a bit for request to be published
        await asyncio.sleep(0.1)

        # Check that a permission request was published
        assert bus.get_pending_request_for_session("test:123") is not None

    @pytest.mark.asyncio
    async def test_ask_user_question_defaults_to_suggested_validation(self, handler):
        handler.set_session_context("test:123", "telegram", "456")

        captured = {}

        async def fake_request_interaction(**kwargs):
            captured.update(kwargs)
            return InteractionResponse(
                request_id="req-1",
                session_key="test:123",
                action="answer",
                content="xbot-ubuntu",
            )

        handler.request_interaction = fake_request_interaction

        decision, updated = await handler.can_use_tool(
            "AskUserQuestion",
            {
                "questions": [
                    {
                        "header": "名称",
                        "question": "请告诉我飞书应用的具体名称是什么？",
                        "options": [
                            {"label": "xbot"},
                            {"label": "xbot-prod"},
                            {"label": "Other"},
                        ],
                    }
                ]
            },
            None,
        )

        assert decision == "allow"
        assert captured["metadata"]["validation_mode"] == "suggested"
        assert captured["metadata"]["allow_free_text"] is True
        assert "也可输入你自己的内容" in captured["prompt"]
        assert updated["answers"][0]["answer"] == "xbot-ubuntu"

    @pytest.mark.asyncio
    async def test_ask_user_question_accepts_reply_action(self, handler):
        handler.set_session_context("test:123", "telegram", "456")

        async def fake_request_interaction(**kwargs):
            return InteractionResponse(
                request_id="req-1",
                session_key="test:123",
                action="reply",
                content="xbot-ubuntu",
            )

        handler.request_interaction = fake_request_interaction

        decision, updated = await handler.can_use_tool(
            "AskUserQuestion",
            {
                "questions": [
                    {
                        "header": "名称",
                        "question": "请告诉我飞书应用的具体名称是什么？",
                        "options": [{"label": "xbot"}, {"label": "Other"}],
                    }
                ]
            },
            None,
        )

        assert decision == "allow"
        assert updated["answers"][0]["answer"] == "xbot-ubuntu"

    @pytest.mark.asyncio
    async def test_ask_user_question_multi_prompt_only_mentions_commas(self, handler):
        handler.set_session_context("test:123", "telegram", "456")

        captured = {}

        async def fake_request_interaction(**kwargs):
            captured.update(kwargs)
            return InteractionResponse(
                request_id="req-1",
                session_key="test:123",
                action="answer",
                content="A, 高",
            )

        handler.request_interaction = fake_request_interaction

        await handler.can_use_tool(
            "AskUserQuestion",
            {
                "questions": [
                    {"header": "方案", "question": "选哪个？", "options": [{"label": "A"}, {"label": "B"}]},
                    {"header": "优先级", "question": "多高？", "options": [{"label": "高"}, {"label": "低"}]},
                ]
            },
            None,
        )

        assert "逗号" in captured["prompt"]
        assert "空格" not in captured["prompt"]
        assert "也可输入你自己的内容" in captured["prompt"]

    @pytest.mark.asyncio
    async def test_ask_user_question_can_opt_into_strict_validation(self, handler):
        handler.set_session_context("test:123", "telegram", "456")

        captured = {}

        async def fake_request_interaction(**kwargs):
            captured.update(kwargs)
            return InteractionResponse(
                request_id="req-1",
                session_key="test:123",
                action="answer",
                content="xbot",
            )

        handler.request_interaction = fake_request_interaction

        decision, updated = await handler.can_use_tool(
            "AskUserQuestion",
            {
                "validation_mode": "strict",
                "allow_free_text": False,
                "questions": [
                    {
                        "header": "环境",
                        "question": "请选择环境",
                        "options": [
                            {"label": "xbot"},
                            {"label": "xbot-prod"},
                        ],
                    }
                ]
            },
            None,
        )

        assert decision == "allow"
        assert captured["metadata"]["validation_mode"] == "strict"
        assert captured["metadata"]["allow_free_text"] is False
        assert "也可输入你自己的内容" not in captured["prompt"]
        assert updated["answers"][0]["answer"] == "xbot"

    @pytest.mark.asyncio
    async def test_ask_user_question_without_options_does_not_reference_suggestions(self, handler):
        handler.set_session_context("test:123", "telegram", "456")

        captured = {}

        async def fake_request_interaction(**kwargs):
            captured.update(kwargs)
            return InteractionResponse(
                request_id="req-1",
                session_key="test:123",
                action="answer",
                content="自由文本",
            )

        handler.request_interaction = fake_request_interaction

        await handler.can_use_tool(
            "AskUserQuestion",
            {
                "questions": [
                    {
                        "header": "备注",
                        "question": "请补充备注",
                        "options": [],
                    }
                ]
            },
            None,
        )

        assert "上面的建议项" not in captured["prompt"]

    @pytest.mark.asyncio
    async def test_permission_request_timeout(self, bus):
        handler = PermissionRequestHandler(bus=bus, timeout=0.2)
        handler.set_session_context("test:123", "telegram", "456")

        # Request permission but don't respond
        decision, result = await handler.can_use_tool("exec", {"command": "ls"}, None)
        assert decision == "deny"
        assert "Timeout" in result

    @pytest.mark.asyncio
    async def test_interaction_request_flow(self, bus):
        handler = PermissionRequestHandler(bus=bus, timeout=0.5)
        handler.set_session_context("test:123", "telegram", "456")

        async def request_interaction():
            return await handler.request_interaction(
                kind="confirmation",
                prompt="继续执行吗？",
                suggestions=["确认", "取消"],
                session_key="test:123",
            )

        task = asyncio.create_task(request_interaction())
        await asyncio.sleep(0.1)

        request_id = bus.get_pending_interaction_for_session("test:123")
        assert request_id is not None

        ok = await bus.submit_interaction_response(
            InteractionResponse(
                request_id=request_id,
                session_key="test:123",
                action="confirm",
                content="确认",
            )
        )
        assert ok is True
        response = await task
        assert response.action == "confirm"
        assert response.content == "确认"

    @pytest.mark.asyncio
    async def test_interaction_request_without_context_returns_cancel(self, bus):
        handler = PermissionRequestHandler(bus=bus, timeout=0.5)
        response = await handler.request_interaction(
            kind="question",
            prompt="请输入",
            session_key="missing-session",
        )
        assert response.action == "cancel"
        assert "No active session context" in response.content


class TestCreatePermissionHandler:
    """Tests for create_permission_handler factory function."""

    def test_create_channel_handler(self):
        bus = MessageBus()
        handler = create_permission_handler(mode="channel", bus=bus)
        assert isinstance(handler, PermissionRequestHandler)

    def test_create_channel_handler_requires_bus(self):
        with pytest.raises(ValueError, match="Channel mode requires a MessageBus"):
            create_permission_handler(mode="channel", bus=None)

    def test_create_cli_handler(self):
        handler = create_permission_handler(mode="cli")
        assert isinstance(handler, CLIPermissionHandler)

    def test_create_cli_handler_non_interactive(self):
        handler = create_permission_handler(mode="cli", non_interactive=True)
        assert isinstance(handler, CLIPermissionHandler)
        assert handler.interactive is False

    def test_create_interactive_handler(self):
        handler = create_permission_handler(mode="interactive")
        assert isinstance(handler, InteractivePermissionHandler)

    def test_create_interactive_with_spinner(self):
        mock_spinner = MagicMock()
        handler = create_permission_handler(mode="interactive", thinking_spinner=mock_spinner)
        assert isinstance(handler, InteractivePermissionHandler)
        assert handler._thinking == mock_spinner


class TestBuildCanUseToolCallback:
    """Tests for build_can_use_tool_callback method."""

    @pytest.mark.asyncio
    async def test_callback_returns_allow(self):
        handler = CLIPermissionHandler(auto_approve_safe_tools=True)
        callback = handler.build_can_use_tool_callback()

        result = await callback("read_file", {"path": "/tmp"}, None)
        # Check it's a PermissionResultAllow
        assert hasattr(result, "updated_input")

    @pytest.mark.asyncio
    async def test_callback_returns_deny(self):
        handler = CLIPermissionHandler(auto_approve_safe_tools=False, interactive=False)
        callback = handler.build_can_use_tool_callback()

        result = await callback("exec", {"command": "ls"}, None)
        # Check it's a PermissionResultDeny
        assert hasattr(result, "message")
