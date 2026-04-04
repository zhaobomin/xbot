"""Generate a structured session summary before reset / fresh-start.

The summary captures the user's current goals, key decisions, in-progress
tasks and important facts so they can be re-injected as a
``<system-reminder>`` on the next turn after the context is lost.
"""

from __future__ import annotations

import json
from typing import Any

from xbot.logging import get_logger

logger = get_logger(__name__)

_SUMMARY_SYSTEM_PROMPT = """\
You are a session-context summariser.  Given the recent conversation
history, produce a concise JSON object with the following fields:

{
  "current_goals": ["<one-liner describing each active user goal>"],
  "key_decisions": ["<important decisions already made>"],
  "in_progress":   ["<tasks/topics still open>"],
  "important_facts": ["<facts the user mentioned that should survive reset>"]
}

Rules:
- Each array MUST contain 1-5 short strings (≤ 30 words each).
- Output ONLY the JSON object—no markdown fences, no commentary.
- If a field has nothing relevant, use an empty array [].
- Use the same language as the conversation.
"""


async def generate_session_summary(
    backend: object,
    session: object,
    *,
    max_messages: int = 50,
) -> dict[str, Any] | None:
    """Create a structured summary from recent session messages.

    Args:
        backend: A backend instance with ``call_for_auxiliary()`` method.
        session: A ``Session`` object with ``messages`` and ``metadata``.
        max_messages: How many recent messages to feed to the LLM.

    Returns:
        Parsed summary dict on success, ``None`` on any failure.
        On success, the summary is also stored in
        ``session.metadata["working_summary"]``.
    """
    # Safely access session.messages via get_history() or fallback.
    messages: list[dict[str, Any]] = []
    get_history = getattr(session, "get_history", None)
    if callable(get_history):
        messages = get_history(max_messages=max_messages)
    else:
        raw = getattr(session, "messages", None) or []
        messages = list(raw[-max_messages:])

    if not messages:
        logger.debug("[SessionSummary] No messages to summarise – skipping.")
        return None

    # Build a compact transcript for the LLM.
    transcript_lines: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            # Handle multimodal content blocks – take text parts only.
            content = " ".join(
                block.get("text", "") for block in content if isinstance(block, dict)
            )
        # Truncate very long messages to save tokens.
        if len(content) > 500:
            content = content[:500] + "…"
        transcript_lines.append(f"[{role}] {content}")

    transcript = "\n".join(transcript_lines)

    try:
        response = await backend.call_for_auxiliary(
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            max_tokens=1024,
            temperature=0.0,
        )
    except Exception:
        logger.warning("[SessionSummary] call_for_auxiliary failed", exc_info=True)
        return None

    # Parse the JSON response.
    raw_text: str = getattr(response, "content", "") or ""
    # Strip markdown fences if the model added them despite instructions.
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[-1]
    if raw_text.endswith("```"):
        raw_text = raw_text.rsplit("```", 1)[0]
    raw_text = raw_text.strip()

    try:
        summary = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[SessionSummary] Failed to parse summary JSON: %s", raw_text[:200])
        return None

    if not isinstance(summary, dict):
        logger.warning("[SessionSummary] Unexpected summary type: %s", type(summary).__name__)
        return None

    # Persist into session metadata.
    metadata = getattr(session, "metadata", None)
    if isinstance(metadata, dict):
        metadata["working_summary"] = summary

    logger.info("[SessionSummary] Generated summary with %d goals, %d in-progress items",
                len(summary.get("current_goals", [])),
                len(summary.get("in_progress", [])))
    return summary
