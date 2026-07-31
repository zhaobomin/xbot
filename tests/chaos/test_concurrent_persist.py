"""Chaos test: ConversationStore concurrent persist under cross-module
    pressure (AgentService persist + MemoryConsolidator save + CronTool delete).

Simulates the real production scenario where MULTIPLE callers concurrently:
  * add_message + save (agent turn output)
  * mark_metadata_dirty + save (sdk_session_id refresh, consolidation offset)
  * invalidate (session reset)
  * delete_message + save (message retraction / compaction)

All on the SAME session key.  Since ConversationStore has no per-session
asyncio lock (and shouldn't — it's sync), the races surface via
interleaved event-loop yields in the caller path.  After the
_evict_if_needed fix (pre-insert eviction) same-session callers always
share a single in-memory object, so the danger is:
  * file-level torn state when concurrent _save_full + append race on disk
  * metadata + messages going out of sync (metadata newer than messages)
  * _new_messages list mutation while save is iterating it

Bug class: permanent data loss on disk from concurrent full-save.
"""

from __future__ import annotations

import asyncio
import gc
import shutil
import tempfile
from pathlib import Path

import pytest

from xbot.runtime.session.conversation_store import ConversationStore

pytestmark = [pytest.mark.chaos]


def _tmp_workspace() -> Path:
    return Path(tempfile.mkdtemp(prefix="chaos_persist_"))


class TestConcurrentPersistSameSession:
    """N coroutines concurrently add_message + save on the same session."""

    async def test_concurrent_appenders_no_message_loss(self):
        """10 coroutines each append 50 messages and save per message.
        After all settle, reload from disk: total messages MUST equal
        10 * 50 = 500.  Any deficit is a data-loss bug.
        """
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=100)
            key = "web:u:race"

            async def appender(worker_id: int) -> None:
                for i in range(50):
                    sess = store.get_or_create(key)
                    sess.add_message("user", f"w{worker_id}-m{i}")
                    store.save(sess)
                    # Yield to let other coroutines interleave.
                    await asyncio.sleep(0)

            await asyncio.gather(*(appender(w) for w in range(10)))

            # Verify in-memory count.
            sess = store.get_or_create(key)
            assert len(sess.messages) == 500, (
                f"in-memory message count wrong: {len(sess.messages)}"
            )

            # Verify disk count (reload).
            store.invalidate(key)
            reloaded = store.get_or_create(key)
            assert len(reloaded.messages) == 500, (
                f"DISK DATA LOSS: expected 500 messages, "
                f"got {len(reloaded.messages)} after concurrent append"
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_metadata_dirty_race_no_message_loss(self):
        """Simulate the real-world scenario: one coroutine does
        mark_metadata_dirty + save (sdk_session_id update), while
        another coroutine appends messages + save.  The metadata-dirty
        save triggers _save_full which rewrites the ENTIRE file.  If
        it races with an append that added messages after the
        _save_full snapshot, those messages are lost.

        With the eviction fix: both callers share the same session
        object, so _save_full should include ALL messages.  This test
        verifies that.
        """
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=100)
            key = "web:u:meta-race"

            async def message_writer() -> None:
                for i in range(100):
                    sess = store.get_or_create(key)
                    sess.add_message("assistant", f"turn-{i}")
                    store.save(sess)
                    await asyncio.sleep(0)

            async def metadata_mutator() -> None:
                for i in range(100):
                    sess = store.get_or_create(key)
                    sess.metadata["sdk_session_id"] = f"session-{i}"
                    sess.metadata["last_consolidated"] = i
                    sess.mark_metadata_dirty()
                    store.save(sess)
                    await asyncio.sleep(0)

            await asyncio.gather(message_writer(), metadata_mutator())

            # Verify all messages are preserved.
            store.invalidate(key)
            reloaded = store.get_or_create(key)
            assert len(reloaded.messages) == 100, (
                f"DISK DATA LOSS: expected 100 messages, "
                f"got {len(reloaded.messages)} after metadata-dirty race"
            )

            # Verify metadata converged to latest.
            assert reloaded.metadata.get("sdk_session_id") == "session-99", (
                f"metadata lost: {reloaded.metadata.get('sdk_session_id')}"
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_concurrent_invalidate_interleaved_with_save(self):
        """One coroutine repeatedly saves messages, another repeatedly
        invalidates the session from cache.

        Bug class: after invalidate, the next get_or_create reloads from
        disk.  If the save hasn't flushed yet (append pending), the reload
        is stale.  With single-threaded async, saves are synchronous so
        this should be safe — but this test verifies it.
        """
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=100)
            key = "web:u:inv-race"

            async def writer() -> None:
                for i in range(200):
                    sess = store.get_or_create(key)
                    sess.add_message("user", f"m-{i}")
                    store.save(sess)
                    await asyncio.sleep(0)

            async def invalidator() -> None:
                for _ in range(200):
                    store.invalidate(key)
                    await asyncio.sleep(0)

            await asyncio.gather(writer(), invalidator())

            # After everything settles: reload from disk.
            store.invalidate(key)
            reloaded = store.get_or_create(key)

            # Due to invalidate + re-load, the session in cache may not
            # have all 200 messages (the writer might have gotten a fresh
            # object after invalidation and appended from scratch).  But
            # the DISK must have ALL messages that were ever successfully
            # saved (append-only file).
            contents = [m["content"] for m in reloaded.messages]
            assert len(contents) == 200, (
                f"DISK DATA LOSS: expected 200 messages, got {len(contents)}"
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    async def test_concurrent_compact_vs_append(self):
        """compact() does a full rewrite.  If it races with an append that
        dirties _new_messages after the compact read the messages list,
        the append is lost.  Test with 5 compacters + 5 appenders.
        """
        ws = _tmp_workspace()
        try:
            store = ConversationStore(workspace=ws, max_cache_size=100)
            key = "web:u:compact-race"
            message_count = 0

            async def appender(worker_id: int) -> None:
                nonlocal message_count
                for i in range(20):
                    sess = store.get_or_create(key)
                    sess.add_message("user", f"w{worker_id}-m{i}")
                    message_count += 1
                    store.save(sess)
                    await asyncio.sleep(0)

            async def compacter() -> None:
                for _ in range(20):
                    sess = store.get_or_create(key)
                    if sess.messages:
                        store.compact(sess)
                    await asyncio.sleep(0)

            await asyncio.gather(
                *(appender(w) for w in range(5)),
                *(compacter() for _ in range(5)),
            )

            store.invalidate(key)
            reloaded = store.get_or_create(key)
            assert len(reloaded.messages) == message_count, (
                f"DISK DATA LOSS: expected {message_count} messages, "
                f"got {len(reloaded.messages)} after compact/append race"
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)
