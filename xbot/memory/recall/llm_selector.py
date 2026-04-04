from __future__ import annotations

import asyncio
import json

from xbot.logging import get_logger
from xbot.memory.models import MAX_RELEVANT_MEMORIES, MemoryHeader

logger = get_logger(__name__)

RECALL_SYSTEM_PROMPT = """\
You are selecting memories relevant to the user's current query.
Given the user's message and a list of available memory files with descriptions,
select up to 5 files that are most likely to be helpful.
Only include memories you are confident will be relevant based on their name and description.
If none are relevant, return an empty list.
Do not explain your reasoning."""

SELECT_MEMORIES_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "select_memories",
            "description": "Select memory files relevant to the user query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selected_filenames": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": MAX_RELEVANT_MEMORIES,
                    }
                },
                "required": ["selected_filenames"],
            },
        },
    }
]


async def select_relevant_memories_llm(
    query: str,
    headers: list[MemoryHeader],
    backend: object,
    *,
    timeout: float = 5.0,
) -> list[MemoryHeader] | None:
    """LLM-based memory recall.

    Returns selected headers on success, None on failure (caller should fallback
    to keyword matching).
    """
    if not headers or not query.strip():
        return []

    manifest = [
        {"filename": h.filename, "description": h.description or "", "type": h.memory_type or ""}
        for h in headers
    ]

    try:
        response = await asyncio.wait_for(
            backend.call_for_auxiliary(  # type: ignore[union-attr]
                messages=[
                    {"role": "system", "content": RECALL_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"query": query, "memories": manifest},
                            ensure_ascii=False,
                        ),
                    },
                ],
                tools=SELECT_MEMORIES_TOOL,
                tool_choice={"type": "function", "function": {"name": "select_memories"}},
                max_tokens=256,
            ),
            timeout=timeout,
        )
    except Exception:
        logger.debug("LLM memory recall failed, will fallback to keyword matching")
        return None  # 超时/API 错误 → 降级

    for tool_call in getattr(response, "tool_calls", None) or []:
        if tool_call.name != "select_memories":
            continue
        selected = tool_call.arguments.get("selected_filenames", [])
        name_map = {h.filename: h for h in headers}
        return [name_map[fn] for fn in selected if fn in name_map][:MAX_RELEVANT_MEMORIES]
    return []
