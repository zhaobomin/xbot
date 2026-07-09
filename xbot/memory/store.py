"""Memory system for persistent agent memory."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from xbot.platform.logging.core import get_logger
from xbot.platform.utils.helpers import (
    ensure_dir,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
)

logger = get_logger(__name__)
if TYPE_CHECKING:
    from xbot.runtime.core.service import AgentService
    from xbot.runtime.session.conversation_store import ConversationSession, ConversationStore


# === Consolidation Prompt Templates ===

CONSOLIDATION_SYSTEM_PROMPT = """You are a memory consolidation agent. Extract important information from conversations and save it for future sessions.

## Extraction Guidelines

### → MEMORY.md (Long-term Facts)
Extract to long-term memory if:
- User stated preferences or constraints
- Project context that will be referenced later
- Important configuration or relationships
- Decisions that affect future behavior

### → HISTORY.md (Event Log)
Format: `[YYYY-MM-DD HH:MM] <summary>`
Extract to history if:
- Task completed or failed with outcome
- Bug found and fix applied
- Decision made with brief reasoning
- Learning or insight discovered

### → Ignore
- Casual conversation without substance
- Routine tool execution details
- Temporary context without future value

Be selective - only extract information that will be useful in future sessions."""

CONSOLIDATION_USER_TEMPLATE = """## Current Long-term Memory
{current_memory}

## Conversation to Process
{formatted_messages}

## Context Size
- Current MEMORY.md: ~{memory_tokens} tokens
- Conversation: ~{conversation_tokens} tokens

Call the save_memory tool with your extraction."""

# === Tool Definition ===

_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph for HISTORY.md summarizing key events, decisions, or outcomes. "
                        "Start with [YYYY-MM-DD HH:MM]. Include details useful for grep search. "
                        "Examples: task completions, bug fixes, decisions made, insights discovered.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated MEMORY.md content as markdown. Include all existing "
                        "facts plus new ones. Remove obsolete information. "
                        "Return 'NO_CHANGE' if nothing new to add.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


def _ensure_text(value: Any) -> str:
    """Normalize tool-call payload values to text for file storage."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _normalize_save_memory_args(args: Any) -> dict[str, Any] | None:
    """Normalize provider tool-call arguments to the expected dict shape."""
    if isinstance(args, str):
        args = json.loads(args)
    if isinstance(args, list):
        return args[0] if args and isinstance(args[0], dict) else None
    return args if isinstance(args, dict) else None

_TOOL_CHOICE_ERROR_MARKERS = (
    "tool_choice",
    "toolchoice",
    "does not support",
    'should be ["none", "auto"]',
)


