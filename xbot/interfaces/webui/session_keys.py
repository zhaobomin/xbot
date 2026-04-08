"""Session key mapping between web-visible and xbot runtime keys."""

from __future__ import annotations


def to_internal_session_key(web_session_key: str) -> str:
    normalized = (web_session_key or "").strip()
    if not normalized:
        return "cli:web-admin-default"
    if normalized.startswith("cli:"):
        return normalized
    if normalized.startswith("web:"):
        suffix = normalized[4:].replace(":", "-")
        return f"cli:web-{suffix}"
    return f"cli:{normalized.replace(':', '-')}"
