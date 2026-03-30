import asyncio
from pathlib import Path

import pytest

from xbot_codex.codex.transport import CodexEvent, CodexTransport


class FakeProcess:
    def __init__(self, lines: list[bytes]):
        self.stdout = FakeStream(lines)
        self.stdin = FakeWriter()
        self.returncode = 0

    async def wait(self) -> int:
        return self.returncode


class FakeStream:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        await asyncio.sleep(0)
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeWriter:
    def __init__(self) -> None:
        self.buffer: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.buffer.append(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stream_events_parses_json_delta_and_final() -> None:
    process = FakeProcess(
        [
            b'{"type":"message.delta","delta":"hel"}\n',
            b'{"type":"message.delta","delta":"lo"}\n',
            b'{"type":"message.final","content":"hello"}\n',
        ]
    )
    transport = CodexTransport(binary_path="codex")

    events = [event async for event in transport.read_events(process)]

    assert events == [
        CodexEvent(type="message.delta", content="", delta="hel"),
        CodexEvent(type="message.delta", content="", delta="lo"),
        CodexEvent(type="message.final", content="hello", delta=""),
    ]


@pytest.mark.asyncio
async def test_stream_events_maps_item_completed_agent_message_to_final() -> None:
    process = FakeProcess(
        [
            b'{"type":"thread.started","thread_id":"t1"}\n',
            b'{"type":"turn.started"}\n',
            b'{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Hi."}}\n',
            b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}\n',
        ]
    )
    transport = CodexTransport(binary_path="codex")

    events = [event async for event in transport.read_events(process)]

    assert events == [CodexEvent(type="message.final", content="Hi.", delta="")]


@pytest.mark.asyncio
async def test_stream_events_falls_back_to_plain_text() -> None:
    process = FakeProcess([b"plain line\n"])
    transport = CodexTransport(binary_path="codex")

    events = [event async for event in transport.read_events(process)]

    assert events == [CodexEvent(type="message.delta", content="", delta="plain line")]


@pytest.mark.asyncio
async def test_run_prompt_passes_skip_git_repo_check(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Process:
        def __init__(self) -> None:
            self.stdout = FakeStream([])
            self.stdin = FakeWriter()
            self.returncode = 0

        async def wait(self) -> int:
            return 0

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    transport = CodexTransport(binary_path="codex")

    events = [
        event
        async for event in transport.run_prompt(
            "telegram:1",
            "hello",
            model=None,
            mode=None,
            profile=None,
            workdir=str(tmp_path / "wd"),
        )
    ]

    assert events == []
    argv = captured["argv"]
    assert "--skip-git-repo-check" in argv


@pytest.mark.asyncio
async def test_run_prompt_maps_dangerous_mode_to_bypass_flag(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Process:
        def __init__(self) -> None:
            self.stdout = FakeStream([])
            self.stdin = FakeWriter()
            self.returncode = 0

        async def wait(self) -> int:
            return 0

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    transport = CodexTransport(binary_path="codex")

    _ = [
        event
        async for event in transport.run_prompt(
            "telegram:1",
            "hello",
            model=None,
            mode="dangerous",
            profile=None,
            workdir=str(tmp_path / "wd"),
        )
    ]

    argv = captured["argv"]
    assert "--dangerously-bypass-approvals-and-sandbox" in argv


@pytest.mark.asyncio
async def test_stream_events_ignores_path_warning_line() -> None:
    process = FakeProcess(
        [b"WARNING: proceeding, even though we could not update PATH: Operation not permitted (os error 1)\n"]
    )
    transport = CodexTransport(binary_path="codex")

    events = [event async for event in transport.read_events(process)]

    assert events == []


@pytest.mark.asyncio
async def test_stream_events_ignores_internal_json_state_events() -> None:
    process = FakeProcess(
        [
            b'{"type":"thread.started","thread_id":"t1"}\n',
            b'{"type":"turn.started"}\n',
            b'{"type":"message.delta","delta":"ok"}\n',
        ]
    )
    transport = CodexTransport(binary_path="codex")

    events = [event async for event in transport.read_events(process)]

    assert events == [CodexEvent(type="message.delta", content="", delta="ok")]


@pytest.mark.asyncio
async def test_stream_events_compacts_auth_failure_noise_into_one_error() -> None:
    process = FakeProcess(
        [
            b'2026-03-30T00:54:21.395586Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when Auth(TokenRefreshFailed("Server returned error response: invalid_grant: Invalid refresh token"))\n'
        ]
    )
    process.returncode = 1
    transport = CodexTransport(binary_path="codex")

    events = [event async for event in transport.read_events(process)]

    assert events == [
        CodexEvent(
            type="error",
            content="Codex authentication failed: invalid refresh token. Re-run `codex login` for the service account.",
        )
    ]


@pytest.mark.asyncio
async def test_stream_events_compacts_rmcp_oauth_failure_into_one_error() -> None:
    process = FakeProcess(
        [
            b"2026-03-30T01:12:46.119782Z  WARN codex_rmcp_client::oauth: failed to read OAuth tokens from keyring: Platform secure storage failure: A default keychain could not be found.\n",
            b'2026-03-30T01:12:47.276187Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer realm=\\"OAuth\\", error=\\"invalid_token\\", error_description=\\"Missing or invalid access token\\"" })\n',
        ]
    )
    process.returncode = 1
    transport = CodexTransport(binary_path="codex")

    events = [event async for event in transport.read_events(process)]

    assert events == [
        CodexEvent(
            type="error",
            content="Codex MCP authentication failed for the service environment. Remove MCP entries from the service Codex config or re-authenticate them for this service account.",
        )
    ]


@pytest.mark.asyncio
async def test_stream_events_ignores_internal_log_followups() -> None:
    process = FakeProcess(
        [
            b"2026-03-30T00:54:20.673560Z WARN codex_core::shell_snapshot: Failed to create shell snapshot for bash\n",
            b"Caused by:\n",
            b"Operation not permitted (os error 1)\n",
            b'{"type":"message.final","content":"hello"}\n',
        ]
    )
    transport = CodexTransport(binary_path="codex")

    events = [event async for event in transport.read_events(process)]

    assert events == [CodexEvent(type="message.final", content="hello", delta="")]


@pytest.mark.asyncio
async def test_run_prompt_passes_isolated_codex_home_env(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Process:
        def __init__(self) -> None:
            self.stdout = FakeStream([])
            self.stdin = FakeWriter()
            self.returncode = 0

        async def wait(self) -> int:
            return 0

    async def fake_exec(*argv, **kwargs):
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    codex_home = tmp_path / "codex-home"
    transport = CodexTransport(
        binary_path="codex",
        env={"HOME": str(codex_home), "CODEX_HOME": str(codex_home)},
    )

    _ = [
        event
        async for event in transport.run_prompt(
            "telegram:1",
            "hello",
            model=None,
            mode=None,
            profile=None,
            workdir=str(tmp_path / "wd"),
        )
    ]

    env = captured["kwargs"]["env"]
    assert env["HOME"] == str(codex_home)
    assert env["CODEX_HOME"] == str(codex_home)
    assert codex_home.exists()