def _is_tool_choice_unsupported(content: str | None) -> bool:
    """Detect provider errors caused by forced tool_choice being unsupported."""
    text = (content or "").lower()
    return any(m in text for m in _TOOL_CHOICE_ERROR_MARKERS)


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log)."""

    _MAX_FAILURES_BEFORE_RAW_ARCHIVE = 5  # Match ReMeMemoryStore for consistency

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
        self._consecutive_failures = 0

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            try:
                return self.memory_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                logger.warning("Memory file contains invalid UTF-8, returning empty")
                return ""
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            # Skip messages with neither content nor tool_calls
            has_content = message.get("content")
            has_tool_calls = message.get("tool_calls")

            if not has_content and not has_tool_calls:
                continue

            # Format tool_calls if present
            tool_info = ""
            if has_tool_calls:
                tool_names = [tc.get("name", "?") for tc in has_tool_calls]
                tool_info = f" [tools: {', '.join(tool_names)}]"
            elif message.get("tools_used"):
                tool_info = f" [tools: {', '.join(message['tools_used'])}]"

            content = has_content if has_content else f"(tool calls: {len(has_tool_calls)})"
            if isinstance(content, (list, dict)):
                content = f"({type(content).__name__}: {len(str(content))} chars)"
            role = str(message.get("role") or "unknown")

            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {role.upper()}{tool_info}: {content}"
            )
        return "\n".join(lines)

    async def consolidate(
        self,
        messages: list[dict],
        backend: "AgentService",
    ) -> bool:
        """Consolidate the provided message chunk into MEMORY.md + HISTORY.md."""
        if not messages:
            return True

        current_memory = self.read_long_term()
        formatted_messages = self._format_messages(messages)

        # Estimate token counts for context size hint
        from xbot.platform.utils.helpers import estimate_prompt_tokens
        memory_tokens = estimate_prompt_tokens([
            {"role": "user", "content": current_memory}
        ]) if current_memory else 0
        conversation_tokens = estimate_prompt_tokens([
            {"role": "user", "content": formatted_messages}
        ])

        # Build prompt using the template
        prompt = CONSOLIDATION_USER_TEMPLATE.format(
            current_memory=current_memory or "(empty)",
            formatted_messages=formatted_messages,
            memory_tokens=memory_tokens,
            conversation_tokens=conversation_tokens,
        )

        chat_messages = [
            {"role": "system", "content": CONSOLIDATION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            forced = {"type": "function", "function": {"name": "save_memory"}}
            response = await backend.call_for_consolidation(
                messages=chat_messages,
                tools=_SAVE_MEMORY_TOOL,
                tool_choice=forced,
            )

            if response.finish_reason == "error" and _is_tool_choice_unsupported(
                response.content
            ):
                logger.warning("Forced tool_choice unsupported, retrying with auto")
                response = await backend.call_for_consolidation(
                    messages=chat_messages,
                    tools=_SAVE_MEMORY_TOOL,
                    tool_choice="auto",
                )

            if not response.has_tool_calls:
                logger.warning(
                    "Memory consolidation: LLM did not call save_memory "
                    "(finish_reason=%s, content_len=%s, content_preview=%s)",
                    response.finish_reason,
                    len(response.content or ""),
                    (response.content or "")[:200],
                )
                return self._fail_or_raw_archive(messages)

            args = _normalize_save_memory_args(response.tool_calls[0].arguments)
            if args is None:
                logger.warning("Memory consolidation: unexpected save_memory arguments")
                return self._fail_or_raw_archive(messages)

            if "history_entry" not in args or "memory_update" not in args:
                logger.warning("Memory consolidation: save_memory payload missing required fields")
                return self._fail_or_raw_archive(messages)

            entry = args["history_entry"]
            update = args["memory_update"]

            if entry is None or update is None:
                logger.warning("Memory consolidation: save_memory payload contains null required fields")
                return self._fail_or_raw_archive(messages)

            entry = _ensure_text(entry).strip()
            if not entry:
                logger.warning("Memory consolidation: history_entry is empty after normalization")
                return self._fail_or_raw_archive(messages)

            self.append_history(entry)
            update = _ensure_text(update).strip()

            # Handle NO_CHANGE indicator - don't update memory
            if update.upper() == "NO_CHANGE":
                logger.debug("Memory consolidation: LLM returned NO_CHANGE, keeping existing memory")
            elif not update:
                # Empty update - keep existing memory
                logger.debug("Memory consolidation: empty memory_update, keeping existing memory")
            elif update != current_memory:
                self.write_long_term(update)

            self._consecutive_failures = 0
            logger.info("Memory consolidation done for %s messages", len(messages))
            return True
        except Exception:
            logger.exception("Memory consolidation failed")
            return self._fail_or_raw_archive(messages)

    def _fail_or_raw_archive(self, messages: list[dict]) -> bool:
        """Increment failure count; after threshold, raw-archive messages and return True."""
        self._consecutive_failures += 1
        if self._consecutive_failures < self._MAX_FAILURES_BEFORE_RAW_ARCHIVE:
            return False
        self._raw_archive(messages)
        self._consecutive_failures = 0
        return True

    def _raw_archive(self, messages: list[dict]) -> None:
        """Fallback: dump raw messages to HISTORY.md without LLM summarization."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.append_history(
            f"[{ts}] [RAW] {len(messages)} messages\n"
            f"{self._format_messages(messages)}"
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived %s messages", len(messages)
        )


