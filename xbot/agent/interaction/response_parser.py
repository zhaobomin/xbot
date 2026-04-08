"""Helpers for parsing user responses to permission/interaction prompts."""

from __future__ import annotations

from xbot.agent.interaction.ask_user_validation import (
    normalize_validation_mode as _normalize_validation_mode,
)

ALLOW_RESPONSE_KEYWORDS = frozenset({"允许", "allow", "yes", "y", "是", "ok", "同意", "确认"})
DENY_RESPONSE_KEYWORDS = frozenset({"拒绝", "deny", "no", "n", "否", "取消"})


def normalize_response_text(content: str) -> str:
    """Normalize raw response text for keyword matching."""
    return content.strip().lower()


def is_response_keyword(content: str) -> bool:
    """Return whether content is an allow/deny keyword."""
    normalized = normalize_response_text(content)
    return normalized in ALLOW_RESPONSE_KEYWORDS or normalized in DENY_RESPONSE_KEYWORDS


def parse_permission_response(content: str) -> tuple[str | None, str]:
    """Parse permission response decision and reason.

    Returns:
        (decision, reason)
        - decision: "allow" / "deny" / None
        - reason: "User denied" only for deny, otherwise empty
    """
    normalized = normalize_response_text(content)
    if normalized in ALLOW_RESPONSE_KEYWORDS:
        return "allow", ""
    if normalized in DENY_RESPONSE_KEYWORDS:
        return "deny", "User denied"
    return None, ""


def derive_interaction_action(kind: str, content: str) -> str:
    """Map interaction kind + user response to action."""
    normalized = normalize_response_text(content)

    if kind not in {"confirmation", "approval"}:
        return "reply"

    if normalized in ALLOW_RESPONSE_KEYWORDS:
        return "confirm" if kind == "confirmation" else "allow"
    if normalized in DENY_RESPONSE_KEYWORDS:
        return "cancel" if kind == "confirmation" else "deny"
    return "reply"


def normalize_validation_mode(mode: str | None) -> str:
    """Backward-compatible wrapper for AskUserQuestion mode canonicalization."""
    return _normalize_validation_mode(mode)
