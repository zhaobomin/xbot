"""
对比测试：当前 _receive_with_boundary 实现 vs 建议方案。

不修改业务代码，通过独立函数复现两种实现，
用相同的消息序列验证各场景下的行为差异。

运行方式：pytest tests/test_boundary_comparison.py -v
"""

import asyncio
import pytest
from typing import AsyncIterator


# ─── Fake message types ────────────────────────────────────────────────────

class FakeSystemMessage:
    def __init__(self, subtype: str, data=None):
        self.subtype = subtype
        self.data = data or {}
    def __repr__(self):
        return f"SystemMessage(subtype={self.subtype!r})"

class FakeAssistantMessage:
    def __init__(self, label: str = ""):
        self.label = label
    def __repr__(self):
        return f"AssistantMessage({self.label!r})"

class FakeUserMessage:
    def __init__(self, content: str = "", parent_tool_use_id=None):
        self.content = content
        self.parent_tool_use_id = parent_tool_use_id
    def __repr__(self):
        pid = self.parent_tool_use_id
        return f"UserMessage({self.content!r}, pid={pid!r})"

class FakeResultMessage:
    def __init__(self, subtype: str = "success"):
        self.subtype = subtype
    def __repr__(self):
        return f"ResultMessage({self.subtype!r})"


# ─── Fake client ───────────────────────────────────────────────────────────

class FakeClient:
    """模拟 SDK client，将预设消息列表作为 receive_messages() 的输出。"""
    def __init__(self, messages: list):
        self._messages = messages

    async def receive_messages(self):
        for msg in self._messages:
            yield msg


# ─── 当前代码实现（从 claude_sdk_backend.py 原样复制，不做任何修改） ──────

_MAX_STALE_DISCARD = 50

async def current_impl(client, session_key: str) -> AsyncIterator:
    """当前代码的 _receive_with_boundary 实现（原样复制）。"""
    boundary_crossed = False
    stale_count = 0
    pending_init = None

    async for message in client.receive_messages():
        if not boundary_crossed:
            if isinstance(message, FakeSystemMessage) and message.subtype == "init":
                pending_init = message
                continue

            if isinstance(message, FakeUserMessage) and message.parent_tool_use_id is None:
                boundary_crossed = True
                if pending_init is not None:
                    yield pending_init
                    pending_init = None
                continue

            stale_count += 1
            if stale_count >= _MAX_STALE_DISCARD:
                boundary_crossed = True
                if pending_init is not None:
                    yield pending_init
                    pending_init = None
            continue

        yield message
        if isinstance(message, FakeResultMessage):
            return


# ─── 建议方案实现 ──────────────────────────────────────────────────────────

async def proposed_impl(client, session_key: str) -> AsyncIterator:
    """建议方案：SystemMessage(init) 作为唯一 boundary + UserMessage 永远过滤。"""
    boundary_crossed = False
    stale_count = 0

    async for message in client.receive_messages():
        # UserMessage 是协议回显，永远不透传
        if isinstance(message, FakeUserMessage):
            continue

        if not boundary_crossed:
            if isinstance(message, FakeSystemMessage) and message.subtype == "init":
                boundary_crossed = True
                yield message
                continue

            stale_count += 1
            if stale_count >= _MAX_STALE_DISCARD:
                boundary_crossed = True
            continue

        yield message
        if isinstance(message, FakeResultMessage):
            return


# ─── Helper ────────────────────────────────────────────────────────────────

async def collect(impl_fn, messages: list, session_key: str = "test-session") -> list:
    """运行指定实现，收集所有 yield 的消息。"""
    client = FakeClient(messages)
    return [msg async for msg in impl_fn(client, session_key)]


# ============================================================================
#  场景 1：首次查询（无历史，无 UserMessage）
#
#  实际消息流（来自生产日志 15:30:14，prompt="你好"）：
#    SystemMessage(init) → AssistantMessage → AssistantMessage → ResultMessage
#
#  这是最常见的场景：新会话的第一个请求，没有历史回放，
#  流中不会出现任何 UserMessage。
# ============================================================================

