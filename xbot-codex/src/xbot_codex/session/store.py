from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


@dataclass
class Session:
    session_key: str
    channel: str
    chat_id: str
    codex_workdir: str
    codex_profile: str | None = None
    codex_model: str | None = None
    codex_mode: str | None = None
    codex_session_id: str | None = None
    runtime_session_id: str = field(default_factory=lambda: uuid4().hex)
    process_state: str = "idle"
    last_error: str | None = None
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SessionStore:
    def __init__(self, default_workdir_root: str):
        self.default_workdir_root = Path(default_workdir_root)
        self._sessions: dict[str, Session] = {}

    def get(self, session_key: str) -> Session | None:
        return self._sessions.get(session_key)

    def get_or_create(self, channel: str, chat_id: str) -> Session:
        session_key = f"{channel}:{chat_id}"
        existing = self._sessions.get(session_key)
        if existing is not None:
            return existing
        session = Session(
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            codex_workdir=str(self.default_workdir_root / channel / chat_id),
        )
        self._sessions[session_key] = session
        return session

    def reset(self, session_key: str) -> Session:
        session = self._sessions[session_key]
        session.runtime_session_id = uuid4().hex
        session.codex_session_id = None
        session.process_state = "idle"
        session.last_error = None
        session.last_activity_at = datetime.now(UTC)
        return session

    def touch(self, session_key: str) -> None:
        if session_key in self._sessions:
            self._sessions[session_key].last_activity_at = datetime.now(UTC)

    def running_sessions(self) -> int:
        return sum(1 for session in self._sessions.values() if session.process_state == "running")

    def mark_error(self, session_key: str, error: str) -> None:
        session = self._sessions[session_key]
        session.process_state = "error"
        session.last_error = error
        session.last_activity_at = datetime.now(UTC)

    def active_session_keys(self) -> list[str]:
        return [key for key, session in self._sessions.items() if session.process_state == "running"]
