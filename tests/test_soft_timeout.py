"""Tests for crew execution without wall-clock timeout enforcement."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from xbot.crew.agent_pool import AgentPool, TaskProgress
from xbot.crew.models import AgentRole, TaskDefinition, TaskResult
from xbot.crew.process import SequentialProcess
from xbot.crew.state import CrewStateManager


def _make_process(task: TaskDefinition, pool: AgentPool | MagicMock) -> SequentialProcess:
    crew_config = MagicMock()
    crew_config.tasks = [task]
    crew_config.agents = {
        "test_agent": AgentRole(
            name="test_agent",
            description="Test",
            goal="Test",
        )
    }
    crew_config.global_context = ""
    crew_config.max_context_length = 4000
    crew_config.output.enabled = False
    crew_config.output.max_output_size = 100000

    context = MagicMock()
    context.build_agent_context = MagicMock(return_value=("test prompt", None))

    return SequentialProcess(
        pool=pool,
        context=context,
        permission_handler=MagicMock(),
        crew_config=crew_config,
        state_manager=CrewStateManager(task_names=[task.name], task_definitions=[task]),
    )


class TestTaskDefinitionTimeout:
    def test_timeout_defaults_to_none(self) -> None:
        task = TaskDefinition(name="test_task", description="Test task", agent="test_agent")
        assert task.timeout is None

    def test_timeout_is_metadata_only(self) -> None:
        task = TaskDefinition(
            name="test_task",
            description="Test task",
            agent="test_agent",
            timeout=300,
        )
        assert task.timeout == 300


class TestTaskResultQuality:
    def test_quality_defaults_to_full(self) -> None:
        result = TaskResult(
            task_name="test",
            agent_name="test",
            output="test",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        assert result.quality == "full"
        assert result.extended_count == 0

    def test_quality_can_be_partial(self) -> None:
        result = TaskResult(
            task_name="test",
            agent_name="test",
            output="test",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            quality="partial",
            extended_count=2,
        )
        assert result.quality == "partial"
        assert result.extended_count == 2


class TestTaskProgress:
    def test_task_progress_defaults(self) -> None:
        progress = TaskProgress()
        assert progress.delta_content == ""
        assert progress.total_content == ""
        assert progress.is_final is False

    def test_task_progress_with_content(self) -> None:
        progress = TaskProgress(delta_content="Hello", total_content="Hello World")
        assert progress.delta_content == "Hello"
        assert progress.total_content == "Hello World"


class TestCrewExecutionTimeoutSemantics:
    @pytest.mark.asyncio
    async def test_explicit_timeout_does_not_interrupt_streaming_task(self) -> None:
        async def mock_stream(*args, **kwargs):
            yield TaskProgress(delta_content="Start", total_content="Start", is_final=False)
            await asyncio.sleep(0.01)
            yield TaskProgress(delta_content="End", total_content="StartEnd", is_final=True)

        pool = MagicMock(spec=AgentPool)
        pool.run_task_streaming = mock_stream
        task = TaskDefinition(
            name="test_task",
            description="Test",
            agent="test_agent",
            timeout=1,
        )
        process = _make_process(task, pool)

        result = await process._execute_single_task(task)

        assert result.status == "success"
        assert result.output == "StartEnd"

    @pytest.mark.asyncio
    async def test_stream_end_without_final_returns_last_output(self) -> None:
        async def early_end_stream(*args, **kwargs):
            yield TaskProgress(delta_content="partial", total_content="partial", is_final=False)

        pool = MagicMock(spec=AgentPool)
        pool.run_task_streaming = early_end_stream
        task = TaskDefinition(name="test_task", description="Test", agent="test_agent")
        process = _make_process(task, pool)

        result = await process._execute_single_task(task)

        assert result.status == "success"
        assert result.output == "partial"

    @pytest.mark.asyncio
    async def test_unknown_agent_still_fails_fast(self) -> None:
        task = TaskDefinition(name="test_task", description="Test", agent="unknown_agent")
        process = _make_process(task, MagicMock(spec=AgentPool))

        result = await process._execute_single_task(task)

        assert result.status == "failed"
        assert "unknown_agent" in result.output
        assert "not found" in result.output.lower()
