"""Shared fixtures for integration tests.

These tests use real components with minimal mocking to verify
cross-module behavior matches production execution paths.
"""

from __future__ import annotations

import pytest

from xbot.platform.bus.queue import MessageBus
from xbot.runtime.state.runtime_registry import RuntimeSessionRegistry


@pytest.fixture
def bus() -> MessageBus:
    """Fresh MessageBus instance (real, not mocked)."""
    return MessageBus()


@pytest.fixture
def registry() -> RuntimeSessionRegistry:
    """Fresh RuntimeSessionRegistry with underlying SessionCoordinator."""
    return RuntimeSessionRegistry()


@pytest.fixture
def session_key() -> str:
    """Standard test session key."""
    return "web:testuser:session1"
