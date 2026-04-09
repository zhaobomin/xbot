from __future__ import annotations

from xbot.platform.utils.helpers import load_init_pack


def test_default_init_pack_exposes_commands_list() -> None:
    pack = load_init_pack("default")
    commands = pack.get("commands", [])
    assert isinstance(commands, list)
