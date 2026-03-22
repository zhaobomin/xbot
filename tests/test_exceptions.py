"""Tests for xbot.exceptions module."""

import pytest

from xbot.exceptions import (
    XbotError,
    ConfigurationError,
    ProviderConfigError,
    ChannelConfigError,
    SessionError,
    SessionNotFoundError,
    SessionStateError,
    BackendError,
    BackendNotInitializedError,
    BackendConnectionError,
    ProviderNotSupportedError,
    ChannelError,
    ChannelNotRunningError,
    MessageDeliveryError,
    PermissionDeniedError,
    ToolError,
    ToolNotFoundError,
    ToolExecutionError,
    PermissionRequestError,
    MemoryError,
    MemoryConsolidationError,
    MemoryStoreError,
)


class TestXbotError:
    """Tests for the base XbotError class."""

    def test_basic_error(self):
        """Test creating a basic error."""
        error = XbotError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.message == "Something went wrong"
        assert error.code == "XbotError"
        assert error.details == {}
        assert error.cause is None

    def test_error_with_code(self):
        """Test error with custom code."""
        error = XbotError("Error", code="CUSTOM_ERROR")
        assert error.code == "CUSTOM_ERROR"

    def test_error_with_details(self):
        """Test error with details."""
        error = XbotError("Error", details={"key": "value"})
        assert error.details == {"key": "value"}
        assert "key=value" in str(error)

    def test_error_with_cause(self):
        """Test error with cause."""
        original = ValueError("Original error")
        error = XbotError("Wrapped error", cause=original)
        assert error.cause == original
        assert "Original error" in str(error)

    def test_to_dict(self):
        """Test serialization to dictionary."""
        error = XbotError(
            "Test error",
            code="TEST",
            details={"foo": "bar"},
        )
        result = error.to_dict()
        assert result["error"] == "TEST"
        assert result["message"] == "Test error"
        assert result["details"] == {"foo": "bar"}


class TestExceptionHierarchy:
    """Tests for exception hierarchy."""

    def test_configuration_errors(self):
        """Test configuration error hierarchy."""
        assert issubclass(ProviderConfigError, ConfigurationError)
        assert issubclass(ChannelConfigError, ConfigurationError)
        assert issubclass(ConfigurationError, XbotError)

    def test_session_errors(self):
        """Test session error hierarchy."""
        assert issubclass(SessionNotFoundError, SessionError)
        assert issubclass(SessionStateError, SessionError)
        assert issubclass(SessionError, XbotError)

    def test_backend_errors(self):
        """Test backend error hierarchy."""
        assert issubclass(BackendNotInitializedError, BackendError)
        assert issubclass(BackendConnectionError, BackendError)
        assert issubclass(ProviderNotSupportedError, BackendError)
        assert issubclass(BackendError, XbotError)

    def test_channel_errors(self):
        """Test channel error hierarchy."""
        assert issubclass(ChannelNotRunningError, ChannelError)
        assert issubclass(MessageDeliveryError, ChannelError)
        assert issubclass(PermissionDeniedError, ChannelError)
        assert issubclass(ChannelError, XbotError)

    def test_tool_errors(self):
        """Test tool error hierarchy."""
        assert issubclass(ToolNotFoundError, ToolError)
        assert issubclass(ToolExecutionError, ToolError)
        assert issubclass(PermissionRequestError, ToolError)
        assert issubclass(ToolError, XbotError)

    def test_memory_errors(self):
        """Test memory error hierarchy."""
        assert issubclass(MemoryConsolidationError, MemoryError)
        assert issubclass(MemoryStoreError, MemoryError)
        assert issubclass(MemoryError, XbotError)


class TestSpecificExceptions:
    """Tests for specific exception types."""

    def test_provider_config_error(self):
        """Test ProviderConfigError."""
        error = ProviderConfigError(
            "Invalid provider",
            details={"provider": "unknown"},
        )
        assert "Invalid provider" in str(error)
        assert error.details["provider"] == "unknown"

    def test_provider_not_supported_error(self):
        """Test ProviderNotSupportedError."""
        error = ProviderNotSupportedError(
            "Provider 'test' not supported",
            details={
                "provider": "test",
                "supported_providers": ["anthropic", "openai"],
            },
        )
        assert error.code == "ProviderNotSupportedError"

    def test_tool_not_found_error(self):
        """Test ToolNotFoundError."""
        error = ToolNotFoundError(
            "Tool 'missing' not found",
            details={
                "requested_tool": "missing",
                "available_tools": ["shell", "web"],
            },
        )
        assert error.details["requested_tool"] == "missing"

    def test_tool_execution_error_with_cause(self):
        """Test ToolExecutionError with cause."""
        original = RuntimeError("Script failed")
        error = ToolExecutionError(
            "Tool execution failed",
            details={"tool": "shell"},
            cause=original,
        )
        assert error.cause == original