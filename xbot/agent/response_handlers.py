"""Runtime response handlers for permission/interaction replies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from xbot.agent.response_parser import (
    derive_interaction_action,
    is_response_keyword,
    parse_permission_response,
)
from xbot.agent.state_machine import SessionPhase
from xbot.bus.events import InboundMessage, OutboundMessage
from xbot.bus.queue import InteractionResponse, PermissionResponse

if TYPE_CHECKING:
    from xbot.agent.runtime import AgentRuntime


class RuntimeResponseHandlers:
    """Encapsulates runtime handlers for pending user responses."""

    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime

    @property
    def _bus(self):
        return self._runtime.bus

    @property
    def _state_coordinator(self):
        return self._runtime._state_coordinator

    async def handle_permission_response(self, msg: InboundMessage) -> bool:
        """Check if the message is a permission response and handle it."""
        if self._bus is None:
            return False

        decision, reason = parse_permission_response(msg.content)
        is_permission_keyword = is_response_keyword(msg.content)
        if decision is None:
            return False

        request_id = self._bus.get_pending_request_for_session(msg.session_key)
        if not request_id:
            if is_permission_keyword:
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="⚠️ 没有待处理的权限请求，可能已超时过期。请重新发起操作。",
                    )
                )
                return True
            return False

        current_phase = self._state_coordinator.get_phase(msg.session_key)
        if current_phase not in {
            SessionPhase.WAITING_PERMISSION,
            SessionPhase.IDLE,
            SessionPhase.RUNNING,
        }:
            logger.debug(
                f"Ignoring permission response for session in {current_phase.value} state"
            )
            return True

        if current_phase != SessionPhase.WAITING_PERMISSION:
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.WAITING_PERMISSION, reason="pending_permission_detected")

        response = PermissionResponse(
            request_id=request_id,
            session_key=msg.session_key,
            decision=decision,
            reason=reason,
        )
        submitted = await self._bus.submit_permission_response(response)
        if not submitted:
            logger.warning(f"Permission response no longer pending: request={request_id}")
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.IDLE, reason="permission_response_expired")
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="⚠️ 权限请求已过期或被取消，请重新发起操作。",
                )
            )
            return True

        logger.info(f"Permission response submitted: {decision} for request {request_id}")

        async with self._state_coordinator.transaction(
            msg.session_key, validate_on_commit=False
        ) as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="permission_response_submitted")

        return True

    async def handle_interaction_response(self, msg: InboundMessage, retry_count: int = 0) -> bool:
        """Handle pending generic interaction replies for a session."""
        if self._bus is None:
            return False

        if self._runtime._is_local_runtime_command(msg.content):
            return False

        content = msg.content.strip()
        is_interaction_keyword = is_response_keyword(content)

        request_id = self._bus.get_pending_interaction_for_session(msg.session_key)
        if not request_id:
            if is_interaction_keyword:
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="⚠️ 没有待处理的交互请求，可能已超时过期。请重新发起操作。",
                    )
                )
                return True
            return False

        current_phase = self._state_coordinator.get_phase(msg.session_key)
        if current_phase not in {
            SessionPhase.WAITING_INTERACTION,
            SessionPhase.IDLE,
            SessionPhase.RUNNING,
        }:
            logger.debug(
                f"Ignoring interaction response for session in {current_phase.value} state"
            )
            # Give user feedback about why their response wasn't processed
            phase_messages = {
                SessionPhase.WAITING_PERMISSION: "⚠️ 当前有待处理的权限请求，请先完成权限确认后再回答此问题。",
                SessionPhase.STOPPING: "⚠️ 系统正在关闭中，交互已取消。",
                SessionPhase.ERROR: "⚠️ 会话遇到错误，无法处理交互请求。",
                SessionPhase.RESETTING: "⚠️ 会话正在重置中，请稍后再试。",
            }
            fallback_msg = f"⚠️ 当前状态为「{current_phase.value}」，无法处理交互请求。"
            user_msg = phase_messages.get(current_phase, fallback_msg)

            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=user_msg,
                )
            )
            # Clean up retry count to prevent memory leak
            if hasattr(self._runtime, '_interaction_retry_counts'):
                self._runtime._interaction_retry_counts.pop(msg.session_key, None)
            return True

        if current_phase != SessionPhase.WAITING_INTERACTION:
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.WAITING_INTERACTION, reason="pending_interaction_detected")

        req = self._bus.get_interaction_request(request_id)
        if req is None:
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.IDLE, reason="interaction_request_expired")
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="⚠️ 交互请求已过期或被取消，请重新发起操作。",
                )
            )
            # Clean up retry count
            if hasattr(self._runtime, '_interaction_retry_counts'):
                self._runtime._interaction_retry_counts.pop(msg.session_key, None)
            return True

        # AskUserQuestion 答案验证：检查用户回复是否在有效选项内
        # 保存原始输入用于日志记录
        original_input = content
        if req.kind == "question" and req.metadata:
            valid_options = req.metadata.get("valid_options", [])
            if valid_options:
                # 精确匹配或模糊匹配（忽略大小写、前后空格）
                normalized_content = content.lower().strip()
                normalized_options = [str(opt).lower().strip() for opt in valid_options]

                # 查找匹配选项的原始值
                matched_option = None
                for i, opt in enumerate(normalized_options):
                    if normalized_content == opt:
                        matched_option = valid_options[i]
                        break

                if matched_option is None:
                    retry_count += 1
                    # Update retry count in runtime
                    if hasattr(self._runtime, '_interaction_retry_counts'):
                        self._runtime._interaction_retry_counts[msg.session_key] = retry_count

                    if retry_count >= 3:
                        await self._bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=f"⚠️ 答案无效已达 3 次，交互已取消。\n有效选项：{', '.join(str(opt) for opt in valid_options)}",
                            )
                        )
                        async with self._state_coordinator.transaction(
                            msg.session_key, validate_on_commit=False
                        ) as tx:
                            tx.set_phase(SessionPhase.IDLE, reason="invalid_answer_max_retries")
                        # Clean up retry count
                        self._runtime._interaction_retry_counts.pop(msg.session_key, None)
                        return True

                    # Build options list string
                    options_str = "\n".join(f"  • {opt}" for opt in valid_options)
                    await self._bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=f"⚠️ 答案无效，请从以下选项中选择：\n{options_str}\n\n当前回复「{original_input}」不在有效选项中（第 {retry_count}/3 次尝试）",
                        )
                    )
                    return True

                # 匹配成功，使用标准化后的选项值，清理重试计数
                content = matched_option
                if hasattr(self._runtime, '_interaction_retry_counts'):
                    self._runtime._interaction_retry_counts.pop(msg.session_key, None)

        action = derive_interaction_action(kind=req.kind, content=content)
        # 构建响应 metadata，包含原始输入用于日志记录
        response_metadata = {"original_input": original_input}
        if req.metadata:
            response_metadata.update(req.metadata)
        submitted = await self._bus.submit_interaction_response(
            InteractionResponse(
                request_id=request_id,
                session_key=msg.session_key,
                action=action,
                content=content,
                metadata=response_metadata,
            )
        )
        if not submitted:
            logger.warning(f"Interaction response no longer pending: request={request_id}")
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.IDLE, reason="interaction_response_expired")
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="⚠️ 交互请求已过期或被取消，请重新发起操作。",
                )
            )
            # Clean up retry count
            if hasattr(self._runtime, '_interaction_retry_counts'):
                self._runtime._interaction_retry_counts.pop(msg.session_key, None)
            return True

        logger.info(f"Interaction response submitted: action={action}, request={request_id}")

        async with self._state_coordinator.transaction(
            msg.session_key, validate_on_commit=False
        ) as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="interaction_response_submitted")

        # Clean up retry count on success
        if hasattr(self._runtime, '_interaction_retry_counts'):
            self._runtime._interaction_retry_counts.pop(msg.session_key, None)

        return True
