from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MemoryCommand:
    name: str
    argument: str | None = None


class MemoryCommandRouter:
    ARG_COMMANDS = {
        "/remember",
        "/forget",
        "/memory-read",
        "/memory-search",
    }
    ZERO_ARG_COMMANDS = {
        "/memories",
    }

    def parse(self, text: str) -> MemoryCommand | None:
        raw = (text or "").strip()
        if not raw.startswith("/"):
            return None
        command, _, rest = raw.partition(" ")
        if command in self.ZERO_ARG_COMMANDS:
            return MemoryCommand(name=command, argument=None)
        if command in self.ARG_COMMANDS:
            argument = rest.strip() or None
            return MemoryCommand(name=command, argument=argument)
        return None
