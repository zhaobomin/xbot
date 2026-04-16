"""Tests for CronTool session cleanup functionality."""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from xbot.capabilities.tool_adapter import ToolAdapter
from xbot.runtime.session.conversation_store import ConversationStore
from xbot.runtime.system.cron.service import CronService
from xbot.runtime.system.cron.types import CronSchedule
from xbot.tools.cron import CronTool


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def cron_store_path(temp_workspace):
    """Create a path for cron store."""
    cron_dir = temp_workspace / "cron"
    cron_dir.mkdir(parents=True)
    return cron_dir / "jobs.json"


@pytest.fixture
def sessions_dir(temp_workspace):
    """Create sessions directory."""
    sessions = temp_workspace / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    return sessions


@pytest.fixture
def cron_service(cron_store_path):
    """Create a CronService instance."""
    service = CronService(store_path=cron_store_path)
    yield service
    service.stop()


@pytest.fixture
def conversation_store(temp_workspace):
    """Create a ConversationStore instance."""
    return ConversationStore(workspace=temp_workspace)


@pytest.fixture
def cron_tool(cron_service, conversation_store):
    """Create a CronTool instance with conversation_store."""
    tool = CronTool(
        cron_service=cron_service,
        conversation_store=conversation_store,
    )
    tool.set_context("test_channel", "test_chat_id")
    return tool


def create_test_session_file(sessions_dir: Path, session_key: str) -> Path:
    """Helper to create a test session file."""
    safe_key = session_key.replace(":", "_")
    session_path = sessions_dir / f"{safe_key}.jsonl"

    metadata = {
        "_type": "metadata",
        "key": session_key,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "metadata": {},
        "last_consolidated": 0,
    }

    with open(session_path, "w") as f:
        f.write(json.dumps(metadata) + "\n")

    return session_path


class TestCronToolInit:
    """Tests for CronTool initialization."""

    def test_init_with_cron_service_only(self, cron_service):
        """Test CronTool works without conversation_store (backward compatible)."""
        tool = CronTool(cron_service=cron_service)
        assert tool._cron == cron_service
        assert tool._conversation_store is None

    def test_init_with_conversation_store(self, cron_service, conversation_store):
        """Test CronTool with conversation_store."""
        tool = CronTool(
            cron_service=cron_service,
            conversation_store=conversation_store,
        )
        assert tool._cron == cron_service
        assert tool._conversation_store == conversation_store


class TestCronToolRemoveJob:
    """Tests for _remove_job session cleanup."""

    @pytest.mark.asyncio
    async def test_remove_job_without_conversation_store(self, cron_service):
        """Test that remove_job works when conversation_store is None."""
        tool = CronTool(cron_service=cron_service)
        await cron_service.start()

        # Add a job
        schedule = CronSchedule(kind="every", every_ms=60000)
        job = cron_service.add_job(
            name="test_job",
            schedule=schedule,
            message="Test message",
            deliver=True,
            channel="test",
            to="test_chat",
        )

        # Remove should still work
        result = tool._remove_job(job.id)
        assert result == f"Removed job {job.id}"

    @pytest.mark.asyncio
    async def test_remove_job_cleans_session_file(
        self, cron_service, conversation_store, sessions_dir
    ):
        """Test that remove_job cleans up session file."""
        await cron_service.start()

        tool = CronTool(
            cron_service=cron_service,
            conversation_store=conversation_store,
        )

        # Add a job
        schedule = CronSchedule(kind="every", every_ms=60000)
        job = cron_service.add_job(
            name="test_job",
            schedule=schedule,
            message="Test message",
            deliver=True,
            channel="test",
            to="test_chat",
        )

        # Create session file
        session_key = f"cron:{job.id}"
        session_path = create_test_session_file(sessions_dir, session_key)
        assert session_path.exists(), "Session file should exist before removal"

        # Remove the job
        result = tool._remove_job(job.id)
        assert result == f"Removed job {job.id}"

        # Session file should be cleaned up
        assert not session_path.exists(), "Session file should be cleaned up after removal"

    @pytest.mark.asyncio
    async def test_remove_job_handles_missing_session_file(
        self, cron_service, conversation_store
    ):
        """Test that remove_job handles missing session file gracefully."""
        await cron_service.start()

        tool = CronTool(
            cron_service=cron_service,
            conversation_store=conversation_store,
        )

        # Add a job (no session file created)
        schedule = CronSchedule(kind="every", every_ms=60000)
        job = cron_service.add_job(
            name="test_job",
            schedule=schedule,
            message="Test message",
            deliver=True,
            channel="test",
            to="test_chat",
        )

        # Remove should work even without session file
        result = tool._remove_job(job.id)
        assert result == f"Removed job {job.id}"

    @pytest.mark.asyncio
    async def test_remove_nonexistent_job(self, cron_tool, cron_service):
        """Test removing a job that doesn't exist."""
        await cron_service.start()
        result = cron_tool._remove_job("nonexistent_job_id")
        assert result == "Job nonexistent_job_id not found"

    def test_remove_job_without_job_id(self, cron_tool):
        """Test that remove requires job_id."""
        result = cron_tool._remove_job(None)
        assert result == "Error: job_id is required for remove"

    def test_remove_job_with_empty_job_id(self, cron_tool):
        """Test that remove rejects empty job_id."""
        result = cron_tool._remove_job("")
        assert result == "Error: job_id is required for remove"


