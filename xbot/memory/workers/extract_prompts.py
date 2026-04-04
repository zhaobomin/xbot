from __future__ import annotations


def build_extract_memories_prompt(new_message_count: int, existing_memories: str) -> str:
    manifest = (
        f"\n\n## Existing memory files\n\n{existing_memories}\n\nCheck this list before writing — update an existing file rather than creating a duplicate."
        if existing_memories
        else ""
    )
    return (
        f"You are now acting as the memory extraction subagent. Analyze the most recent ~{new_message_count} messages above and use them to update your persistent memory systems.\n\n"
        "You MUST only use content from the last "
        f"~{new_message_count} messages to update your persistent memories. "
        "Do not waste any turns attempting to investigate or verify that content further — no grepping source files, no reading code to confirm a pattern exists, no git commands."
        f"{manifest}\n\n"
        "If the user explicitly asks you to remember something, save it immediately as whichever type fits best. "
        "If they ask you to forget something, find and remove the relevant entry.\n\n"
        "MEMORY.md is an index, not a memory. Never write memory content directly into MEMORY.md."
    )
