"""Integration tests for Goal mode CLI commands and full loop flow.

Covers:
- All 8 CLI commands: init, list, show, modify, run, pause, resume, clear
- Full PLAN → ACT → VERIFY → ACHIEVED flow
- PLAN → ACT → VERIFY fail → rollback → UNMET flow
- Git precondition checks (clean repo vs dirty repo vs non-git)
- .goal/ directory exclusion from git status (bug fix regression)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from xbot.interfaces.cli.goal import GoalRunner, goal_app
from xbot.runtime.session.goal_store import (
    GoalPhase,
    GoalState,
    GoalStatus,
    GoalStore,
    generate_goal_id,
)

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def git_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary git repo with an initial commit and chdir into it."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    # Initial commit so HEAD exists
    readme = tmp_path / "README.md"
    readme.write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def non_git_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary directory without git."""
    # Use a subdirectory to avoid conflict with git_workspace fixture's tmp_path
    d = tmp_path / "nongit"
    d.mkdir()
    monkeypatch.chdir(d)
    return d


@pytest.fixture
def store(git_workspace: Path) -> GoalStore:
    return GoalStore(git_workspace)


@pytest.fixture
def sample_goal(store: GoalStore, git_workspace: Path) -> GoalState:
    """Create and save a sample goal."""
    goal = GoalState(
        goal_id=generate_goal_id(),
        objective="implement user registration",
        workspace=str(git_workspace),
        session_key="goal:test-001",
        verify_cmd="pytest tests/",
        max_loops=3,
    )
    store.save(goal)
    return goal


def _make_fake_service():
    """Create a mock AgentService with required async methods."""
    svc = AsyncMock()
    svc.initialize = AsyncMock()
    svc.close_mcp = AsyncMock()
    svc.process_direct = AsyncMock(return_value="")
    return svc


# ---------------------------------------------------------------------------
# CLI Command Tests: init
# ---------------------------------------------------------------------------