class TestScenario1_FirstQuery:
    """场景 1：首次查询，无历史，流中无 UserMessage。"""

    MESSAGES = [
        FakeSystemMessage("init", {"session_id": "sid-1"}),
        FakeAssistantMessage("response-part-1"),
        FakeAssistantMessage("response-part-2"),
        FakeResultMessage(),
    ]

    @pytest.mark.asyncio
    async def test_current_impl_HANGS(self):
        """当前代码：init 被暂存，后续消息全部丢弃，无 UserMessage 建立 boundary → 流耗尽无输出。"""
        result = await collect(current_impl, self.MESSAGES)
        # 当前代码：没有 UserMessage → boundary 永远不会被设置
        # 所有消息被当作 stale 丢弃（init 暂存、AssistantMsg×2 丢弃、ResultMsg 丢弃）
        # 流结束后收集到的是空列表（在真实场景中流不会结束，会死锁）
        assert result == [], (
            f"当前代码在无 UserMessage 时应该收集不到任何消息，实际得到: {result}"
        )

    @pytest.mark.asyncio
    async def test_proposed_impl_WORKS(self):
        """建议方案：init 直接做 boundary，所有消息正确 yield。"""
        result = await collect(proposed_impl, self.MESSAGES)
        assert len(result) == 4, f"建议方案应 yield 全部 4 条消息，实际: {result}"
        assert isinstance(result[0], FakeSystemMessage)
        assert isinstance(result[1], FakeAssistantMessage)
        assert result[1].label == "response-part-1"
        assert isinstance(result[2], FakeAssistantMessage)
        assert result[2].label == "response-part-2"
        assert isinstance(result[3], FakeResultMessage)


# ============================================================================
#  场景 2：正常复用 client，有历史回放
#
#  实际消息流（来自生产日志 16:00:15，prompt="上海天气"，有 1 轮历史）：
#    SystemMessage(init) → AssistantMsg(hist) → AssistantMsg(hist)
#    → UserMessage(history echo) → AssistantMsg(current) → AssistantMsg(current)
#    → ResultMessage
# ============================================================================

class TestScenario2_WithHistory:
    """场景 2：复用 client，有历史回放的 UserMessage。"""

    MESSAGES = [
        FakeSystemMessage("init", {"session_id": "sid-1"}),
        FakeAssistantMessage("hist-resp-1"),
        FakeAssistantMessage("hist-resp-2"),
        FakeUserMessage("prev-query", parent_tool_use_id=None),
        FakeAssistantMessage("current-resp-1"),
        FakeAssistantMessage("current-resp-2"),
        FakeResultMessage(),
    ]

    @pytest.mark.asyncio
    async def test_current_impl_drops_history_assistants(self):
        """当前代码：init 暂存后，hist-resp-1/2 被当 stale 丢弃，UserMessage 建立 boundary。"""
        result = await collect(current_impl, self.MESSAGES)
        labels = [getattr(m, "label", None) for m in result]
        # init 被暂存，hist-resp-1/2 被丢弃，UserMessage 触发 boundary 后 yield init
        # 然后 current-resp-1/2 和 ResultMessage 被 yield
        assert isinstance(result[0], FakeSystemMessage), "第一条应该是暂存的 init"
        assert "hist-resp-1" not in labels, "当前代码会丢弃 init 后、UserMessage 前的历史 AssistantMsg"
        assert "hist-resp-2" not in labels
        assert "current-resp-1" in labels
        assert isinstance(result[-1], FakeResultMessage)

    @pytest.mark.asyncio
    async def test_proposed_impl_yields_all(self):
        """建议方案：init 直接 boundary，UserMessage 被过滤，其余正确 yield。"""
        result = await collect(proposed_impl, self.MESSAGES)
        # 7 条消息中 UserMessage 被过滤，剩 6 条
        assert len(result) == 6, f"建议方案应 yield 6 条消息（UserMessage 被过滤），实际: {len(result)}"
        assert isinstance(result[0], FakeSystemMessage)
        labels = [getattr(m, "label", None) for m in result if hasattr(m, "label")]
        assert "hist-resp-1" in labels
        assert "hist-resp-2" in labels
        assert "current-resp-1" in labels
        assert isinstance(result[-1], FakeResultMessage)
        assert not any(isinstance(m, FakeUserMessage) for m in result)


# ============================================================================
#  场景 3：缓冲区残留 — stale 消息中包含 UserMessage（核心 bug 场景）
#
#  假设前一个请求有历史回放，响应未完全消费就中断了，
#  缓冲区残留：[stale AssistantMsg, stale UserMsg(history), stale ResultMsg]
#  然后当前请求的消息追加在后面。
# ============================================================================

