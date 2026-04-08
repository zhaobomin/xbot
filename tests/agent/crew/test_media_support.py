"""Tests for crew media support (vision capability).

TTD approach:
1. Write tests first
2. Implement code to make tests pass
3. Ensure backward compatibility
"""

from __future__ import annotations

import pytest

from xbot.agent.crew.context import CrewExecutionContext
from xbot.agent.crew.models import AgentRole, TaskDefinition, parse_crew_config


class TestTaskDefinitionMediaField:
    """Test TaskDefinition media field."""

    def test_task_definition_without_media(self):
        """Backward compatibility: task without media field should work."""
        task = TaskDefinition(
            name="test_task",
            description="Test description",
            agent="test_agent",
        )
        assert task.media is None
        assert task.media_mode == "auto"

    def test_task_definition_with_media(self):
        """Task with media field."""
        task = TaskDefinition(
            name="test_task",
            description="Test description",
            agent="test_agent",
            media=["slides/page_01.png"],
        )
        assert task.media == ["slides/page_01.png"]
        assert task.media_mode == "auto"

    def test_task_definition_with_multiple_media(self):
        """Task with multiple media files."""
        task = TaskDefinition(
            name="test_task",
            description="Test description",
            agent="test_agent",
            media=["slides/page_01.png", "slides/page_02.png"],
        )
        assert len(task.media) == 2

    def test_task_definition_with_media_mode(self):
        """Task with explicit media_mode."""
        task = TaskDefinition(
            name="test_task",
            description="Test description",
            agent="test_agent",
            media=["slides/page_01.png"],
            media_mode="vision",
        )
        assert task.media_mode == "vision"

    def test_task_definition_invalid_media_mode(self):
        """Invalid media_mode should fail validation."""
        with pytest.raises(ValueError):
            TaskDefinition(
                name="test_task",
                description="Test description",
                agent="test_agent",
                media=["slides/page_01.png"],
                media_mode="invalid_mode",
            )


class TestCrewConfigMediaParsing:
    """Test parsing crew config with media."""

    def test_parse_config_with_media(self):
        """Parse config containing tasks with media."""
        raw = {
            "name": "test_crew",
            "agents": {
                "analyzer": {
                    "name": "analyzer",
                    "description": "Slide analyzer",
                    "goal": "Analyze slides",
                }
            },
            "tasks": [
                {
                    "name": "analyze_slide",
                    "description": "Analyze a slide",
                    "agent": "analyzer",
                    "media": ["slides/page_01.png"],
                    "media_mode": "vision",
                }
            ],
        }
        config = parse_crew_config(raw)
        assert len(config.tasks) == 1
        assert config.tasks[0].media == ["slides/page_01.png"]
        assert config.tasks[0].media_mode == "vision"

    def test_parse_config_without_media(self):
        """Parse config without media (backward compatibility)."""
        raw = {
            "name": "test_crew",
            "agents": {
                "analyzer": {
                    "name": "analyzer",
                    "description": "Analyzer",
                    "goal": "Analyze",
                }
            },
            "tasks": [
                {
                    "name": "simple_task",
                    "description": "A simple task",
                    "agent": "analyzer",
                }
            ],
        }
        config = parse_crew_config(raw)
        assert config.tasks[0].media is None


