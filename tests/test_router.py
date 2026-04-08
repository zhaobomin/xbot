"""Tests for AgentService as router replacement.

This test file verifies AgentService can replace AgentRouter.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from xbot.agent.service import AgentService
from xbot.agent.types import AgentConfig


class TestAgentServiceAsRouter:
    """Tests for AgentService as router replacement."""

    @pytest.fixture
    def config(self) -> AgentConfig:
        """Create a test config."""
        return AgentConfig(
            model="claude-sonnet-4-6",
            system_prompt="Test prompt",
        )

    @pytest.fixture
    def shared_resources(self, tmp_path) -> dict[str, Any]:
        """Create shared resources."""
        return {"workspace": str(tmp_path), "config": MagicMock()}

    @pytest.mark.asyncio
    async def test_initialize(self, config: AgentConfig, shared_resources: dict[str, Any]) -> None:
        """Test service initialization."""
        service = AgentService()
        await service.initialize(config, shared_resources)
        assert service._initialized is True

    @pytest.mark.asyncio
    async def test_shutdown(self, config: AgentConfig, shared_resources: dict[str, Any]) -> None:
        """Test service shutdown."""
        service = AgentService()
        await service.initialize(config, shared_resources)
        await service.shutdown()
        assert service._initialized is False
