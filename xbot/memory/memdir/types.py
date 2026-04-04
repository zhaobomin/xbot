from __future__ import annotations

from xbot.memory.models import MemoryType

MEMORY_PROMPT_RULES = """
If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.

## What NOT to save in memory
- Code patterns, architecture, file paths, or project structure.
- Git history or recent changes.
- Debugging recipes or fixes that already live in the code.
- Anything already documented in CLAUDE.md files.
- Ephemeral task details or current conversation-only state.

## When to access memories
- When memories seem relevant, or the user references prior work.
- You MUST access memory when the user explicitly asks you to recall, check, or remember.
- If the user says to ignore or not use memory, proceed as if MEMORY.md were empty.

## Before recommending from memory
- If the memory names a file path: check the file exists.
- If the memory names a function or flag: grep for it.
- If the user is about to act on your recommendation, verify first.
""".strip()

VALID_MEMORY_TYPES: tuple[MemoryType, ...] = ("user", "feedback", "project", "reference")
