from __future__ import annotations

from xbot.memory.models import InstructionFile


def render_instruction_files(files: list[InstructionFile]) -> str:
    if not files:
        return ""
    chunks = ["# Claude Instructions"]
    for item in files:
        chunks.append(f"## {item.path.name}\n\n{item.content}")
    return "\n\n".join(chunks)