class TestScenario3_StaleWithUserMessage:
    """场景 3：缓冲区有残留消息，且残留中包含 UserMessage（最危险的场景）。"""

    STALE_ASSISTANT = FakeAssistantMessage("stale-resp")
    STALE_USER = FakeUserMessage("stale-history-echo", parent_tool_use_id=None)
    STALE_ASSISTANT2 = FakeAssistantMessage("stale-resp-2")
    STALE_RESULT = FakeResultMessage("success")

    FRESH_INIT = FakeSystemMessage("init", {"session_id": "sid-new"})
    FRESH_ASSISTANT = FakeAssistantMessage("fresh-resp")
    FRESH_RESULT = FakeResultMessage("success")

    MESSAGES = [
        STALE_ASSISTANT,
        STALE_USER,
        STALE_ASSISTANT2,
        STALE_RESULT,
        FRESH_INIT,
        FRESH_ASSISTANT,
        FRESH_RESULT,
    ]

    @pytest.mark.asyncio
    async def test_current_impl_consumes_stale_response(self):
        """当前代码：stale UserMessage 误判为 boundary → 消费了 stale 的 ResultMessage，真正响应被丢弃。"""
        result = await collect(current_impl, self.MESSAGES)
        # stale_assistant → stale_count=1, 丢弃
        # stale_user(pid=None) → boundary! pending_init=None, continue
        # stale_assistant2 → boundary已穿越, yield
        # stale_result → yield + return
        # fresh_init, fresh_assistant, fresh_result → 永远不会被读到
        labels = [getattr(m, "label", None) for m in result if hasattr(m, "label")]
        assert "stale-resp-2" in labels, (
            "当前代码会把 stale AssistantMessage 当成当前响应 yield 出去"
        )
        assert "fresh-resp" not in labels, (
            "当前代码的真正响应永远不会被读到"
        )

    @pytest.mark.asyncio
    async def test_proposed_impl_skips_all_stale(self):
        """建议方案：所有 stale 消息（含 UserMessage）被正确丢弃，从 fresh init 开始消费。"""
        result = await collect(proposed_impl, self.MESSAGES)
        labels = [getattr(m, "label", None) for m in result if hasattr(m, "label")]
        assert "stale-resp" not in labels, "stale 消息应被丢弃"
        assert "stale-resp-2" not in labels, "stale 消息应被丢弃"
        assert isinstance(result[0], FakeSystemMessage), "第一条应该是 fresh init"
        assert result[0].data.get("session_id") == "sid-new"
        assert "fresh-resp" in labels, "真正的响应应该被 yield"
        assert isinstance(result[-1], FakeResultMessage)


# ============================================================================
#  场景 4：缓冲区残留 — 只有非 UserMessage 的 stale 消息
#
#  前一个请求的最后几条 AssistantMessage + ResultMessage 未消费。
#  这是更常见的轻度残留场景。
# ============================================================================

class TestScenario4_StaleWithoutUserMessage:
    """场景 4：缓冲区有残留，但残留中不包含 UserMessage。"""

    MESSAGES = [
        FakeAssistantMessage("stale-1"),
        FakeAssistantMessage("stale-2"),
        FakeResultMessage("stale-success"),
        FakeSystemMessage("init", {"session_id": "sid-ok"}),
        FakeAssistantMessage("fresh-1"),
        FakeResultMessage("fresh-success"),
    ]

    @pytest.mark.asyncio
    async def test_current_impl_discards_stale_but_also_discards_fresh(self):
        """当前代码：stale 消息被丢弃（正确），但 init 只暂存，fresh-1 也被丢弃。"""
        result = await collect(current_impl, self.MESSAGES)
        # stale-1 → stale_count=1, 丢弃
        # stale-2 → stale_count=2, 丢弃
        # stale ResultMessage → stale_count=3, 丢弃
        # init → 暂存 pending_init
        # fresh-1 → stale_count=4, 丢弃 ← 错误！
        # fresh ResultMessage → stale_count=5, 丢弃 ← 错误！
        # 流结束，无输出
        labels = [getattr(m, "label", None) for m in result if hasattr(m, "label")]
        assert "fresh-1" not in labels, (
            "当前代码在无 UserMessage 场景下 fresh 消息也会被丢弃"
        )
        assert result == [], f"当前代码收集为空，实际: {result}"

    @pytest.mark.asyncio
    async def test_proposed_impl_correctly_handles(self):
        """建议方案：stale 消息丢弃，从 init 开始正确消费。"""
        result = await collect(proposed_impl, self.MESSAGES)
        assert isinstance(result[0], FakeSystemMessage)
        labels = [getattr(m, "label", None) for m in result if hasattr(m, "label")]
        assert "stale-1" not in labels
        assert "stale-2" not in labels
        assert "fresh-1" in labels
        assert isinstance(result[-1], FakeResultMessage)


# ============================================================================
#  场景 5：无 stale 消息，纯 tool-use 循环
#
#  实际消息流（来自生产日志 15:31:47，有多次 tool call）：
#    init → AssistantMsg(tool_use) → UserMsg(tool_result, pid!=None)
#    → AssistantMsg(tool_use) → UserMsg(tool_result, pid!=None)
#    → AssistantMsg(final) → ResultMessage
#
#  注意：tool result 的 UserMessage 的 parent_tool_use_id 不为 None。
# ============================================================================

