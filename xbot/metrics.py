"""Prometheus metrics for xbot monitoring.

This module provides optional Prometheus metrics for monitoring xbot's
performance and health. Metrics are only collected if prometheus_client
is installed.

Usage:
    # In your code:
    from xbot.metrics import metrics

    # Record a request
    metrics.record_request(channel="telegram", status="success")

    # Record tool execution
    metrics.record_tool_execution(tool_name="shell", duration_ms=150)

    # Expose metrics endpoint:
    from prometheus_client import start_http_server
    start_http_server(9090)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from prometheus_client import Counter, Gauge, Histogram


# Check if prometheus_client is available
try:
    from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, REGISTRY
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Counter = None
    Gauge = None
    Histogram = None
    REGISTRY = None
    logger.debug("prometheus_client not installed. Metrics collection disabled.")


class NoOpMetric:
    """No-op metric for when Prometheus is not available."""

    def labels(self, *args, **kwargs):
        return self

    def inc(self, *args, **kwargs):
        pass

    def dec(self, *args, **kwargs):
        pass

    def set(self, *args, **kwargs):
        pass

    def observe(self, *args, **kwargs):
        pass


class Metrics:
    """Centralized metrics collection for xbot.

    Provides metrics for:
    - Request counts and latencies per channel
    - Tool execution counts and durations
    - Session state transitions
    - Client pool statistics
    - Error counts

    All metrics are prefixed with 'xbot_'.
    """

    def __init__(self, enabled: bool = True, registry: Optional["CollectorRegistry"] = None):
        self.enabled = enabled and PROMETHEUS_AVAILABLE
        self.registry = registry or REGISTRY

        if not self.enabled:
            self._create_noop_metrics()
            return

        self._create_metrics()

    def _create_noop_metrics(self):
        """Create no-op metrics when Prometheus is not available."""
        self.requests_total = NoOpMetric()
        self.request_duration = NoOpMetric()
        self.tool_executions_total = NoOpMetric()
        self.tool_duration = NoOpMetric()
        self.sessions_total = NoOpMetric()
        self.session_phase = NoOpMetric()
        self.clients_total = NoOpMetric()
        self.client_pool_size = NoOpMetric()
        self.errors_total = NoOpMetric()
        self.messages_processed = NoOpMetric()
        self.message_size_bytes = NoOpMetric()

    def _create_metrics(self):
        """Create Prometheus metrics."""
        # Request metrics
        self.requests_total = Counter(
            "xbot_requests_total",
            "Total number of requests processed",
            ["channel", "status"],
            registry=self.registry,
        )

        self.request_duration = Histogram(
            "xbot_request_duration_seconds",
            "Request processing duration in seconds",
            ["channel"],
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
            registry=self.registry,
        )

        # Tool metrics
        self.tool_executions_total = Counter(
            "xbot_tool_executions_total",
            "Total number of tool executions",
            ["tool_name", "status"],
            registry=self.registry,
        )

        self.tool_duration = Histogram(
            "xbot_tool_duration_seconds",
            "Tool execution duration in seconds",
            ["tool_name"],
            buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=self.registry,
        )

        # Session metrics
        self.sessions_total = Counter(
            "xbot_sessions_total",
            "Total number of sessions created",
            registry=self.registry,
        )

        self.session_phase = Gauge(
            "xbot_session_phase",
            "Current session phase (0=idle, 1=running, 2=waiting_permission, 3=waiting_interaction, 4=stopping, 5=resetting, 6=error)",
            ["session_key"],
            registry=self.registry,
        )

        # Client pool metrics
        self.clients_total = Counter(
            "xbot_clients_total",
            "Total number of SDK clients created",
            registry=self.registry,
        )

        self.client_pool_size = Gauge(
            "xbot_client_pool_size",
            "Current size of the client pool",
            registry=self.registry,
        )

        # Error metrics
        self.errors_total = Counter(
            "xbot_errors_total",
            "Total number of errors",
            ["component", "error_type"],
            registry=self.registry,
        )

        # Message metrics
        self.messages_processed = Counter(
            "xbot_messages_processed_total",
            "Total number of messages processed",
            ["channel", "direction"],
            registry=self.registry,
        )

        self.message_size_bytes = Histogram(
            "xbot_message_size_bytes",
            "Message size in bytes",
            ["channel", "direction"],
            buckets=[100, 500, 1000, 5000, 10000, 50000, 100000],
            registry=self.registry,
        )

    def record_request(
        self,
        channel: str,
        status: str,
        duration_seconds: Optional[float] = None,
    ) -> None:
        """Record a processed request.

        Args:
            channel: Channel name (telegram, feishu, etc.)
            status: Request status (success, error, timeout)
            duration_seconds: Request processing duration
        """
        if not self.enabled:
            return

        self.requests_total.labels(channel=channel, status=status).inc()

        if duration_seconds is not None:
            self.request_duration.labels(channel=channel).observe(duration_seconds)

    def record_tool_execution(
        self,
        tool_name: str,
        status: str,
        duration_seconds: Optional[float] = None,
    ) -> None:
        """Record a tool execution.

        Args:
            tool_name: Name of the tool
            status: Execution status (success, error)
            duration_seconds: Execution duration
        """
        if not self.enabled:
            return

        self.tool_executions_total.labels(tool_name=tool_name, status=status).inc()

        if duration_seconds is not None:
            self.tool_duration.labels(tool_name=tool_name).observe(duration_seconds)

    def record_session_created(self) -> None:
        """Record a new session created."""
        if not self.enabled:
            return

        self.sessions_total.inc()

    def set_session_phase(self, session_key: str, phase: int) -> None:
        """Set the current phase for a session.

        Args:
            session_key: Session identifier
            phase: Phase number (0-6)
        """
        if not self.enabled:
            return

        self.session_phase.labels(session_key=session_key).set(phase)

    def clear_session_phase(self, session_key: str) -> None:
        """Clear session phase metrics when session ends."""
        if not self.enabled:
            return

        try:
            self.session_phase.remove(session_key)
        except KeyError:
            pass  # Session wasn't tracked

    def record_client_created(self) -> None:
        """Record a new SDK client created."""
        if not self.enabled:
            return

        self.clients_total.inc()

    def set_client_pool_size(self, size: int) -> None:
        """Set the current client pool size."""
        if not self.enabled:
            return

        self.client_pool_size.set(size)

    def record_error(self, component: str, error_type: str) -> None:
        """Record an error.

        Args:
            component: Component where error occurred (backend, channel, tool)
            error_type: Error class name
        """
        if not self.enabled:
            return

        self.errors_total.labels(component=component, error_type=error_type).inc()

    def record_message(
        self,
        channel: str,
        direction: str,  # "inbound" or "outbound"
        size_bytes: Optional[int] = None,
    ) -> None:
        """Record a processed message.

        Args:
            channel: Channel name
            direction: Message direction
            size_bytes: Message size in bytes
        """
        if not self.enabled:
            return

        self.messages_processed.labels(channel=channel, direction=direction).inc()

        if size_bytes is not None:
            self.message_size_bytes.labels(channel=channel, direction=direction).observe(size_bytes)


# Global metrics instance
metrics = Metrics()


class MetricsTimer:
    """Context manager for timing operations."""

    def __init__(self, callback, *args, **kwargs):
        self.callback = callback
        self.args = args
        self.kwargs = kwargs
        self.start_time: Optional[float] = None

    def __enter__(self):
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time is not None:
            duration = time.perf_counter() - self.start_time
            self.callback(*self.args, duration_seconds=duration, **self.kwargs)
        return False

    async def __aenter__(self):
        self.start_time = time.perf_counter()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.start_time is not None:
            duration = time.perf_counter() - self.start_time
            self.callback(*self.args, duration_seconds=duration, **self.kwargs)
        return False


def time_request(channel: str) -> MetricsTimer:
    """Create a timer for request processing.

    Usage:
        async with time_request("telegram") as t:
            # process request
            pass
    """
    return MetricsTimer(metrics.record_request, channel=channel, status="success")


def time_tool(tool_name: str) -> MetricsTimer:
    """Create a timer for tool execution."""
    return MetricsTimer(metrics.record_tool_execution, tool_name=tool_name, status="success")