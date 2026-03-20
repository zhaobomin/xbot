"""Session-scoped runtime trace helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from xbot.session.manager import SessionManager


def append_session_trace(
    sessions: SessionManager | None,
    session_key: str,
    event: str,
    data: dict[str, Any],
    *,
    limit: int = 50,
) -> None:
    if sessions is None:
        return

    session = sessions.get_or_create(session_key)
    trace = list(session.metadata.get("runtime_trace", []))
    trace.append(
        {
            "event": event,
            "timestamp": datetime.now().isoformat(),
            **data,
        }
    )
    session.metadata["runtime_trace"] = trace[-limit:]
    sessions.save(session)
