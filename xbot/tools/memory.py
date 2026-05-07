"""Memory tool for reading, searching, and writing long-term memory.

This tool wraps ReMeMemoryStore to provide memory operations.
Compatible with existing MEMORY.md format.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from xbot.platform.logging.core import get_logger
from xbot.tools.base import Tool

logger = get_logger(__name__)


class MemoryTool(Tool):
    """Tool for memory operations.

    Actions:
    - read: Read current long-term memory (all or specific section)
    - search: Search memory using vector + BM25 hybrid search
    - write: Write or update a section in memory
    - append: Append content to history log
    """

    name = "memory"
    description = "Read, search, and write long-term memory. Use to remember important facts or recall past information."

    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "search", "write", "append"],
                "description": "Action to perform: read, search, write, or append"
            },
            "section": {
                "type": "string",
                "description": "For read: section name to read (optional, reads all if not specified). For write: section name to create/update."
            },
            "content": {
                "type": "string",
                "description": "For write: content to write (markdown). For append: entry to append to history."
            },
            "query": {
                "type": "string",
                "description": "For search: search query"
            },
            "max_results": {
                "type": "integer",
                "description": "For search: maximum results to return (default 5)",
                "default": 5
            }
        },
        "required": ["action"]
    }

    def __init__(
        self,
        workspace: str | Path,
        memory_store: Any = None,
    ):
        """Initialize memory tool.

        Args:
            workspace: Workspace directory
            memory_store: Optional ReMeMemoryStore instance (will be created if not provided)
        """
        self.workspace = Path(workspace)
        self.memory_dir = self.workspace / "memory"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
        self._memory_store = memory_store

    def _get_memory_store(self):
        """Get or create memory store."""
        if self._memory_store is None:
            from xbot.memory.reme import ReMeMemoryStore
            self._memory_store = ReMeMemoryStore(self.workspace)
        return self._memory_store

    async def execute(
        self,
        action: str,
        section: str | None = None,
        content: str | None = None,
        query: str | None = None,
        max_results: int = 5,
        **kwargs,
    ) -> str:
        """Execute memory action.

        Args:
            action: Action to perform
            section: Section name for read/write
            content: Content for write/append
            query: Search query
            max_results: Max search results

        Returns:
            Result string
        """
        if action == "read":
            return self._read(section)
        elif action == "search":
            return await self._search(query, max_results)
        elif action == "write":
            return self._write(section, content)
        elif action == "append":
            return self._append(content)
        else:
            return f"Unknown action: {action}"

    def _read(self, section: str | None = None) -> str:
        """Read memory content.

        Args:
            section: Optional section name to read

        Returns:
            Memory content
        """
        if not self.memory_file.exists():
            return "No long-term memory found."

        content = self.memory_file.read_text(encoding="utf-8")

        if section is None:
            return content

        # Extract specific section
        pattern = rf"(^## {re.escape(section)}.*?)(?=^## |\Z)"
        match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
        if match:
            return match.group(1).strip()
        return f"Section '{section}' not found."

    async def _search(self, query: str | None, max_results: int) -> str:
        """Search memory.

        Args:
            query: Search query
            max_results: Maximum results

        Returns:
            Search results
        """
        if not query:
            return "Please provide a search query."

        store = self._get_memory_store()

        # Try ReMe search first
        search_memory = getattr(store, "search_memory", None)
        if callable(search_memory):
            try:
                results = await search_memory(query, max_results)
                if results:
                    lines = [f"Found {len(results)} result(s):\n"]
                    for i, r in enumerate(results, 1):
                        lines.append(f"### Result {i}")
                        lines.append(f"Source: {r.get('source', 'unknown')}")
                        lines.append(f"Score: {r.get('score', 0):.2f}")
                        memory_text = r.get("memory", "")
                        preview = memory_text[:500]
                        suffix = "..." if len(memory_text) > 500 else ""
                        lines.append(f"Content:\n{preview}{suffix}")
                        lines.append("")
                    return "\n".join(lines)
            except Exception as e:
                logger.warning(f"ReMe search failed: {e}, using fallback")

        # Fallback: simple grep
        return self._fallback_search(query, max_results)

    def _fallback_search(self, query: str, max_results: int) -> str:
        """Fallback search using simple text matching."""
        results = []
        query_lower = query.lower()

        if self.memory_file.exists():
            content = self.memory_file.read_text(encoding="utf-8")
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if query_lower in line.lower():
                    # Get context around the match
                    start = max(0, i - 2)
                    end = min(len(lines), i + 3)
                    context = "\n".join(lines[start:end])
                    results.append(f"Line {i+1}:\n{context}")

        if self.history_file.exists():
            content = self.history_file.read_text(encoding="utf-8")
            lines = content.split("\n")
            for line in lines:
                if query_lower in line.lower() and len(results) < max_results:
                    results.append(f"[History] {line[:200]}...")

        if not results:
            return f"No results found for '{query}'."

        return f"Found {len(results[:max_results])} result(s):\n\n" + "\n\n---\n\n".join(results[:max_results])

    def _write(self, section: str | None, content: str | None) -> str:
        """Write or update a section in memory.

        Args:
            section: Section name (required)
            content: Content to write (required)

        Returns:
            Result message
        """
        if not section:
            return "Please provide a section name."
        if not content:
            return "Please provide content to write."

        # Ensure memory directory exists
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Read existing content
        existing = ""
        if self.memory_file.exists():
            existing = self.memory_file.read_text(encoding="utf-8")

        # Check if section exists
        pattern = rf"(^## {re.escape(section)}.*?)(?=^## |\Z)"
        match = re.search(pattern, existing, re.MULTILINE | re.DOTALL)

        new_section = f"## {section}\n\n{content}\n\n"

        if match:
            # Update existing section
            updated = existing[:match.start()] + new_section + existing[match.end():]
        else:
            # Add new section before the footer
            footer_pattern = r"\n---\n\n\*This file is automatically updated"
            if re.search(footer_pattern, existing):
                replacement = f"\n\n{new_section}---\n\n*This file is automatically updated"
                updated = re.sub(footer_pattern, lambda _m: replacement, existing)
            else:
                updated = existing + "\n\n" + new_section

        self.memory_file.write_text(updated.rstrip() + "\n", encoding="utf-8")
        return f"Section '{section}' written to memory."

    def _append(self, content: str | None) -> str:
        """Append entry to history log.

        Args:
            content: Entry to append

        Returns:
            Result message
        """
        if not content:
            return "Please provide content to append."

        from datetime import datetime

        # Ensure memory directory exists
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M]")
        entry = f"{timestamp} {content}\n\n"

        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry)

        return "Entry appended to history log."
