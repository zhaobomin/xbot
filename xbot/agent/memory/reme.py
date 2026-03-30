"""ReMe-based memory system for xbot.

This module integrates ReMe (https://github.com/agentscope-ai/ReMe) as the memory backend.
ReMe provides:
- File-based memory storage (MEMORY.md + daily journals)
- Vector + BM25 hybrid search
- Automatic context compaction
- Long-term memory summarization
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xbot.logging import get_logger

logger = get_logger(__name__)

from xbot.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

# Patch sqlite3 for chromadb compatibility (requires sqlite >= 3.35.0)
# This must be done before any chromadb imports
def _patch_sqlite3() -> bool:
    """Patch sqlite3 to use pysqlite3 if system sqlite is too old."""
    try:
        import sqlite3
        version = tuple(int(x) for x in sqlite3.sqlite_version.split('.'))
        if version < (3, 35, 0):
            try:
                import pysqlite3
                sys.modules['sqlite3'] = pysqlite3
                logger.debug(f"sqlite3 patched with pysqlite3")
                return True
            except ImportError:
                logger.warning("pysqlite3 not available, ReMe may not work")
                return False
        return True
    except Exception as e:
        logger.warning(f"Failed to patch sqlite3: {e}")
        return False

_sqlite_patched = _patch_sqlite3()

# ReMe imports with graceful fallback
_REME_AVAILABLE = False
try:
    # ReMe dependency key_value.aio installs beartype import hooks by default.
    # Disable it to avoid global import side effects in host runtimes/tests.
    os.environ.setdefault("PY_KEY_VALUE_DISABLE_BEARTYPE", "1")
    from reme.reme_light import ReMeLight
    _REME_AVAILABLE = True
except ImportError:
    ReMeLight = None  # type: ignore
except RuntimeError as e:
    # Chroma sqlite version error
    if "sqlite3" in str(e):
        logger.warning(f"ReMe unavailable due to sqlite3 version: {e}")
        ReMeLight = None  # type: ignore
    else:
        raise


class ReMeMemoryStore:
    """ReMe-backed memory store with hybrid search and auto-compaction.

    This class wraps ReMe's ReMeLight to provide:
    - Compatible interface with existing MemoryStore
    - Enhanced search (vector + BM25)
    - Automatic context compaction
    - Daily journal entries

    Directory structure:
        working_dir/
        ├── MEMORY.md              # Long-term memory (persistent facts)
        ├── memory/
        │   └── YYYY-MM-DD.md      # Daily journals
        ├── dialog/
        │   └── YYYY-MM-DD.jsonl   # Raw conversations
        └── tool_result/           # Cached tool outputs
    """

    def __init__(
        self,
        workspace: Path,
        llm_config: dict[str, Any] | None = None,
        embedding_config: dict[str, Any] | None = None,
        enable_vector_search: bool = False,
    ):
        """Initialize ReMe memory store.

        Args:
            workspace: Root directory for memory files
            llm_config: LLM configuration for summarization
                {"model_name": "gpt-4.1-nano", "backend": "openai"}
            embedding_config: Embedding model configuration
                {"model_name": "text-embedding-3-small", "backend": "openai"}
            enable_vector_search: Enable vector-based search (requires more memory)
        """
        self.workspace = workspace
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"

        self._llm_config = llm_config or {}
        self._embedding_config = embedding_config
        self._enable_vector_search = enable_vector_search

        self._reme: ReMeLight | None = None
        self._initialized = False
        self._lock = asyncio.Lock()

        # Compatibility with MemoryConsolidator
        self._MAX_FAILURES_BEFORE_RAW_ARCHIVE = 5

    async def _ensure_initialized(self) -> bool:
        """Lazy initialization of ReMe backend with timeout protection."""
        if self._initialized:
            return self._reme is not None

        async with self._lock:
            if self._initialized:
                return self._reme is not None

            if not _REME_AVAILABLE:
                logger.warning("ReMe not available, falling back to basic memory")
                self._initialized = True
                return False

            try:
                # Build ReMe config
                reme_config = self._build_reme_config()

                self._reme = ReMeLight(
                    working_dir=str(self.workspace),
                    default_as_llm_config=reme_config.get("llm", {}),
                    default_embedding_model_config=reme_config.get("embedding"),
                    default_file_store_config={
                        "fts_enabled": True,  # BM25 full-text search
                        "vector_enabled": self._enable_vector_search,
                    },
                    enable_load_env=True,
                )
                # Add timeout to prevent blocking during initialization
                # ReMe can take a long time to initialize due to ChromaDB setup
                await asyncio.wait_for(self._reme.start(), timeout=30.0)
                self._initialized = True
                logger.info("ReMe memory store initialized successfully")
                return True
            except asyncio.TimeoutError:
                logger.warning("ReMe initialization timed out (30s), using fallback mode")
                self._initialized = True
                return False
            except Exception as e:
                logger.warning(f"ReMe initialization failed: {e}, using fallback mode")
                self._initialized = True
                return False

    def _build_reme_config(self) -> dict[str, Any]:
        """Build ReMe configuration from xbot settings."""
        config: dict[str, Any] = {}

        # LLM config - ReMe expects "model_name" key
        if self._llm_config:
            config["llm"] = {
                "model_name": self._llm_config.get("model", self._llm_config.get("model_name", "gpt-4.1-nano")),
                "backend": self._llm_config.get("backend", "openai"),
            }
        else:
            config["llm"] = {"model_name": "gpt-4.1-nano"}

        # Embedding config
        if self._embedding_config:
            config["embedding"] = {
                "model_name": self._embedding_config.get("model", self._embedding_config.get("model_name")),
                "backend": self._embedding_config.get("backend", "openai"),
            }

        return config

    # === Compatible interface with MemoryStore ===

    def read_long_term(self) -> str:
        """Read long-term memory from MEMORY.md."""
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        """Write long-term memory to MEMORY.md."""
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        """Append entry to history log."""
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        """Get memory context for prompt building."""
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    # === ReMe-enhanced methods ===

    async def search_memory(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Search memories using vector + BM25 hybrid search.

        Args:
            query: Search query
            max_results: Maximum number of results

        Returns:
            List of memory entries with scores
        """
        if not await self._ensure_initialized() or not self._reme:
            # Fallback: simple grep in memory files
            return self._fallback_search(query, max_results)

        try:
            results = await self._reme.memory_search(query=query, max_results=max_results)
            # ReMe returns ToolResponse with content as list of dicts
            if hasattr(results, 'content') and results.content:
                # content is a list like [{'type': 'text', 'text': '...json...'}]
                import json
                for item in results.content:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        text = item.get('text', '')
                        try:
                            parsed = json.loads(text)
                            # parsed is a list of search results
                            return [
                                {
                                    "memory": r.get("snippet", ""),
                                    "source": r.get("path", "unknown"),
                                    "score": r.get("score", 1.0),
                                    "start_line": r.get("start_line"),
                                    "end_line": r.get("end_line"),
                                }
                                for r in parsed
                            ]
                        except json.JSONDecodeError:
                            pass
            return []
        except Exception as e:
            logger.warning(f"Memory search failed: {e}")
            return self._fallback_search(query, max_results)

    def _fallback_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        """Fallback search using simple text matching."""
        results = []
        query_lower = query.lower()

        # Search in MEMORY.md
        if self.memory_file.exists():
            content = self.memory_file.read_text(encoding="utf-8")
            if query_lower in content.lower():
                results.append({
                    "memory": content[:500],
                    "source": "MEMORY.md",
                    "score": 1.0,
                })

        # Search in HISTORY.md
        if self.history_file.exists() and len(results) < max_results:
            content = self.history_file.read_text(encoding="utf-8")
            lines = content.split("\n")
            for line in lines:
                if query_lower in line.lower():
                    results.append({
                        "memory": line,
                        "source": "HISTORY.md",
                        "score": 0.8,
                    })
                    if len(results) >= max_results:
                        break

        return results[:max_results]

    async def compact_context(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 120000,
        reserve_tokens: int = 10000,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Compact messages if they exceed token limit.

        Args:
            messages: Conversation messages
            max_tokens: Maximum tokens before compaction
            reserve_tokens: Tokens to reserve for recent messages

        Returns:
            Tuple of (processed_messages, summary)
        """
        if not await self._ensure_initialized() or not self._reme:
            return messages, None

        try:
            processed, summary = await self._reme.pre_reasoning_hook(
                messages=messages,
                system_prompt="",
                compressed_summary="",
                max_input_length=max_tokens,
                compact_ratio=0.7,
                memory_compact_reserve=reserve_tokens,
                enable_tool_result_compact=True,
                tool_result_compact_keep_n=3,
            )
            return processed, summary
        except Exception as e:
            logger.warning(f"Context compaction failed: {e}")
            return messages, None

    async def summarize_to_memory(
        self,
        messages: list[dict[str, Any]],
        language: str = "zh",
    ) -> bool:
        """Summarize conversation to daily memory file.

        Args:
            messages: Conversation messages to summarize
            language: Language for summary (zh/en)

        Returns:
            True if successful
        """
        if not await self._ensure_initialized() or not self._reme:
            return False

        try:
            await self._reme.summary_memory(messages=messages, language=language)
            return True
        except Exception as e:
            logger.warning(f"Memory summarization failed: {e}")
            return False

    # === Consolidation compatible with MemoryStore ===

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        """Format messages for consolidation."""
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    async def consolidate(
        self,
        messages: list[dict],
        backend: "ClaudeSDKBackend",
    ) -> bool:
        """Consolidate messages into memory.

        This method provides compatibility with the existing MemoryStore.consolidate()
        interface, but internally uses ReMe's summarization if available.

        Args:
            messages: Messages to consolidate
            backend: Claude SDK backend (used by fallback MemoryStore)

        Returns:
            True if successful
        """
        if not messages:
            return True

        # Try ReMe summarization first
        if await self._ensure_initialized() and self._reme:
            try:
                await self._reme.summary_memory(messages=messages, language="zh")
                return True
            except Exception as e:
                logger.warning(f"ReMe summarization failed, falling back: {e}")

        # Fallback: use original consolidation logic
        return await self._fallback_consolidate(messages, backend)

    async def _fallback_consolidate(
        self,
        messages: list[dict],
        backend: "ClaudeSDKBackend",
    ) -> bool:
        """Fallback consolidation using original logic."""
        # Import here to avoid circular dependency
        from xbot.agent.memory.store import MemoryStore

        temp_store = MemoryStore(self.workspace)
        return await temp_store.consolidate(messages, backend)

    async def close(self) -> None:
        """Clean up resources."""
        if self._reme:
            try:
                await self._reme.close()
            except Exception as e:
                logger.warning(f"ReMe close error: {e}")
            self._reme = None


def create_memory_store(
    workspace: Path,
    use_reme: bool = True,
    llm_config: dict[str, Any] | None = None,
    embedding_config: dict[str, Any] | None = None,
    enable_vector_search: bool = False,
) -> ReMeMemoryStore:
    """Factory function to create a memory store.

    Args:
        workspace: Workspace directory
        use_reme: Use ReMe backend if available
        llm_config: LLM configuration
        embedding_config: Embedding configuration
        enable_vector_search: Enable vector search

    Returns:
        ReMeMemoryStore instance
    """
    if use_reme and _REME_AVAILABLE:
        return ReMeMemoryStore(
            workspace=workspace,
            llm_config=llm_config,
            embedding_config=embedding_config,
            enable_vector_search=enable_vector_search,
        )

    # Return a store that will use fallback mode
    return ReMeMemoryStore(
        workspace=workspace,
        enable_vector_search=False,
    )
