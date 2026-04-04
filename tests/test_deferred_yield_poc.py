"""
概念验证：deferred-yield 并行注入模式。

验证 async generator + create_task 可以实现：
  - 用户消息零延迟发送
  - recall 在后台并行运行
  - recall 结果在完成后注入 stdin
  - 主流程（reader）不被 recall 阻塞

模拟架构：
  FakeTransport  = stdin(Queue) + stdout(Queue)
  FakeCLI        = 从 stdin 读消息 → 处理 → 写响应到 stdout
  FakeClient     = 遍历 async generator 逐条写入 stdin（与 ClaudeSDKClient.query() 行为一致）
"""

from __future__ import annotations

import asyncio
import time

import pytest


# ── 模拟 SDK 双通道 ──────────────────────────────────────────


class FakeTransport:
    """模拟 stdin/stdout 双通道。"""

    def __init__(self) -> None:
        self.stdin: asyncio.Queue[str] = asyncio.Queue()  # writer → CLI
        self.stdout: asyncio.Queue[str] = asyncio.Queue()  # CLI → reader

    async def write(self, message: str) -> None:
        await self.stdin.put(message)

    async def read(self) -> str:
        return await self.stdout.get()


class FakeCLI:
    """模拟 SDK CLI 子进程：从 stdin 读消息，处理后写响应到 stdout。"""

    def __init__(self, transport: FakeTransport, processing_delay: float = 0.1) -> None:
        self.transport = transport
        self.processing_delay = processing_delay
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            msg = await self.transport.stdin.get()
            if msg == "__DONE__":
                await self.transport.stdout.put("__RESULT__")
                break
            # 模拟 LLM 处理延迟
            await asyncio.sleep(self.processing_delay)
            await self.transport.stdout.put(f"response_to:{msg}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class FakeClient:
    """模拟 ClaudeSDKClient.query() — 遍历 async generator 逐条写入 stdin。"""

    def __init__(self, transport: FakeTransport) -> None:
        self.transport = transport

    async def query(self, prompt: object) -> None:
        """与真实 ClaudeSDKClient.query() 行为一致：遍历 async iterable 写入 stdin。"""
        async for msg in prompt:  # type: ignore[union-attr]
            await self.transport.write(msg)


# ── 被验证的模式 ─────────────────────────────────────────────


async def deferred_yield_generator(
    user_message: str,
    recall_task: asyncio.Task[str] | None = None,
):
    """与 spec 中 _build_text_query 行为一致的 deferred-yield generator。"""
    # 1. 立即 yield 用户消息
    yield user_message

    # 2. 等待 recall，完成后 yield 记忆消息
    if recall_task is not None:
        recalled = ""
        try:
            recalled = await recall_task
        except Exception:
            pass  # recall 失败 → 静默跳过
        if recalled:
            yield recalled


# ── 测试用例 ─────────────────────────────────────────────────


async def test_user_message_arrives_before_recall_completes() -> None:
    """核心验证：用户消息零延迟到达，不等 recall。"""
    transport = FakeTransport()
    cli = FakeCLI(transport, processing_delay=0.05)
    client = FakeClient(transport)
    cli.start()

    # 模拟一个慢 recall（2 秒）
    async def slow_recall() -> str:
        await asyncio.sleep(2.0)
        return "memory:important_fact"

    recall_task = asyncio.create_task(slow_recall())
    t0 = time.perf_counter()

    # 关键：query 在后台运行
    query_task = asyncio.create_task(
        client.query(deferred_yield_generator("hello", recall_task=recall_task))
    )

    # 主流程立即读响应
    first_response = await transport.read()
    first_response_time = time.perf_counter() - t0

    # 断言：第一条响应在 recall 完成之前就到了（远小于 2 秒）
    assert first_response == "response_to:hello"
    assert first_response_time < 0.5, (
        f"First response took {first_response_time:.2f}s, expected < 0.5s"
    )

    # 等待 recall 完成 → 第二条消息到达
    second_response = await transport.read()
    second_response_time = time.perf_counter() - t0

    assert second_response == "response_to:memory:important_fact"
    assert second_response_time >= 1.5, (
        f"Second response arrived at {second_response_time:.2f}s, expected >= 1.5s (recall takes 2s)"
    )

    # 清理
    await transport.write("__DONE__")
    result = await transport.read()
    assert result == "__RESULT__"
    await query_task
    await cli.stop()


async def test_recall_failure_does_not_block() -> None:
    """验证 recall 失败时主流程不受影响。"""
    transport = FakeTransport()
    cli = FakeCLI(transport, processing_delay=0.05)
    client = FakeClient(transport)
    cli.start()

    async def failing_recall() -> str:
        await asyncio.sleep(0.1)
        raise RuntimeError("API timeout")

    recall_task = asyncio.create_task(failing_recall())

    query_task = asyncio.create_task(
        client.query(deferred_yield_generator("hello", recall_task=recall_task))
    )

    # 用户消息正常到达
    first_response = await transport.read()
    assert first_response == "response_to:hello"

    # generator 应该正常结束（不 yield 第二条），不抛异常
    await query_task

    # CLI 仍在正常工作
    await transport.write("__DONE__")
    result = await transport.read()
    assert result == "__RESULT__"
    await cli.stop()


async def test_no_recall_task_is_single_message() -> None:
    """验证不传 recall_task 时行为与当前一致（只 yield 一条消息）。"""
    transport = FakeTransport()
    cli = FakeCLI(transport, processing_delay=0.05)
    client = FakeClient(transport)
    cli.start()

    query_task = asyncio.create_task(
        client.query(deferred_yield_generator("hello", recall_task=None))
    )

    first_response = await transport.read()
    assert first_response == "response_to:hello"

    # query 应该已经完成
    await query_task

    await transport.write("__DONE__")
    result = await transport.read()
    assert result == "__RESULT__"
    await cli.stop()


async def test_recall_completes_before_first_yield_still_works() -> None:
    """验证 recall 已经完成时，两条消息背靠背快速发出。"""
    transport = FakeTransport()
    cli = FakeCLI(transport, processing_delay=0.05)
    client = FakeClient(transport)
    cli.start()

    async def instant_recall() -> str:
        return "memory:cached_fact"

    recall_task = asyncio.create_task(instant_recall())
    await asyncio.sleep(0)  # 让 recall 先完成

    t0 = time.perf_counter()
    query_task = asyncio.create_task(
        client.query(deferred_yield_generator("hello", recall_task=recall_task))
    )

    first_response = await transport.read()
    second_response = await transport.read()
    total_time = time.perf_counter() - t0

    assert first_response == "response_to:hello"
    assert second_response == "response_to:memory:cached_fact"
    assert total_time < 0.5, f"Both responses took {total_time:.2f}s, expected < 0.5s"

    await query_task
    await transport.write("__DONE__")
    await transport.read()
    await cli.stop()


async def test_recall_returns_empty_string_no_second_message() -> None:
    """验证 recall 返回空字符串时不 yield 第二条消息（首轮无记忆文件场景）。"""
    transport = FakeTransport()
    cli = FakeCLI(transport, processing_delay=0.05)
    client = FakeClient(transport)
    cli.start()

    async def empty_recall() -> str:
        await asyncio.sleep(0.1)
        return ""

    recall_task = asyncio.create_task(empty_recall())

    query_task = asyncio.create_task(
        client.query(deferred_yield_generator("hello", recall_task=recall_task))
    )

    first_response = await transport.read()
    assert first_response == "response_to:hello"

    # generator 只 yield 了一条，query 应该正常结束
    await query_task

    # CLI 仍正常
    await transport.write("__DONE__")
    result = await transport.read()
    assert result == "__RESULT__"
    await cli.stop()