class TestScenario5_ToolUseLoop:
    """场景 5：正常的 tool-use 循环，UserMessage 是 tool result（pid!=None）。"""

    MESSAGES = [
        FakeSystemMessage("init", {"session_id": "sid-1"}),
        FakeAssistantMessage("tool-call-1"),
        FakeUserMessage("tool-result-1", parent_tool_use_id="tu-001"),
        FakeAssistantMessage("tool-call-2"),
        FakeUserMessage("tool-result-2", parent_tool_use_id="tu-002"),
        FakeAssistantMessage("final-response"),
        FakeResultMessage(),
    ]

    @pytest.mark.asyncio
    async def test_current_impl_HANGS(self):
        """当前代码：tool result 的 UserMessage pid!=None 不触发 boundary → 所有消息丢弃。"""
        result = await collect(current_impl, self.MESSAGES)
        # init → 暂存
        # tool-call-1 → stale_count=1
        # tool-result-1(pid="tu-001") → pid != None, 不是 boundary → stale_count=2
        # tool-call-2 → stale_count=3
        # tool-result-2(pid="tu-002") → stale_count=4
        # final-response → stale_count=5
        # ResultMessage → stale_count=6
        # 流结束，无输出
        assert result == [], f"当前代码在 tool-use 场景（无 bare UserMessage）下也会丢失全部消息: {result}"

    @pytest.mark.asyncio
    async def test_proposed_impl_WORKS(self):
        """建议方案：init 做 boundary，UserMessage 被过滤，其余正确 yield。"""
        result = await collect(proposed_impl, self.MESSAGES)
        # 7 条中 2 条 UserMessage 被过滤，剩 5 条
        assert len(result) == 5, f"应 yield 5 条消息（UserMessage 被过滤），实际: {len(result)}"
        assert isinstance(result[0], FakeSystemMessage)
        assert not any(isinstance(m, FakeUserMessage) for m in result)
        assert isinstance(result[-1], FakeResultMessage)


# ============================================================================
#  场景 6：极端 — stale init（前一个请求完全未消费）
#
#  理论场景：前一个 query 发出后，一条消息都没消费就中断了。
#  缓冲区包含完整的前一个响应 + 当前响应。
#  注意：这在实际中几乎不会发生（interrupt → release_client → 销毁 client）。
# ============================================================================

class TestScenario6_StaleInit:
    """场景 6：极端场景 — 缓冲区包含 stale init（两个 init）。"""

    STALE_INIT = FakeSystemMessage("init", {"session_id": "sid-old"})
    STALE_RESPONSE = FakeAssistantMessage("stale-old-response")
    STALE_RESULT = FakeResultMessage("stale-success")

    FRESH_INIT = FakeSystemMessage("init", {"session_id": "sid-new"})
    FRESH_RESPONSE = FakeAssistantMessage("fresh-new-response")
    FRESH_RESULT = FakeResultMessage("fresh-success")

    MESSAGES = [
        STALE_INIT,
        STALE_RESPONSE,
        STALE_RESULT,
        FRESH_INIT,
        FRESH_RESPONSE,
        FRESH_RESULT,
    ]

    @pytest.mark.asyncio
    async def test_current_impl_drops_everything(self):
        """当前代码：stale init 暂存，后续全部丢弃（无 UserMessage），流耗尽。"""
        result = await collect(current_impl, self.MESSAGES)
        # stale init → 暂存 pending_init
        # stale response → stale_count=1
        # stale result → stale_count=2
        # fresh init → 覆盖 pending_init（旧的丢了）
        # fresh response → stale_count=3
        # fresh result → stale_count=4
        # 流结束，无输出
        assert result == [], f"当前代码在双 init 场景下丢失全部消息: {result}"

    @pytest.mark.asyncio
    async def test_proposed_impl_takes_first_init(self):
        """建议方案：第一个 init（stale）做 boundary，会消费 stale 响应。这是已知的理论局限。"""
        result = await collect(proposed_impl, self.MESSAGES)
        # stale init → boundary! yield
        # stale response → yield
        # stale result → yield + return
        # fresh 消息不会被读到
        assert isinstance(result[0], FakeSystemMessage)
        assert result[0].data.get("session_id") == "sid-old", (
            "建议方案在 stale init 场景下会误消费旧响应（已知局限，实际不发生）"
        )
        # 记录：这个场景两种方案都不完美，但实际中 release_client 会销毁 client，不会出现


