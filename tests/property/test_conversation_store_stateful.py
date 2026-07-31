"""Stateful property-based tests for ConversationStore.

Uses Hypothesis RuleBasedStateMachine to generate random sequences of
add_message / delete_message / save / compact operations and verify
invariants hold after every step.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import settings, HealthCheck
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from xbot.runtime.session.conversation_store import ConversationStore


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_ROLES = st.sampled_from(["user", "assistant", "tool"])
_CONTENT = st.text(min_size=0, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",)))


# ---------------------------------------------------------------------------
# Stateful machine
# ---------------------------------------------------------------------------


class ConversationStoreStateMachine(RuleBasedStateMachine):
    """Model-based test: mirror operations on a simple Python list and verify
    that ConversationStore behaves identically."""

    def __init__(self):
        super().__init__()
        self._tmp_dir: Path | None = None
        self._store: ConversationStore | None = None
        # Model: simple list of (role, content) tuples
        self._model_messages: list[tuple[str, str]] = []
        self._session_key = "test-session"
        self._last_consolidated: int = 0

    @initialize()
    def setup(self):
        self._tmp_dir = Path(tempfile.mkdtemp())
        self._store = ConversationStore(workspace=self._tmp_dir)
        self._model_messages = []
        self._last_consolidated = 0

    @rule(role=_ROLES, content=_CONTENT)
    def add_message(self, role: str, content: str):
        """Add a message and save."""
        session = self._store.get_or_create(self._session_key)
        session.add_message(role=role, content=content)
        self._store.save(session)
        self._model_messages.append((role, content))

    @rule(data=st.data())
    def delete_message(self, data):
        """Delete a message at a random index."""
        session = self._store.get_or_create(self._session_key)
        n = len(session.messages)
        if n == 0:
            # Try delete on empty — should return False
            result = self._store.delete_message(session, 0)
            assert result is False
            return
        # Pick random index (sometimes out of bounds)
        idx = data.draw(st.integers(min_value=-1, max_value=n + 1))
        result = self._store.delete_message(session, idx)
        if 0 <= idx < n:
            assert result is True
            # Update model
            del self._model_messages[idx]
            # Adjust last_consolidated
            if idx < self._last_consolidated:
                self._last_consolidated = max(0, self._last_consolidated - 1)
        else:
            assert result is False

    @rule()
    def compact(self):
        """Force a full rewrite."""
        session = self._store.get_or_create(self._session_key)
        self._store.compact(session)

    @rule()
    def reload_from_disk(self):
        """Invalidate cache and reload from disk — simulates restart."""
        self._store.invalidate(self._session_key)

    @invariant()
    def messages_match_model(self):
        """The store's messages should always match our model."""
        if self._store is None:
            return
        session = self._store.get_or_create(self._session_key)
        actual = [(m["role"], m["content"]) for m in session.messages]
        assert actual == self._model_messages, (
            f"Mismatch: store has {len(actual)} msgs, model has {len(self._model_messages)}"
        )

    @invariant()
    def last_consolidated_in_bounds(self):
        """last_consolidated must always be <= len(messages)."""
        if self._store is None:
            return
        session = self._store.get_or_create(self._session_key)
        assert 0 <= session.last_consolidated <= len(session.messages)

    @invariant()
    def file_is_valid_jsonl(self):
        """The on-disk file must always be valid JSONL (no partial lines)."""
        if self._store is None or self._tmp_dir is None:
            return
        sessions_dir = self._tmp_dir / "sessions"
        if not sessions_dir.exists():
            return
        for fpath in sessions_dir.glob("*.jsonl"):
            with open(fpath, "r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        json.loads(line)
                    except json.JSONDecodeError:
                        raise AssertionError(
                            f"Invalid JSONL at {fpath.name}:{lineno}: {line!r}"
                        )

    def teardown(self):
        import shutil
        if self._tmp_dir and self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir, ignore_errors=True)


# Hypothesis settings: allow enough examples for meaningful coverage
TestConversationStore = ConversationStoreStateMachine.TestCase
TestConversationStore.settings = settings(
    max_examples=200,
    stateful_step_count=30,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
