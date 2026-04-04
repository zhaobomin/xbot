from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.models import MemoryType
from xbot.memory.workers.extract_prompts import build_extract_memories_prompt
from xbot.memory.workers.operations import PERSIST_MEMORIES_TOOL, apply_memory_operations


class MemoryService:
    def __init__(
        self,
        workspace: str | Path,
        *,
        backend: object | None = None,
        store: MemoryDirStore | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.backend = backend
        self.store = store or MemoryDirStore(self.workspace)

    async def remember(
        self,
        text: str,
        session_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        content = (text or "").strip()
        if not content:
            return "Usage: /remember <text>"
        if await self._remember_via_auxiliary(content, session_key, metadata=metadata):
            return f"Saved memory topic from: {content}"
        path = self.store.create_memory(
            memory_type=self._infer_type(content),
            title=content,
            description=self._one_line(content),
            body=content,
        )
        return f"Saved memory topic at {path}"

    async def forget(self, query: str, session_key: str) -> str:
        del session_key
        needle = (query or "").strip()
        if not needle:
            return "Usage: /forget <query>"
        matches = self._find_matching_paths(needle)
        for path in matches:
            self.store.delete_memory(path)
        if not matches:
            return f"No memories matched '{needle}'."
        return f"Deleted {len(matches)} memory topic(s) matching '{needle}'."

    def list_memories(self) -> str:
        headers = self.store.list_memories()
        if not headers:
            return "No memories found."
        return "\n".join(
            f"- {header.name or header.filename} [{header.memory_type or 'unknown'}] — {header.description or 'No description'}"
            for header in headers
        )

    def read_memory(self, path_or_name: str) -> str:
        target = (path_or_name or "").strip()
        if not target:
            return self.store.load_index_for_prompt()
        path = self._resolve_path_or_name(target)
        if path is None:
            return f"No memory found for '{target}'."
        doc = self.store.read_memory(path)
        return (
            f"# {doc.name}\n\n"
            f"Type: {doc.memory_type}\n"
            f"Description: {doc.description}\n"
            f"Updated: {doc.updated_at}\n\n"
            f"{doc.body}"
        )

    def search_memories(self, query: str, max_results: int = 5) -> str:
        needle = (query or "").strip().lower()
        if not needle:
            return "Please provide a search query."
        matches: list[str] = []
        for header in self.store.list_memories():
            if len(matches) >= max_results:
                break
            path = header.file_path
            doc = self.store.read_memory(path)
            haystacks = [
                header.name or "",
                header.description or "",
                header.filename,
                doc.body,
            ]
            if any(needle in value.lower() for value in haystacks):
                matches.append(
                    f"- {header.name or header.filename} [{header.memory_type or 'unknown'}] — {header.description or 'No description'}"
                )
        if not matches:
            return f"No results found for '{query}'."
        return "\n".join(matches)

    async def _remember_via_auxiliary(
        self,
        content: str,
        session_key: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not hasattr(self.backend, "call_for_auxiliary"):
            return False
        manifest = self.store.load_index_for_prompt()
        prompt = (
            build_extract_memories_prompt(1, manifest)
            + "\n\nThis invocation is from an explicit /remember command. Save the durable memory immediately."
        )
        response = await self.backend.call_for_auxiliary(
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "session_key": session_key,
                            "metadata": metadata or {},
                            "messages": [{"role": "user", "content": content}],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            tools=PERSIST_MEMORIES_TOOL,
            tool_choice={"type": "function", "function": {"name": "persist_memories"}},
            max_tokens=2048,
        )
        for tool_call in getattr(response, "tool_calls", []):
            if tool_call.name != "persist_memories":
                continue
            apply_memory_operations(self.store, tool_call.arguments.get("operations", []))
            return True
        return False

    def _resolve_path_or_name(self, target: str) -> Path | None:
        candidate = Path(target)
        if candidate.exists():
            try:
                return self.store.resolve_managed_path(candidate)
            except ValueError:
                return None
        relative = self.store.memory_dir / target
        if relative.exists():
            return self.store.resolve_managed_path(relative)
        normalized = target.strip().lower()
        for header in self.store.list_memories():
            if normalized in {
                (header.name or "").strip().lower(),
                Path(header.filename).stem.lower(),
                header.filename.lower(),
                header.file_path.relative_to(self.store.memory_dir).as_posix().lower(),
            }:
                return header.file_path
        return None

    def _find_matching_paths(self, query: str) -> list[Path]:
        needle = query.lower()
        matches: list[Path] = []
        for header in self.store.list_memories():
            doc = self.store.read_memory(header.file_path)
            haystacks = [
                header.name or "",
                header.description or "",
                header.filename,
                doc.body,
            ]
            if any(needle in value.lower() for value in haystacks):
                matches.append(header.file_path)
        return matches

    @staticmethod
    def _infer_type(content: str) -> MemoryType:
        lowered = content.lower()
        if any(keyword in lowered for keyword in ("prefer", "always", "never", "don't", "do not")):
            return "feedback"
        if any(keyword in lowered for keyword in ("dashboard", "url", "link", "grafana", "slack", "notion")):
            return "reference"
        if any(keyword in lowered for keyword in ("i am", "my ", "me ", "prefer")):
            return "user"
        return "project"

    @staticmethod
    def _one_line(content: str, limit: int = 96) -> str:
        compact = " ".join(content.split())
        return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"