class TestCronToolExecute:
    """Tests for CronTool.execute() method."""

    @pytest.mark.asyncio
    async def test_execute_remove_action(self, cron_tool, cron_service, sessions_dir):
        """Test execute with remove action."""
        await cron_service.start()

        # Add a job first
        schedule = CronSchedule(kind="every", every_ms=60000)
        job = cron_service.add_job(
            name="test_job",
            schedule=schedule,
            message="Test message",
            deliver=True,
            channel="test",
            to="test_chat",
        )

        # Create session file
        session_key = f"cron:{job.id}"
        session_path = create_test_session_file(sessions_dir, session_key)

        # Execute remove action
        result = await cron_tool.execute(action="remove", job_id=job.id)
        assert result == f"Removed job {job.id}"
        assert not session_path.exists()

    @pytest.mark.asyncio
    async def test_execute_list_action(self, cron_tool, cron_service):
        """Test execute with list action."""
        await cron_service.start()

        # Add a job
        schedule = CronSchedule(kind="every", every_ms=60000)
        job = cron_service.add_job(
            name="test_job",
            schedule=schedule,
            message="Test message",
            deliver=True,
            channel="test",
            to="test_chat",
        )

        # Execute list action
        result = await cron_tool.execute(action="list")
        assert "test_job" in result
        assert job.id in result

    @pytest.mark.asyncio
    async def test_execute_add_action(self, cron_tool, cron_service):
        """Test execute with add action."""
        await cron_service.start()

        # Execute add action
        result = await cron_tool.execute(
            action="add",
            message="Test reminder message",
            every_seconds=3600,
        )
        assert "Created job" in result

        # Verify job was created
        jobs = cron_service.list_jobs()
        assert len(jobs) == 1
        assert "Test reminder message"[:30] in jobs[0].name

    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, cron_tool):
        """Test execute with unknown action."""
        result = await cron_tool.execute(action="unknown")
        assert result == "Unknown action: unknown"


class TestCronToolIntegration:
    """Integration tests for CronTool with full workflow."""

    @pytest.mark.asyncio
    async def test_add_and_remove_workflow(
        self, cron_tool, cron_service, conversation_store, sessions_dir
    ):
        """Test full workflow: add job, create session, remove job."""
        await cron_service.start()

        # 1. Add a job
        result = await cron_tool.execute(
            action="add",
            message="Daily reminder",
            cron_expr="0 9 * * *",
            tz="Asia/Shanghai",
        )
        assert "Created job" in result

        # Get job ID from result
        jobs = cron_service.list_jobs()
        job_id = jobs[0].id

        # 2. Simulate session file from job execution
        session_key = f"cron:{job_id}"
        session_path = create_test_session_file(sessions_dir, session_key)
        assert session_path.exists()

        # 3. Remove the job
        result = await cron_tool.execute(action="remove", job_id=job_id)
        assert result == f"Removed job {job_id}"

        # 4. Verify session file is cleaned up
        assert not session_path.exists(), "Session file should be cleaned up"

        # 5. Verify job is removed from store
        jobs_after = cron_service.list_jobs()
        assert len(jobs_after) == 0

    @pytest.mark.asyncio
    async def test_multiple_jobs_cleanup(
        self, cron_tool, cron_service, conversation_store, sessions_dir
    ):
        """Test cleanup works correctly for multiple jobs."""
        await cron_service.start()

        # Add multiple jobs
        schedule = CronSchedule(kind="every", every_ms=60000)
        job1 = cron_service.add_job(
            name="job1",
            schedule=schedule,
            message="Message 1",
            deliver=True,
            channel="test",
            to="test",
        )
        job2 = cron_service.add_job(
            name="job2",
            schedule=schedule,
            message="Message 2",
            deliver=True,
            channel="test",
            to="test",
        )

        # Create session files for both
        session1_path = create_test_session_file(sessions_dir, f"cron:{job1.id}")
        session2_path = create_test_session_file(sessions_dir, f"cron:{job2.id}")

        # Remove first job
        cron_tool._remove_job(job1.id)
        assert not session1_path.exists()
        assert session2_path.exists(), "Other session file should remain"

        # Remove second job
        cron_tool._remove_job(job2.id)
        assert not session2_path.exists()


