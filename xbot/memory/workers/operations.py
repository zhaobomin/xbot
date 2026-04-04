from __future__ import annotations

from pathlib import Path
from typing import Any

from xbot.logging import get_logger
from xbot.memory.memdir.secrets import scan_for_secrets
from xbot.memory.memdir.store import MemoryDirStore

logger = get_logger(__name__)

PERSIST_MEMORIES_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "persist_memories",
            "description": "Create, update, or delete Claude-style durable memory topic files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": ["create", "update", "delete"],
                                },
                                "memory_type": {
                                    "type": "string",
                                    "enum": ["user", "feedback", "project", "reference"],
                                },
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "content": {"type": "string"},
                                "path": {"type": "string"},
                            },
                            "required": ["action"],
                        },
                    }
                },
                "required": ["operations"],
            },
        },
    }
]


def apply_memory_operations(store: MemoryDirStore, operations: list[dict[str, Any]]) -> None:
    for operation in operations:
        action = operation.get("action")
        if action in ("create", "update"):
            content = operation.get("content", "")
            detected = scan_for_secrets(content)
            if detected:
                logger.warning(
                    "Memory content may contain secrets (%s), proceeding with write",
                    ", ".join(detected),
                )
        if action == "create":
            store.create_memory(
                memory_type=operation["memory_type"],
                title=operation["title"],
                description=operation["description"],
                body=operation["content"],
            )
        elif action == "update":
            store.update_memory(
                store.resolve_managed_path(Path(operation["path"])),
                body=operation["content"],
                description=operation.get("description"),
            )
        elif action == "delete":
            store.delete_memory(store.resolve_managed_path(Path(operation["path"])))
