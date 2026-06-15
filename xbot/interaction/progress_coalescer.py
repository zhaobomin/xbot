"""Progress message coalescing for partial SDK streams.

This utility reduces noisy per-token progress updates by merging nearby chunks
into fewer user-visible updates.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Hashable


@dataclass
class CoalescedEvent:
    """A coalesced progress event ready to emit."""

    key: Hashable
    text: str


@dataclass
class _BufferState:
    kind: str
    body: str
    started_at: float
    updated_at: float


class ProgressCoalescer:
    """Coalesce partial progress texts by key.

    Expected usage:
    - call ``push(...)`` for each progress chunk
    - periodically call ``flush_due()`` on timer ticks
    - call ``flush_key()`` before final response for a turn
    """

    BUFFERABLE_EVENT_TYPES = {"thinking", "content_delta", "progress"}
    _THINKING_PREFIX = "Thinking:"

    def __init__(
        self,
        *,
        debounce_ms: int = 250,
        max_wait_ms: int = 1200,
        max_chars: int = 220,
    ) -> None:
        self._debounce_s = max(0.0, debounce_ms / 1000.0)
        self._max_wait_s = max(0.1, max_wait_ms / 1000.0)
        self._max_chars = max(32, max_chars)
        self._buffers: dict[Hashable, _BufferState] = {}

    def push(
        self,
        *,
        key: Hashable,
        text: str,
        event_type: str,
        tool_hint: bool,
        now: float | None = None,
    ) -> list[CoalescedEvent]:
        """Push one progress chunk and return any ready-to-emit events."""
        now_ts = now if now is not None else time.monotonic()
        if not text:
            return []

        if tool_hint or event_type not in self.BUFFERABLE_EVENT_TYPES:
            flushed = self.flush_key(key)
            return flushed + [CoalescedEvent(key=key, text=text)]

        kind, body = self._normalize_piece(text)
        if not body:
            return []

        state = self._buffers.get(key)
        if state is None:
            self._buffers[key] = _BufferState(
                kind=kind,
                body=body,
                started_at=now_ts,
                updated_at=now_ts,
            )
            return []

        if state.kind != kind:
            flushed = self.flush_key(key)
            self._buffers[key] = _BufferState(
                kind=kind,
                body=body,
                started_at=now_ts,
                updated_at=now_ts,
            )
            return flushed

        state.body = self._append_body(state.kind, state.body, body)
        state.updated_at = now_ts

        elapsed = now_ts - state.started_at
        if elapsed >= self._max_wait_s or len(self._render(state)) >= self._max_chars:
            return self.flush_key(key)
        return []

    def flush_due(self, *, now: float | None = None) -> list[CoalescedEvent]:
        """Flush buffers that have exceeded debounce since last update."""
        now_ts = now if now is not None else time.monotonic()
        ready: list[Hashable] = []
        for key, state in self._buffers.items():
            if (
                now_ts - state.updated_at >= self._debounce_s
                or now_ts - state.started_at >= self._max_wait_s
            ):
                ready.append(key)

        out: list[CoalescedEvent] = []
        for key in ready:
            out.extend(self.flush_key(key))
        return out

    def flush_key(self, key: Hashable) -> list[CoalescedEvent]:
        """Flush one key buffer (if present)."""
        state = self._buffers.pop(key, None)
        if state is None:
            return []
        rendered = self._render(state)
        return [CoalescedEvent(key=key, text=rendered)] if rendered else []

    def flush_all(self) -> list[CoalescedEvent]:
        """Flush all buffered events."""
        keys = list(self._buffers.keys())
        out: list[CoalescedEvent] = []
        for key in keys:
            out.extend(self.flush_key(key))
        return out

    def _normalize_piece(self, text: str) -> tuple[str, str]:
        raw = text.strip()
        if raw.startswith(self._THINKING_PREFIX):
            return "thinking", raw[len(self._THINKING_PREFIX) :].lstrip()
        return "plain", raw

    def _render(self, state: _BufferState) -> str:
        body = state.body.strip()
        if not body:
            return ""
        if state.kind == "thinking":
            return f"{self._THINKING_PREFIX} {body}"
        return body

    @staticmethod
    def _append_body(kind: str, left: str, right: str) -> str:
        if not left:
            return right
        if not right:
            return left

        if kind == "thinking":
            return left + right

        if left.endswith((" ", "\n")) or right.startswith((" ", "\n", ",", ".", "!", "?", "，", "。", "！", "？", "、")):
            return left + right
        return left + " " + right