class TestToolAdapterIntegration:
    """Tests for ToolAdapter passing conversation_store to CronTool."""

    def test_tool_adapter_creates_cron_tool_with_conversation_store(
        self, temp_workspace, cron_store_path, conversation_store
    ):
        """Test that ToolAdapter passes conversation_store to CronTool."""
        # Create a minimal CronService
        cron_service = CronService(store_path=cron_store_path)

        # Create shared_resources with both cron_service and conversation_store
        shared_resources = {
            "cron_service": cron_service,
            "conversation_store": conversation_store,
            "bus": MagicMock(),  # Mock bus for message tool
        }

        # Create ToolAdapter
        adapter = ToolAdapter(
            workspace=str(temp_workspace),
            shared_resources=shared_resources,
        )

        # Trigger tool registration
        adapter._ensure_core_tools_registered()

        # Get the cron tool
        cron_tool = adapter.get_tool("cron")
        assert cron_tool is not None
        assert isinstance(cron_tool, CronTool)
        assert cron_tool._conversation_store == conversation_store

        cron_service.stop()

    def test_tool_adapter_creates_cron_tool_without_conversation_store(
        self, temp_workspace, cron_store_path
    ):
        """Test backward compatibility: ToolAdapter works without conversation_store."""
        cron_service = CronService(store_path=cron_store_path)

        # Create shared_resources WITHOUT conversation_store
        shared_resources = {
            "cron_service": cron_service,
            "bus": MagicMock(),
        }

        adapter = ToolAdapter(
            workspace=str(temp_workspace),
            shared_resources=shared_resources,
        )
        adapter._ensure_core_tools_registered()

        cron_tool = adapter.get_tool("cron")
        assert cron_tool is not None
        assert isinstance(cron_tool, CronTool)
        # conversation_store should be None (backward compatible)
        assert cron_tool._conversation_store is None

        cron_service.stop()

    @pytest.mark.asyncio
    async def test_full_integration_via_tool_adapter(
        self, temp_workspace, cron_store_path, conversation_store, sessions_dir
    ):
        """Full integration test: ToolAdapter -> CronTool -> remove_job -> session cleanup."""
        cron_service = CronService(store_path=cron_store_path)
        await cron_service.start()

        # Create ToolAdapter with all required resources
        shared_resources = {
            "cron_service": cron_service,
            "conversation_store": conversation_store,
            "bus": MagicMock(),
        }

        adapter = ToolAdapter(
            workspace=str(temp_workspace),
            shared_resources=shared_resources,
        )
        adapter._ensure_core_tools_registered()

        # Get cron tool and set context
        cron_tool = adapter.get_tool("cron")
        cron_tool.set_context("test_channel", "test_chat_id")

        # Add a job
        schedule = CronSchedule(kind="every", every_ms=60000)
        job = cron_service.add_job(
            name="integration_test_job",
            schedule=schedule,
            message="Integration test message",
            deliver=True,
            channel="test_channel",
            to="test_chat_id",
        )

        # Create session file
        session_key = f"cron:{job.id}"
        session_path = create_test_session_file(sessions_dir, session_key)
        assert session_path.exists()

        # Remove via tool adapter
        result = cron_tool._remove_job(job.id)
        assert result == f"Removed job {job.id}"

        # Verify cleanup
        assert not session_path.exists(), "Session file should be cleaned up via ToolAdapter"

        await cron_service.shutdown()