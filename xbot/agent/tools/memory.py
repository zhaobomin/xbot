"""Claude-style memory tool for topic-file durable memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xbot.agent.tools.base import Tool
from xbot.memory.integration.service import MemoryService
from xbot.memory.memdir.store import MemoryDirStore


class MemoryTool(Tool):
    """Tool for Claude-style topic-file memory operations."""

    name = "memory"
    description = "Read, search, and manage long-term memory topics."

    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "read",
                    "search",
                    "write_topic",
                    "update_topic",
                    "delete_topic",
                ],
                "description": "Memory action to perform.",
            },
            "path": {
                "type": "string",
                "description": "Topic file path (e.g. 'project/legacy-memory.md'). For 'read': omit to get the index, or provide a path to read that topic.",
            },
            "query": {
                "type": "string",
                "description": "Search query.",
            },
            "memory_type": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
                "description": "Topic type for write_topic.",
            },
            "title": {
                "type": "string",
                "description": "Topic title for write_topic.",
            },
            "description": {
                "type": "string",
                "description": "One-line topic description.",
            },
            "content": {
                "type": "string",
                "description": "Topic content for write or update.",
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "description": "Maximum search results.",
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        workspace: str | Path,
        memory_store: Any = None,
        memory_service: MemoryService | None = None,
    ):
        self.workspace = Path(workspace)
        self._memory_store = memory_store
        self._memory_service = memory_service

    def _get_memory_service(self) -> MemoryService:
        if self._memory_service is None:
            self._memory_service = MemoryService(self.workspace, store=self._get_memory_store())
        return self._memory_service

    def _get_memory_store(self) -> MemoryDirStore:
        if self._memory_store is None:
            self._memory_store = MemoryDirStore(self.workspace)
        return self._memory_store

    async def execute(
        self,
        action: str,
        path: str | None = None,
        query: str | None = None,
        memory_type: str | None = None,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        max_results: int = 5,
        **_: Any,
    ) -> str:
        if action == "list":
            return self._list()
        if action == "read":
            return self._read(path)
        if action == "search":
            return await self._search(query, max_results)
        if action == "write_topic":
            return self._write_topic(memory_type, title, description, content)
        if action == "update_topic":
            return self._update_topic(path, content, description)
        if action == "delete_topic":
            return self._delete_topic(path)
        return f"Unknown action: {action}"

    def _list(self) -> str:
        return self._get_memory_service().list_memories()

    def _read(self, path: str | None) -> str:
        if not path:
            index = self._get_memory_store().load_index_for_prompt()
            if not index.strip():
                return "No memory topics found."
            return (
                "Below is the memory index. To read a specific topic, "
                "call this tool again with action='read' and path set to "
                "the relative path shown in parentheses.\n\n" + index
            )
        return self._get_memory_service().read_memory(path)

    async def _search(self, query: str | None, max_results: int) -> str:
        if not query:
            return "Please provide a search query."
        return self._get_memory_service().search_memories(query, max_results=max_results)

    def _write_topic(
        self,
        memory_type: str | None,
        title: str | None,
        description: str | None,
        content: str | None,
    ) -> str:
        if not memory_type or not title or not description or not content:
            return "Please provide memory_type, title, description, and content."
        path = self._get_memory_store().create_memory(
            memory_type=memory_type,  # type: ignore[arg-type]
            title=title,
            description=description,
            body=content,
        )
        return f"Saved memory topic at {path}"

    def _update_topic(
        self,
        path: str | None,
        content: str | None,
        description: str | None,
    ) -> str:
        if not path or not content:
            return "Please provide path and content."
        self._get_memory_store().update_memory(Path(path), body=content, description=description)
        return f"Updated memory topic at {path}"

    def _delete_topic(self, path: str | None) -> str:
        if not path:
            return "Please provide path."
        self._get_memory_store().delete_memory(Path(path))
        return f"Deleted memory topic at {path}"
