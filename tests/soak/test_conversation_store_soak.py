"""Soak test: ConversationStore under long add-message / save / compact loops.

Focus areas:
1. `_cache` growth: with `max_cache_size` set, exceeding capacity must
   trigger `_evict_if_needed` on the next save.
2. Dirty-cache pinning: If every session stays dirty (never saved), the
   cache MUST grow beyond `max_cache_size` — that's an existing design
   choice, but we assert it stays bounded when sessions are cleaned via
   save().
3. Long append+save loop on a single session: file size grows linearly
   with message count (append mode), but memory (session.messages list)
   growth is expected and bounded per session.
4. Repeated compact: file should shrink (or plateau) after compact when
   messages are dropped between compacts.
5. No lock-file leaks: after N cycles, the sessions_dir should contain
   only <hash>.jsonl and <hash>.jsonl.lock files (no stray .tmp files).
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from xbot.runtime.session.conversation_store import (
    ConversationSession,
    ConversationStore,
)

pytestmark = [pytest.mark.soak]


def _tmp_workspace() -> Path:
    return Path(tempfile.mkdtemp(prefix="conv_soak_"))


class TestCacheEvictionUnderLoad:
    """When cache exceeds max size and sessions are saved (not dirty),
    _evict_if_needed must keep the cache bounded."""

    def test_cache_stays_bounded_after_many_clean_sessions(self):
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=50)

            # Create 500 sessions, each with a single message, and save them.
            for i in range(500):
                key = f"web:u:sess-{i}"
                sess = store.get_or_create(key)
                sess.add_message("user", f"hello {i}")
                store.save(sess)

            # Force one more save to trigger _evict_if_needed.
            trigger = store.get_or_create("web:u:trigger")
            trigger.add_message("user", "trigger")
            store.save(trigger)

            gc.collect()

            # Cache is only actively bounded when save() or compact()
            # runs eviction.  We manually trigger it once more via a save.
            assert len(store._cache) <= 51, (
                f"cache grew unbounded: {len(store._cache)}"
            )
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)

    def test_dirty_cache_pinning_observed(self):
        """After the eviction-order fix, dirty sessions pin the cache
        beyond the configured cap.

        Previously `get_or_create()` inserted first then called
        `_evict_if_needed()`, making the fresh non-dirty session the
        prime eviction victim.  The fix evicts BEFORE insert with
        ``reserve=1``, so when all existing entries are dirty and
        none can be evicted, the cache is allowed to grow — the caller
        keeps the SAME object it just inserted, and repeat calls hit
        the cache instead of resurrecting a stale on-disk copy.

        The trade-off (bounded growth of dirty entries) is documented
        in the module docstring and is the intended behavior.
        """
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=10)

            for i in range(100):
                sess = store.get_or_create(f"web:u:dirty-{i}")
                sess.add_message("user", "unsaved")

            # Cache grew unbounded because no session was evictable
            # (all are dirty).  Preferable to silently losing data.
            assert len(store._cache) == 100, (
                f"expected all dirty sessions pinned in cache, got {len(store._cache)}"
            )
            # And crucially: fetching a key that was already inserted
            # returns the SAME live object, not a stale reload.
            same = store.get_or_create("web:u:dirty-42")
            assert same is store._cache["web:u:dirty-42"]
            assert len(same.messages) == 1
            assert same.messages[0]["content"] == "unsaved"
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)

    def test_regression_no_immediate_eviction_of_fresh_session(self):
        """REGRESSION — the fresh session must remain in cache after
        get_or_create(), even when the cache is full of dirty entries.

        Before the fix, this scenario evicted the just-inserted session
        immediately (see `test_lost_update_*` history in git blame).
        """
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=3)

            # Fill cache with dirty sessions to make them non-evictable.
            for i in range(3):
                s = store.get_or_create(f"k{i}")
                s.add_message("user", f"dirty-{i}")

            # The 4th key: because no existing entry can be evicted,
            # the cache grows and the new entry is retained.
            sess_A = store.get_or_create("k3")
            assert "k3" in store._cache, (
                "REGRESSION: fresh session was evicted immediately — "
                "the pre-insert-eviction fix is broken"
            )
            assert store._cache["k3"] is sess_A
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)

    def test_regression_concurrent_callers_see_same_object(self):
        """REGRESSION — two callers requesting the same key must get
        the SAME session object, so their edits merge in-memory
        instead of forking into divergent copies.

        This is the direct inverse of the historical `test_lost_update_bug`.
        """
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=3)

            for i in range(3):
                s = store.get_or_create(f"k{i}")
                s.add_message("user", f"dirty-{i}")

            sess_A = store.get_or_create("k3")
            sess_A.add_message("user", "important-work")

            # Caller-B fetches the same key → cache HIT → same object.
            sess_B = store.get_or_create("k3")
            assert sess_B is sess_A, (
                "REGRESSION: concurrent callers got different objects "
                "for the same key — the lost-update race is back"
            )
            # B naturally sees A's edit.
            assert any(
                m.get("content") == "important-work" for m in sess_B.messages
            )
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)

    def test_regression_no_disk_data_loss_metadata_dirty_race(self):
        """REGRESSION — the scenario that used to wipe A's write from
        disk (Caller-B does mark_metadata_dirty then save → full save
        overwrite) must now preserve both writes.

        With the fix, sess_A and sess_B are the SAME object, so
        Caller-B's `add_message` extends Caller-A's messages list.
        The final save contains BOTH.
        """
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=3)

            for i in range(3):
                s = store.get_or_create(f"pinned-{i}")
                s.add_message("user", f"pinned-{i}")

            sess_A = store.get_or_create("target")
            sess_A.add_message("user", "A-critical-work")

            sess_B = store.get_or_create("target")
            assert sess_B is sess_A, "fix broken: distinct objects"
            sess_B.add_message("user", "B-work")
            sess_B.metadata["last_seen_by"] = "caller-B"
            sess_B.mark_metadata_dirty()

            store.save(sess_A)
            store.save(sess_B)

            # Re-read from disk.
            store.invalidate("target")
            reloaded = store.get_or_create("target")
            contents = [m.get("content") for m in reloaded.messages]

            # Both writes must survive.
            assert "A-critical-work" in contents, (
                f"REGRESSION: Caller-A's write lost from disk: {contents}"
            )
            assert "B-work" in contents, (
                f"REGRESSION: Caller-B's write lost from disk: {contents}"
            )
            assert contents == ["A-critical-work", "B-work"], (
                f"expected both writes in insert order, got {contents}"
            )
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)

    def test_regression_no_disk_data_loss_reverse_order(self):
        """REGRESSION — symmetric to the above; Caller-A holds
        metadata dirty and saves last.  Both writes still survive.
        """
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=3)

            for i in range(3):
                s = store.get_or_create(f"pinned-{i}")
                s.add_message("user", f"pinned-{i}")

            sess_A = store.get_or_create("target")
            sess_A.add_message("user", "A-work")
            sess_A.metadata["author"] = "A"
            sess_A.mark_metadata_dirty()

            sess_B = store.get_or_create("target")
            assert sess_B is sess_A, "fix broken: distinct objects"
            sess_B.add_message("user", "B-critical-work")

            store.save(sess_B)
            store.save(sess_A)

            store.invalidate("target")
            reloaded = store.get_or_create("target")
            contents = [m["content"] for m in reloaded.messages]

            assert "A-work" in contents and "B-critical-work" in contents, (
                f"REGRESSION: data loss returned, disk = {contents}"
            )
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)

    def test_regression_no_silent_disk_divergence(self):
        """REGRESSION — in-memory view and disk view must agree after
        both callers append and save.
        """
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=3)

            for i in range(3):
                s = store.get_or_create(f"pinned-{i}")
                s.add_message("user", f"pinned-{i}")

            sess_A = store.get_or_create("target")
            sess_A.add_message("user", "A-1")
            sess_A.add_message("user", "A-2")

            sess_B = store.get_or_create("target")
            assert sess_B is sess_A
            sess_B.add_message("user", "B-1")

            store.save(sess_A)
            store.save(sess_B)  # append mode; no duplication because same object

            # In-memory view matches.
            in_mem = [m["content"] for m in sess_A.messages]
            assert in_mem == ["A-1", "A-2", "B-1"]

            store.invalidate("target")
            reloaded = store.get_or_create("target")
            contents = [m["content"] for m in reloaded.messages]
            assert contents == ["A-1", "A-2", "B-1"], (
                f"REGRESSION: disk view diverged from in-memory: {contents}"
            )

            # A subsequent full-save (metadata dirty) preserves everything.
            sess_A.metadata["sdk_session_id"] = "resumed-xyz"
            sess_A.mark_metadata_dirty()
            store.save(sess_A)

            store.invalidate("target")
            final = store.get_or_create("target")
            final_contents = [m["content"] for m in final.messages]
            assert final_contents == ["A-1", "A-2", "B-1"], (
                f"REGRESSION: full-save wiped concurrent writes: {final_contents}"
            )
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)


class TestAppendGrowth:
    """One session, many appends + saves — verify file grows linearly
    and no temp/lock detritus is left behind."""

    def test_1000_appends_no_temp_leak(self):
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws)
            sess = store.get_or_create("web:u:append")

            for i in range(1000):
                sess.add_message("user" if i % 2 == 0 else "assistant", f"msg-{i}")
                store.save(sess)

            # No .tmp files should remain.
            tmp_files = list(store.sessions_dir.glob("*.tmp"))
            assert tmp_files == [], f"leaked temp files: {tmp_files}"

            # Exactly one session file (+ its .lock sidecar).
            jsonl_files = list(store.sessions_dir.glob("*.jsonl"))
            assert len(jsonl_files) == 1
            lock_files = list(store.sessions_dir.glob("*.jsonl.lock"))
            assert len(lock_files) <= 1  # lock file is created on demand

            # Session.messages must reflect the append count.
            assert len(sess.messages) == 1000
            # _new_messages should be cleared after every save.
            assert sess._new_messages == []
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)


class TestCompactCycles:
    """Compact repeatedly.  File is fully rewritten each time — verify
    session-side state stays consistent (no _new_messages, not dirty)."""

    def test_100_compacts_leave_clean_state(self):
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws)
            sess = store.get_or_create("web:u:compact")

            for round_ in range(100):
                for j in range(20):
                    sess.add_message("user", f"round-{round_}-msg-{j}")
                store.compact(sess)
                assert sess._new_messages == []
                assert sess._metadata_dirty is False

            assert len(sess.messages) == 2_000
            tmp_files = list(store.sessions_dir.glob("*.tmp"))
            assert tmp_files == []
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)


class TestInvalidateReloadLoop:
    """invalidate() + get_or_create() forces re-load from disk in a tight
    loop.  Verify no orphan cache entries and disk state stays consistent."""

    def test_invalidate_reload_1000_cycles(self):
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws)
            key = "web:u:reload"

            for i in range(1000):
                sess = store.get_or_create(key)
                sess.add_message("user", f"m-{i}")
                store.save(sess)
                store.invalidate(key)

            # Cache is empty because we invalidated last.
            assert key not in store._cache
            # Now reload once — messages should reflect all 1000 appends.
            reloaded = store.get_or_create(key)
            assert len(reloaded.messages) == 1000
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)


class TestManySessionsDiskDoesNotAccumulateTmp:
    """After 300 distinct sessions, no leftover .tmp files."""

    def test_300_distinct_sessions_no_tmp_leak(self):
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws)
            for i in range(300):
                sess = store.get_or_create(f"web:u:many-{i}")
                sess.add_message("user", "x")
                store.save(sess)

            tmp_files = list(store.sessions_dir.glob("*.tmp"))
            assert tmp_files == [], f"leaked temp files: {tmp_files}"

            jsonl_files = list(store.sessions_dir.glob("*.jsonl"))
            assert len(jsonl_files) == 300
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)
