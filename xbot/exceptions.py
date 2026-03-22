"""Custom exceptions for xbot.

This module defines a hierarchy of exceptions for consistent error handling
across the codebase.

Exception Hierarchy:
    XbotError (base)
    ├── ConfigurationError
    │   ├── ProviderConfigError
    │   └── ChannelConfigError
    ├── SessionError
    │   ├── SessionNotFoundError
    │   └── SessionStateError
    ├── BackendError
    │   ├── BackendNotInitializedError
    │   ├── BackendConnectionError
    │   └── ProviderNotSupportedError
    ├── ChannelError
    │   ├── ChannelNotRunningError
    │   ├── MessageDeliveryError
    │   └── PermissionDeniedError
    ├── ToolError
    │   ├── ToolNotFoundError
    │   ├── ToolExecutionError
    │   └── PermissionRequestError
    └── MemoryError
        ├── MemoryConsolidationError
        └── MemoryStoreError
"""

from typing import Any, Optional


class XbotError(Exception):
    """Base exception for all xbot errors.

    All custom exceptions should inherit from this class.
    Provides a consistent interface for error messages and context.
    """

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        cause: Optional[Exception] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__
        self.details = details or {}
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to a dictionary for serialization."""
        result = {
            "error": self.code,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        if self.cause:
            result["cause"] = str(self.cause)
        return result

    def __str__(self) -> str:
        parts = [self.message]
        if self.details:
            parts.append(f"details={self.details}")
        if self.cause:
            parts.append(f"caused by: {self.cause}")
        return " | ".join(parts)


# =============================================================================
# Configuration Errors
# =============================================================================

class ConfigurationError(XbotError):
    """Base exception for configuration-related errors."""
    pass


class ProviderConfigError(ConfigurationError):
    """Raised when a provider configuration is invalid or missing."""
    pass


class ChannelConfigError(ConfigurationError):
    """Raised when a channel configuration is invalid or missing."""
    pass


# =============================================================================
# Session Errors
# =============================================================================

class SessionError(XbotError):
    """Base exception for session-related errors."""
    pass


class SessionNotFoundError(SessionError):
    """Raised when a session cannot be found."""
    pass


class SessionStateError(SessionError):
    """Raised when a session state transition is invalid."""
    pass


# =============================================================================
# Backend Errors
# =============================================================================

class BackendError(XbotError):
    """Base exception for backend-related errors."""
    pass


class BackendNotInitializedError(BackendError):
    """Raised when backend operations are attempted before initialization."""
    pass


class BackendConnectionError(BackendError):
    """Raised when backend connection fails."""
    pass


class ProviderNotSupportedError(BackendError):
    """Raised when a provider is not supported by the backend."""
    pass


# =============================================================================
# Channel Errors
# =============================================================================

class ChannelError(XbotError):
    """Base exception for channel-related errors."""
    pass


class ChannelNotRunningError(ChannelError):
    """Raised when operations are attempted on a stopped channel."""
    pass


class MessageDeliveryError(ChannelError):
    """Raised when message delivery fails."""
    pass


class PermissionDeniedError(ChannelError):
    """Raised when a user is not allowed to access the channel."""
    pass


# =============================================================================
# Tool Errors
# =============================================================================

class ToolError(XbotError):
    """Base exception for tool-related errors."""
    pass


class ToolNotFoundError(ToolError):
    """Raised when a requested tool is not found."""
    pass


class ToolExecutionError(ToolError):
    """Raised when tool execution fails."""
    pass


class PermissionRequestError(ToolError):
    """Raised when permission request handling fails."""
    pass


# =============================================================================
# Memory Errors
# =============================================================================

class MemoryError(XbotError):
    """Base exception for memory-related errors."""
    pass


class MemoryConsolidationError(MemoryError):
    """Raised when memory consolidation fails."""
    pass


class MemoryStoreError(MemoryError):
    """Raised when memory storage operations fail."""
    pass