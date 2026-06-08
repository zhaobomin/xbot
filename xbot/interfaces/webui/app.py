"""Compatibility wrapper for the integrated gateway WebUI app."""

from xbot.interfaces.gateway.app import (  # noqa: F401
    _clear_login_rate_limit,
    _safe_websocket_send_json,
    create_app,
    validate_safe_name,
)
