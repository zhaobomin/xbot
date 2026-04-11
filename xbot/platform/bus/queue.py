"""Async message queue for decoupled channel-agent communication."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from xbot.platform.bus.events import InboundMessage, OutboundMessage
from xbot.platform.logging.core import get_logger

# Constants for request pool management
logger = get_logger(__name__)
MAX_PENDING_REQUESTS = 1000
REQUEST_TIMEOUT_SECONDS = 600  # 10 minutes default timeout


@dataclass
class PermissionRequest:
    """权限请求消息，用于 SDK 需要用户确认的场景。"""

    request_id: str
    session_key: str
    channel: str
    chat_id: str
    tool_name: str
    tool_input: dict[str, Any]
    message: str  # 给用户的提示信息
    suggestions: list[str] = field(default_factory=list)  # 可选的建议回复
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)  # 请求创建时间

    def is_expired(self, timeout: float = REQUEST_TIMEOUT_SECONDS) -> bool:
        """检查请求是否已超时。"""
        return time.time() - self.created_at > timeout


@dataclass
class PermissionResponse:
    """权限响应消息，用户对权限请求的回复。"""

    request_id: str
    session_key: str
    decision: Literal["allow", "deny"]
    reason: str = ""
    updated_input: dict[str, Any] | None = None


@dataclass
class InteractionRequest:
    """通用交互请求（例如 ask question / confirm / approve）。"""

    request_id: str
    session_key: str
    channel: str
    chat_id: str
    kind: Literal["question", "confirmation", "approval"] = "question"
    prompt: str = ""
    suggestions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)  # 请求创建时间

    def is_expired(self, timeout: float = REQUEST_TIMEOUT_SECONDS) -> bool:
        """检查请求是否已超时。"""
        return time.time() - self.created_at > timeout


@dataclass
class InteractionResponse:
    """通用交互响应。"""

    request_id: str
    session_key: str
    action: str = "reply"  # reply / confirm / cancel / allow / deny
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.

    Also supports permission request/response flow for SDK interactions.
    """

    def __init__(self, max_queue_size: int = 1000, max_pending_requests: int = MAX_PENDING_REQUESTS):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=max_queue_size)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(maxsize=max_queue_size)
        self._max_pending_requests = max_pending_requests

        # 权限请求/响应支持
        self._pending_permission_responses: dict[str, asyncio.Event] = {}
        self._permission_results: dict[str, PermissionResponse] = {}
        self._permission_lock = asyncio.Lock()
        # 按会话追踪 pending request: session_key -> request_id
        self._session_pending_requests: dict[str, str] = {}
        # 存储 PermissionRequest 对象用于超时检查
        self._permission_requests: dict[str, PermissionRequest] = {}

        # 通用交互请求/响应支持
        self._pending_interaction_responses: dict[str, asyncio.Event] = {}
        self._interaction_results: dict[str, InteractionResponse] = {}
        self._interaction_requests: dict[str, InteractionRequest] = {}
        self._interaction_lock = asyncio.Lock()
        self._session_pending_interactions: dict[str, str] = {}

    def _cleanup_expired_permission_requests_unlocked(self) -> int:
        """清理超时的权限请求。必须在持有 _permission_lock 时调用。

        Returns:
            清理的请求数量
        """
        expired_keys = []
        for request_id, req in self._permission_requests.items():
            if req.is_expired():
                expired_keys.append(request_id)

        for request_id in expired_keys:
            self._permission_requests.pop(request_id, None)
            self._pending_permission_responses.pop(request_id, None)
            self._permission_results.pop(request_id, None)
            # 也清理 session 映射
            for session_key, rid in list(self._session_pending_requests.items()):
                if rid == request_id:
                    self._session_pending_requests.pop(session_key, None)

        if expired_keys:
            logger.warning(f"Cleaned up {len(expired_keys)} expired permission request(s)")

        return len(expired_keys)

    def _cleanup_expired_interaction_requests_unlocked(self) -> int:
        """清理超时的交互请求。必须在持有 _interaction_lock 时调用。

        Returns:
            清理的请求数量
        """
        expired_keys = []
        for request_id, req in self._interaction_requests.items():
            if req.is_expired():
                expired_keys.append(request_id)

        for request_id in expired_keys:
            self._interaction_requests.pop(request_id, None)
            self._pending_interaction_responses.pop(request_id, None)
            self._interaction_results.pop(request_id, None)
            # 也清理 session 映射
            for session_key, rid in list(self._session_pending_interactions.items()):
                if rid == request_id:
                    self._session_pending_interactions.pop(session_key, None)

        if expired_keys:
            logger.warning(f"Cleaned up {len(expired_keys)} expired interaction request(s)")

        return len(expired_keys)

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    # =========================================================================
    # Permission Request/Response Support
    # =========================================================================

    async def publish_permission_request(self, req: PermissionRequest) -> None:
        """发布权限请求，发送消息到 Channel 询问用户。

        Args:
            req: 权限请求对象
        """
        # 追踪会话的 pending request，并预注册 waiter 事件，避免
        # "用户先回复、waiter 后注册"导致的竞态丢失。
        async with self._permission_lock:
            # 检查并清理超时请求
            if len(self._permission_requests) >= self._max_pending_requests:
                cleaned = self._cleanup_expired_permission_requests_unlocked()
                if len(self._permission_requests) >= self._max_pending_requests:
                    logger.warning(
                        f"Permission request pool at capacity ({len(self._permission_requests)}/{self._max_pending_requests}), "
                        f"cleaned {cleaned} expired request(s)"
                    )

            previous_request_id = self._session_pending_requests.get(req.session_key)
            if previous_request_id and previous_request_id != req.request_id:
                prev_event = self._pending_permission_responses.get(previous_request_id)
                if prev_event is not None and not prev_event.is_set():
                    self._permission_results[previous_request_id] = PermissionResponse(
                        request_id=previous_request_id,
                        session_key=req.session_key,
                        decision="deny",
                        reason="Superseded by a newer permission request",
                    )
                    prev_event.set()
                else:
                    self._pending_permission_responses.pop(previous_request_id, None)
                    self._permission_results.pop(previous_request_id, None)
                    self._permission_requests.pop(previous_request_id, None)
            self._session_pending_requests[req.session_key] = req.request_id
            self._pending_permission_responses.setdefault(req.request_id, asyncio.Event())
            # 存储请求对象用于超时检查
            self._permission_requests[req.request_id] = req

        # 发送消息通知用户
        await self.publish_outbound(OutboundMessage(
            channel=req.channel,
            chat_id=req.chat_id,
            content=req.message,
            metadata={
                **(req.metadata or {}),
                "permission_request_id": req.request_id,
                "permission_request": True,
                "suggestions": req.suggestions,
            }
        ))

    async def publish_interaction_request(self, req: InteractionRequest) -> None:
        """发布通用交互请求并通知用户。"""
        async with self._interaction_lock:
            # 检查并清理超时请求
            if len(self._interaction_requests) >= self._max_pending_requests:
                cleaned = self._cleanup_expired_interaction_requests_unlocked()
                if len(self._interaction_requests) >= self._max_pending_requests:
                    logger.warning(
                        f"Interaction request pool at capacity ({len(self._interaction_requests)}/{self._max_pending_requests}), "
                        f"cleaned {cleaned} expired request(s)"
                    )

            previous_request_id = self._session_pending_interactions.get(req.session_key)
            if previous_request_id and previous_request_id != req.request_id:
                # Cancel stale interaction for the same session to avoid dangling waiters.
                prev_event = self._pending_interaction_responses.get(previous_request_id)
                if prev_event is not None and not prev_event.is_set():
                    self._interaction_results[previous_request_id] = InteractionResponse(
                        request_id=previous_request_id,
                        session_key=req.session_key,
                        action="cancel",
                        content="Superseded by a newer interaction request",
                    )
                    prev_event.set()
                else:
                    self._interaction_requests.pop(previous_request_id, None)
                    self._pending_interaction_responses.pop(previous_request_id, None)
                    self._interaction_results.pop(previous_request_id, None)

            self._session_pending_interactions[req.session_key] = req.request_id
            self._interaction_requests[req.request_id] = req
            # Pre-register waiter to avoid race with very fast user replies.
            self._pending_interaction_responses.setdefault(req.request_id, asyncio.Event())

        await self.publish_outbound(OutboundMessage(
            channel=req.channel,
            chat_id=req.chat_id,
            content=req.prompt,
            metadata={
                **(req.metadata or {}),
                "interaction_request_id": req.request_id,
                "interaction_request": True,
                "interaction_kind": req.kind,
                "suggestions": req.suggestions,
            },
        ))

    async def wait_permission_response(
        self,
        request_id: str,
        timeout: float = 300.0,
    ) -> PermissionResponse:
        """等待权限响应（在 SDK 回调中使用）。

        Args:
            request_id: 请求 ID
            timeout: 超时时间（秒）

        Returns:
            PermissionResponse 对象
        """
        event = asyncio.Event()
        async with self._permission_lock:
            event = self._pending_permission_responses.setdefault(request_id, event)

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            async with self._permission_lock:
                result = self._permission_results.pop(request_id, None)
                self._pending_permission_responses.pop(request_id, None)
                self._permission_requests.pop(request_id, None)  # Fix: clean up request
                to_remove = [k for k, v in self._session_pending_requests.items() if v == request_id]
                for k in to_remove:
                    del self._session_pending_requests[k]
            if result is None:
                return PermissionResponse(
                    request_id=request_id,
                    session_key="",
                    decision="deny",
                    reason="No response received",
                )
            return result
        except asyncio.TimeoutError:
            async with self._permission_lock:
                self._pending_permission_responses.pop(request_id, None)
                self._permission_results.pop(request_id, None)
                self._permission_requests.pop(request_id, None)  # Fix: clean up request
                to_remove = [k for k, v in self._session_pending_requests.items() if v == request_id]
                for k in to_remove:
                    del self._session_pending_requests[k]
            return PermissionResponse(
                request_id=request_id,
                session_key="",
                decision="deny",
                reason="Timeout waiting for user response",
            )
        except asyncio.CancelledError:
            # Clean up on cancellation
            async with self._permission_lock:
                self._pending_permission_responses.pop(request_id, None)
                self._permission_results.pop(request_id, None)
                self._permission_requests.pop(request_id, None)
                to_remove = [k for k, v in self._session_pending_requests.items() if v == request_id]
                for k in to_remove:
                    del self._session_pending_requests[k]
            raise

    async def wait_interaction_response(
        self,
        request_id: str,
        timeout: float = 300.0,
    ) -> InteractionResponse:
        """等待通用交互响应。"""
        event = asyncio.Event()
        async with self._interaction_lock:
            event = self._pending_interaction_responses.setdefault(request_id, event)

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            async with self._interaction_lock:
                result = self._interaction_results.pop(request_id, None)
                self._pending_interaction_responses.pop(request_id, None)
                self._interaction_requests.pop(request_id, None)
                to_remove = [k for k, v in self._session_pending_interactions.items() if v == request_id]
                for k in to_remove:
                    del self._session_pending_interactions[k]
            if result is None:
                return InteractionResponse(
                    request_id=request_id,
                    session_key="",
                    action="cancel",
                    content="No response received",
                )
            return result
        except asyncio.TimeoutError:
            async with self._interaction_lock:
                self._pending_interaction_responses.pop(request_id, None)
                self._interaction_requests.pop(request_id, None)
                to_remove = [k for k, v in self._session_pending_interactions.items() if v == request_id]
                for k in to_remove:
                    del self._session_pending_interactions[k]
            return InteractionResponse(
                request_id=request_id,
                session_key="",
                action="cancel",
                content="Timeout waiting for user response",
            )
        except asyncio.CancelledError:
            # Clean up on cancellation
            async with self._interaction_lock:
                self._pending_interaction_responses.pop(request_id, None)
                self._interaction_results.pop(request_id, None)
                self._interaction_requests.pop(request_id, None)
                to_remove = [k for k, v in self._session_pending_interactions.items() if v == request_id]
                for k in to_remove:
                    del self._session_pending_interactions[k]
            raise

    async def submit_permission_response(self, resp: PermissionResponse) -> bool:
        """提交权限响应（从用户消息解析后调用）。

        Args:
            resp: 权限响应对象

        Returns:
            True 如果成功匹配到等待中的请求，False 否则
        """
        async with self._permission_lock:
            event = self._pending_permission_responses.get(resp.request_id)
            if event is None:
                return False
            self._permission_results[resp.request_id] = resp
            event.set()
            # 清理会话追踪
            if resp.session_key in self._session_pending_requests:
                if self._session_pending_requests[resp.session_key] == resp.request_id:
                    del self._session_pending_requests[resp.session_key]
        return True

    async def submit_interaction_response(self, resp: InteractionResponse) -> bool:
        """提交通用交互响应。"""
        async with self._interaction_lock:
            event = self._pending_interaction_responses.get(resp.request_id)
            if event is None:
                return False
            self._interaction_results[resp.request_id] = resp
            event.set()
            if resp.session_key in self._session_pending_interactions:
                if self._session_pending_interactions[resp.session_key] == resp.request_id:
                    del self._session_pending_interactions[resp.session_key]
        return True

    def get_pending_request_for_session(self, session_key: str) -> str | None:
        """获取指定会话的 pending request_id。

        Args:
            session_key: 会话 key

        Returns:
            request_id 如果有待处理的请求，否则 None
        """
        return self._session_pending_requests.get(session_key)

    def get_pending_interaction_for_session(self, session_key: str) -> str | None:
        """获取指定会话的 pending 通用交互 request_id。"""
        return self._session_pending_interactions.get(session_key)

    def get_interaction_request(self, request_id: str) -> InteractionRequest | None:
        """获取通用交互请求详情。"""
        return self._interaction_requests.get(request_id)

    def has_pending_permission_request(self, request_id: str) -> bool:
        """检查是否有等待中的权限请求。"""
        return request_id in self._pending_permission_responses

    def clear_permission_request(self, request_id: str) -> None:
        """清除权限请求状态。

        注意：此方法不获取锁，仅限 aclear_permission_request 内部调用。
        """
        self._pending_permission_responses.pop(request_id, None)
        self._permission_results.pop(request_id, None)
        self._permission_requests.pop(request_id, None)  # Fix: clean up request
        to_remove = [k for k, v in self._session_pending_requests.items() if v == request_id]
        for k in to_remove:
            del self._session_pending_requests[k]

    def clear_interaction_request(self, request_id: str) -> None:
        """清除通用交互请求状态。

        注意：此方法不获取锁，仅限 aclear_interaction_request 内部调用。
        """
        self._pending_interaction_responses.pop(request_id, None)
        self._interaction_results.pop(request_id, None)
        self._interaction_requests.pop(request_id, None)
        to_remove = [k for k, v in self._session_pending_interactions.items() if v == request_id]
        for k in to_remove:
            del self._session_pending_interactions[k]

    async def aclear_permission_request(self, request_id: str) -> None:
        """异步清除权限请求状态（带锁保护）。"""
        async with self._permission_lock:
            self._pending_permission_responses.pop(request_id, None)
            self._permission_results.pop(request_id, None)
            self._permission_requests.pop(request_id, None)  # Fix: clean up request
            to_remove = [k for k, v in self._session_pending_requests.items() if v == request_id]
            for k in to_remove:
                del self._session_pending_requests[k]

    async def aclear_interaction_request(self, request_id: str) -> None:
        """异步清除通用交互请求状态（带锁保护）。"""
        async with self._interaction_lock:
            self._pending_interaction_responses.pop(request_id, None)
            self._interaction_results.pop(request_id, None)
            self._interaction_requests.pop(request_id, None)
            to_remove = [k for k, v in self._session_pending_interactions.items() if v == request_id]
            for k in to_remove:
                del self._session_pending_interactions[k]

    def clear_session_requests(self, session_key: str) -> dict[str, bool]:
        """清理指定会话下挂起的权限与交互请求。

        .. deprecated:: 使用 aclear_session_requests 代替。
           此同步方法委托给异步版本；在事件循环中请直接使用 aclear_session_requests。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aclear_session_requests(session_key))
        raise RuntimeError(
            "clear_session_requests() cannot be used inside a running event loop; use aclear_session_requests()"
        )

    async def aclear_session_requests(self, session_key: str) -> dict[str, bool]:
        """异步清理指定会话下挂起的权限与交互请求（带锁保护）。

        Args:
            session_key: 会话标识

        Returns:
            清理结果字典
        """
        cleared_permission = False
        cleared_interaction = False

        # 先获取需要清理的 request_id（在锁内）
        async with self._permission_lock:
            request_id = self._session_pending_requests.get(session_key)
            if request_id:
                self._pending_permission_responses.pop(request_id, None)
                self._permission_results.pop(request_id, None)
                self._permission_requests.pop(request_id, None)  # Fix: clean up request
                del self._session_pending_requests[session_key]
                cleared_permission = True

        async with self._interaction_lock:
            interaction_id = self._session_pending_interactions.get(session_key)
            if interaction_id:
                self._pending_interaction_responses.pop(interaction_id, None)
                self._interaction_results.pop(interaction_id, None)
                self._interaction_requests.pop(interaction_id, None)
                del self._session_pending_interactions[session_key]
                cleared_interaction = True

        return {
            "permission": cleared_permission,
            "interaction": cleared_interaction,
        }

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
