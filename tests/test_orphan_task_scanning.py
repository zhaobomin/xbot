"""Tests for Phase 3 orphan-task assumptions and session tagging behavior."""

from __future__ import annotations

import asyncio

import pytest

from xbot.agent.runtime import AgentRuntime


class TestTaskSessionTagging:
    """Task-to-session matching must rely on explicit metadata only."""

    def test_task_tagging_uses_explicit_metadata_only(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)

            async def worker():
                await asyncio.sleep(0)

            task = loop.create_task(worker())
            AgentRuntime._tag_task_for_session(task, "user:1")

            assert AgentRuntime._task_belongs_to_session(task, "user:1") is True
            assert AgentRuntime._task_belongs_to_session(task, "user:10") is False

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                loop.run_until_complete(task)
        finally:
            asyncio.set_event_loop(None)
            loop.close()
