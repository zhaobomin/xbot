from __future__ import annotations

from xbot.memory.integration.command_router import MemoryCommandRouter


def test_memory_command_router_parses_argument_commands() -> None:
    router = MemoryCommandRouter()

    command = router.parse("/remember release freeze starts tomorrow")

    assert command is not None
    assert command.name == "/remember"
    assert command.argument == "release freeze starts tomorrow"


def test_memory_command_router_parses_zero_argument_commands() -> None:
    router = MemoryCommandRouter()

    command = router.parse("/memories")

    assert command is not None
    assert command.name == "/memories"
    assert command.argument is None


def test_memory_command_router_rejects_non_memory_command() -> None:
    router = MemoryCommandRouter()

    assert router.parse("/deploy now") is None
