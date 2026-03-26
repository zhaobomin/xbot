"""Permission request handlers for Claude SDK Agent.

This module provides handlers for permission requests from the Claude SDK,
supporting both Channel mode (gateway) and CLI mode (direct/interactive).

Usage:
    # Channel mode (gateway)
    handler = PermissionRequestHandler(bus=bus)
    options.can_use_tool = handler.build_can_use_tool_callback()

    # CLI mode
    handler = CLIPermissionHandler()
    options.can_use_tool = handler.build_can_use_tool_callback()

    # Or use the factory:
    handler = create_permission_handler(mode="channel", bus=bus)
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import json
import time
import uuid
from contextlib import nullcontext
from typing import Any, Literal

from loguru import logger

from xbot.bus.queue import (
    InteractionRequest,
    InteractionResponse,
    MessageBus,
    PermissionRequest,
)


class BasePermissionHandler:
    """权限处理器的基类。"""

    def __init__(
        self,
        auto_approve_safe_tools: bool = True,
        safe_tools: set[str] | None = None,
    ):
        """初始化基类。

        Args:
            auto_approve_safe_tools: 是否自动批准安全工具
            safe_tools: 安全工具集合
        """
        self.auto_approve_safe_tools = auto_approve_safe_tools
        self._safe_tools = safe_tools or {
            "read_file", "list_dir", "web_search", "web_fetch",
            "message", "cron", "read", "ls",
        }

    def is_safe_tool(self, tool_name: str) -> bool:
        """检查工具是否为安全工具。"""
        return tool_name in self._safe_tools

    def add_safe_tool(self, tool_name: str) -> None:
        """添加工具到安全工具列表。"""
        self._safe_tools.add(tool_name)

    @staticmethod
    def summarize_input(
        tool_input: dict[str, Any],
        max_len: int = 100,
        tool_name: str | None = None,
    ) -> str:
        """摘要工具输入用于显示。

        Args:
            tool_input: 工具输入参数
            max_len: 最大长度（默认 100 字符）
            tool_name: 工具名称（用于特殊处理，如 AskUserQuestion）

        Returns:
            格式化后的输入摘要
        """
        if not tool_input:
            return ""

        # AskUserQuestion 特殊处理：显示完整问题和选项
        if tool_name == "AskUserQuestion":
            return BasePermissionHandler._format_ask_user_question(tool_input)

        try:
            s = json.dumps(tool_input, ensure_ascii=False)
            if len(s) > max_len:
                return s[:max_len] + "..."
            return s
        except Exception:
            return str(tool_input)[:max_len]

    @staticmethod
    def _format_ask_user_question(tool_input: dict[str, Any]) -> str:
        """格式化 AskUserQuestion 工具的输入，显示完整问题和选项。

        Args:
            tool_input: AskUserQuestion 的输入参数

        Returns:
            格式化后的问题文本
        """
        questions = tool_input.get("questions", [])
        if not questions:
            return json.dumps(tool_input, ensure_ascii=False)

        parts = []
        for i, q in enumerate(questions, 1):
            header = q.get("header", f"问题 {i}")
            question = q.get("question", "")
            options = q.get("options", [])
            multi_select = q.get("multiSelect", False)

            parts.append(f"[{header}]")
            if question:
                parts.append(f"  {question}")
            if options:
                parts.append("  可选：")
                for opt in options:
                    label = opt.get("label", "")
                    desc = opt.get("description", "")
                    if desc:
                        parts.append(f"  • {label}: {desc}")
                    else:
                        parts.append(f"  • {label}")
            if multi_select:
                parts.append("  (可多选)")
            parts.append("")

        return "\n".join(parts).strip()

    @staticmethod
    def _parse_answers(
        user_response: str,
        questions: list[dict[str, Any]],
        question_options_map: list[list[str]],
    ) -> list[dict[str, str]]:
        """解析用户回复，构建 AskUserQuestion 期望的 answers 格式。

        Args:
            user_response: 用户回复的文本，可能是单个答案或多个答案（用逗号分开）
            questions: 原始问题列表
            question_options_map: 每个问题对应的选项列表

        Returns:
            answers 列表，格式为 [{"question": "...", "answer": "..."}]
        """
        import re

        def match_option(candidate: str, valid_options: list[str]) -> str | None:
            """尝试匹配候选答案到有效选项。"""
            candidate_lower = candidate.lower().strip()
            for opt in valid_options:
                opt_lower = opt.lower()
                # 精确匹配（忽略大小写）
                if candidate_lower == opt_lower:
                    return opt
                # 包含匹配（候选包含在选项中，或选项包含候选）
                if candidate_lower and (candidate_lower in opt_lower or opt_lower in candidate_lower):
                    return opt
            return None

        # 分割多个答案：只用逗号分割（支持中文逗号、英文逗号、顿号）
        # 不用空格分割，避免破坏包含空格的选项
        parts = re.split(r'[，,、]+', user_response.strip())
        parts = [p.strip() for p in parts if p.strip()]

        # 校验：答案数量与问题数量是否匹配
        num_parts = len(parts)
        num_questions = len(questions)
        if num_parts != num_questions:
            logger.warning(
                f"[AskUserQuestion] Answer count mismatch: "
                f"received {num_parts} answer(s) for {num_questions} question(s). "
                f"Input: '{user_response}'"
            )
            if num_parts < num_questions:
                logger.warning(
                    f"[AskUserQuestion] Missing answers for {num_questions - num_parts} question(s). "
                    f"Empty answers will be used for unanswered questions."
                )
            else:
                logger.warning(
                    f"[AskUserQuestion] Extra answers ({num_parts - num_questions}) will be ignored."
                )

        # 构建答案列表
        answers = []
        for i, q in enumerate(questions):
            question_text = q.get("question", "")
            valid_options = question_options_map[i] if i < len(question_options_map) else []

            # 获取对应位置的答案
            answer = ""
            if i < len(parts):
                candidate = parts[i]
                # 尝试匹配到有效选项
                matched = match_option(candidate, valid_options)
                if matched:
                    answer = matched
                else:
                    # 答案未匹配到任何有效选项，记录警告
                    if valid_options:
                        logger.warning(
                            f"[AskUserQuestion] Answer '{candidate}' for question {i+1} "
                            f"does not match any valid options: {valid_options}. "
                            f"Using raw input as answer."
                        )
                    answer = candidate

            answers.append({
                "question": question_text,
                "answer": answer,
            })

        return answers

    def format_permission_message(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> str:
        """格式化权限请求消息。"""
        # AskUserQuestion 使用特殊格式，显示完整问题和选项
        if tool_name == "AskUserQuestion":
            input_summary = self.summarize_input(tool_input, tool_name=tool_name)
            return "📝 " + input_summary

        input_summary = self.summarize_input(tool_input)

        msg = f"🔐 需要权限确认\n\n工具: {tool_name}"
        if input_summary:
            msg += f"\n参数: {input_summary}"
        msg += "\n\n请回复「允许」或「拒绝」"
        return msg

    def build_can_use_tool_callback(self):
        """构建 SDK 可用的回调函数。

        Returns:
            适用于 ClaudeAgentOptions.can_use_tool 的回调
        """
        try:
            from claude_agent_sdk.types import (
                PermissionResultAllow,
                PermissionResultDeny,
                ToolPermissionContext,
            )
        except ImportError:
            raise ImportError(
                "claude-agent-sdk is not installed. "
                "Install it with: pip install claude-agent-sdk"
            )

        async def callback(
            tool_name: str,
            tool_input: dict[str, Any],
            context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            decision, result = await self.can_use_tool(tool_name, tool_input, context)
            if decision == "allow":
                return PermissionResultAllow(updated_input=result)
            else:
                return PermissionResultDeny(message=result)

        return callback

    async def can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: Any,
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """处理权限请求（子类实现）。"""
        raise NotImplementedError("Subclasses must implement can_use_tool()")

    async def request_interaction(
        self,
        *,
        kind: Literal["question", "confirmation", "approval"] = "question",
        prompt: str,
        suggestions: list[str] | None = None,
        session_key: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float = 300.0,
    ) -> InteractionResponse:
        """处理通用用户交互（子类可覆盖）。"""
        _ = (kind, prompt, suggestions, session_key, channel, chat_id, metadata, timeout)
        return InteractionResponse(
            request_id="",
            session_key=session_key or "",
            action="cancel",
            content="Interaction is not supported by this handler",
        )


class PermissionRequestHandler(BasePermissionHandler):
    """Channel 模式的权限请求处理器。

    通过 MessageBus 将权限请求发送到 Channel，等待用户回复。
    适用于 gateway 模式。
    """

    def __init__(
        self,
        bus: MessageBus,
        timeout: float = 300.0,
        auto_approve_safe_tools: bool = True,
        safe_tools: set[str] | None = None,
    ):
        """初始化 Channel 模式处理器。

        Args:
            bus: 消息总线
            timeout: 等待用户响应的超时时间（秒）
            auto_approve_safe_tools: 是否自动批准安全工具
            safe_tools: 安全工具集合
        """
        super().__init__(auto_approve_safe_tools, safe_tools)
        self.bus = bus
        self.timeout = timeout

        # 会话上下文: session_key -> {"channel": str, "chat_id": str}
        self._session_context: dict[str, dict[str, str]] = {}
        # 当前处理的会话（task-local，避免并发会话串扰）
        self._current_session_key: ContextVar[str | None] = ContextVar(
            "permission_current_session_key",
            default=None,
        )
        # Context TTL cleanup
        self._context_timestamps: dict[str, float] = {}
        self._context_ttl = 3600  # 1 hour TTL for session contexts

    def set_session_context(
        self,
        session_key: str,
        channel: str,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """设置会话上下文（在 process() 开始时调用）。"""
        self._session_context[session_key] = {
            "channel": channel,
            "chat_id": chat_id,
            "metadata": dict(metadata or {}),
        }
        self._context_timestamps[session_key] = time.time()

        # Periodic cleanup of expired contexts
        self._cleanup_expired_contexts()

    def clear_session_context(self, session_key: str) -> None:
        """清除会话上下文。"""
        self._session_context.pop(session_key, None)
        self._context_timestamps.pop(session_key, None)
        if self._current_session_key.get() == session_key:
            self._current_session_key.set(None)

    def _cleanup_expired_contexts(self) -> None:
        """清理过期的 session context (TTL-based)."""
        now = time.time()
        expired = [
            key for key, ts in self._context_timestamps.items()
            if now - ts > self._context_ttl
        ]
        for key in expired:
            self._session_context.pop(key, None)
            self._context_timestamps.pop(key, None)

        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired permission contexts")

    def set_current_session(self, session_key: str) -> None:
        """设置当前正在处理的会话。"""
        self._current_session_key.set(session_key)

    def get_current_session_key(self) -> str | None:
        """获取当前会话 key。"""
        current = self._current_session_key.get()
        if current:
            return current
        try:
            keys = list(self._session_context.keys())
            if len(keys) == 1:
                return keys[0]
        except (IndexError, RuntimeError):
            pass
        return None

    async def can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: Any,
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """处理权限请求。"""
        # 1. 检查是否为安全工具
        if self.auto_approve_safe_tools and self.is_safe_tool(tool_name):
            logger.debug(f"Auto-approving safe tool: {tool_name}")
            return "allow", tool_input

        # 2. AskUserQuestion 特殊处理：使用 InteractionRequest 而非 PermissionRequest
        # 因为用户回复的是选项内容，不是 yes/no
        if tool_name == "AskUserQuestion":
            return await self._handle_ask_user_question(tool_input)

        # 3. 获取会话上下文
        session_key = self.get_current_session_key()
        if not session_key or session_key not in self._session_context:
            logger.warning(f"No session context for permission request: {tool_name}")
            return "deny", "No active session context"

        ctx = self._session_context[session_key]

        # 4. 创建权限请求
        request_id = str(uuid.uuid4())
        request = PermissionRequest(
            request_id=request_id,
            session_key=session_key,
            channel=ctx["channel"],
            chat_id=ctx["chat_id"],
            tool_name=tool_name,
            tool_input=tool_input,
            message=self.format_permission_message(tool_name, tool_input),
            suggestions=["允许", "拒绝"],
            metadata=dict(ctx.get("metadata") or {}),
        )

        # 5. 发送请求并等待响应
        logger.info(f"Sending permission request: {tool_name} (id={request_id})")
        await self.bus.publish_permission_request(request)

        response = await self.bus.wait_permission_response(
            request_id,
            timeout=self.timeout
        )

        logger.info(f"Permission response: {response.decision} for {tool_name}")

        if response.decision == "allow":
            return "allow", response.updated_input or tool_input
        else:
            return "deny", response.reason or "User denied"

    async def _handle_ask_user_question(
        self,
        tool_input: dict[str, Any],
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """处理 AskUserQuestion 工具，使用 InteractionRequest。

        AskUserQuestion 需要特殊处理，因为用户回复的是选项内容，不是 yes/no。
        使用 InteractionRequest 可以正确处理选项匹配。

        支持多问题场景：将多个问题合并展示，用户回复多个答案（用分隔符分开）。
        """
        session_key = self.get_current_session_key()
        if not session_key or session_key not in self._session_context:
            logger.warning("No session context for AskUserQuestion")
            return "deny", "No active session context"

        ctx = self._session_context[session_key]

        # 提取问题和选项
        questions = tool_input.get("questions", [])
        if not questions:
            return "deny", "No questions provided"

        # 构建合并的提示消息和所有有效选项
        prompt_parts = []
        all_valid_options: list[str] = []
        question_headers: list[str] = []
        question_options_map: list[list[str]] = []  # 每个问题的选项列表

        for i, q in enumerate(questions, 1):
            header = q.get("header", f"问题 {i}")
            question_text = q.get("question", "")
            options = q.get("options", [])
            option_labels = [opt.get("label", "") for opt in options if opt.get("label")]

            question_headers.append(header)
            question_options_map.append(option_labels)
            all_valid_options.extend(option_labels)

            # 构建单个问题的提示
            part = f"[{header}]"
            if question_text:
                part += f"\n{question_text}"
            if option_labels:
                part += "\n" + " / ".join(option_labels)

            prompt_parts.append(part)

        # 合并所有问题
        if len(questions) == 1:
            prompt = prompt_parts[0]
        else:
            # 多问题：提示用户用分隔符回复
            prompt = "请依次回答以下问题，答案之间用空格或逗号分隔：\n\n"
            prompt += "\n\n".join(prompt_parts)
            prompt += "\n\n示例回复：答案1, 答案2, 答案3"

        # 发起交互请求
        response = await self.request_interaction(
            kind="question",
            prompt=prompt,
            suggestions=all_valid_options,
            metadata={
                "valid_options": all_valid_options,
                "question_options_map": question_options_map,  # 每个问题的选项
                "question_count": len(questions),
                "multi_select": any(q.get("multiSelect", False) for q in questions),
                "original_questions": questions,
            },
        )

        if response.action == "answer" and response.content:
            # 解析用户回复，构建 answers
            answers = self._parse_answers(
                response.content,
                questions,
                question_options_map,
            )

            # 构建更新后的 tool_input
            updated_input = dict(tool_input)
            updated_input["answers"] = answers
            logger.info(f"AskUserQuestion answered: {answers}")
            return "allow", updated_input
        else:
            logger.info(f"AskUserQuestion cancelled or no answer: {response.action}")
            return "deny", response.content or "User cancelled"

    async def request_interaction(
        self,
        *,
        kind: Literal["question", "confirmation", "approval"] = "question",
        prompt: str,
        suggestions: list[str] | None = None,
        session_key: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float = 300.0,
    ) -> InteractionResponse:
        """通过 MessageBus 发起交互并等待用户响应。"""
        resolved_session = session_key or self.get_current_session_key()
        if not resolved_session or resolved_session not in self._session_context:
            return InteractionResponse(
                request_id="",
                session_key=resolved_session or "",
                action="cancel",
                content="No active session context",
            )

        ctx = self._session_context[resolved_session]
        request_id = str(uuid.uuid4())
        request = InteractionRequest(
            request_id=request_id,
            session_key=resolved_session,
            channel=channel or ctx["channel"],
            chat_id=chat_id or ctx["chat_id"],
            kind=kind,
            prompt=prompt,
            suggestions=list(suggestions or []),
            metadata=dict(metadata or ctx.get("metadata") or {}),
        )
        await self.bus.publish_interaction_request(request)
        return await self.bus.wait_interaction_response(request_id, timeout=timeout)


class CLIPermissionHandler(BasePermissionHandler):
    """CLI 模式的权限请求处理器。

    直接在终端与用户交互，适用于命令模式和交互模式。
    """

    def __init__(
        self,
        auto_approve_safe_tools: bool = True,
        interactive: bool = True,
        safe_tools: set[str] | None = None,
    ):
        """初始化 CLI 模式处理器。

        Args:
            auto_approve_safe_tools: 是否自动批准安全工具
            interactive: 是否允许交互式询问（False 则自动拒绝）
            safe_tools: 安全工具集合
        """
        super().__init__(auto_approve_safe_tools, safe_tools)
        self.interactive = interactive

    async def can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: Any,
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """处理权限请求。"""
        # 1. 检查是否为安全工具
        if self.auto_approve_safe_tools and self.is_safe_tool(tool_name):
            logger.debug(f"Auto-approving safe tool: {tool_name}")
            return "allow", tool_input

        # 2. 非交互模式：拒绝需要确认的工具
        if not self.interactive:
            return "deny", f"Non-interactive mode: tool '{tool_name}' requires permission"

        # 3. 交互模式：在终端询问用户
        return await self._ask_user_in_terminal(tool_name, tool_input)

    async def request_interaction(
        self,
        *,
        kind: Literal["question", "confirmation", "approval"] = "question",
        prompt: str,
        suggestions: list[str] | None = None,
        session_key: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float = 300.0,
    ) -> InteractionResponse:
        """在 CLI 终端处理通用交互。"""
        _ = (channel, chat_id, metadata, timeout)
        if not self.interactive:
            return InteractionResponse(
                request_id="",
                session_key=session_key or "",
                action="cancel",
                content="Non-interactive mode",
            )

        return await self._ask_interaction_in_terminal(
            kind=kind,
            prompt=prompt,
            suggestions=suggestions or [],
            session_key=session_key or "",
        )

    async def _ask_user_in_terminal(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """在终端询问用户。"""
        try:
            from rich.console import Console
            from rich.prompt import Prompt
        except ImportError:
            # Fallback to basic input
            return await self._ask_user_basic(tool_name, tool_input)

        console = Console()

        # 显示请求信息
        console.print()
        console.print("[yellow]🔐 权限请求[/yellow]")
        console.print(f"  工具: [cyan]{tool_name}[/cyan]")

        input_summary = self.summarize_input(tool_input)
        if input_summary:
            console.print(f"  参数: [dim]{input_summary}[/dim]")

        console.print()

        # 在线程中运行同步的 prompt
        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: Prompt.ask(
                    "允许执行？",
                    choices=["y", "n", "a"],
                    default="y",
                )
            )
        except (KeyboardInterrupt, EOFError):
            return "deny", "User cancelled"

        if response == "y":
            return "allow", tool_input
        elif response == "a":
            self.add_safe_tool(tool_name)
            return "allow", tool_input
        else:
            return "deny", "User denied"

    async def _ask_user_basic(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """基本输入方式（无 rich）。"""
        print()
        print("🔐 权限请求")
        print(f"  工具: {tool_name}")
        print(f"  参数: {self.summarize_input(tool_input)}")
        print()

        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: input("允许执行？[y/n/a]: ").strip().lower()
            )
        except (KeyboardInterrupt, EOFError):
            return "deny", "User cancelled"

        if response in ("y", "yes", "是", "允许"):
            return "allow", tool_input
        elif response in ("a", "always", "总是"):
            self.add_safe_tool(tool_name)
            return "allow", tool_input
        else:
            return "deny", "User denied"

    async def _ask_interaction_in_terminal(
        self,
        *,
        kind: Literal["question", "confirmation", "approval"],
        prompt: str,
        suggestions: list[str],
        session_key: str,
    ) -> InteractionResponse:
        """终端交互：question 接收文本，confirmation/approval 接收 y/n。"""
        try:
            from rich.console import Console
            from rich.prompt import Prompt
        except ImportError:
            return await self._ask_interaction_basic(
                kind=kind,
                prompt=prompt,
                session_key=session_key,
            )

        console = Console()
        console.print()
        console.print("[yellow]💬 需要输入[/yellow]")
        console.print(prompt)
        if suggestions:
            console.print(f"[dim]建议: {' / '.join(suggestions)}[/dim]")

        loop = asyncio.get_running_loop()
        try:
            if kind in {"confirmation", "approval"}:
                raw = await loop.run_in_executor(
                    None,
                    lambda: Prompt.ask("请选择", choices=["y", "n"], default="y"),
                )
                action = "confirm" if kind == "confirmation" else "allow"
                if raw == "n":
                    action = "cancel" if kind == "confirmation" else "deny"
                return InteractionResponse(
                    request_id="",
                    session_key=session_key,
                    action=action,
                    content=raw,
                )

            text = await loop.run_in_executor(None, lambda: Prompt.ask("请输入", default=""))
            return InteractionResponse(
                request_id="",
                session_key=session_key,
                action="reply",
                content=(text or "").strip(),
            )
        except (KeyboardInterrupt, EOFError):
            return InteractionResponse(
                request_id="",
                session_key=session_key,
                action="cancel",
                content="User cancelled",
            )

    async def _ask_interaction_basic(
        self,
        *,
        kind: Literal["question", "confirmation", "approval"],
        prompt: str,
        session_key: str,
    ) -> InteractionResponse:
        print()
        print("💬 需要输入")
        print(prompt)
        loop = asyncio.get_running_loop()
        try:
            if kind in {"confirmation", "approval"}:
                raw = await loop.run_in_executor(None, lambda: input("请选择 [y/n]: ").strip().lower())
                action = "confirm" if kind == "confirmation" else "allow"
                if raw in {"n", "no", "否", "取消"}:
                    action = "cancel" if kind == "confirmation" else "deny"
                return InteractionResponse(request_id="", session_key=session_key, action=action, content=raw)

            text = await loop.run_in_executor(None, lambda: input("请输入: ").strip())
            return InteractionResponse(
                request_id="",
                session_key=session_key,
                action="reply",
                content=text,
            )
        except (KeyboardInterrupt, EOFError):
            return InteractionResponse(
                request_id="",
                session_key=session_key,
                action="cancel",
                content="User cancelled",
            )


class InteractivePermissionHandler(CLIPermissionHandler):
    """交互模式的权限请求处理器。

    与 prompt_toolkit 和 spinner 集成，提供更好的用户体验。
    """

    def __init__(
        self,
        auto_approve_safe_tools: bool = True,
        safe_tools: set[str] | None = None,
    ):
        """初始化交互模式处理器。"""
        super().__init__(auto_approve_safe_tools, interactive=True, safe_tools=safe_tools)
        self._thinking: Any = None  # _ThinkingSpinner reference

    def set_thinking_spinner(self, spinner: Any) -> None:
        """设置当前的 spinner 引用。"""
        self._thinking = spinner

    async def _ask_user_in_terminal(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> tuple[Literal["allow"], dict] | tuple[Literal["deny"], str]:
        """在终端询问用户（交互模式，暂停 spinner）。"""
        try:
            from rich.console import Console
            from rich.prompt import Prompt
        except ImportError:
            return await self._ask_user_basic(tool_name, tool_input)

        console = Console()

        # 暂停 spinner
        pause_context = (
            self._thinking.pause()
            if self._thinking and hasattr(self._thinking, 'pause')
            else nullcontext()
        )

        with pause_context:
            # 显示请求信息
            console.print()
            console.print("[yellow]🔐 权限请求[/yellow]")
            console.print(f"  工具: [cyan]{tool_name}[/cyan]")

            input_summary = self.summarize_input(tool_input)
            if input_summary:
                console.print(f"  参数: [dim]{input_summary}[/dim]")

            console.print()

            # 获取用户输入
            loop = asyncio.get_running_loop()
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: Prompt.ask(
                        "允许执行？",
                        choices=["y", "n", "a"],
                        default="y",
                    )
                )
            except (KeyboardInterrupt, EOFError):
                return "deny", "User cancelled"

            if response == "y":
                return "allow", tool_input
            elif response == "a":
                self.add_safe_tool(tool_name)
                return "allow", tool_input
            else:
                return "deny", "User denied"

    async def request_interaction(
        self,
        *,
        kind: Literal["question", "confirmation", "approval"] = "question",
        prompt: str,
        suggestions: list[str] | None = None,
        session_key: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float = 300.0,
    ) -> InteractionResponse:
        """在交互模式下暂停 spinner 进行问答。"""
        _ = (channel, chat_id, metadata, timeout)
        if not self.interactive:
            return InteractionResponse(
                request_id="",
                session_key=session_key or "",
                action="cancel",
                content="Non-interactive mode",
            )
        pause_context = (
            self._thinking.pause()
            if self._thinking and hasattr(self._thinking, "pause")
            else nullcontext()
        )
        with pause_context:
            return await self._ask_interaction_in_terminal(
                kind=kind,
                prompt=prompt,
                suggestions=suggestions or [],
                session_key=session_key or "",
            )


def create_permission_handler(
    mode: Literal["channel", "cli", "interactive"],
    *,
    bus: MessageBus | None = None,
    auto_approve_safe_tools: bool = True,
    timeout: float = 300.0,
    thinking_spinner: Any = None,
    non_interactive: bool = False,
    safe_tools: set[str] | None = None,
) -> BasePermissionHandler:
    """创建适合当前模式的权限处理器。

    Args:
        mode: 运行模式
            - "channel": Gateway 模式，通过 Channel 交互
            - "cli": CLI 命令模式，直接终端交互
            - "interactive": CLI 交互模式，与 spinner 集成
        bus: 消息总线（channel 模式必需）
        auto_approve_safe_tools: 是否自动批准安全工具
        timeout: 等待用户响应的超时时间
        thinking_spinner: 交互模式的 spinner
        non_interactive: CLI 非交互模式
        safe_tools: 安全工具集合

    Returns:
        对应模式的权限处理器实例
    """
    if mode == "channel":
        if bus is None:
            raise ValueError("Channel mode requires a MessageBus")
        return PermissionRequestHandler(
            bus=bus,
            timeout=timeout,
            auto_approve_safe_tools=auto_approve_safe_tools,
            safe_tools=safe_tools,
        )
    elif mode == "interactive":
        handler = InteractivePermissionHandler(
            auto_approve_safe_tools=auto_approve_safe_tools,
            safe_tools=safe_tools,
        )
        if thinking_spinner:
            handler.set_thinking_spinner(thinking_spinner)
        return handler
    else:  # cli
        return CLIPermissionHandler(
            auto_approve_safe_tools=auto_approve_safe_tools,
            interactive=not non_interactive,
            safe_tools=safe_tools,
        )
