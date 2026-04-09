"""ConversationSession-scoped runtime trace helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from xbot.runtime.session.conversation_store import ConversationStore


def append_session_trace(
    sessions: ConversationStore | None,
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
