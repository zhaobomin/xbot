"""Tests for xbot.metrics module."""

import pytest


class TestMetricsNoPrometheus:
    """Tests for metrics when Prometheus is not available."""

    def test_noop_metrics(self):
        """Test that no-op metrics don't raise errors."""
        from xbot.metrics import Metrics, NoOpMetric, PROMETHEUS_AVAILABLE

        if PROMETHEUS_AVAILABLE:
            pytest.skip("Prometheus is available, skipping no-op test")

        metrics = Metrics(enabled=True)

        # All operations should work without error
        metrics.record_request("telegram", "success", 1.0)
        metrics.record_tool_execution("shell", "success", 0.5)
        metrics.record_session_created()
        metrics.set_session_phase("test:123", 1)
        metrics.clear_session_phase("test:123")
        metrics.record_client_created()
        metrics.set_client_pool_size(10)
        metrics.record_error("backend", "RuntimeError")
        metrics.record_message("telegram", "inbound", 100)

    def test_noop_metric_methods(self):
        """Test NoOpMetric methods."""
        from xbot.metrics import NoOpMetric

        metric = NoOpMetric()

        # All methods should work without error
        metric.labels("foo")
        metric.inc()
        metric.dec()
        metric.set(5)
        metric.observe(1.5)


class TestMetricsTimer:
    """Tests for MetricsTimer."""

    def test_sync_timer(self):
        """Test sync context manager."""
        from xbot.metrics import MetricsTimer, metrics

        calls = []

        def callback(duration_seconds, **kwargs):
            calls.append(duration_seconds)

        timer = MetricsTimer(callback, foo="bar")

        with timer:
            pass

        assert len(calls) == 1
        assert calls[0] >= 0

    @pytest.mark.asyncio
    async def test_async_timer(self):
        """Test async context manager."""
        import asyncio
        from xbot.metrics import MetricsTimer

        calls = []

        def callback(duration_seconds, **kwargs):
            calls.append(duration_seconds)

        timer = MetricsTimer(callback)

        async with timer:
            await asyncio.sleep(0.01)

        assert len(calls) == 1
        assert calls[0] >= 0.01


class TestMetricsHelpers:
    """Tests for metrics helper functions."""

    def test_time_request(self):
        """Test time_request helper."""
        from xbot.metrics import time_request

        timer = time_request("telegram")
        assert timer is not None
        assert timer.callback.__name__ == "record_request"

    def test_time_tool(self):
        """Test time_tool helper."""
        from xbot.metrics import time_tool

        timer = time_tool("shell")
        assert timer is not None
        assert timer.callback.__name__ == "record_tool_execution"