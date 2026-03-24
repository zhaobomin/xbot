"""Delegation tracking for Claude SDK backend.

This module provides data structures for tracking agent delegation decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DelegationTrace:
    """Trace record for delegation decisions.

    Records when and why the agent made a delegation decision,
    including the mode (main, handoff, background) and candidates.

    Attributes:
        timestamp: ISO format timestamp of the decision
        session_key: Session identifier
        decision_mode: One of "main", "handoff", "background"
        reason: Explanation for the decision
        candidates: List of candidate agent names (for handoff)
    """

    timestamp: str
    session_key: str
    decision_mode: str
    reason: str
    candidates: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation of the trace.
        """
        return {
            "timestamp": self.timestamp,
            "session_key": self.session_key,
            "mode": self.decision_mode,
            "reason": self.reason,
            "candidates": self.candidates,
        }