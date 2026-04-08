"""Tests for health check service."""

import asyncio
from unittest.mock import MagicMock

import pytest

from xbot.agent.monitoring.health import (
    HealthCheckResult,
    HealthCheckService,
    HealthStatus,
)


class TestHealthStatus:
    """Tests for HealthStatus dataclass."""

    def test_create(self) -> None:
        """Test creating a health status."""
        status = HealthStatus(name="test", healthy=True, message="OK")
        assert status.name == "test"
        assert status.healthy is True
        assert status.message == "OK"


class TestHealthCheckResult:
    """Tests for HealthCheckResult dataclass."""

    def test_to_dict(self) -> None:
        """Test converting result to dict."""
        result = HealthCheckResult(
            healthy=True,
            timestamp="2026-03-21T00:00:00Z",
            uptime_seconds=100.5,
            components=[
                HealthStatus(name="agent", healthy=True, message="running"),
            ],
        )

        data = result.to_dict()
        assert data["healthy"] is True
        assert data["timestamp"] == "2026-03-21T00:00:00Z"
        assert data["uptime_seconds"] == 100.5
        assert len(data["components"]) == 1
        assert data["components"][0]["name"] == "agent"


class TestHealthCheckService:
    """Tests for HealthCheckService."""

    @pytest.fixture
    def service(self) -> HealthCheckService:
        """Create a health check service for testing."""
        return HealthCheckService(port=18080, host="127.0.0.1")

    def test_init(self, service: HealthCheckService) -> None:
        """Test service initialization."""
        assert service.port == 18080
        assert service.host == "127.0.0.1"

    def test_register_checker(self, service: HealthCheckService) -> None:
        """Test registering a health checker."""
        checker = MagicMock(return_value=HealthStatus(name="test", healthy=True))
        service.register_checker("test", checker)
        assert "test" in service._checkers

    def test_update_status(self, service: HealthCheckService) -> None:
        """Test updating component status."""
        service.update_status("agent", "running")
        assert service._status["agent"] == "running"

        service.update_status("channels", ["telegram", "discord"])
        assert service._status["channels"] == ["telegram", "discord"]

    @pytest.mark.asyncio
    async def test_check_component(self, service: HealthCheckService) -> None:
        """Test checking a component."""
        checker = MagicMock(return_value=HealthStatus(name="test", healthy=True, message="OK"))
        service.register_checker("test", checker)

        result = await service._check_component("test")
        assert result.healthy is True
        assert result.message == "OK"

    @pytest.mark.asyncio
    async def test_check_component_no_checker(self, service: HealthCheckService) -> None:
        """Test checking a component with no checker registered."""
        result = await service._check_component("unknown")
        assert result.healthy is True
        assert "No checker registered" in result.message

    @pytest.mark.asyncio
    async def test_check_component_error(self, service: HealthCheckService) -> None:
        """Test checking a component that raises an error."""
        def bad_checker():
            raise RuntimeError("Something went wrong")

        service.register_checker("bad", bad_checker)
        result = await service._check_component("bad")
        assert result.healthy is False
        assert "Something went wrong" in result.message

    @pytest.mark.asyncio
    async def test_check_all(self, service: HealthCheckService) -> None:
        """Test checking all components."""
        service.register_checker("a", lambda: HealthStatus(name="a", healthy=True))
        service.register_checker("b", lambda: HealthStatus(name="b", healthy=True))

        result = await service._check_all()
        assert result.healthy is True
        assert len(result.components) == 2

    @pytest.mark.asyncio
    async def test_check_all_with_failure(self, service: HealthCheckService) -> None:
        """Test checking all components with one failure."""
        service.register_checker("a", lambda: HealthStatus(name="a", healthy=True))
        service.register_checker("b", lambda: HealthStatus(name="b", healthy=False, message="error"))

        result = await service._check_all()
        assert result.healthy is False
        assert len(result.components) == 2

    @pytest.mark.asyncio
    async def test_start_stop(self, service: HealthCheckService) -> None:
        """Test starting and stopping the service."""
        # Use a random high port to avoid conflicts with running services
        service.port = 58080
        service._app = None
        service._runner = None
        service._site = None

        await service.start()
        assert service._runner is not None

        # Give the server a moment to start
        await asyncio.sleep(0.1)

        await service.stop()
        assert service._runner is None