class TestGoalInit:
    def test_init_creates_goal(self, git_workspace: Path):
        result = runner.invoke(
            goal_app,
            ["init", "implement login feature"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Goal created:" in result.output
        assert "implement login feature" in result.output

        # Verify state file written
        goal_dir = git_workspace / ".goal"
        assert goal_dir.exists()
        files = list(goal_dir.glob("goal-*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["objective"] == "implement login feature"
        assert data["status"] == "initialized"
        assert data["max_loops"] == 10

    def test_init_with_verify_and_max_loops(self, git_workspace: Path):
        result = runner.invoke(
            goal_app,
            ["init", "add API endpoint", "--verify", "make test", "--max-loops", "5"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "make test" in result.output
        assert "5" in result.output

        goal_dir = git_workspace / ".goal"
        files = list(goal_dir.glob("goal-*.json"))
        data = json.loads(files[0].read_text())
        assert data["verify_cmd"] == "make test"
        assert data["max_loops"] == 5


# ---------------------------------------------------------------------------
# CLI Command Tests: list
# ---------------------------------------------------------------------------


class TestGoalList:
    def test_list_empty(self, git_workspace: Path):
        result = runner.invoke(goal_app, ["list"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "No goals found" in result.output

    def test_list_shows_goals(self, git_workspace: Path, sample_goal: GoalState):
        result = runner.invoke(goal_app, ["list"], catch_exceptions=False)
        assert result.exit_code == 0
        assert sample_goal.goal_id in result.output
        assert "initialized" in result.output


# ---------------------------------------------------------------------------
# CLI Command Tests: show
# ---------------------------------------------------------------------------


class TestGoalShow:
    def test_show_no_goal(self, git_workspace: Path):
        result = runner.invoke(goal_app, ["show"], catch_exceptions=False)
        assert result.exit_code == 1
        assert "No goal found" in result.output

    def test_show_displays_details(self, git_workspace: Path, sample_goal: GoalState):
        result = runner.invoke(
            goal_app, ["show", sample_goal.goal_id], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert sample_goal.goal_id in result.output
        assert "implement user registration" in result.output
        assert "pytest tests/" in result.output
        assert "0/3" in result.output


# ---------------------------------------------------------------------------
# CLI Command Tests: modify
# ---------------------------------------------------------------------------


class TestGoalModify:
    def test_modify_verify_cmd(self, git_workspace: Path, sample_goal: GoalState):
        result = runner.invoke(
            goal_app,
            ["modify", sample_goal.goal_id, "--verify", "make check"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "make check" in result.output
        assert "Goal updated" in result.output

        # Verify persisted
        store = GoalStore(git_workspace)
        reloaded = store.load(sample_goal.goal_id)
        assert reloaded is not None
        assert reloaded.verify_cmd == "make check"

    def test_modify_max_loops(self, git_workspace: Path, sample_goal: GoalState):
        result = runner.invoke(
            goal_app,
            ["modify", sample_goal.goal_id, "--max-loops", "20"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "20" in result.output

        store = GoalStore(git_workspace)
        reloaded = store.load(sample_goal.goal_id)
        assert reloaded is not None
        assert reloaded.max_loops == 20

    def test_modify_nothing(self, git_workspace: Path, sample_goal: GoalState):
        result = runner.invoke(
            goal_app,
            ["modify", sample_goal.goal_id],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Nothing to modify" in result.output


# ---------------------------------------------------------------------------
# CLI Command Tests: clear
# ---------------------------------------------------------------------------


class TestGoalClear:
    def test_clear_with_force(self, git_workspace: Path, sample_goal: GoalState):
        result = runner.invoke(
            goal_app,
            ["clear", sample_goal.goal_id, "--force"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Goal cleared" in result.output

        # Verify deleted
        store = GoalStore(git_workspace)
        assert store.load(sample_goal.goal_id) is None

    def test_clear_active_requires_force(self, git_workspace: Path, sample_goal: GoalState):
        # Make it active
        store = GoalStore(git_workspace)
        sample_goal.status = GoalStatus.ACTIVE
        store.save(sample_goal)

        result = runner.invoke(
            goal_app,
            ["clear", sample_goal.goal_id],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        assert "active" in result.output.lower()

    def test_clear_no_goal(self, git_workspace: Path):
        result = runner.invoke(
            goal_app, ["clear", "--force"], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "No goal found" in result.output


# ---------------------------------------------------------------------------
# CLI Command Tests: pause
# ---------------------------------------------------------------------------


class TestGoalPause:
    def test_pause_shows_hint(self, git_workspace: Path):
        result = runner.invoke(goal_app, ["pause"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Ctrl-C" in result.output


# ---------------------------------------------------------------------------
# CLI Command Tests: resume (alias for run)
# ---------------------------------------------------------------------------


class TestGoalResume:
    def test_resume_no_goal(self, git_workspace: Path):
        result = runner.invoke(goal_app, ["resume"], catch_exceptions=False)
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# GoalStore unit tests
# ---------------------------------------------------------------------------


class TestGoalStore:
    def test_save_and_load_roundtrip(self, store: GoalStore, git_workspace: Path):
        goal = GoalState(
            goal_id="goal-20260731-abc12345",
            objective="test roundtrip",
            workspace=str(git_workspace),
            session_key="goal:test",
        )
        store.save(goal)
        loaded = store.load("goal-20260731-abc12345")
        assert loaded is not None
        assert loaded.objective == "test roundtrip"
        assert loaded.status == GoalStatus.INITIALIZED
        assert loaded.current_phase is None

    def test_find_active(self, store: GoalStore, git_workspace: Path):
        g1 = GoalState(
            goal_id="goal-20260731-aaa",
            objective="old achieved",
            workspace=str(git_workspace),
            session_key="goal:g1",
            status=GoalStatus.ACHIEVED,
        )
        g2 = GoalState(
            goal_id="goal-20260731-bbb",
            objective="active one",
            workspace=str(git_workspace),
            session_key="goal:g2",
            status=GoalStatus.PAUSED,
        )
        store.save(g1)
        store.save(g2)

        found = store.find_active()
        assert found is not None
        assert found.goal_id == "goal-20260731-bbb"

    def test_delete(self, store: GoalStore, git_workspace: Path):
        goal = GoalState(
            goal_id="goal-20260731-del",
            objective="to delete",
            workspace=str(git_workspace),
            session_key="goal:del",
        )
        store.save(goal)
        assert store.delete("goal-20260731-del") is True
        assert store.load("goal-20260731-del") is None
        assert store.delete("goal-20260731-del") is False


# ---------------------------------------------------------------------------
# Git precondition tests (REGRESSION for .goal/ exclusion bug)
# ---------------------------------------------------------------------------


class TestGitPreconditions:
    """REGRESSION: .goal/ dir must not cause checkpoint mode to be disabled."""

    def test_goal_dir_does_not_dirty_status(self, git_workspace: Path):
        """After goal init, _check_git_preconditions should still return True."""
        # Create .goal/ directory (simulating goal init)
        goal_dir = git_workspace / ".goal"
        goal_dir.mkdir()
        (goal_dir / "goal-test.json").write_text("{}")

        runner_obj = GoalRunner.__new__(GoalRunner)
        runner_obj.goal = MagicMock()
        runner_obj.store = MagicMock()

        result = runner_obj._check_git_preconditions(git_workspace)
        assert result is True, ".goal/ should be excluded from git status check"

    def test_real_dirty_file_disables_checkpoint(self, git_workspace: Path):
        """A real uncommitted file should disable checkpoint mode."""
        (git_workspace / "dirty.txt").write_text("uncommitted")

        runner_obj = GoalRunner.__new__(GoalRunner)
        runner_obj.goal = MagicMock()
        runner_obj.store = MagicMock()

        result = runner_obj._check_git_preconditions(git_workspace)
        assert result is False

    def test_non_git_returns_false(self, non_git_workspace: Path):
        runner_obj = GoalRunner.__new__(GoalRunner)
        runner_obj.goal = MagicMock()
        runner_obj.store = MagicMock()

        result = runner_obj._check_git_preconditions(non_git_workspace)
        assert result is False

    def test_git_clean_preserves_goal_dir(self, git_workspace: Path):
        """REGRESSION: git clean -fd -e .goal must not delete .goal/ state."""
        goal_dir = git_workspace / ".goal"
        goal_dir.mkdir()
        state_file = goal_dir / "goal-test.json"
        state_file.write_text('{"status":"active"}')

        # Also create an untracked file that SHOULD be cleaned
        (git_workspace / "generated.txt").write_text("should be removed")

        # Get current HEAD
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_workspace,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Simulate rollback
        runner_obj = GoalRunner.__new__(GoalRunner)
        runner_obj._git_reset_hard(git_workspace, head)

        # .goal/ must survive
        assert state_file.exists(), ".goal/ should be preserved after git clean"
        assert state_file.read_text() == '{"status":"active"}'

        # Generated file should be gone
        assert not (git_workspace / "generated.txt").exists()


# ---------------------------------------------------------------------------
# Full loop integration: PLAN → ACT → VERIFY → ACHIEVED
# ---------------------------------------------------------------------------


class TestGoalRunLoop:
    """Test the full GoalRunner loop with mocked agent service."""

    @pytest.fixture
    def goal_in_repo(self, git_workspace: Path) -> GoalState:
        store = GoalStore(git_workspace)
        goal = GoalState(
            goal_id="goal-20260731-run01",
            objective="add a hello function",
            workspace=str(git_workspace),
            session_key="goal:run01",
            verify_cmd="test -f hello.py",
            max_loops=3,
        )
        store.save(goal)
        return goal

    @pytest.mark.integration
    def test_full_loop_achieved(self, git_workspace: Path, goal_in_repo: GoalState):
        """PLAN → ACT (completed) → VERIFY (pass) → ACHIEVED."""
        store = GoalStore(git_workspace)
        call_count = {"n": 0}

        async def fake_process_direct(content, **kwargs):
            call_count["n"] += 1
            on_progress = kwargs.get("on_progress")

            if "[GOAL MODE]" in content or "[PLAN]" in content:
                # PLAN phase — just return plan text
                return "I will create hello.py"

            if "[ACT]" in content or content == "Continue executing.":
                # ACT phase — create the file and signal completed
                (git_workspace / "hello.py").write_text("def hello(): pass\n")
                if on_progress:
                    await on_progress(
                        "",
                        event_type="result",
                        event_data={"terminal_reason": "completed"},
                    )
                return "Done creating hello.py"

            if "[VERIFY PASSED]" in content:
                return "Great, verification passed."

            return ""

        fake_svc = _make_fake_service()
        fake_svc.process_direct = AsyncMock(side_effect=fake_process_direct)

        with patch.object(GoalRunner, "_create_service", return_value=fake_svc):
            goal_runner = GoalRunner(goal_in_repo, store)
            asyncio.run(goal_runner.run())

        # Verify final state
        final = store.load(goal_in_repo.goal_id)
        assert final is not None
        assert final.status == GoalStatus.ACHIEVED
        assert final.loop_count == 0  # Achieved on first loop (no increment)

    @pytest.mark.integration
    def test_loop_verify_fail_then_unmet(self, git_workspace: Path, goal_in_repo: GoalState):
        """VERIFY always fails → rollback each loop → eventually UNMET."""
        store = GoalStore(git_workspace)
        # Set max_loops=2 for faster test
        goal_in_repo.max_loops = 2
        store.save(goal_in_repo)

        async def fake_process_direct(content, **kwargs):
            on_progress = kwargs.get("on_progress")

            if "[GOAL MODE]" in content or "[PLAN]" in content:
                return "plan"

            if "[ACT]" in content or content == "Continue executing.":
                # Create a file (will be rolled back)
                (git_workspace / "attempt.txt").write_text("attempt")
                if on_progress:
                    await on_progress(
                        "",
                        event_type="result",
                        event_data={"terminal_reason": "completed"},
                    )
                return "done"

            if "[VERIFY FAILED]" in content:
                return "acknowledged"

            return ""

        fake_svc = _make_fake_service()
        fake_svc.process_direct = AsyncMock(side_effect=fake_process_direct)

        with patch.object(GoalRunner, "_create_service", return_value=fake_svc):
            goal_runner = GoalRunner(goal_in_repo, store)
            asyncio.run(goal_runner.run())

        final = store.load(goal_in_repo.goal_id)
        assert final is not None
        assert final.status == GoalStatus.UNMET
        assert final.loop_count == 2

    @pytest.mark.integration
    def test_act_continuation(self, git_workspace: Path, goal_in_repo: GoalState):
        """ACT phase continues until terminal_reason='completed'."""
        store = GoalStore(git_workspace)
        # Override verify to simple file existence
        goal_in_repo.verify_cmd = "test -f result.txt"
        store.save(goal_in_repo)

        act_calls = {"n": 0}

        async def fake_process_direct(content, **kwargs):
            on_progress = kwargs.get("on_progress")

            if "[GOAL MODE]" in content or "[PLAN]" in content:
                return "plan"

            if "[ACT]" in content or content == "Continue executing.":
                act_calls["n"] += 1
                # Complete on 3rd ACT call
                if act_calls["n"] >= 3:
                    (git_workspace / "result.txt").write_text("done")
                    if on_progress:
                        await on_progress(
                            "",
                            event_type="result",
                            event_data={"terminal_reason": "completed"},
                        )
                else:
                    # max_turns hit (no terminal_reason set → will continue)
                    if on_progress:
                        await on_progress(
                            "",
                            event_type="result",
                            event_data={"terminal_reason": "max_turns"},
                        )
                return "working..."

            if "[VERIFY PASSED]" in content:
                return "verified"

            return ""

        fake_svc = _make_fake_service()
        fake_svc.process_direct = AsyncMock(side_effect=fake_process_direct)

        with patch.object(GoalRunner, "_create_service", return_value=fake_svc):
            goal_runner = GoalRunner(goal_in_repo, store)
            asyncio.run(goal_runner.run())

        final = store.load(goal_in_repo.goal_id)
        assert final is not None
        assert final.status == GoalStatus.ACHIEVED
        assert act_calls["n"] == 3

    @pytest.mark.integration
    def test_self_verification_achieved(self, git_workspace: Path):
        """Without verify_cmd, agent self-verifies with [GOAL_ACHIEVED] marker."""
        store = GoalStore(git_workspace)
        goal = GoalState(
            goal_id="goal-20260731-self",
            objective="refactor module",
            workspace=str(git_workspace),
            session_key="goal:self",
            verify_cmd=None,  # No external verify
            max_loops=3,
        )
        store.save(goal)

        async def fake_process_direct(content, **kwargs):
            on_progress = kwargs.get("on_progress")

            if "[GOAL MODE]" in content or "[PLAN]" in content:
                return "plan"

            if "[ACT]" in content or content == "Continue executing.":
                if on_progress:
                    await on_progress(
                        "",
                        event_type="result",
                        event_data={"terminal_reason": "completed"},
                    )
                return "done"

            if "[VERIFY]" in content:
                return "The refactoring is complete. [GOAL_ACHIEVED]"

            return ""

        fake_svc = _make_fake_service()
        fake_svc.process_direct = AsyncMock(side_effect=fake_process_direct)

        with patch.object(GoalRunner, "_create_service", return_value=fake_svc):
            goal_runner = GoalRunner(goal, store)
            asyncio.run(goal_runner.run())

        final = store.load(goal.goal_id)
        assert final is not None
        assert final.status == GoalStatus.ACHIEVED

    @pytest.mark.integration
    def test_checkpoint_and_rollback(self, git_workspace: Path, goal_in_repo: GoalState):
        """Verify that rollback actually reverts files created during ACT."""
        store = GoalStore(git_workspace)
        goal_in_repo.max_loops = 2
        goal_in_repo.verify_cmd = "false"  # Always fails
        store.save(goal_in_repo)

        loop_num = {"n": 0}

        async def fake_process_direct(content, **kwargs):
            on_progress = kwargs.get("on_progress")

            if "[GOAL MODE]" in content or "[PLAN]" in content:
                loop_num["n"] += 1
                return "plan"

            if "[ACT]" in content or content == "Continue executing.":
                # Create a file that should be rolled back
                (git_workspace / f"loop{loop_num['n']}.txt").write_text(f"loop {loop_num['n']}")
                if on_progress:
                    await on_progress(
                        "",
                        event_type="result",
                        event_data={"terminal_reason": "completed"},
                    )
                return "done"

            if "[VERIFY FAILED]" in content:
                return "acknowledged"

            return ""

        fake_svc = _make_fake_service()
        fake_svc.process_direct = AsyncMock(side_effect=fake_process_direct)

        with patch.object(GoalRunner, "_create_service", return_value=fake_svc):
            goal_runner = GoalRunner(goal_in_repo, store)
            asyncio.run(goal_runner.run())

        # After rollback, files created during ACT should be gone
        assert not (git_workspace / "loop1.txt").exists(), "loop1.txt should be rolled back"
        assert not (git_workspace / "loop2.txt").exists(), "loop2.txt should be rolled back"

        # But .goal/ state should survive
        final = store.load(goal_in_repo.goal_id)
        assert final is not None
        assert final.status == GoalStatus.UNMET


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestGoalErrorHandling:
    """Tests for exception resilience — goal runner must never crash."""

    @pytest.fixture
    def goal_in_repo(self, git_workspace: Path) -> GoalState:
        store = GoalStore(git_workspace)
        goal = GoalState(
            goal_id="goal-20260731-err01",
            objective="handle errors gracefully",
            workspace=str(git_workspace),
            session_key="goal:err01",
            verify_cmd="test -f done.txt",
            max_loops=2,
        )
        store.save(goal)
        return goal

    @pytest.mark.integration
    def test_agent_exception_does_not_crash(self, git_workspace: Path, goal_in_repo: GoalState):
        """process_direct raising RuntimeError → goal continues, not crash."""
        store = GoalStore(git_workspace)
        call_count = {"n": 0}

        async def exploding_process_direct(content, **kwargs):
            call_count["n"] += 1
            on_progress = kwargs.get("on_progress")

            # First 2 calls raise, then succeed
            if call_count["n"] <= 2:
                raise RuntimeError("SDK query timed out after 30s")

            # After recovery: PLAN → ACT (complete) → VERIFY (pass)
            if "[GOAL MODE]" in content or "[PLAN]" in content:
                return "plan"
            if "[ACT]" in content or content == "Continue executing.":
                (git_workspace / "done.txt").write_text("done")
                if on_progress:
                    await on_progress(
                        "",
                        event_type="result",
                        event_data={"terminal_reason": "completed"},
                    )
                return "done"
            if "[VERIFY PASSED]" in content:
                return "ok"
            return ""

        fake_svc = _make_fake_service()
        fake_svc.process_direct = AsyncMock(side_effect=exploding_process_direct)

        with patch.object(GoalRunner, "_create_service", return_value=fake_svc):
            goal_runner = GoalRunner(goal_in_repo, store)
            asyncio.run(goal_runner.run())

        final = store.load(goal_in_repo.goal_id)
        assert final is not None
        # Goal should complete (errors were transient, recovered)
        assert final.status == GoalStatus.ACHIEVED
        assert call_count["n"] > 2  # Proves retries happened

    @pytest.mark.integration
    def test_transient_error_in_act_continues(self, git_workspace: Path, goal_in_repo: GoalState):
        """Error in ACT → terminal_reason stays None → ACT continues (not break)."""
        store = GoalStore(git_workspace)
        act_calls = {"n": 0}

        async def flaky_process_direct(content, **kwargs):
            on_progress = kwargs.get("on_progress")

            if "[GOAL MODE]" in content or "[PLAN]" in content:
                return "plan"

            if "[ACT]" in content or content == "Continue executing.":
                act_calls["n"] += 1
                # Fail on call 1, succeed on call 2
                if act_calls["n"] == 1:
                    raise RuntimeError("transient network error")
                (git_workspace / "done.txt").write_text("done")
                if on_progress:
                    await on_progress(
                        "",
                        event_type="result",
                        event_data={"terminal_reason": "completed"},
                    )
                return "done"

            if "[VERIFY PASSED]" in content:
                return "ok"
            return ""

        fake_svc = _make_fake_service()
        fake_svc.process_direct = AsyncMock(side_effect=flaky_process_direct)

        with patch.object(GoalRunner, "_create_service", return_value=fake_svc):
            goal_runner = GoalRunner(goal_in_repo, store)
            asyncio.run(goal_runner.run())

        final = store.load(goal_in_repo.goal_id)
        assert final is not None
        assert final.status == GoalStatus.ACHIEVED
        # ACT was called twice: first failed (exception), second succeeded
        assert act_calls["n"] == 2

    @pytest.mark.integration
    def test_crash_recovery_from_active(self, git_workspace: Path, goal_in_repo: GoalState):
        """Goal stuck in ACTIVE (crash) → goal run resumes with rollback."""
        store = GoalStore(git_workspace)
        # Simulate crash: goal left in ACTIVE with a checkpoint
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_workspace,
            capture_output=True,
            text=True,
        ).stdout.strip()

        goal_in_repo.status = GoalStatus.ACTIVE
        goal_in_repo.current_phase = GoalPhase.ACT
        goal_in_repo.checkpoint = head
        store.save(goal_in_repo)

        # Create a "dirty" file that should be rolled back on recovery
        (git_workspace / "partial_work.txt").write_text("from crashed ACT")

        async def fake_process_direct(content, **kwargs):
            on_progress = kwargs.get("on_progress")
            if "[ACT]" in content or content == "Continue executing.":
                (git_workspace / "done.txt").write_text("done")
                if on_progress:
                    await on_progress(
                        "",
                        event_type="result",
                        event_data={"terminal_reason": "completed"},
                    )
            if "[VERIFY PASSED]" in content:
                return "ok"
            return ""

        fake_svc = _make_fake_service()
        fake_svc.process_direct = AsyncMock(side_effect=fake_process_direct)

        with patch.object(GoalRunner, "_create_service", return_value=fake_svc):
            result = runner.invoke(
                goal_app, ["run", goal_in_repo.goal_id], catch_exceptions=False
            )

        assert result.exit_code == 0
        assert "interrupted abnormally" in result.output
        # partial_work.txt should have been rolled back during recovery
        assert not (git_workspace / "partial_work.txt").exists()

    @pytest.mark.integration
    def test_verify_cmd_subprocess_error(self, git_workspace: Path):
        """If _run_cmd raises (subprocess failure), verify treats as fail, not crash."""
        store = GoalStore(git_workspace)
        goal = GoalState(
            goal_id="goal-20260731-verr",
            objective="test verify error",
            workspace=str(git_workspace),
            session_key="goal:verr",
            verify_cmd="/nonexistent_binary_xyz",  # Will fail to execute
            max_loops=1,
        )
        store.save(goal)

        async def fake_process_direct(content, **kwargs):
            on_progress = kwargs.get("on_progress")
            if "[ACT]" in content or content == "Continue executing.":
                if on_progress:
                    await on_progress(
                        "",
                        event_type="result",
                        event_data={"terminal_reason": "completed"},
                    )
            return ""

        fake_svc = _make_fake_service()
        fake_svc.process_direct = AsyncMock(side_effect=fake_process_direct)

        with patch.object(GoalRunner, "_create_service", return_value=fake_svc):
            goal_runner = GoalRunner(goal, store)
            asyncio.run(goal_runner.run())

        # Should end as UNMET (verify failed), NOT crash
        final = store.load(goal.goal_id)
        assert final is not None
        assert final.status == GoalStatus.UNMET


class TestEndToEndCLIFlow:
    """Full CLI workflow exercising all commands sequentially."""

    @pytest.mark.integration
    def test_full_cli_lifecycle(self, git_workspace: Path):
        """Exercise: init → list → show → modify → run (mocked) → clear."""

        # 1. init
        result = runner.invoke(
            goal_app,
            ["init", "create API module", "--verify", "make test", "--max-loops", "3"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Goal created:" in result.output

        # Extract goal_id from output
        for line in result.output.splitlines():
            if "Goal created:" in line:
                goal_id = line.split("Goal created:")[-1].strip()
                # Remove any rich markup remnants
                goal_id = goal_id.replace("[/green]", "").replace("[green]", "").strip()
                break
        else:
            pytest.fail("Could not extract goal_id from init output")

        # 2. list
        result = runner.invoke(goal_app, ["list"], catch_exceptions=False)
        assert result.exit_code == 0
        assert goal_id in result.output

        # 3. show
        result = runner.invoke(
            goal_app, ["show", goal_id], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "create API module" in result.output
        assert "make test" in result.output

        # 4. modify
        result = runner.invoke(
            goal_app,
            ["modify", goal_id, "--max-loops", "5"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Goal updated" in result.output

        # Confirm modification
        result = runner.invoke(
            goal_app, ["show", goal_id], catch_exceptions=False
        )
        assert "0/5" in result.output

        # 5. run (with mocked agent)
        async def fake_process_direct(content, **kwargs):
            on_progress = kwargs.get("on_progress")
            if "[ACT]" in content or content == "Continue executing.":
                (git_workspace / "api_module.py").write_text("# API module\n")
                if on_progress:
                    await on_progress(
                        "",
                        event_type="result",
                        event_data={"terminal_reason": "completed"},
                    )
            return ""

        # Override verify_cmd to check our file
        store = GoalStore(git_workspace)
        goal = store.load(goal_id)
        assert goal is not None
        goal.verify_cmd = f"test -f {git_workspace}/api_module.py"
        store.save(goal)

        fake_svc = _make_fake_service()
        fake_svc.process_direct = AsyncMock(side_effect=fake_process_direct)

        with patch.object(GoalRunner, "_create_service", return_value=fake_svc):
            result = runner.invoke(
                goal_app, ["run", goal_id], catch_exceptions=False
            )
        assert result.exit_code == 0
        assert "Goal achieved" in result.output or "achieved" in result.output.lower()

        # 6. show after achieved
        result = runner.invoke(
            goal_app, ["show", goal_id], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "achieved" in result.output

        # 7. clear
        result = runner.invoke(
            goal_app, ["clear", goal_id, "--force"], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "Goal cleared" in result.output

        # Verify gone
        result = runner.invoke(
            goal_app, ["show", goal_id], catch_exceptions=False
        )
        assert result.exit_code == 1

    @pytest.mark.integration
    def test_pause_hint(self, git_workspace: Path):
        """pause command shows hint about Ctrl-C."""
        result = runner.invoke(goal_app, ["pause"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Ctrl-C" in result.output
