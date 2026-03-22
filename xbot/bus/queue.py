"""Async message queue for decoupled channel-agent communication."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from xbot.bus.events import InboundMessage, OutboundMessage


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

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

        # 权限请求/响应支持
        self._pending_permission_responses: dict[str, asyncio.Event] = {}
        self._permission_results: dict[str, PermissionResponse] = {}
        self._permission_lock = asyncio.Lock()
        # 按会话追踪 pending request: session_key -> request_id
        self._session_pending_requests: dict[str, str] = {}

        # 通用交互请求/响应支持
        self._pending_interaction_responses: dict[str, asyncio.Event] = {}
        self._interaction_results: dict[str, InteractionResponse] = {}
        self._interaction_requests: dict[str, InteractionRequest] = {}
        self._interaction_lock = asyncio.Lock()
        self._session_pending_interactions: dict[str, str] = {}

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
        # 追踪会话的 pending request
        async with self._permission_lock:
            self._session_pending_requests[req.session_key] = req.request_id

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
            self._session_pending_interactions[req.session_key] = req.request_id
            self._interaction_requests[req.request_id] = req

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
            self._pending_permission_responses[request_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            async with self._permission_lock:
                result = self._permission_results.pop(request_id, None)
                self._pending_permission_responses.pop(request_id, None)
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
                to_remove = [k for k, v in self._session_pending_requests.items() if v == request_id]
                for k in to_remove:
                    del self._session_pending_requests[k]
            return PermissionResponse(
                request_id=request_id,
                session_key="",
                decision="deny",
                reason="Timeout waiting for user response",
            )

    async def wait_interaction_response(
        self,
        request_id: str,
        timeout: float = 300.0,
    ) -> InteractionResponse:
        """等待通用交互响应。"""
        event = asyncio.Event()
        async with self._interaction_lock:
            self._pending_interaction_responses[request_id] = event

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
        """清除权限请求状态。"""
        self._pending_permission_responses.pop(request_id, None)
        self._permission_results.pop(request_id, None)
        # 清理相关的 session 追踪
        to_remove = [k for k, v in self._session_pending_requests.items() if v == request_id]
        for k in to_remove:
            del self._session_pending_requests[k]

    def clear_interaction_request(self, request_id: str) -> None:
        """清除通用交互请求状态。"""
        self._pending_interaction_responses.pop(request_id, None)
        self._interaction_results.pop(request_id, None)
        self._interaction_requests.pop(request_id, None)
        to_remove = [k for k, v in self._session_pending_interactions.items() if v == request_id]
        for k in to_remove:
            del self._session_pending_interactions[k]

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