# ============================================================================
#  场景 7：安全阀测试
#
#  超过 50 条 stale 消息后，强制建立 boundary。
# ============================================================================

class TestScenario7_SafetyValve:
    """场景 7：大量 stale 消息触发安全阀。"""

    @pytest.mark.asyncio
    async def test_current_impl_safety_valve(self):
        """当前代码：安全阀在 50 条 stale 后触发，暂存的 init 被 yield。"""
        init = FakeSystemMessage("init", {"session_id": "sid-1"})
        stale_msgs = [FakeAssistantMessage(f"stale-{i}") for i in range(55)]
        fresh = FakeAssistantMessage("fresh-after-valve")
        result_msg = FakeResultMessage()

        messages = [init] + stale_msgs + [fresh, result_msg]
        result = await collect(current_impl, messages)
        # init 暂存，50 条 stale 后安全阀触发，yield pending_init
        # 第 51-55 条 stale 因为 continue 在安全阀分支后继续被 discard（stale_count=51 时 boundary_crossed 已 True 但 continue 仍执行）
        # 实际上看代码，安全阀触发后 boundary_crossed=True, 然后 continue
        # 下一条消息进入时 boundary_crossed=True → yield
        # 所以第 51 条 stale 会被 yield（因为 boundary 已穿越）
        assert len(result) > 0, "安全阀应触发并开始 yield"

    @pytest.mark.asyncio
    async def test_proposed_impl_safety_valve(self):
        """建议方案：安全阀同样在 50 条后触发。"""
        stale_msgs = [FakeAssistantMessage(f"stale-{i}") for i in range(55)]
        fresh = FakeAssistantMessage("fresh-after-valve")
        result_msg = FakeResultMessage()

        # 注意：没有 init，纯 stale 消息（极端情况）
        messages = stale_msgs + [fresh, result_msg]
        result = await collect(proposed_impl, messages)
        # 50 条后安全阀触发 boundary_crossed=True, continue
        # 第 51 条开始被 yield
        assert len(result) > 0, "安全阀应触发并开始 yield"


# ============================================================================
#  场景 8：混合 — stale 消息 + 有历史回放的正常响应
#
#  最接近实际 bug 触发场景：
#  buffer = [stale AssistantMsg] + [init → hist_assistant → hist_user → response → result]
# ============================================================================

class TestScenario8_MildStaleWithHistory:
    """场景 8：少量 stale 消息 + 正常的有历史回放响应。"""

    MESSAGES = [
        # 1 条 stale 残留
        FakeAssistantMessage("stale-leftover"),
        # 当前请求的完整响应
        FakeSystemMessage("init", {"session_id": "sid-current"}),
        FakeAssistantMessage("hist-assistant"),
        FakeUserMessage("hist-query", parent_tool_use_id=None),
        FakeAssistantMessage("current-response"),
        FakeResultMessage(),
    ]

    @pytest.mark.asyncio
    async def test_current_impl_misses_fresh_response(self):
        """当前代码：stale-leftover 丢弃(ok)，init 暂存，hist-assistant 丢弃，
        hist-query(UserMsg) 触发 boundary → yield init + current-response + result。
        hist-assistant 丢失但 current-response 幸存。"""
        result = await collect(current_impl, self.MESSAGES)
        labels = [getattr(m, "label", None) for m in result if hasattr(m, "label")]
        # 在这个场景下，当前代码 "碰巧" 能工作：
        # - stale-leftover → stale_count=1
        # - init → 暂存
        # - hist-assistant → stale_count=2 ← 丢了（但它是历史，不影响当前响应内容）
        # - hist-query(UserMsg) → boundary! yield init
        # - current-response → yield
        # - result → yield + return
        assert "current-response" in labels, "当前代码在此场景碰巧能获取到当前响应"
        assert "hist-assistant" not in labels, "但会丢弃历史 AssistantMsg"

    @pytest.mark.asyncio
    async def test_proposed_impl_handles_correctly(self):
        """建议方案：stale 丢弃，init 建立 boundary，UserMessage 过滤，其余 yield。"""
        result = await collect(proposed_impl, self.MESSAGES)
        labels = [getattr(m, "label", None) for m in result if hasattr(m, "label")]
        assert "stale-leftover" not in labels, "stale 消息应被丢弃"
        assert isinstance(result[0], FakeSystemMessage), "init 应该是第一条 yield 的消息"
        assert "hist-assistant" in labels, "历史 AssistantMsg 应被保留"
        assert "current-response" in labels
        assert isinstance(result[-1], FakeResultMessage)
        assert not any(isinstance(m, FakeUserMessage) for m in result), "UserMessage 不应透传"
