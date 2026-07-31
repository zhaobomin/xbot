"""Integration tests: ConversationStore race conditions and edge cases.

Scenarios covered:
1. Concurrent revoke (pop) of the same message from two "perspectives".
2. Adding a message while a metadata-dirty full save runs.
3. Loading a session whose JSONL file has a corrupt last line.
4. Attempting to delete a message at an out-of-bounds index (raw list pop).
5. Ensuring last_consolidated never exceeds len(messages) after repeated deletes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xbot.runtime.session.conversation_store import ConversationSession, ConversationStore

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path, max_cache_size: int = 100) -> ConversationStore:
    """Create a ConversationStore rooted in a temporary directory."""
    return ConversationStore(workspace=tmp_path, max_cache_size=max_cache_size)


# ---------------------------------------------------------------------------
# 1. test_concurrent_revoke_same_session
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestConcurrentRevokeSameSession:
    """Simulate two concurrent deletions of message[0] via raw list pop.

    Both "callers" hold references to the same session object. Each pops
    index 0, marks the session dirty, and saves. The result should have
    exactly (original - 2) messages with no IndexError.
    """

    def test_concurrent_revoke_same_session(self, tmp_path: Path):
        store = _make_store(tmp_path)
        session = store.get_or_create("race:revoke")

        # Add 5 messages and persist
        for i in range(5):
            session.add_message("user", f"msg-{i}")
        store.save(session)

        assert len(session.messages) == 5

        # --- Simulate two concurrent deletions from "two perspectives" ---
        # Perspective A: pop index 0
        removed_a = session.messages.pop(0)
        session._metadata_dirty = True
        store.save(session)

        # Perspective B: pop index 0 again (which is the *next* message now)
        removed_b = session.messages.pop(0)
        session._metadata_dirty = True
        store.save(session)

        # Assertions: no IndexError occurred, 3 messages remain
        assert len(session.messages) == 3
        assert removed_a["content"] == "msg-0"
        assert removed_b["content"] == "msg-1"

        # Verify on-disk consistency
        store.invalidate("race:revoke")
        reloaded = store.get("race:revoke")
        assert reloaded is not None
        assert len(reloaded.messages) == 3
        expected_contents = ["msg-2", "msg-3", "msg-4"]
        actual_contents = [m["content"] for m in reloaded.messages]
        assert actual_contents == expected_contents


# ---------------------------------------------------------------------------
# 2. test_add_message_while_delete_full_save_runs
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAddMessageWhileFullSaveRuns:
    """Verify that a message appended via _new_messages after a full save
    (triggered by _metadata_dirty) is captured by a subsequent save,
    with no duplicates.
    """

    def test_add_message_while_delete_full_save_runs(self, tmp_path: Path):
        store = _make_store(tmp_path)
        session = store.get_or_create("race:add-during-save")

        # Seed with 3 messages and persist
        for i in range(3):
            session.add_message("user", f"original-{i}")
        store.save(session)

        # Mark metadata dirty so next save does a full rewrite
        session._metadata_dirty = True

        # Perform the full save (simulates the "first" save path)
        store.save(session)

        # After full save, _new_messages should be cleared and _metadata_dirty False
        assert session._metadata_dirty is False
        assert session._new_messages == []

        # Now simulate adding a new message as if it arrived between two saves.
        # We directly append to _new_messages to mimic a concurrent add scenario.
        new_msg = {
            "role": "assistant",
            "content": "late-arrival",
            "timestamp": "2024-01-01T00:00:00",
        }
        session.messages.append(new_msg)
        session._new_messages.append(new_msg)

        # Second save — should append the new message
        store.save(session)

        # Reload from disk to verify final state
        store.invalidate("race:add-during-save")
        reloaded = store.get("race:add-during-save")
        assert reloaded is not None

        contents = [m["content"] for m in reloaded.messages]

        # All original messages must be present
        for i in range(3):
            assert f"original-{i}" in contents

        # The late arrival must be present exactly once (no duplication)
        assert "late-arrival" in contents
        assert contents.count("late-arrival") == 1

        # Total count: 3 originals + 1 new = 4
        assert len(reloaded.messages) == 4


# ---------------------------------------------------------------------------
# 3. test_load_tolerates_corrupt_last_line
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLoadToleratesCorruptLastLine:
    """Appending a corrupt (non-JSON) line to a session's JSONL file
    must not cause an unhandled exception. The store should skip the
    bad line and return all valid messages.
    """

    def test_load_tolerates_corrupt_last_line(self, tmp_path: Path):
        store = _make_store(tmp_path)
        key = "race:corrupt-tail"
        session = store.get_or_create(key)

        # Save 4 valid messages
        for i in range(4):
            session.add_message("user", f"valid-{i}")
        store.save(session)

        # Manually append a corrupted (non-JSON) line to the .jsonl file
        path = store._get_session_path(key)
        assert path.exists()
        with open(path, "a", encoding="utf-8") as f:
            f.write("THIS IS NOT VALID JSON {{{[broken\n")

        # Evict from cache so the next get() must re-read from disk
        store.invalidate(key)

        # This must NOT raise an unhandled exception
        reloaded = store.get(key)

        # The session should load successfully with all valid messages
        assert reloaded is not None
        contents = [m["content"] for m in reloaded.messages]
        assert len(contents) == 4
        for i in range(4):
            assert f"valid-{i}" in contents

    def test_corrupt_line_in_middle_also_tolerated(self, tmp_path: Path):
        """Corrupt line in the middle of the file is also skipped."""
        store = _make_store(tmp_path)
        key = "race:corrupt-mid"
        session = store.get_or_create(key)

        session.add_message("user", "before-corrupt")
        store.save(session)

        # Inject a corrupt line followed by a valid message
        path = store._get_session_path(key)
        with open(path, "a", encoding="utf-8") as f:
            f.write("GARBAGE LINE\n")
            f.write(json.dumps({"role": "user", "content": "after-corrupt"}) + "\n")

        store.invalidate(key)
        reloaded = store.get(key)

        assert reloaded is not None
        contents = [m["content"] for m in reloaded.messages]
        assert "before-corrupt" in contents
        assert "after-corrupt" in contents


# ---------------------------------------------------------------------------
# 4. test_delete_message_out_of_bounds_returns_gracefully
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteMessageOutOfBoundsReturnsGracefully:
    """Attempting to pop an out-of-bounds index on session.messages raises
    IndexError (standard Python list behavior). The test documents that:
    - The exception CAN be caught without corrupting session state.
    - After the failed operation, the session remains fully intact.

    Note: The store's delete_message() method guards against this and
    returns False. This test exercises the raw list behavior directly.
    """

    def test_delete_message_out_of_bounds_returns_gracefully(self, tmp_path: Path):
        store = _make_store(tmp_path)
        session = store.get_or_create("race:oob-delete")

        # Add exactly 3 messages
        for i in range(3):
            session.add_message("user", f"msg-{i}")
        store.save(session)

        original_contents = [m["content"] for m in session.messages]
        assert len(original_contents) == 3

        # Attempting to pop index 99 raises IndexError — document this behavior
        with pytest.raises(IndexError):
            session.messages.pop(99)

        # After the failed pop, session state is completely intact
        assert len(session.messages) == 3
        assert [m["content"] for m in session.messages] == original_contents

        # The session can still be saved and reloaded correctly
        session.add_message("user", "after-failed-pop")
        store.save(session)

        store.invalidate("race:oob-delete")
        reloaded = store.get("race:oob-delete")
        assert reloaded is not None
        assert len(reloaded.messages) == 4
        assert reloaded.messages[-1]["content"] == "after-failed-pop"

    def test_store_delete_message_guards_oob(self, tmp_path: Path):
        """The store's own delete_message() returns False for OOB indices
        instead of raising, and does not corrupt state."""
        store = _make_store(tmp_path)
        session = store.get_or_create("race:oob-guarded")
        for i in range(3):
            session.add_message("user", f"m-{i}")
        store.save(session)

        # delete_message with index 99 should return False gracefully
        result = store.delete_message(session, 99)
        assert result is False
        assert len(session.messages) == 3


# ---------------------------------------------------------------------------
# 5. test_last_consolidated_never_exceeds_len_messages
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLastConsolidatedNeverExceedsLenMessages:
    """Start with 10 messages and last_consolidated=5. Repeatedly delete
    messages (marking dirty and saving each time) until only 2 remain.
    Assert the invariant: last_consolidated <= len(messages) after each save.

    Finding: The store's delete_message() auto-decrements last_consolidated
    when deleting a message before the consolidation watermark. This test
    verifies that invariant holds throughout the entire deletion sequence.
    """

    def test_last_consolidated_never_exceeds_len_messages(self, tmp_path: Path):
        store = _make_store(tmp_path)
        session = store.get_or_create("race:consolidated-bounds")

        # Add 10 messages
        for i in range(10):
            session.add_message("user", f"msg-{i}")

        # Set consolidation watermark at 5
        session.last_consolidated = 5
        session._metadata_dirty = True
        store.save(session)

        assert len(session.messages) == 10
        assert session.last_consolidated == 5

        # Track invariant violations (if any) as findings
        violations: list[str] = []

        # Delete messages one-by-one from the front until only 2 remain
        while len(session.messages) > 2:
            prev_len = len(session.messages)
            prev_consolidated = session.last_consolidated

            # Use the store's delete_message (which handles index adjustment)
            store.delete_message(session, 0)

            # Check the invariant after each deletion + save
            if session.last_consolidated > len(session.messages):
                violations.append(
                    f"VIOLATION: after deleting from {prev_len} msgs "
                    f"(last_consolidated was {prev_consolidated}): "
                    f"now last_consolidated={session.last_consolidated}, "
                    f"len(messages)={len(session.messages)}"
                )

            assert session.last_consolidated <= len(session.messages), (
                f"last_consolidated ({session.last_consolidated}) exceeds "
                f"len(messages) ({len(session.messages)})"
            )

        # Final state: 2 messages remain
        assert len(session.messages) == 2
        assert session.last_consolidated <= 2

        # Verify persistence is consistent
        store.invalidate("race:consolidated-bounds")
        reloaded = store.get("race:consolidated-bounds")
        assert reloaded is not None
        assert len(reloaded.messages) == 2
        assert reloaded.last_consolidated <= len(reloaded.messages)

        # Document: no violations found means the implementation correctly
        # auto-adjusts last_consolidated on each delete_message call.
        assert violations == [], f"Invariant violations found: {violations}"
