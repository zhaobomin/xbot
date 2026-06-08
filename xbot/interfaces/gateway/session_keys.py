"""Session key mapping between client-visible and xbot runtime keys."""

from __future__ import annotations

from xbot.platform.bus.events import IM_CHANNELS


def to_internal_session_key(web_session_key: str) -> str:
    normalized = (web_session_key or "").strip()
    if not normalized:
        return "web:admin:default"

    namespace, sep, _rest = normalized.partition(":")
    if namespace in {"web", "app", "cli", "im", "cron"} or normalized == "heartbeat":
        return normalized
    if sep and namespace in IM_CHANNELS:
        return f"im:{normalized}"
    return normalized
