from __future__ import annotations

import asyncio
from pathlib import Path

from xbot.logging import get_logger

logger = get_logger(__name__)

from xbot.memory.workers.auto_dream import AutoDreamWorker
from xbot.memory.workers.extract_memories import ExtractMemoriesWorker


class MemoryTurnHooks:
    def __init__(
        self,
        workspace: Path,
        *,
        extractor: ExtractMemoriesWorker | None = None,
        dreamer: AutoDreamWorker | None = None,
        extract_enabled: bool = True,
        auto_dream_enabled: bool = True,
    ) -> None:
        self.workspace = Path(workspace)
        self.extractor = extractor or ExtractMemoriesWorker(self.workspace)
        self.dreamer = dreamer or AutoDreamWorker(self.workspace)
        self.extract_enabled = extract_enabled
        self.auto_dream_enabled = auto_dream_enabled

    async def handle_turn_end(
        self,
        session_key: str,
        *,
        messages: list[dict] | None = None,
        is_subagent: bool,
        direct_memory_write: bool,
    ) -> None:
        # Sequential execution: extract first, then dream.
        # Eliminates concurrent write races on memory files.
        # Dream has min_hours+min_sessions gates so it rarely fires.
        if self.extract_enabled and not is_subagent:
            await self.extractor.request_run(
                session_key,
                messages=messages,
                direct_memory_write=direct_memory_write,
            )
        if self.auto_dream_enabled:
            await self.dreamer.maybe_run(session_key)

    async def force_extract(
        self, session_key: str, messages: list[dict] | None = None
    ) -> None:
        """Force immediate memory extraction (e.g. before compact).

        Safe to call concurrently with ``handle_turn_end`` -- the underlying
        ``ExtractMemoriesWorker`` uses a per-session lock.
        """
        if not self.extract_enabled or self.extractor is None:
            return
        try:
            await self.extractor.request_run(
                session_key, messages=messages, direct_memory_write=False
            )
        except Exception as e:
            logger.warning("[MemoryTurnHooks] force_extract failed: %s", e)
