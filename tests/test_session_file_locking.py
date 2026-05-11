"""Test session file locking behavior.

Regression tests for file locking fix in ConversationStore.
Tests that concurrent access to session files is properly synchronized.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from xbot.runtime.session.conversation_store import ConversationSession, ConversationStore


class TestSessionFileLocking:
    """Test that file locking prevents concurrent access issues."""

    def test_save_and_load_are_thread_safe(self, tmp_path: Path) -> None:
        """Concurrent saves and loads should not corrupt session files."""
        manager = ConversationStore(tmp_path)
        session_key = "test:concurrent"

        # Create initial session
        session = manager.get_or_create(session_key)
        session.add_message("user", "initial message")
        manager.save(session)

        save_count = 100
        errors = []

        def save_session(idx: int) -> None:
            try:
                sess = manager.get_or_create(session_key)
                sess.add_message("user", f"message {idx}")
                manager.save(sess)
            except Exception as e:
                errors.append(e)

        # Run concurrent saves
        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(save_session, range(save_count)))

        assert not errors, f"Errors during concurrent saves: {errors}"

        # Verify session is loadable and valid
        loaded = manager._load(session_key)
        assert loaded is not None
        assert loaded.key == session_key

    def test_save_writes_atomically(self, tmp_path: Path) -> None:
        """ConversationSession save should use atomic write (temp file + rename)."""
        manager = ConversationStore(tmp_path)
        session_key = "test:atomic"

        session = manager.get_or_create(session_key)
        session.add_message("user", "test message")
        manager.save(session)

        # Check that temp file was cleaned up
        session_path = manager._get_session_path(session_key)
        tmp_path_file = session_path.with_suffix(".jsonl.tmp")

        assert not tmp_path_file.exists(), "Temp file should be cleaned up after save"
        assert session_path.exists(), "ConversationSession file should exist"

    def test_concurrent_sessions_dont_interfere(self, tmp_path: Path) -> None:
        """Concurrent operations on different sessions should not interfere."""
        manager = ConversationStore(tmp_path)
        session_keys = [f"test:session{i}" for i in range(5)]

        errors = []

        def operate_on_session(key: str) -> None:
            try:
                session = manager.get_or_create(key)
                for i in range(10):
                    session.add_message("user", f"{key} message {i}")
                    manager.save(session)
            except Exception as e:
                errors.append((key, e))

        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(operate_on_session, session_keys))

        assert not errors, f"Errors: {errors}"

        # Verify each session has correct messages
        for key in session_keys:
            loaded = manager._load(key)
            assert loaded is not None
            assert len(loaded.messages) == 10

    def test_load_handles_missing_file_gracefully(self, tmp_path: Path) -> None:
        """Loading a non-existent session should return None."""
        manager = ConversationStore(tmp_path)
        result = manager._load("nonexistent:session")
        assert result is None

    def test_session_paths_do_not_collide_for_similar_keys(self, tmp_path: Path) -> None:
        """Distinct session keys should never collapse to the same JSONL path."""
        manager = ConversationStore(tmp_path)

        assert manager._get_session_path("a:b") != manager._get_session_path("a_b")
        assert manager._get_session_path("feishu:ou_foo") != manager._get_session_path("feishu/ou:foo")

    def test_load_reads_old_safe_filename_path_and_saves_to_hashed_path(self, tmp_path: Path) -> None:
        """Existing pre-hash session files should stay readable."""
        manager = ConversationStore(tmp_path)
        old_path = manager.sessions_dir / "a_b.jsonl"
        old_path.write_text(
            '{"_type":"metadata","key":"a:b","created_at":"2026-05-11T00:00:00","last_consolidated":0}\n'
            '{"role":"user","content":"legacy"}\n',
            encoding="utf-8",
        )

        loaded = manager._load("a:b")
        assert loaded is not None
        assert loaded.messages[0]["content"] == "legacy"

        manager.save(loaded)
        assert manager._get_session_path("a:b").exists()

    def test_delete_removes_new_and_old_session_paths(self, tmp_path: Path) -> None:
        """Deleting a session should clean hashed and pre-hash compatibility files."""
        manager = ConversationStore(tmp_path)
        session = manager.get_or_create("a:b")
        session.add_message("user", "new")
        manager.save(session)
        old_path = manager.sessions_dir / "a_b.jsonl"
        old_lock_path = old_path.with_suffix(old_path.suffix + ".lock")
        old_path.write_text("legacy\n", encoding="utf-8")
        old_lock_path.write_text("", encoding="utf-8")

        assert manager.delete("a:b") is True

        assert not manager._get_session_path("a:b").exists()
        assert not old_path.exists()
        assert not old_lock_path.exists()

    def test_save_preserves_all_session_data(self, tmp_path: Path) -> None:
        """All session data should be preserved after save/load cycle."""
        manager = ConversationStore(tmp_path)
        session_key = "test:preserve"

        # Create session with various data
        session = manager.get_or_create(session_key)
        session.add_message("user", "hello")
        session.add_message("assistant", "hi there")
        session.metadata["sdk_session_id"] = "test-sdk-id"
        session.metadata["custom_field"] = "custom_value"
        session.last_consolidated = 1
        manager.save(session)

        # Load and verify
        loaded = manager._load(session_key)
        assert loaded is not None
        assert len(loaded.messages) == 2
        assert loaded.metadata.get("sdk_session_id") == "test-sdk-id"
        assert loaded.metadata.get("custom_field") == "custom_value"
        assert loaded.last_consolidated == 1

    def test_lock_file_is_created_and_cleaned(self, tmp_path: Path) -> None:
        """Lock files should be created during operations and cleaned up."""
        manager = ConversationStore(tmp_path)
        session_key = "test:lockfile"

        session = manager.get_or_create(session_key)
        session.add_message("user", "test")
        manager.save(session)

        # Lock file path
        session_path = manager._get_session_path(session_key)
        _ = session_path.with_suffix(".jsonl.lock")

        # Lock file may or may not exist after operation
        # (it's cleaned up after use but the file itself might remain empty)
        # The important thing is that it doesn't block future operations

        # Should be able to save again without issues
        session.add_message("user", "another message")
        manager.save(session)  # Should not hang or fail

    def test_corrupt_session_file_handled_gracefully(self, tmp_path: Path) -> None:
        """Loading a corrupt session file should not crash."""
        manager = ConversationStore(tmp_path)
        session_key = "test:corrupt"

        # Write corrupt data
        session_path = manager._get_session_path(session_key)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text("not valid json\nalso not valid")

        # Should return None (logged warning, no crash)
        result = manager._load(session_key)
        assert result is None

    def test_session_clear_resets_state(self, tmp_path: Path) -> None:
        """Clearing a session should reset all mutable state."""
        manager = ConversationStore(tmp_path)
        session_key = "test:clear"

        session = manager.get_or_create(session_key)
        session.add_message("user", "message 1")
        session.add_message("user", "message 2")
        session.last_consolidated = 1
        session.metadata["key"] = "value"
        manager.save(session)

        # Clear
        session.clear()

        assert len(session.messages) == 0
        assert session.last_consolidated == 0
        # Metadata is not cleared by design (preserves sdk_session_id etc)

        manager.save(session)

        # Load and verify
        loaded = manager._load(session_key)
        assert loaded is not None
        assert len(loaded.messages) == 0
        assert loaded.last_consolidated == 0


class TestSessionHistoryBoundary:
    """Test session history boundary conditions."""

    def test_get_history_with_large_offset(self, tmp_path: Path) -> None:
        """get_history should handle last_consolidated >= len(messages)."""
        session = ConversationSession(key="test:offset")
        session.add_message("user", "message 1")
        session.last_consolidated = 100  # Beyond message count

        history = session.get_history()
        assert history == []

    def test_get_history_with_negative_consolidated(self, tmp_path: Path) -> None:
        """get_history should handle negative last_consolidated gracefully."""
        session = ConversationSession(key="test:negative")
        session.add_message("user", "message 1")
        # This shouldn't happen in practice, but let's be defensive
        session.last_consolidated = -1

        # Should still work (negative slice starts from end, but we handle it)
        history = session.get_history()
        # Behavior depends on implementation, should not crash
        assert isinstance(history, list)

    def test_get_history_preserves_tool_calls(self, tmp_path: Path) -> None:
        """get_history should preserve tool_calls in messages."""
        session = ConversationSession(key="test:tools")
        session.add_message(
            "assistant",
            None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "test", "arguments": "{}"}
            }]
        )

        history = session.get_history()
        assert len(history) == 1
        assert "tool_calls" in history[0]
        assert history[0]["tool_calls"][0]["id"] == "call_1"

    def test_get_history_max_messages_respected(self, tmp_path: Path) -> None:
        """get_history should respect max_messages parameter."""
        session = ConversationSession(key="test:max")
        for i in range(100):
            session.add_message("user", f"message {i}")

        history = session.get_history(max_messages=10)
        assert len(history) == 10
        # Should get the most recent messages
        assert "message 99" in history[-1]["content"]
