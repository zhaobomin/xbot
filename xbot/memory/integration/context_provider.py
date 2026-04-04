from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xbot.memory.instructions.loader import InstructionLoader
from xbot.memory.instructions.render import render_instruction_files
from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.memdir.types import MEMORY_PROMPT_RULES
from xbot.memory.recall.prefetch import RelevantMemoryMatcher
from xbot.memory.recall.surfacing import surface_memory_documents

from xbot.logging import get_logger

logger = get_logger(__name__)

_SURFACE_BYTE_LIMIT = 12_000
_SURFACE_ITEM_BYTE_LIMIT = 2_400


@dataclass(slots=True)
class MemoryContextFragments:
    instructions: str = ""
    memory_index: str = ""
    relevant_memories: str = ""


class MemoryContextProvider:
    def __init__(self, workspace: Path, memory_store: MemoryDirStore | None = None):
        self.workspace = Path(workspace)
        self.instructions = InstructionLoader(self.workspace)
        self.memory_store = memory_store or MemoryDirStore(self.workspace)
        self.matcher = RelevantMemoryMatcher(self.memory_store)
        self._surfaced_paths: set[Path] = set()

    def build_fragments(
        self,
        user_message: str = "",
        file_paths: list[str] | None = None,
    ) -> MemoryContextFragments:
        return MemoryContextFragments(
            instructions=self.build_instruction_fragment(file_paths or []),
            memory_index=self.build_memory_index_fragment(),
            relevant_memories=(
                self.build_relevant_memory_fragment(user_message)
                if user_message
                else ""
            ),
        )

    def build_instruction_fragment(self, file_paths: list[str]) -> str:
        if file_paths:
            items = []
            seen: set[Path] = set()
            for file_path in file_paths:
                target = Path(file_path)
                if not target.is_absolute():
                    target = (self.workspace / target).resolve()
                for item in self.instructions.get_instruction_files_for_path(target):
                    if item.path not in seen:
                        seen.add(item.path)
                        items.append(item)
        else:
            items = self.instructions.get_instruction_files()

        if not items:
            return ""
        return render_instruction_files(items)

    def build_memory_index_fragment(self) -> str:
        index = self.memory_store.load_index_for_prompt().strip()
        if not index:
            return ""
        return "# Memory Index\n\n" + MEMORY_PROMPT_RULES.strip() + "\n\n" + index

    def build_relevant_memory_fragment(self, user_message: str) -> str:
        self.matcher.select(user_message)
        selected = self.matcher.collect_ready()
        if not selected:
            return ""
        return surface_memory_documents(
            self.memory_store,
            selected,
            surfaced_paths=self._surfaced_paths,
            total_byte_limit=_SURFACE_BYTE_LIMIT,
            item_byte_limit=_SURFACE_ITEM_BYTE_LIMIT,
        )

    async def recall_relevant_memories(self, user_message: str, backend: object) -> str:
        """Async LLM-based memory recall. Returns formatted memory text or ""."""
        headers = self.memory_store.scan_headers()
        if not headers:
            return ""

        from xbot.memory.recall.llm_selector import select_relevant_memories_llm

        result = await select_relevant_memories_llm(user_message, headers, backend)

        if result is None:
            # LLM 失败 → 降级到关键词匹配
            from xbot.memory.recall.selector import select_relevant_memories

            logger.debug("LLM recall failed, falling back to keyword matching")
            result = select_relevant_memories(user_message, headers)

        if not result:
            return ""

        return surface_memory_documents(
            self.memory_store,
            result,
            surfaced_paths=self._surfaced_paths,
            total_byte_limit=_SURFACE_BYTE_LIMIT,
            item_byte_limit=_SURFACE_ITEM_BYTE_LIMIT,
        )
