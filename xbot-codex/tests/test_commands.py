from xbot_codex.commands import Command, parse_command


def test_parse_known_command_without_argument() -> None:
    cmd = parse_command("!status")
    assert cmd == Command(name="status", arg=None)


def test_parse_known_command_with_argument() -> None:
    cmd = parse_command("!model gpt-5-codex")
    assert cmd == Command(name="model", arg="gpt-5-codex")


def test_parse_non_command_returns_none() -> None:
    assert parse_command("hello codex") is None


def test_parse_unknown_command_returns_none() -> None:
    assert parse_command("!session list") is None