class MemoryConsolidator:
    """Owns consolidation policy, locking, and session offset updates.

    ## Design Note: Relationship with SDK ConversationSession History

    Memory consolidation in xbot serves a different purpose than SDK context management:

    - **This consolidator**: Archives messages to MEMORY.md/HISTORY.md for long-term
      persistent memory. This enables the agent to recall important facts across
      sessions and after restarts.

    - **SDK context management**: The Claude SDK maintains its own session history
      via the `resume` parameter. SDK handles context window optimization through
      its internal mechanisms (e.g., /compact command).

    The `last_consolidated` offset in ConversationSession tracks which messages have been
    archived to files, NOT which messages the SDK has in its context. This is
    intentional - the two systems manage different concerns:

    1. xbot consolidation → persistent searchable memory (HISTORY.md, MEMORY.md)
    2. SDK history → temporary context window management

    Changes to `last_consolidated` do NOT affect what the SDK sends to the LLM.
    """

    _MAX_CONSOLIDATION_ROUNDS = 5
    TRIGGER_RATIO = 0.7  # 触发阈值比例：context_window 的 70%
    MIN_RESERVE_TURNS = 5  # 最小保留对话轮数
    MIN_RESERVE_TOKENS = 5_000  # 最小保留 tokens

    def __init__(
        self,
        workspace: Path,
        backend: "AgentService",
        sessions: ConversationStore,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        memory_store: MemoryStore | None = None,
    ):
        # Use provided memory store or create default MemoryStore
        self.store = memory_store if memory_store is not None else MemoryStore(workspace)
        self.backend = backend
        self.sessions = sessions
        self.context_window_tokens = context_window_tokens
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._locks: dict[str, asyncio.Lock] = {}

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared consolidation lock for one session."""
        return self._locks.setdefault(session_key, asyncio.Lock())

    def _cleanup_lock_if_idle(self, session_key: str, lock: asyncio.Lock) -> None:
        if lock.locked():
            return
        waiters = getattr(lock, "_waiters", None)
        if waiters:
            return
        if self._locks.get(session_key) is lock:
            self._locks.pop(session_key, None)

    async def consolidate_messages(self, messages: list[dict[str, object]]) -> bool:
        """Archive a selected message chunk into persistent memory.

        No global lock is held across the (10-30s) LLM call so that
        consolidation in one session doesn't block message handling in
        another. Per-session serialization is still provided by the
        session-level lock from :meth:`get_lock`.
        """
        return await self.store.consolidate(messages, self.backend)

    def pick_consolidation_boundary(
        self,
        session: ConversationSession,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens.

        Respects MIN_RESERVE_TURNS to always keep the last N turns unconsolidated.
        """
        start = session.last_consolidated
        total_messages = len(session.messages)

        if start >= total_messages or tokens_to_remove <= 0:
            return None

        # 计算最小保留边界：保留最后 MIN_RESERVE_TURNS 轮对话
        # 每轮 = 1 user + 1 assistant = 2 messages
        min_reserve_messages = self.MIN_RESERVE_TURNS * 2
        max_consolidate_idx = total_messages - min_reserve_messages

        # 如果对话太短，无法保留最小轮数，则不归档
        if max_consolidate_idx <= start:
            logger.debug(
                "Cannot consolidate: need to reserve %s turns (%s messages), "
                "only %s messages available after start",
                self.MIN_RESERVE_TURNS,
                min_reserve_messages,
                total_messages - start,
            )
            return None

        removed_tokens = 0
        last_valid_boundary: tuple[int, int] | None = None

        for idx in range(start, max_consolidate_idx):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_valid_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_valid_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_valid_boundary

    def estimate_session_prompt_tokens(self, session: ConversationSession) -> tuple[int, str]:
        """Estimate current prompt size for the normal session history view."""
        from xbot.platform.bus.events import parse_session_key

        history = session.get_history(max_messages=0)
        _channel, _chat_id = parse_session_key(session.key)
        channel, chat_id = (_channel or None, _chat_id or None)
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
        )
        return estimate_prompt_tokens_chain(
            None,  # No provider needed - use tiktoken fallback
            None,  # No model needed for tiktoken
            probe_messages,
            self._get_tool_definitions(),
        )

    async def archive_messages(self, messages: list[dict[str, object]]) -> bool:
        """Archive messages with guaranteed persistence (retries until raw-dump fallback)."""
        if not messages:
            return True
        for _ in range(self.store._MAX_FAILURES_BEFORE_RAW_ARCHIVE):
            if await self.consolidate_messages(messages):
                return True
        return True

    async def maybe_consolidate_by_tokens(self, session: ConversationSession) -> None:
        """Loop: archive old messages until prompt fits within (1 - TRIGGER_RATIO) of context window."""
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        try:
            async with lock:
                # 使用 70% 阈值：触发阈值 = context_window * 0.7
                # 目标 = context_window * 0.3（保留 30% 空间）
                trigger_threshold = int(self.context_window_tokens * self.TRIGGER_RATIO)
                target = int(self.context_window_tokens * (1 - self.TRIGGER_RATIO))
                estimated, source = self.estimate_session_prompt_tokens(session)
                if estimated <= 0:
                    return
                if estimated < trigger_threshold:
                    logger.debug(
                        "Token consolidation idle %s: %s/%s (trigger at %s) via %s",
                        session.key,
                        estimated,
                        self.context_window_tokens,
                        trigger_threshold,
                        source,
                    )
                    return

                for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                    if estimated <= target:
                        return

                    boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                    if boundary is None:
                        logger.debug(
                            "Token consolidation: no safe boundary for %s (round %s)",
                            session.key,
                            round_num,
                        )
                        return

                    end_idx = boundary[0]
                    chunk = session.messages[session.last_consolidated:end_idx]
                    if not chunk:
                        return

                    logger.info(
                        "Token consolidation round %s for %s: %s/%s via %s, chunk=%s msgs",
                        round_num,
                        session.key,
                        estimated,
                        self.context_window_tokens,
                        source,
                        len(chunk),
                    )
                    if not await self.consolidate_messages(chunk):
                        return
                    session.last_consolidated = end_idx
                    self.sessions.save(session)

                    estimated, source = self.estimate_session_prompt_tokens(session)
                    if estimated <= 0:
                        return
        finally:
            self._cleanup_lock_if_idle(session.key, lock)

    async def force_consolidate(self, session: ConversationSession, reserve_last_n: int | None = None) -> dict[str, Any]:
        """Force consolidate unconsolidated messages in a session.

        This is triggered by the /compact command.

        Args:
            session: The session to consolidate
            reserve_last_n: Number of turns to reserve. None = use MIN_RESERVE_TURNS,
                           0 = consolidate all (no reserve)

        Returns:
            Dict with consolidation stats: {
                "messages_consolidated": int,
                "tokens_before": int,
                "tokens_after": int,
                "success": bool,
            }
        """
        if not session.messages:
            return {
                "messages_consolidated": 0,
                "tokens_before": 0,
                "tokens_after": 0,
                "success": True,
            }

        # Use MIN_RESERVE_TURNS if reserve_last_n is None, otherwise use provided value
        # reserve_last_n=0 means consolidate all (no reserve)
        reserve_turns = self.MIN_RESERVE_TURNS if reserve_last_n is None else reserve_last_n
        reserve_messages = reserve_turns * 2

        lock = self.get_lock(session.key)
        try:
            async with lock:
                # Get tokens before
                tokens_before, _ = self.estimate_session_prompt_tokens(session)

                # Calculate safe boundary respecting reserve
                total_messages = len(session.messages)
                max_consolidate_idx = total_messages - reserve_messages

                # Start from last_consolidated
                start = session.last_consolidated

                # If conversation too short to reserve, don't consolidate (unless reserve=0)
                if reserve_turns > 0 and max_consolidate_idx <= start:
                    logger.debug(
                        "Force consolidation: cannot consolidate, need to reserve %s turns (%s messages)",
                        reserve_turns,
                        reserve_messages,
                    )
                    return {
                        "messages_consolidated": 0,
                        "tokens_before": tokens_before,
                        "tokens_after": tokens_before,
                        "success": True,
                    }

                # When reserve=0, consolidate all unconsolidated messages
                end_idx = max_consolidate_idx if reserve_turns > 0 else total_messages

                # Get messages to consolidate
                unconsolidated = session.messages[start:end_idx]
                if not unconsolidated:
                    return {
                        "messages_consolidated": 0,
                        "tokens_before": tokens_before,
                        "tokens_after": tokens_before,
                        "success": True,
                    }

                messages_count = len(unconsolidated)
                logger.info(
                    "Force consolidation for %s: %s messages, %s tokens (reserving %s turns)",
                    session.key,
                    messages_count,
                    tokens_before,
                    reserve_turns,
                )

                # Consolidate selected messages
                success = await self.consolidate_messages(unconsolidated)
                if success:
                    session.last_consolidated = start + messages_count
                    self.sessions.save(session)

                # Get tokens after
                tokens_after, _ = self.estimate_session_prompt_tokens(session)

                return {
                    "messages_consolidated": messages_count,
                    "tokens_before": tokens_before,
                    "tokens_after": tokens_after,
                    "success": success,
                }
        finally:
            self._cleanup_lock_if_idle(session.key, lock)
