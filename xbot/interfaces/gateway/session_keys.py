"""Session key mapping between client-visible and xbot runtime keys."""

from __future__ import annotations

from secrets import token_hex

from xbot.platform.bus.events import IM_CHANNELS


def to_internal_session_key(web_session_key: str) -> str:
    normalized = (web_session_key or "").strip()
    if not normalized:
        return f"web:admin:{token_hex(6)}"

    namespace, sep, _rest = normalized.partition(":")
    if namespace in {"web", "app", "cli", "im", "cron"} or normalized == "heartbeat":
        return normalized
    if sep and namespace in IM_CHANNELS:
        return f"im:{normalized}"
    return normalized


def runtime_route_from_session_key(session_key: str, fallback_user_id: str) -> tuple[str, str]:
    normalized = (session_key or "").strip()
    namespace, sep, rest = normalized.partition(":")
    if sep and namespace in {"web", "app", "cli"}:
        return namespace, rest or fallback_user_id
    if sep and namespace == "im":
        provider, provider_sep, chat_id = rest.partition(":")
        if provider_sep and provider:
            return provider, chat_id
    return "web", fallback_user_id