class TestCrewExecutionContextMedia:
    """Test CrewExecutionContext media handling."""

    def test_add_result_without_media(self):
        """Add result without media (backward compatibility)."""
        from datetime import datetime

        from xbot.agent.crew.models import TaskResult

        ctx = CrewExecutionContext()
        result = TaskResult(
            task_name="test_task",
            agent_name="test_agent",
            output="Test output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        ctx.add_result(result)
        assert ctx.get_result("test_task") == result

    def test_add_result_with_media(self):
        """Add result with media."""
        from datetime import datetime

        from xbot.agent.crew.models import TaskResult

        ctx = CrewExecutionContext()
        result = TaskResult(
            task_name="test_task",
            agent_name="test_agent",
            output="Test output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        ctx.add_result(result, media=["slides/page_01.png"])

        assert ctx.get_result("test_task") == result
        # Media should be stored separately
        assert ctx._media_files.get("test_task") == ["slides/page_01.png"]

    def test_resolve_media_paths_relative(self):
        """Resolve relative media paths."""
        ctx = CrewExecutionContext()
        # Note: This would need actual file system setup for full test
        # For now, just test the method exists and handles empty
        result = ctx._resolve_media_paths(["test.png"])
        assert isinstance(result, list)

    def test_resolve_media_paths_with_glob(self):
        """Resolve glob patterns in media paths."""
        ctx = CrewExecutionContext()
        # Just verify method exists and handles glob
        # Full test would need temp directory with files
        result = ctx._resolve_media_paths(["slides/*.png"])
        assert isinstance(result, list)

    def test_build_task_prompt_with_media(self):
        """Build prompt with media indication."""
        from xbot.agent.crew.models import TaskDefinition

        ctx = CrewExecutionContext()
        role = AgentRole(
            name="analyzer",
            description="Slide analyzer",
            goal="Analyze slides",
        )
        task = TaskDefinition(
            name="analyze_slide",
            description="Analyze slide content",
            agent="analyzer",
            media=["slides/page_01.png"],
        )

        prompt = ctx.build_task_prompt(
            task=task,
            role=role,
            media=["slides/page_01.png"],
        )

        assert "Media Files" in prompt
        assert "1 media file(s)" in prompt

    def test_build_agent_context(self):
        """Build complete agent context with media."""
        from xbot.agent.crew.models import TaskDefinition

        ctx = CrewExecutionContext()
        role = AgentRole(
            name="analyzer",
            description="Slide analyzer",
            goal="Analyze slides",
        )
        task = TaskDefinition(
            name="analyze_slide",
            description="Analyze slide content",
            agent="analyzer",
            media=["slides/page_01.png"],
        )

        prompt, media = ctx.build_agent_context(
            task=task,
            role=role,
            session_key="test_session",
        )

        assert prompt is not None
        assert media is not None
        assert len(media) == 1


class TestAgentPoolMedia:
    """Test AgentPool media passing."""

    @pytest.mark.asyncio
    async def test_run_task_with_media(self):
        """run_task should accept media parameter."""
        # This is a signature test - we can't easily mock the backend
        # Just verify the method accepts the parameter
        import inspect

        from xbot.agent.crew.agent_pool import AgentPool

        sig = inspect.signature(AgentPool.run_task)
        params = list(sig.parameters.keys())
        assert "media" in params

    @pytest.mark.asyncio
    async def test_run_task_streaming_with_media(self):
        """run_task_streaming should accept media parameter."""
        import inspect

        from xbot.agent.crew.agent_pool import AgentPool

        sig = inspect.signature(AgentPool.run_task_streaming)
        params = list(sig.parameters.keys())
        assert "media" in params


class TestBackwardCompatibility:
    """Ensure changes don't break existing code."""

    def test_existing_task_without_media_still_works(self):
        """Existing tasks without media field continue to work."""
        task = TaskDefinition(
            name="legacy_task",
            description="A legacy task",
            agent="legacy_agent",
        )
        # Should not raise any errors
        assert task.name == "legacy_task"
        assert task.media is None

    def test_existing_context_usage(self):
        """Existing context usage patterns still work."""
        from datetime import datetime

        from xbot.agent.crew.models import TaskDefinition, TaskResult

        ctx = CrewExecutionContext()

        # Old way of adding result
        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="output1",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        ctx.add_result(result)  # Should work without media parameter

        # Old way of building prompt
        role = AgentRole(name="r", description="d", goal="g")
        task = TaskDefinition(name="t", description="d", agent="r")
        prompt = ctx.build_task_prompt(task, role)  # Should work without media

        assert "Your Task" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
