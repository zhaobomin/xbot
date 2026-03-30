from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_COMMANDS = {"help", "new", "stop", "status", "mode", "model", "reset"}


@dataclass(frozen=True)
class Command:
    name: str
    arg: str | None = None


def parse_command(text: str) -> Command | None:
    value = text.strip()
    if not value.startswith("!"):
        return None
    parts = value[1:].split(maxsplit=1)
    if not parts or parts[0] not in SUPPORTED_COMMANDS:
        return None
    return Command(name=parts[0], arg=parts[1].strip() if len(parts) > 1 else None)
