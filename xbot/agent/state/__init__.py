"""Session state management.

This module provides simplified session state management using SessionManager
as the single source of truth.
"""

from xbot.agent.state.machine import SessionPhase, SessionState
from xbot.agent.state.session_manager import SessionManager

__all__ = [
    "SessionManager",
    "SessionPhase",
    "SessionState",
]
