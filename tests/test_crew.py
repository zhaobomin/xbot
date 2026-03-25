"""Comprehensive tests for xbot.agent.crew module."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from xbot.agent.crew.context import (
    CrewExecutionContext,
    load_checkpoint,
    save_checkpoint,
)
from xbot.agent.crew.models import (
    AgentRole,
    CrewConfig,
    ProcessType,
    TaskDefinition,
    TaskResult,
    load_crew_config,
)
from xbot.agent.crew.state import (
    CrewPhase,
    CrewStateManager,
    InvalidTransitionError,
    TaskPhase,
)

# ============================================================================
# Helpers
# ============================================================================


def _make_role(**overrides: Any) -> AgentRole:
    defaults = dict(name="r1", description="role desc", goal="role goal")
    defaults.update(overrides)
    return AgentRole(**defaults)


def _make_task(**overrides: Any) -> TaskDefinition:
    defaults = dict(name="t1", description="task desc", agent="r1")
    defaults.update(overrides)
    return TaskDefinition(**defaults)


def _make_result(
    task_name: str = "t1",
    agent_name: str = "r1",
    status: str = "success",
    output: str = "done",
    **overrides: Any,
) -> TaskResult:
    now = datetime.now()
    defaults = dict(
        task_name=task_name,
        agent_name=agent_name,
        output=output,
        status=status,
        started_at=now,
        finished_at=now + timedelta(seconds=1),
    )
    defaults.update(overrides)
    return TaskResult(**defaults)


def _make_crew_config(**overrides: Any) -> CrewConfig:
    defaults = dict(
        name="test-crew",
        agents={"r1": _make_role()},
        tasks=[_make_task()],
        workspace="/tmp/test-ws",
    )
    defaults.update(overrides)
    return CrewConfig(**defaults)


def _write_yaml(data: dict, tmpdir: Path) -> Path:
    p = tmpdir / "crew.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return p


# ============================================================================
# models.py tests
# ============================================================================


class TestTaskResult:
    def test_effective_output_uses_raw_by_default(self):
        r = _make_result(output="raw")
        assert r.effective_output == "raw"

    def test_effective_output_prefers_human_edit(self):
        r = _make_result(output="raw", human_edited_output="edited")
        assert r.effective_output == "edited"

    def test_effective_output_empty_string_edit_still_used(self):
        """Empty string human edit should still override raw (explicit empty)."""
        r = _make_result(output="raw", human_edited_output="")
        assert r.effective_output == ""

    def test_annotations_default_empty(self):
        r = _make_result()
        assert r.human_annotations == []

    def test_annotations_no_shared_mutable_default(self):
        """Each result should have its own annotations list."""
        r1 = _make_result()
        r2 = _make_result()
        r1.human_annotations.append("note")
        assert r2.human_annotations == []


class TestLoadCrewConfig:
    def test_loads_valid_yaml(self, tmp_path: Path):
        data = {
            "name": "test",
            "agents": {
                "scout": {"description": "d", "goal": "g"},
            },
            "tasks": [
                {"name": "t1", "description": "d", "agent": "scout"},
            ],
        }
        p = _write_yaml(data, tmp_path)
        config = load_crew_config(p)
        assert config.name == "test"
        assert "scout" in config.agents
        assert config.agents["scout"].name == "scout"
        assert len(config.tasks) == 1

    def test_resolves_workspace_relative_to_yaml(self, tmp_path: Path):
        data = {
            "name": "test",
            "workspace": "myproject",
            "agents": {"a": {"description": "d", "goal": "g"}},
            "tasks": [{"name": "t1", "description": "d", "agent": "a"}],
        }
        p = _write_yaml(data, tmp_path)
        config = load_crew_config(p)
        assert config.workspace == str(tmp_path / "myproject")

    def test_rejects_unknown_agent_reference(self, tmp_path: Path):
        data = {
            "name": "test",
            "agents": {"a": {"description": "d", "goal": "g"}},
            "tasks": [{"name": "t1", "description": "d", "agent": "MISSING"}],
        }
        p = _write_yaml(data, tmp_path)
        with pytest.raises(ValueError, match="unknown agent"):
            load_crew_config(p)

    def test_rejects_unknown_context_from(self, tmp_path: Path):
        data = {
            "name": "test",
            "agents": {"a": {"description": "d", "goal": "g"}},
            "tasks": [
                {"name": "t1", "description": "d", "agent": "a", "context_from": ["MISSING"]},
            ],
        }
        p = _write_yaml(data, tmp_path)
        with pytest.raises(ValueError, match="context_from"):
            load_crew_config(p)

    def test_rejects_invalid_manager_agent(self, tmp_path: Path):
        data = {
            "name": "test",
            "process": "hierarchical",
            "manager_agent": "MISSING",
            "agents": {"a": {"description": "d", "goal": "g"}},
            "tasks": [{"name": "t1", "description": "d", "agent": "a"}],
        }
        p = _write_yaml(data, tmp_path)
        with pytest.raises(ValueError, match="manager_agent"):
            load_crew_config(p)

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_crew_config(tmp_path / "no_such_file.yaml")

    def test_invalid_yaml_top_level(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_crew_config(p)

    def test_agents_as_list_format(self, tmp_path: Path):
        """Agents can also be specified as a list of dicts."""
        data = {
            "name": "test",
            "agents": [
                {"name": "a", "description": "d", "goal": "g"},
            ],
            "tasks": [{"name": "t1", "description": "d", "agent": "a"}],
        }
        p = _write_yaml(data, tmp_path)
        config = load_crew_config(p)
        assert "a" in config.agents

    def test_default_values(self, tmp_path: Path):
        data = {
            "name": "test",
            "agents": {"a": {"description": "d", "goal": "g"}},
            "tasks": [{"name": "t1", "description": "d", "agent": "a"}],
        }
        p = _write_yaml(data, tmp_path)
        config = load_crew_config(p)
        assert config.process == ProcessType.sequential
        assert config.verbose is False
        assert config.tasks[0].timeout == 600
        assert config.tasks[0].human_review is False
        assert config.tasks[0].human_briefing is False


# ============================================================================
# state.py tests
# ============================================================================


class TestCrewStateManager:
    def test_initial_state(self):
        sm = CrewStateManager(["t1", "t2"])
        assert sm.crew_phase == CrewPhase.CREATED
        assert sm.get_task_phase("t1") == TaskPhase.PENDING
        assert sm.get_task_phase("t2") == TaskPhase.PENDING

    def test_crew_transitions_valid(self):
        sm = CrewStateManager(["t1"])
        sm.transition_crew(CrewPhase.INITIALIZING)
        assert sm.crew_phase == CrewPhase.INITIALIZING
        sm.transition_crew(CrewPhase.RUNNING)
        assert sm.crew_phase == CrewPhase.RUNNING

    def test_crew_transition_invalid_raises(self):
        sm = CrewStateManager(["t1"])
        with pytest.raises(InvalidTransitionError):
            sm.transition_crew(CrewPhase.COMPLETED)  # CREATED -> COMPLETED not valid

    def test_task_transitions_valid(self):
        sm = CrewStateManager(["t1"])
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.QUEUED, "no deps")
        sm.transition_task("t1", TaskPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.COMPLETED)
        assert sm.get_task_phase("t1") == TaskPhase.COMPLETED

    def test_task_transition_invalid_raises(self):
        sm = CrewStateManager(["t1"])
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.QUEUED)
        sm.transition_task("t1", TaskPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            sm.transition_task("t1", TaskPhase.RUNNING)  # COMPLETED -> RUNNING not valid

    def test_unknown_task_raises(self):
        sm = CrewStateManager(["t1"])
        with pytest.raises(KeyError):
            sm.transition_task("NO_SUCH_TASK", TaskPhase.QUEUED)

    def test_auto_sync_running_when_tasks_active(self):
        sm = CrewStateManager(["t1", "t2"])
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.QUEUED)
        sm.transition_task("t1", TaskPhase.RUNNING)
        assert sm.crew_phase == CrewPhase.RUNNING

    def test_auto_sync_paused_when_only_awaiting_review(self):
        sm = CrewStateManager(["t1", "t2"])
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.QUEUED)
        sm.transition_task("t1", TaskPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.AWAITING_REVIEW)
        # t2 still PENDING → no active tasks, one AWAITING_REVIEW
        assert sm.crew_phase == CrewPhase.PAUSED

    def test_auto_sync_running_over_paused_when_queued_exists(self):
        """Bug2 fix: QUEUED task should keep crew RUNNING, not PAUSED."""
        sm = CrewStateManager(["t1", "t2"])
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.QUEUED)
        sm.transition_task("t1", TaskPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.AWAITING_REVIEW)
        # Now t1=AWAITING_REVIEW, crew=PAUSED
        assert sm.crew_phase == CrewPhase.PAUSED
        sm.transition_task("t2", TaskPhase.QUEUED)
        # t2=QUEUED → crew should go back to RUNNING
        assert sm.crew_phase == CrewPhase.RUNNING

    def test_auto_sync_completing_when_all_terminal(self):
        sm = CrewStateManager(["t1", "t2"])
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.QUEUED)
        sm.transition_task("t1", TaskPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.COMPLETED)
        sm.transition_task("t2", TaskPhase.SKIPPED, "upstream")
        assert sm.crew_phase == CrewPhase.COMPLETING

    def test_rejected_counted_as_terminal(self):
        """Bug1 fix: REJECTED should be in terminal set for crew completion."""
        sm = CrewStateManager(["t1", "t2"])
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.QUEUED)
        sm.transition_task("t1", TaskPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.AWAITING_REVIEW)
        sm.transition_task("t1", TaskPhase.REJECTED)
        sm.transition_task("t2", TaskPhase.SKIPPED)
        # Both in terminal: REJECTED + SKIPPED → COMPLETING
        assert sm.crew_phase == CrewPhase.COMPLETING

    def test_force_task_phase(self):
        sm = CrewStateManager(["t1"])
        sm.force_task_phase("t1", TaskPhase.COMPLETED)
        assert sm.get_task_phase("t1") == TaskPhase.COMPLETED

    def test_get_all_task_phases(self):
        sm = CrewStateManager(["t1", "t2"])
        phases = sm.get_all_task_phases()
        assert phases == {"t1": TaskPhase.PENDING, "t2": TaskPhase.PENDING}
        # Should return a copy
        phases["t1"] = TaskPhase.COMPLETED
        assert sm.get_task_phase("t1") == TaskPhase.PENDING

    def test_aborted_state_not_overridden_by_sync(self):
        sm = CrewStateManager(["t1"])
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)
        sm.transition_crew(CrewPhase.ABORTING)
        # Even if a task completes, crew stays ABORTING
        sm.transition_task("t1", TaskPhase.QUEUED)
        sm.transition_task("t1", TaskPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.COMPLETED)
        assert sm.crew_phase == CrewPhase.ABORTING

    def test_on_transition_callback(self):
        transitions = []
        sm = CrewStateManager(
            ["t1"],
            on_transition=lambda *args: transitions.append(args),
        )
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)
        sm.transition_task("t1", TaskPhase.QUEUED, "test")
        assert len(transitions) >= 3
        # Last task transition should be captured
        assert any("t1" in t and "queued" in t for t in transitions)


# ============================================================================
# context.py tests
# ============================================================================


class TestCrewExecutionContext:
    def test_add_and_get_result(self):
        ctx = CrewExecutionContext()
        r = _make_result(task_name="t1")
        ctx.add_result(r)
        assert ctx.get_result("t1") is r
        assert ctx.get_result("t2") is None

    def test_get_upstream_results(self):
        ctx = CrewExecutionContext()
        ctx.add_result(_make_result(task_name="t1"))
        ctx.add_result(_make_result(task_name="t2"))
        task = _make_task(name="t3", context_from=["t1", "t2"])
        upstream = ctx.get_upstream_results(task)
        assert set(upstream.keys()) == {"t1", "t2"}

    def test_get_upstream_results_missing_dep(self):
        ctx = CrewExecutionContext()
        ctx.add_result(_make_result(task_name="t1"))
        task = _make_task(name="t3", context_from=["t1", "MISSING"])
        upstream = ctx.get_upstream_results(task)
        assert "t1" in upstream
        assert "MISSING" not in upstream


class TestBuildTaskPrompt:
    def test_basic_prompt_structure(self):
        ctx = CrewExecutionContext()
        role = _make_role(name="scout", goal="find bugs")
        task = _make_task(name="t1", description="scan code")
        prompt = ctx.build_task_prompt(task, role)
        assert "**scout**" in prompt
        assert "find bugs" in prompt
        assert "scan code" in prompt

    def test_includes_global_context(self):
        ctx = CrewExecutionContext()
        prompt = ctx.build_task_prompt(_make_task(), _make_role(), global_context="project X")
        assert "Project Context" in prompt
        assert "project X" in prompt

    def test_includes_human_briefing(self):
        ctx = CrewExecutionContext()
        prompt = ctx.build_task_prompt(_make_task(), _make_role(), human_briefing="focus auth")
        assert "Additional Instructions from Team Lead" in prompt
        assert "focus auth" in prompt

    def test_includes_upstream_effective_output(self):
        ctx = CrewExecutionContext()
        ctx.add_result(_make_result(
            task_name="t1", output="raw", human_edited_output="edited",
        ))
        task = _make_task(name="t2", context_from=["t1"])
        prompt = ctx.build_task_prompt(task, _make_role())
        assert "edited" in prompt
        assert "raw" not in prompt  # raw replaced by effective_output

    def test_includes_human_annotations(self):
        ctx = CrewExecutionContext()
        r = _make_result(task_name="t1")
        r.human_annotations = ["note1", "note2"]
        ctx.add_result(r)
        task = _make_task(name="t2", context_from=["t1"])
        prompt = ctx.build_task_prompt(task, _make_role())
        assert "Team Lead Review Notes" in prompt
        assert "note1" in prompt
        assert "note2" in prompt

    def test_includes_expected_output(self):
        ctx = CrewExecutionContext()
        task = _make_task(expected_output="JSON format")
        prompt = ctx.build_task_prompt(task, _make_role())
        assert "Expected Output" in prompt
        assert "JSON format" in prompt

    def test_truncates_long_upstream_output(self):
        ctx = CrewExecutionContext()
        ctx.add_result(_make_result(task_name="t1", output="x" * 5000))
        task = _make_task(name="t2", context_from=["t1"])
        prompt = ctx.build_task_prompt(task, _make_role())
        assert "(output truncated)" in prompt
        assert len(prompt) < 6000  # Should not blow up

    def test_backstory_included(self):
        ctx = CrewExecutionContext()
        role = _make_role(backstory="senior engineer")
        prompt = ctx.build_task_prompt(_make_task(), role)
        assert "senior engineer" in prompt


class TestCheckpoint:
    def test_save_and_load_round_trip(self, tmp_path: Path):
        config = _make_crew_config(workspace=str(tmp_path))
        ctx = CrewExecutionContext()
        ctx.add_result(_make_result(task_name="t1", output="done"))

        cp_path = save_checkpoint(
            config, "/tmp/cfg.yaml", ctx,
            crew_phase="running", next_task="t2",
            started_at=datetime.now(),
        )
        assert cp_path.exists()

        cp = load_checkpoint(cp_path)
        assert cp["crew_name"] == "test-crew"
        assert len(cp["completed_tasks"]) == 1
        assert cp["completed_tasks"][0]["name"] == "t1"
        assert cp["next_task"] == "t2"

    def test_saves_all_statuses_including_failed(self, tmp_path: Path):
        """Bug5 fix: checkpoint should include failed/skipped tasks."""
        config = _make_crew_config(workspace=str(tmp_path))
        ctx = CrewExecutionContext()
        ctx.add_result(_make_result(task_name="t1", status="success"))
        ctx.add_result(_make_result(task_name="t2", status="failed", output="error"))

        cp_path = save_checkpoint(
            config, "/tmp/cfg.yaml", ctx,
            crew_phase="running", next_task=None,
            started_at=datetime.now(),
        )
        cp = load_checkpoint(cp_path)
        assert len(cp["completed_tasks"]) == 2
        statuses = {t["name"]: t["status"] for t in cp["completed_tasks"]}
        assert statuses["t1"] == "success"
        assert statuses["t2"] == "failed"

    def test_preserves_human_fields(self, tmp_path: Path):
        config = _make_crew_config(workspace=str(tmp_path))
        ctx = CrewExecutionContext()
        r = _make_result(
            task_name="t1",
            human_edited_output="edited",
            human_briefing_input="briefing",
        )
        r.human_annotations = ["ann1"]
        ctx.add_result(r)

        cp_path = save_checkpoint(
            config, "/tmp/cfg.yaml", ctx,
            crew_phase="running", next_task=None,
            started_at=datetime.now(),
        )
        cp = load_checkpoint(cp_path)
        t = cp["completed_tasks"][0]
        assert t["human_edited_output"] == "edited"
        assert t["human_annotations"] == ["ann1"]
        assert t["human_briefing_input"] == "briefing"

    def test_load_nonexistent_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_checkpoint(tmp_path / "nope.json")


# ============================================================================
# process.py tests (with mocked pool & permission handler)
# ============================================================================


def _mock_pool(outputs: dict[str, str] | None = None):
    """Create a mock AgentPool that returns predetermined outputs."""
    default_output = "mock output"
    pool = AsyncMock()

    async def run_task(role_name: str, prompt: str, session_key: str) -> str:
        if outputs and role_name in outputs:
            return outputs[role_name]
        return default_output

    pool.run_task = AsyncMock(side_effect=run_task)
    return pool


def _mock_permission(responses: list[str] | None = None):
    """Create a mock permission handler with predetermined responses."""
    handler = AsyncMock()
    call_idx = 0

    async def request_interaction(**kwargs):
        nonlocal call_idx
        resp = MagicMock()
        if responses and call_idx < len(responses):
            resp.content = responses[call_idx]
            call_idx += 1
        else:
            resp.content = "continue"
        return resp

    handler.request_interaction = AsyncMock(side_effect=request_interaction)
    return handler


class TestSequentialProcess:
    @pytest.mark.asyncio
    async def test_executes_all_tasks_in_order(self):
        from xbot.agent.crew.process import SequentialProcess

        config = _make_crew_config(
            tasks=[
                _make_task(name="t1"),
                _make_task(name="t2", context_from=["t1"]),
            ],
        )
        sm = CrewStateManager(
            ["t1", "t2"],
            task_definitions=config.tasks,
        )
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)

        ctx = CrewExecutionContext()
        pool = _mock_pool()
        perm = _mock_permission()

        proc = SequentialProcess(
            pool=pool, context=ctx, permission_handler=perm,
            crew_config=config, state_manager=sm,
        )
        results = await proc.execute(config.tasks)

        assert len(results) == 2
        assert results[0].task_name == "t1"
        assert results[1].task_name == "t2"
        assert all(r.status == "success" for r in results)
        assert sm.get_task_phase("t1") == TaskPhase.COMPLETED
        assert sm.get_task_phase("t2") == TaskPhase.COMPLETED

    @pytest.mark.asyncio
    async def test_skips_downstream_on_upstream_failure(self):
        from xbot.agent.crew.process import SequentialProcess

        config = _make_crew_config(
            tasks=[
                _make_task(name="t1"),
                _make_task(name="t2", context_from=["t1"]),
            ],
        )
        sm = CrewStateManager(["t1", "t2"], task_definitions=config.tasks)
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)

        pool = _mock_pool()
        pool.run_task = AsyncMock(side_effect=RuntimeError("boom"))
        ctx = CrewExecutionContext()
        perm = _mock_permission()

        proc = SequentialProcess(
            pool=pool, context=ctx, permission_handler=perm,
            crew_config=config, state_manager=sm,
        )
        results = await proc.execute(config.tasks)

        assert results[0].status == "failed"
        assert results[1].status == "skipped"
        assert sm.get_task_phase("t1") == TaskPhase.FAILED
        assert sm.get_task_phase("t2") == TaskPhase.SKIPPED

    @pytest.mark.asyncio
    async def test_human_review_continue(self):
        from xbot.agent.crew.process import SequentialProcess

        config = _make_crew_config(
            tasks=[_make_task(name="t1", human_review=True)],
        )
        sm = CrewStateManager(["t1"], task_definitions=config.tasks)
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)

        pool = _mock_pool()
        perm = _mock_permission(["continue"])
        ctx = CrewExecutionContext()

        proc = SequentialProcess(
            pool=pool, context=ctx, permission_handler=perm,
            crew_config=config, state_manager=sm,
        )
        results = await proc.execute(config.tasks)
        assert results[0].status == "success"
        assert sm.get_task_phase("t1") == TaskPhase.COMPLETED

    @pytest.mark.asyncio
    async def test_human_review_abort_skips_remaining(self):
        """Bug3 fix: abort should add skipped TaskResults for remaining tasks."""
        from xbot.agent.crew.process import SequentialProcess

        config = _make_crew_config(
            tasks=[
                _make_task(name="t1", human_review=True),
                _make_task(name="t2"),
                _make_task(name="t3"),
            ],
        )
        sm = CrewStateManager(["t1", "t2", "t3"], task_definitions=config.tasks)
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)

        pool = _mock_pool()
        perm = _mock_permission(["abort"])
        ctx = CrewExecutionContext()

        proc = SequentialProcess(
            pool=pool, context=ctx, permission_handler=perm,
            crew_config=config, state_manager=sm,
        )
        results = await proc.execute(config.tasks)

        assert len(results) == 3  # All tasks should be in results
        assert results[0].status == "human_rejected"
        assert results[1].status == "skipped"
        assert results[2].status == "skipped"
        assert sm.get_task_phase("t2") == TaskPhase.SKIPPED
        assert sm.get_task_phase("t3") == TaskPhase.SKIPPED

    @pytest.mark.asyncio
    async def test_human_review_annotate(self):
        from xbot.agent.crew.process import SequentialProcess

        config = _make_crew_config(
            tasks=[_make_task(name="t1", human_review=True)],
        )
        sm = CrewStateManager(["t1"], task_definitions=config.tasks)
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)

        pool = _mock_pool()
        # First response: "annotate", second: the annotation text
        perm = _mock_permission(["annotate", "this is my note"])
        ctx = CrewExecutionContext()

        proc = SequentialProcess(
            pool=pool, context=ctx, permission_handler=perm,
            crew_config=config, state_manager=sm,
        )
        results = await proc.execute(config.tasks)
        assert results[0].human_annotations == ["this is my note"]
        assert sm.get_task_phase("t1") == TaskPhase.COMPLETED

    @pytest.mark.asyncio
    async def test_human_review_edit(self):
        from xbot.agent.crew.process import SequentialProcess

        config = _make_crew_config(
            tasks=[_make_task(name="t1", human_review=True)],
        )
        sm = CrewStateManager(["t1"], task_definitions=config.tasks)
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)

        pool = _mock_pool()
        perm = _mock_permission(["edit", "revised output"])
        ctx = CrewExecutionContext()

        proc = SequentialProcess(
            pool=pool, context=ctx, permission_handler=perm,
            crew_config=config, state_manager=sm,
        )
        results = await proc.execute(config.tasks)
        assert results[0].human_edited_output == "revised output"
        assert results[0].effective_output == "revised output"

    @pytest.mark.asyncio
    async def test_human_review_skip(self):
        from xbot.agent.crew.process import SequentialProcess

        config = _make_crew_config(
            tasks=[_make_task(name="t1", human_review=True)],
        )
        sm = CrewStateManager(["t1"], task_definitions=config.tasks)
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)

        pool = _mock_pool()
        perm = _mock_permission(["skip"])
        ctx = CrewExecutionContext()

        proc = SequentialProcess(
            pool=pool, context=ctx, permission_handler=perm,
            crew_config=config, state_manager=sm,
        )
        results = await proc.execute(config.tasks)
        assert results[0].status == "skipped"
        assert sm.get_task_phase("t1") == TaskPhase.SKIPPED

    @pytest.mark.asyncio
    async def test_human_briefing_injected_into_prompt(self):
        from xbot.agent.crew.process import SequentialProcess

        config = _make_crew_config(
            tasks=[_make_task(name="t1", human_briefing=True)],
        )
        sm = CrewStateManager(["t1"], task_definitions=config.tasks)
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)

        pool = _mock_pool()
        perm = _mock_permission(["fix auth only"])
        ctx = CrewExecutionContext()

        proc = SequentialProcess(
            pool=pool, context=ctx, permission_handler=perm,
            crew_config=config, state_manager=sm,
        )
        await proc.execute(config.tasks)

        # Check that the pool was called with a prompt containing the briefing
        call_args = pool.run_task.call_args
        prompt_used = call_args.args[1] if call_args.args else call_args.kwargs.get("prompt", "")
        assert "fix auth only" in prompt_used

    @pytest.mark.asyncio
    async def test_resume_skips_completed_tasks(self):
        from xbot.agent.crew.process import SequentialProcess

        config = _make_crew_config(
            tasks=[
                _make_task(name="t1"),
                _make_task(name="t2", context_from=["t1"]),
            ],
        )
        sm = CrewStateManager(["t1", "t2"], task_definitions=config.tasks)
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)
        # Simulate: t1 already completed (checkpoint restore)
        sm.force_task_phase("t1", TaskPhase.COMPLETED)

        ctx = CrewExecutionContext()
        ctx.add_result(_make_result(task_name="t1", output="prev output"))

        pool = _mock_pool()
        perm = _mock_permission()

        proc = SequentialProcess(
            pool=pool, context=ctx, permission_handler=perm,
            crew_config=config, state_manager=sm,
        )
        results = await proc.execute(config.tasks)

        assert len(results) == 2
        assert results[0].task_name == "t1"
        assert results[0].output == "prev output"  # from checkpoint
        assert results[1].task_name == "t2"
        assert results[1].status == "success"  # freshly executed
        # Pool should only have been called once (for t2)
        assert pool.run_task.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_marks_task_failed(self):
        from xbot.agent.crew.process import SequentialProcess

        config = _make_crew_config(
            tasks=[_make_task(name="t1", timeout=1)],
        )
        sm = CrewStateManager(["t1"], task_definitions=config.tasks)
        sm.transition_crew(CrewPhase.INITIALIZING)
        sm.transition_crew(CrewPhase.RUNNING)

        pool = _mock_pool()

        async def slow_task(*args, **kwargs):
            await asyncio.sleep(10)
            return "never"

        pool.run_task = AsyncMock(side_effect=slow_task)
        perm = _mock_permission()
        ctx = CrewExecutionContext()

        proc = SequentialProcess(
            pool=pool, context=ctx, permission_handler=perm,
            crew_config=config, state_manager=sm,
        )
        results = await proc.execute(config.tasks)
        assert results[0].status == "failed"
        assert "timed out" in results[0].output


# ============================================================================
# orchestrator.py tests
# ============================================================================


class TestCrewOrchestrator:
    @pytest.mark.asyncio
    async def test_apply_checkpoint_restores_success_only(self):
        """Bug5 fix: only success/completed tasks are restored on resume."""
        from xbot.agent.crew.orchestrator import CrewOrchestrator

        config = _make_crew_config(
            tasks=[
                _make_task(name="t1"),
                _make_task(name="t2"),
            ],
        )
        sm = CrewStateManager(["t1", "t2"])
        ctx = CrewExecutionContext()

        cp = {
            "completed_tasks": [
                {
                    "name": "t1", "agent": "r1", "status": "success",
                    "output": "ok", "started_at": datetime.now().isoformat(),
                    "finished_at": datetime.now().isoformat(),
                },
                {
                    "name": "t2", "agent": "r1", "status": "failed",
                    "output": "err", "started_at": datetime.now().isoformat(),
                    "finished_at": datetime.now().isoformat(),
                },
            ]
        }

        orch = CrewOrchestrator(config, MagicMock(), MagicMock())
        orch._apply_checkpoint(cp, ctx, sm)

        # t1 success → restored
        assert sm.get_task_phase("t1") == TaskPhase.COMPLETED
        assert ctx.get_result("t1") is not None
        # t2 failed → not restored (should be re-executed)
        assert sm.get_task_phase("t2") == TaskPhase.PENDING
        assert ctx.get_result("t2") is None


# ============================================================================
# HierarchicalProcess._parse_plan tests
# ============================================================================


class TestHierarchicalParsePlan:
    def test_parses_valid_json_array(self):
        from xbot.agent.crew.process import HierarchicalProcess

        result = HierarchicalProcess._parse_plan('["t1", "t2", "t3"]')
        assert result == ["t1", "t2", "t3"]

    def test_parses_json_embedded_in_text(self):
        from xbot.agent.crew.process import HierarchicalProcess

        result = HierarchicalProcess._parse_plan(
            'Here is my plan:\n["t1", "t2"]\nDone.'
        )
        assert result == ["t1", "t2"]

    def test_returns_none_for_no_array(self):
        from xbot.agent.crew.process import HierarchicalProcess

        assert HierarchicalProcess._parse_plan("no json here") is None

    def test_returns_none_for_non_string_array(self):
        from xbot.agent.crew.process import HierarchicalProcess

        assert HierarchicalProcess._parse_plan("[1, 2, 3]") is None

    def test_returns_none_for_invalid_json(self):
        from xbot.agent.crew.process import HierarchicalProcess

        assert HierarchicalProcess._parse_plan("[invalid json]") is None


# ============================================================================
# Integration: full YAML → config → show command
# ============================================================================


class TestBugfixYAML:
    def test_loads_example_bugfix_yaml(self):
        p = Path(__file__).resolve().parents[1] / "examples" / "crews" / "bugfix.yaml"
        if not p.exists():
            pytest.skip("bugfix.yaml not found")
        config = load_crew_config(p)
        assert config.name == "bug-fix-crew"
        assert len(config.agents) == 4
        assert len(config.tasks) == 4
        # Verify dependency chain
        task_map = {t.name: t for t in config.tasks}
        assert task_map["fix_bugs"].context_from == ["discover_bugs"]
        assert task_map["review_and_test"].context_from == ["discover_bugs", "fix_bugs"]
        assert task_map["create_pr"].context_from == ["fix_bugs", "review_and_test"]
        # Verify human flags
        assert task_map["discover_bugs"].human_review is True
        assert task_map["fix_bugs"].human_briefing is True
        assert task_map["review_and_test"].human_review is True
        assert task_map["create_pr"].human_review is False
