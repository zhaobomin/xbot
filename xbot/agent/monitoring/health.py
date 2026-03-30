"""Health check service for xbot gateway.

Provides HTTP endpoints for health monitoring and status reporting.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from aiohttp import web
from loguru import logger


@dataclass
class HealthStatus:
    """Health status for a component."""

    name: str
    healthy: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthCheckResult:
    """Overall health check result."""

    healthy: bool
    timestamp: str
    uptime_seconds: float
    components: list[HealthStatus]

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "timestamp": self.timestamp,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "components": [
                {
                    "name": c.name,
                    "healthy": c.healthy,
                    "message": c.message,
                    "details": c.details,
                }
                for c in self.components
            ],
        }


class HealthCheckService:
    """HTTP health check service.

    Provides endpoints:
    - GET /health - Full health check
    - GET /health/live - Liveness probe (always 200 if service running)
    - GET /health/ready - Readiness probe (checks critical components)
    """

    def __init__(
        self,
        port: int = 8080,
        host: str = "127.0.0.1",
        path_prefix: str = "",
    ):
        """Initialize health check service.

        Args:
            port: HTTP server port
            host: HTTP server host
            path_prefix: Optional path prefix (e.g., "/xbot" for /xbot/health)
        """
        self.port = port
        self.host = host
        self.path_prefix = path_prefix.rstrip("/")

        self._start_time = time.time()
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

        # Component health checkers
        self._checkers: dict[str, Callable[[], HealthStatus]] = {}

        # Status tracking
        self._status: dict[str, Any] = {
            "agent": "unknown",
            "channels": [],
            "memory": "unknown",
            "cron": "unknown",
        }

    def register_checker(self, name: str, checker: Callable[[], HealthStatus]) -> None:
        """Register a component health checker.

        Args:
            name: Component name
            checker: Async or sync function returning HealthStatus
        """
        self._checkers[name] = checker

    def update_status(self, component: str, status: dict[str, Any] | str) -> None:
        """Update component status.

        Args:
            component: Component name (agent, channels, memory, cron)
            status: Status info
        """
        self._status[component] = status

    async def _check_component(self, name: str) -> HealthStatus:
        """Run health check for a component."""
        checker = self._checkers.get(name)
        if not checker:
            return HealthStatus(name=name, healthy=True, message="No checker registered")

        try:
            result = checker()
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            return HealthStatus(name=name, healthy=False, message=str(e))

    async def _check_all(self) -> HealthCheckResult:
        """Run all health checks."""
        components = []
        all_healthy = True

        for name in self._checkers:
            status = await self._check_component(name)
            components.append(status)
            if not status.healthy:
                all_healthy = False

        return HealthCheckResult(
            healthy=all_healthy,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            uptime_seconds=time.time() - self._start_time,
            components=components,
        )

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Full health check endpoint."""
        result = await self._check_all()
        status_code = 200 if result.healthy else 503
        return web.json_response(result.to_dict(), status=status_code)

    async def _handle_live(self, request: web.Request) -> web.Response:
        """Liveness probe - always returns 200 if service is running."""
        return web.json_response({"status": "alive"})

    async def _handle_ready(self, request: web.Request) -> web.Response:
        """Readiness probe - checks critical components."""
        # Critical components: agent and at least one channel
        agent_status = self._status.get("agent", "unknown")
        channels = self._status.get("channels", [])

        ready = (
            agent_status in ("running", "unknown")  # unknown = not yet initialized
            and len(channels) > 0
        )

        result = {
            "ready": ready,
            "agent": agent_status,
            "channels": channels,
        }

        return web.json_response(result, status=200 if ready else 503)

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Detailed status endpoint."""
        return web.json_response({
            "uptime_seconds": round(time.time() - self._start_time, 2),
            "start_time": datetime.fromtimestamp(self._start_time).isoformat(),
            **self._status,
        })

    def _create_app(self) -> web.Application:
        """Create aiohttp application."""
        app = web.Application()

        # Route paths
        prefix = self.path_prefix
        app.router.add_get(f"{prefix}/health", self._handle_health)
        app.router.add_get(f"{prefix}/health/live", self._handle_live)
        app.router.add_get(f"{prefix}/health/ready", self._handle_ready)
        app.router.add_get(f"{prefix}/status", self._handle_status)

        return app

    async def start(self) -> None:
        """Start the HTTP health check server."""
        if self._runner:
            return  # Already running

        self._app = self._create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        logger.info(f"Health check service started on http://{self.host}:{self.port}")
        logger.info(f"  GET /health - Full health check")
        logger.info(f"  GET /health/live - Liveness probe")
        logger.info(f"  GET /health/ready - Readiness probe")
        logger.info(f"  GET /status - Detailed status")

    async def stop(self) -> None:
        """Stop the HTTP health check server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            self._app = None
            logger.info("Health check service stopped")


def create_health_service(
    port: int = 8080,
    host: str = "127.0.0.1",
) -> HealthCheckService:
    """Factory function to create a health check service.

    Args:
        port: HTTP server port (default 8080, use gateway_port - 710 for convention)
        host: HTTP server host

    Returns:
        HealthCheckService instance
    """
    return HealthCheckService(port=port, host=host)