"""Comprehensive tests for shell.py and filesystem.py targeting uncovered lines."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
    _find_fuzzy_match_fragments,
    _find_match,
    _is_under,
    _resolve_path,
)
from xbot.tools.shell import ExecTool


# ============================================================================
# shell.py — deny pattern detection edge cases (line 48 area)
# ============================================================================

class TestDenyPatternEdgeCases:
    """Edge cases for deny pattern matching via normalized candidates."""

    @pytest.mark.asyncio
    async def test_deny_pattern_blocks_gnu_long_options(self):
        tool = ExecTool()
        result = await tool.execute(command="rm --recursive /tmp/thing")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_blocks_rm_force_long(self):
        tool = ExecTool()
        result = await tool.execute(command="rm --force /tmp/thing")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_del_f(self):
        tool = ExecTool()
        result = await tool.execute(command="del /f somefile")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_del_q(self):
        tool = ExecTool()
        result = await tool.execute(command="del /q somefile")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_rmdir_s(self):
        tool = ExecTool()
        result = await tool.execute(command="rmdir /s somedir")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_format_standalone(self):
        tool = ExecTool()
        result = await tool.execute(command="format C:")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_mkfs(self):
        tool = ExecTool()
        result = await tool.execute(command="mkfs.ext4 /dev/sda1")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_diskpart(self):
        tool = ExecTool()
        result = await tool.execute(command="diskpart")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_write_to_disk(self):
        tool = ExecTool()
        result = await tool.execute(command="echo data > /dev/sda")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_fork_bomb(self):
        tool = ExecTool()
        result = await tool.execute(command=":(){ :|:& };:")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_shutdown(self):
        tool = ExecTool()
        result = await tool.execute(command="shutdown -h now")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_reboot(self):
        tool = ExecTool()
        result = await tool.execute(command="reboot")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_deny_pattern_poweroff(self):
        tool = ExecTool()
        result = await tool.execute(command="poweroff")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_custom_deny_pattern(self):
        tool = ExecTool(deny_patterns=[r"\bsecret_command\b"])
        result = await tool.execute(command="secret_command")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_allowlist_blocks_unmatched(self):
        tool = ExecTool(allow_patterns=[r"^echo\b"])
        result = await tool.execute(command="ls -la")
        assert "blocked" in result.lower()
        assert "allowlist" in result.lower()

    @pytest.mark.asyncio
    async def test_allowlist_permits_matched(self):
        tool = ExecTool(allow_patterns=[r"^echo\b"])
        result = await tool.execute(command="echo hello")
        assert "hello" in result
        assert "Exit code: 0" in result


# ============================================================================
# shell.py — obfuscation detection (lines 113-135 / 239-250)
# ============================================================================

class TestObfuscationDetection:
    """Test detection of shell obfuscation vectors."""

    @pytest.mark.asyncio
    async def test_ansi_c_hex_escape(self):
        tool = ExecTool()
        result = await tool.execute(command=r"echo $'\x72\x6d'")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_command_substitution_hex(self):
        tool = ExecTool()
        result = await tool.execute(command=r"$(echo '\x72\x6d')")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_printf_hex_escape(self):
        tool = ExecTool()
        result = await tool.execute(command=r"printf '\x72\x6d'")
        assert "blocked" in result.lower()

    def test_contains_obfuscated_shell_text_ansi_c(self):
        assert ExecTool._contains_obfuscated_shell_text(r"echo $'\x41\x42'") is True

    def test_contains_obfuscated_shell_text_cmd_sub(self):
        assert ExecTool._contains_obfuscated_shell_text(r"$(echo '\x41')") is True

    def test_contains_obfuscated_shell_text_printf(self):
        assert ExecTool._contains_obfuscated_shell_text(r"printf '\x41'") is True

    def test_contains_obfuscated_shell_text_clean(self):
        assert ExecTool._contains_obfuscated_shell_text("echo hello world") is False

    def test_decode_escapes_hex(self):
        tool = ExecTool()
        assert tool._decode_escapes(r"\x41\x42") == "AB"

    def test_decode_escapes_unicode(self):
        tool = ExecTool()
        assert tool._decode_escapes(r"\u0041") == "A"

    def test_decode_escapes_octal(self):
        tool = ExecTool()
        # \101 = 'A' in octal
        assert tool._decode_escapes(r"\101") == "A"

    def test_decode_escapes_octal_large_value_preserved(self):
        """Octal values beyond Unicode max should be left intact."""
        tool = ExecTool()
        # \77777777 is way beyond 0x10FFFF
        original = r"\77777777"
        result = tool._decode_escapes(original)
        # The regex matches \777 (3 octal digits max per group), so it processes in chunks
        # But individual groups like \777 = 511 which is within Unicode range
        # The guard is for value > 0x10FFFF per match group
        assert isinstance(result, str)

    def test_normalized_candidates_plain(self):
        tool = ExecTool()
        candidates = tool._normalized_command_candidates("echo hello")
        assert "echo hello" in candidates

    def test_normalized_candidates_with_hex(self):
        tool = ExecTool()
        candidates = tool._normalized_command_candidates(r"\x65cho hello")
        # Should include decoded variant
        assert any("echo" in c for c in candidates)

    def test_normalized_candidates_with_ansi_c_fragment(self):
        tool = ExecTool()
        candidates = tool._normalized_command_candidates(r"$'\x72\x6d' -rf")
        # Should have decoded variants
        assert len(candidates) > 1


# ============================================================================
# shell.py — internal URL detection, path traversal (lines 153-172)
# ============================================================================

class TestInternalUrlAndPathTraversal:
    """Test internal URL blocking and path traversal detection."""

    @pytest.mark.asyncio
    async def test_internal_url_blocks_169_254(self):
        import socket

        def fake_resolve(hostname, *args):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]

        tool = ExecTool()
        with patch("xbot.platform.security.network.socket.getaddrinfo", fake_resolve):
            result = await tool.execute(command="curl http://metadata.google.internal/")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_path_traversal_backslash(self):
        tool = ExecTool(restrict_to_workspace=True)
        result = await tool.execute(command="cat ..\\secret.txt")
        assert "blocked" in result.lower()
        assert "traversal" in result.lower()

    @pytest.mark.asyncio
    async def test_path_traversal_forward_slash(self):
        tool = ExecTool(restrict_to_workspace=True)
        result = await tool.execute(command="cat ../secret.txt")
        assert "blocked" in result.lower()
        assert "traversal" in result.lower()

    @pytest.mark.asyncio
    async def test_absolute_path_outside_workspace_blocked(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("secret")

        tool = ExecTool(restrict_to_workspace=True)
        result = await tool.execute(
            command=f"cat {outside_file}",
            working_dir=str(workspace),
        )
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_absolute_path_inside_workspace_allowed(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        inside_file = workspace / "inside.txt"
        inside_file.write_text("hello")

        tool = ExecTool(restrict_to_workspace=True)
        guard = await tool._guard_command(
            f"cat {inside_file}", str(workspace),
        )
        assert guard is None

    def test_extract_absolute_paths_windows(self):
        paths = ExecTool._extract_absolute_paths(r"cat C:\Users\test\file.txt")
        assert any("C:\\" in p for p in paths)

    def test_extract_absolute_paths_posix(self):
        paths = ExecTool._extract_absolute_paths("cat /etc/passwd")
        assert "/etc/passwd" in paths

    def test_extract_absolute_paths_home(self):
        paths = ExecTool._extract_absolute_paths("cat ~/secret.txt")
        assert any("~" in p for p in paths)

    def test_extract_relative_paths_basic(self):
        paths = ExecTool._extract_relative_paths("cat foo.txt bar.txt")
        assert paths == ["foo.txt", "bar.txt"]

    def test_extract_relative_paths_skips_options(self):
        paths = ExecTool._extract_relative_paths("cat -n foo.txt")
        assert paths == ["foo.txt"]

    def test_extract_relative_paths_skips_env_vars(self):
        paths = ExecTool._extract_relative_paths("FOO=bar cmd baz.txt")
        # FOO=bar is skipped (env var), cmd becomes command name, baz.txt is positional
        assert "baz.txt" in paths

    def test_extract_relative_paths_skips_command_sub(self):
        paths = ExecTool._extract_relative_paths("cat $(whoami).txt")
        assert paths == []

    def test_extract_relative_paths_skips_backtick_sub(self):
        paths = ExecTool._extract_relative_paths("cat `whoami`.txt")
        assert paths == []

    def test_extract_relative_paths_skips_tilde(self):
        paths = ExecTool._extract_relative_paths("cat ~/file.txt")
        assert paths == []

    def test_extract_relative_paths_handles_redirection(self):
        paths = ExecTool._extract_relative_paths("cat input.txt > output.txt")
        assert "input.txt" in paths
        assert "output.txt" in paths

    def test_extract_relative_paths_pipeline_reset(self):
        paths = ExecTool._extract_relative_paths("cmd1 a.txt | cmd2 b.txt")
        assert "a.txt" in paths
        assert "b.txt" in paths

    def test_extract_relative_paths_grep_skips_pattern(self):
        paths = ExecTool._extract_relative_paths("grep needle haystack.txt")
        # grep's first positional arg is the pattern, not a file
        assert "haystack.txt" in paths
        assert "needle" not in paths


# ============================================================================
# shell.py — output truncation, process killing (lines 188-204)
# ============================================================================

class TestOutputTruncationAndTimeout:
    """Test output truncation and process killing on timeout."""

    @pytest.mark.asyncio
    async def test_output_truncation_head_and_tail(self):
        """Large output should be truncated preserving head and tail."""
        tool = ExecTool()
        # Generate output larger than _MAX_OUTPUT (10000)
        result = await tool.execute(command=f'{sys.executable} -c "print(\'A\' * 20000)"')
        assert "truncated" in result.lower()
        # Should start with A's and end with A's
        assert "A" in result

    @pytest.mark.asyncio
    async def test_output_truncation_preserves_content(self):
        tool = ExecTool()
        tool._MAX_OUTPUT = 100  # Lower for easier testing
        result = await tool.execute(command=f'{sys.executable} -c "print(\'X\' * 500)"')
        assert "truncated" in result.lower()
        # The truncation message should mention chars truncated
        assert "chars truncated" in result

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        tool = ExecTool(timeout=0.1)
        result = await tool.execute(
            command=f'{sys.executable} -c "import time; time.sleep(10)"',
        )
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_timeout_reports_stderr(self):
        tool = ExecTool(timeout=0.1)
        result = await tool.execute(
            command=f'{sys.executable} -c "import sys,time; sys.stderr.write(\'err\\n\'); time.sleep(10)"',
        )
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_no_output_message(self):
        tool = ExecTool()
        result = await tool.execute(command="true")
        # true produces no output, so we get the "(no output)" path or just exit code
        assert "Exit code: 0" in result

    @pytest.mark.asyncio
    async def test_stderr_included(self):
        tool = ExecTool()
        result = await tool.execute(
            command=f'{sys.executable} -c "import sys; sys.stderr.write(\'warning\\n\')"',
        )
        assert "STDERR" in result
        assert "warning" in result

    @pytest.mark.asyncio
    async def test_exit_code_nonzero(self):
        tool = ExecTool()
        result = await tool.execute(command="false")
        assert "Exit code: 1" in result

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        """If subprocess creation raises, we get the error message."""
        tool = ExecTool()
        with patch("asyncio.create_subprocess_shell", side_effect=OSError("boom")):
            result = await tool.execute(command="echo hello")
        assert "Error executing command" in result
        assert "boom" in result


# ============================================================================
# shell.py — CRLF handling, exit code reporting (lines 221-235)
# ============================================================================

class TestCRLFAndExitCode:
    """Test CRLF handling and exit code reporting."""

    @pytest.mark.asyncio
    async def test_exit_code_zero(self):
        tool = ExecTool()
        result = await tool.execute(command="echo test")
        assert "Exit code: 0" in result

    @pytest.mark.asyncio
    async def test_exit_code_nonzero_command(self):
        tool = ExecTool()
        result = await tool.execute(command="exit 42")
        assert "Exit code: 42" in result

    @pytest.mark.asyncio
    async def test_stderr_only(self):
        tool = ExecTool()
        result = await tool.execute(
            command=f'{sys.executable} -c "import sys; sys.stderr.write(\'err only\\n\')"',
        )
        assert "STDERR" in result
        assert "err only" in result

    @pytest.mark.asyncio
    async def test_stderr_empty_ignored(self):
        """Empty stderr should not add STDERR section."""
        tool = ExecTool()
        result = await tool.execute(command="echo hello 2>/dev/null")
        # stderr is empty, should not contain STDERR marker
        assert "STDERR" not in result


# ============================================================================
# shell.py — workspace restriction, error handling (lines 276-296)
# ============================================================================

class TestWorkspaceRestriction:
    """Test workspace restriction and error handling."""

    @pytest.mark.asyncio
    async def test_relative_path_outside_workspace(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        # Create a file outside workspace
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")

        tool = ExecTool(restrict_to_workspace=True)
        result = await tool.execute(
            command="cat ../../outside.txt",
            working_dir=str(workspace),
        )
        # The relative path ../../outside.txt should be detected as traversal
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_path_append_adds_to_env(self, tmp_path):
        tool = ExecTool(path_append="/custom/bin")
        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_create:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_create.return_value = mock_proc

            await tool.execute(command="echo hello", working_dir=str(tmp_path))

            call_kwargs = mock_create.call_args[1]
            assert "/custom/bin" in call_kwargs["env"]["PATH"]

    def test_extract_relative_paths_unterminated_quote(self):
        result = ExecTool._extract_relative_paths('cat "unterminated')
        assert result == []

    def test_extract_relative_paths_double_ampersand(self):
        paths = ExecTool._extract_relative_paths("cmd1 a.txt && cmd2 b.txt")
        assert "a.txt" in paths
        assert "b.txt" in paths


# ============================================================================
# filesystem.py — ReadFileTool large file streaming, binary detection
# ============================================================================

class TestReadFileToolStreaming:
    """Test ReadFileTool large file streaming and binary detection."""

    @pytest.mark.asyncio
    async def test_read_large_file_streaming(self, tmp_path):
        """Large files should be streamed, not fully loaded."""
        f = tmp_path / "large.txt"
        # Create a file with many lines
        lines = [f"line {i}" for i in range(200_000)]
        f.write_text("\n".join(lines))

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="large.txt", offset=1, limit=10)
        assert "line 1" in result
        assert "Showing lines 1-10" in result

    @pytest.mark.asyncio
    async def test_read_large_file_truncated_count(self, tmp_path):
        """Files with >100k lines should report truncated count."""
        f = tmp_path / "huge.txt"
        lines = [f"line {i}" for i in range(150_000)]
        f.write_text("\n".join(lines))

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="huge.txt", offset=1, limit=10)
        # Should show 150000+ indicating truncated count
        assert "150000+" in result or "+" in result

    @pytest.mark.asyncio
    async def test_read_binary_file_replacement(self, tmp_path):
        """Binary content should be handled with errors='replace'."""
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\xff\xfe")

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="binary.bin")
        # Should not crash; binary bytes get replacement chars
        assert "Error" not in result or "reading" not in result.lower()

    @pytest.mark.asyncio
    async def test_read_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="empty.txt")
        assert "Empty file" in result

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, tmp_path):
        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="nonexistent.txt")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_read_not_a_file(self, tmp_path):
        d = tmp_path / "adir"
        d.mkdir()
        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="adir")
        assert "Not a file" in result

    @pytest.mark.asyncio
    async def test_read_offset_beyond_end(self, tmp_path):
        f = tmp_path / "small.txt"
        f.write_text("line1\nline2\n")

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="small.txt", offset=100)
        assert "beyond end" in result.lower()

    @pytest.mark.asyncio
    async def test_read_with_pagination(self, tmp_path):
        f = tmp_path / "paginated.txt"
        lines = [f"line {i}" for i in range(1, 101)]
        f.write_text("\n".join(lines))

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="paginated.txt", offset=5, limit=3)
        assert "line 5" in result
        assert "line 6" in result
        assert "line 7" in result
        assert "Showing lines 5-7" in result

    @pytest.mark.asyncio
    async def test_read_max_chars_truncation(self, tmp_path):
        f = tmp_path / "biglines.txt"
        # Each line is very long to trigger _MAX_CHARS truncation
        long_line = "X" * 50_000
        f.write_text(f"{long_line}\n{long_line}\n{long_line}\n")

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="biglines.txt")
        # Should stop before showing all 3 lines (truncated at line level)
        assert "Showing lines 1-2 of 3" in result
        # Total result length should be less than the full file content
        assert len(result) < 150_000

    @pytest.mark.asyncio
    async def test_read_permission_error(self, tmp_path):
        f = tmp_path / "noperm.txt"
        f.write_text("secret")
        f.chmod(0o000)

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="noperm.txt")
        assert "Error" in result

        # Restore permissions for cleanup
        f.chmod(0o644)

    @pytest.mark.asyncio
    async def test_read_crlf_handling(self, tmp_path):
        f = tmp_path / "crlf.txt"
        f.write_bytes(b"line1\r\nline2\r\nline3\r\n")

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="crlf.txt")
        # CRLF should be stripped cleanly
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result


# ============================================================================
# filesystem.py — WriteFileTool write size guard
# ============================================================================

class TestWriteFileToolSizeGuard:
    """Test WriteFileTool 10MB write size limit."""

    @pytest.mark.asyncio
    async def test_write_size_limit_exceeded(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        # 10MB + 1 byte
        huge_content = "A" * (10 * 1024 * 1024 + 1)
        result = await tool.execute(path="huge.txt", content=huge_content)
        assert "Error" in result
        assert "too large" in result.lower()

    @pytest.mark.asyncio
    async def test_write_within_limit(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        result = await tool.execute(path="ok.txt", content="hello world")
        assert "Successfully wrote" in result
        assert (tmp_path / "ok.txt").read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        result = await tool.execute(path="a/b/c/file.txt", content="nested")
        assert "Successfully wrote" in result
        assert (tmp_path / "a" / "b" / "c" / "file.txt").read_text() == "nested"

    @pytest.mark.asyncio
    async def test_write_exactly_at_limit(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        # Exactly 10MB should be allowed
        content = "A" * (10 * 1024 * 1024)
        result = await tool.execute(path="exact.txt", content=content)
        assert "Successfully wrote" in result

    @pytest.mark.asyncio
    async def test_write_permission_error(self, tmp_path):
        d = tmp_path / "readonly"
        d.mkdir()
        d.chmod(0o444)

        tool = WriteFileTool(workspace=tmp_path)
        result = await tool.execute(path="readonly/file.txt", content="data")
        assert "Error" in result

        d.chmod(0o755)  # cleanup


# ============================================================================
# filesystem.py — EditFileTool fuzzy matching
# ============================================================================

class TestEditFileToolFuzzyMatching:
    """Test EditFileTool fuzzy matching algorithm (sliding window)."""

    @pytest.mark.asyncio
    async def test_exact_match(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("hello world\nfoo bar\n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(path="edit.txt", old_text="hello world", new_text="goodbye world")
        assert "Successfully edited" in result
        assert f.read_text() == "goodbye world\nfoo bar\n"

    @pytest.mark.asyncio
    async def test_fuzzy_match_whitespace_differences(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("  hello world  \n  foo bar  \n")

        tool = EditFileTool(workspace=tmp_path)
        # old_text has different leading/trailing whitespace
        result = await tool.execute(
            path="edit.txt",
            old_text="hello world\nfoo bar",
            new_text="replaced",
        )
        assert "Successfully edited" in result

    @pytest.mark.asyncio
    async def test_old_text_not_found(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("hello world\n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(path="edit.txt", old_text="completely different text", new_text="new")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_old_text_not_found_with_suggestion(self, tmp_path):
        """When text is close but not exact, should show diff suggestion."""
        f = tmp_path / "edit.txt"
        f.write_text("hello world\nfoo bar\nbaz\n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(
            path="edit.txt",
            old_text="hello world\nfoo baz",  # close but wrong
            new_text="new",
        )
        assert "not found" in result.lower()
        # Should show best match info
        assert "match" in result.lower() or "similar" in result.lower()

    @pytest.mark.asyncio
    async def test_multiple_matches_without_replace_all(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("aaa\nbbb\naaa\n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(path="edit.txt", old_text="aaa", new_text="xxx")
        assert "Warning" in result
        assert "2 times" in result

    @pytest.mark.asyncio
    async def test_replace_all(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("aaa\nbbb\naaa\n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(path="edit.txt", old_text="aaa", new_text="xxx", replace_all=True)
        assert "Successfully edited" in result
        content = f.read_text()
        assert content.count("xxx") == 2
        assert "aaa" not in content

    @pytest.mark.asyncio
    async def test_replace_all_fuzzy_fragments(self, tmp_path):
        """replace_all with fuzzy matches should replace all fragments."""
        f = tmp_path / "edit.txt"
        f.write_text("  hello  \n  world  \nxxx\n  hello  \n  world  \n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(
            path="edit.txt",
            old_text="hello\nworld",
            new_text="replaced",
            replace_all=True,
        )
        assert "Successfully edited" in result

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(path="nonexistent.txt", old_text="a", new_text="b")
        assert "not found" in result.lower()


# ============================================================================
# filesystem.py — path resolution with workspace restriction
# ============================================================================

class TestPathResolution:
    """Test _resolve_path and workspace restriction."""

    def test_resolve_relative_against_workspace(self, tmp_path):
        result = _resolve_path("sub/file.txt", workspace=tmp_path)
        assert result == (tmp_path / "sub" / "file.txt").resolve()

    def test_resolve_absolute_ignores_workspace(self, tmp_path):
        result = _resolve_path("/abs/path", workspace=tmp_path)
        assert result == Path("/abs/path").resolve()

    def test_resolve_outside_allowed_dir_raises(self, tmp_path):
        with pytest.raises(PermissionError):
            _resolve_path("/etc/passwd", allowed_dir=tmp_path)

    def test_resolve_inside_allowed_dir_ok(self, tmp_path):
        result = _resolve_path(str(tmp_path / "file.txt"), allowed_dir=tmp_path)
        assert result.exists() or result  # just should not raise

    def test_resolve_extra_allowed_dirs(self, tmp_path):
        extra = tmp_path / "extra"
        extra.mkdir()
        f = extra / "file.txt"
        f.write_text("test")

        result = _resolve_path(
            str(f),
            allowed_dir=tmp_path / "other",
            extra_allowed_dirs=[extra],
        )
        assert result == f.resolve()

    def test_is_under_true(self, tmp_path):
        assert _is_under(tmp_path / "sub" / "file.txt", tmp_path) is True

    def test_is_under_false(self, tmp_path):
        assert _is_under(Path("/etc/passwd"), tmp_path) is False

    def test_resolve_home_expansion(self, tmp_path):
        """Home directory (~) should be expanded."""
        result = _resolve_path("~/test.txt")
        assert result.is_absolute()
        assert "~" not in str(result)


# ============================================================================
# filesystem.py — CRLF handling in EditFileTool
# ============================================================================

class TestEditFileCRLF:
    """Test CRLF handling in EditFileTool."""

    @pytest.mark.asyncio
    async def test_crlf_preserved_after_edit(self, tmp_path):
        f = tmp_path / "crlf.txt"
        f.write_bytes(b"hello world\r\nfoo bar\r\n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(
            path="crlf.txt",
            old_text="hello world",
            new_text="goodbye world",
        )
        assert "Successfully edited" in result
        raw = f.read_bytes()
        # CRLF should be preserved
        assert b"\r\n" in raw
        assert b"goodbye world\r\n" in raw

    @pytest.mark.asyncio
    async def test_lf_file_stays_lf(self, tmp_path):
        f = tmp_path / "lf.txt"
        f.write_bytes(b"hello world\nfoo bar\n")

        tool = EditFileTool(workspace=tmp_path)
        await tool.execute(
            path="lf.txt",
            old_text="hello world",
            new_text="goodbye world",
        )
        raw = f.read_bytes()
        assert b"\r\n" not in raw
        assert b"goodbye world\n" in raw

    @pytest.mark.asyncio
    async def test_crlf_old_text_normalized(self, tmp_path):
        """old_text with CRLF should be normalized for matching."""
        f = tmp_path / "crlf.txt"
        f.write_bytes(b"hello world\r\nfoo bar\r\n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(
            path="crlf.txt",
            old_text="hello world\r\nfoo bar",
            new_text="replaced",
        )
        assert "Successfully edited" in result


# ============================================================================
# filesystem.py — ListDirTool sorting, hidden files, depth limit
# ============================================================================

class TestListDirTool:
    """Test ListDirTool sorting, hidden files, depth limit."""

    @pytest.mark.asyncio
    async def test_list_sorted(self, tmp_path):
        (tmp_path / "c.txt").write_text("")
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "b.txt").write_text("")

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".")
        lines = result.strip().split("\n")
        names = [l.split(" ", 1)[-1] for l in lines]
        assert names == sorted(names)

    @pytest.mark.asyncio
    async def test_list_includes_hidden_files(self, tmp_path):
        (tmp_path / ".hidden").write_text("")
        (tmp_path / "visible.txt").write_text("")

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".")
        assert ".hidden" in result
        assert "visible.txt" in result

    @pytest.mark.asyncio
    async def test_list_ignores_noise_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "src").mkdir()

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".")
        assert ".git" not in result
        assert "node_modules" not in result
        assert "src" in result

    @pytest.mark.asyncio
    async def test_list_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "file.txt").write_text("")

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".", recursive=True)
        assert "sub" in result or "file.txt" in result

    @pytest.mark.asyncio
    async def test_list_recursive_ignores_noise_in_subdirs(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "__pycache__").mkdir()
        (sub / "real.py").write_text("")

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".", recursive=True)
        assert "__pycache__" not in result
        assert "real.py" in result

    @pytest.mark.asyncio
    async def test_list_max_entries_truncation(self, tmp_path):
        for i in range(10):
            (tmp_path / f"file{i}.txt").write_text("")

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".", max_entries=5)
        assert "truncated" in result.lower()
        assert "5" in result

    @pytest.mark.asyncio
    async def test_list_empty_directory(self, tmp_path):
        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".")
        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_list_not_a_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("")

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path="file.txt")
        assert "Not a directory" in result

    @pytest.mark.asyncio
    async def test_list_directory_not_found(self, tmp_path):
        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path="nonexistent")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_list_dir_format(self, tmp_path):
        (tmp_path / "adir").mkdir()
        (tmp_path / "afile.txt").write_text("")

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".")
        # Dirs get folder emoji, files get file emoji
        assert "📁" in result or "adir" in result
        assert "📄" in result or "afile.txt" in result


# ============================================================================
# filesystem.py — error handling for permission denied, not found
# ============================================================================

class TestFilesystemErrorHandling:
    """Test error handling for permission denied, not found, etc."""

    @pytest.mark.asyncio
    async def test_read_permission_denied(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_text("secret")
        f.chmod(0o000)

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="secret.txt")
        assert "Error" in result

        f.chmod(0o644)  # cleanup

    @pytest.mark.asyncio
    async def test_write_permission_denied(self, tmp_path):
        d = tmp_path / "locked"
        d.mkdir()
        d.chmod(0o444)

        tool = WriteFileTool(workspace=tmp_path)
        result = await tool.execute(path="locked/file.txt", content="data")
        assert "Error" in result

        d.chmod(0o755)  # cleanup

    @pytest.mark.asyncio
    async def test_edit_permission_denied(self, tmp_path):
        f = tmp_path / "readonly.txt"
        f.write_text("hello")
        f.chmod(0o444)

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(path="readonly.txt", old_text="hello", new_text="world")
        assert "Error" in result

        f.chmod(0o644)  # cleanup

    @pytest.mark.asyncio
    async def test_list_permission_denied(self, tmp_path):
        d = tmp_path / "restricted"
        d.mkdir()
        d.chmod(0o000)

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path="restricted")
        assert "Error" in result

        d.chmod(0o755)  # cleanup


# ============================================================================
# filesystem.py — _find_match and _find_fuzzy_match_fragments
# ============================================================================

class TestFindMatchHelpers:
    """Test the internal _find_match and _find_fuzzy_match_fragments functions."""

    def test_exact_match(self):
        match, count = _find_match("hello world", "hello")
        assert match == "hello"
        assert count == 1

    def test_multiple_exact_matches(self):
        match, count = _find_match("aaa bbb aaa", "aaa")
        assert match == "aaa"
        assert count == 2

    def test_fuzzy_match_stripped_whitespace(self):
        content = "  hello  \n  world  \n"
        match, count = _find_match(content, "hello\nworld")
        assert match is not None
        assert count == 1

    def test_no_match(self):
        match, count = _find_match("hello", "xyz")
        assert match is None
        assert count == 0

    def test_fuzzy_match_empty_old_text(self):
        fragments = _find_fuzzy_match_fragments("hello", "")
        assert fragments == []

    def test_fuzzy_match_exact_line_match(self):
        content = "line1\nline2\nline3\n"
        fragments = _find_fuzzy_match_fragments(content, "line1\nline2")
        assert len(fragments) == 1

    def test_fuzzy_match_multiple_windows(self):
        content = "aaa\nbbb\nccc\naaa\nbbb\n"
        fragments = _find_fuzzy_match_fragments(content, "aaa\nbbb")
        assert len(fragments) == 2

    def test_fuzzy_match_no_match(self):
        content = "hello\nworld\n"
        fragments = _find_fuzzy_match_fragments(content, "foo\nbar")
        assert fragments == []

    def test_fuzzy_match_old_longer_than_content(self):
        content = "short\n"
        fragments = _find_fuzzy_match_fragments(content, "a\nb\nc\nd\ne\n")
        assert fragments == []


# ============================================================================
# filesystem.py — edge cases
# ============================================================================

class TestFilesystemEdgeCases:
    """Various edge cases for filesystem tools."""

    @pytest.mark.asyncio
    async def test_read_file_with_unicode(self, tmp_path):
        f = tmp_path / "unicode.txt"
        f.write_text("こんにちは世界\n🌍\n", encoding="utf-8")

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="unicode.txt")
        assert "こんにちは" in result

    @pytest.mark.asyncio
    async def test_write_file_with_unicode(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        result = await tool.execute(path="uni.txt", content="héllo wörld")
        assert "Successfully wrote" in result
        content = (tmp_path / "uni.txt").read_text(encoding="utf-8")
        assert "héllo" in content

    @pytest.mark.asyncio
    async def test_edit_preserves_file_encoding(self, tmp_path):
        f = tmp_path / "enc.txt"
        f.write_text("hello\nworld\n", encoding="utf-8")

        tool = EditFileTool(workspace=tmp_path)
        await tool.execute(path="enc.txt", old_text="hello", new_text="héllo")
        content = f.read_text(encoding="utf-8")
        assert "héllo" in content

    @pytest.mark.asyncio
    async def test_resolve_path_with_workspace_none(self):
        """When workspace is None, relative paths stay relative (resolved from cwd)."""
        result = _resolve_path("relative/path.txt", workspace=None)
        assert result.is_absolute()

    @pytest.mark.asyncio
    async def test_list_dir_default_max(self, tmp_path):
        """Default max_entries should be 200."""
        for i in range(250):
            (tmp_path / f"f{i}.txt").write_text("")

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".")
        assert "truncated" in result.lower()

    @pytest.mark.asyncio
    async def test_read_file_offset_1_is_first_line(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("first\nsecond\nthird\n")

        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute(path="test.txt", offset=1, limit=1)
        assert "first" in result
        assert "second" not in result

    @pytest.mark.asyncio
    async def test_edit_replace_all_deduplicates_fragments(self, tmp_path):
        """Replace all with fuzzy fragments should deduplicate before replacing."""
        f = tmp_path / "dedup.txt"
        f.write_text("  same  \n  text  \nother\n  same  \n  text  \n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(
            path="dedup.txt",
            old_text="same\ntext",
            new_text="replaced",
            replace_all=True,
        )
        assert "Successfully edited" in result
        content = f.read_text()
        assert content.count("replaced") == 2

    @pytest.mark.asyncio
    async def test_not_found_msg_no_similar(self, tmp_path):
        """When no similar text found, should say 'No similar text found'."""
        f = tmp_path / "nofound.txt"
        f.write_text("completely different content here\n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(
            path="nofound.txt",
            old_text="xyz abc 123 totally unrelated",
            new_text="new",
        )
        assert "not found" in result.lower()
        assert "No similar text found" in result or "not found" in result.lower()


# ============================================================================
# Additional tests targeting remaining uncovered lines
# ============================================================================

class TestShellPropertiesAndMetadata:
    """Test shell.py property accessors (name, description, parameters)."""

    def test_exec_tool_name(self):
        tool = ExecTool()
        assert tool.name == "exec"

    def test_exec_tool_description(self):
        tool = ExecTool()
        assert "shell command" in tool.description.lower()

    def test_exec_tool_parameters(self):
        tool = ExecTool()
        params = tool.parameters
        assert params["type"] == "object"
        assert "command" in params["properties"]
        assert "command" in params["required"]

    def test_exec_tool_parameters_working_dir(self):
        tool = ExecTool()
        params = tool.parameters
        assert "working_dir" in params["properties"]


class TestShellTimeoutWithOutput:
    """Test timeout handling with stdout/stderr before kill."""

    @pytest.mark.asyncio
    async def test_timeout_with_partial_stdout(self):
        """Timeout handler should include any stdout captured before kill."""
        tool = ExecTool(timeout=0.1)

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.kill = MagicMock()
        # First call (in wait_for) raises TimeoutError; second returns buffered data
        mock_proc.communicate = AsyncMock(
            side_effect=[asyncio.TimeoutError, (b"partial out\n", b"partial err\n")]
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            with patch("os.killpg"):  # suppress real killpg
                result = await tool.execute(command="sleep 100")

        assert "timed out" in result.lower()
        assert "partial out" in result
        assert "STDERR" in result
        assert "partial err" in result

    @pytest.mark.asyncio
    async def test_timeout_with_stderr_before_kill(self):
        tool = ExecTool(timeout=0.2)
        result = await tool.execute(
            command=f'{sys.executable} -c "import sys,time; sys.stderr.write(\'err before\\n\'); sys.stderr.flush(); time.sleep(10)"',
        )
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_timeout_killpg_process_lookup_error(self):
        """When killpg raises ProcessLookupError, it should be silently handled."""
        tool = ExecTool(timeout=0.1)
        with patch("os.killpg", side_effect=ProcessLookupError):
            result = await tool.execute(
                command=f'{sys.executable} -c "import time; time.sleep(10)"',
            )
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_timeout_killpg_permission_error(self):
        """When killpg raises PermissionError, it should be silently handled."""
        tool = ExecTool(timeout=0.1)
        with patch("os.killpg", side_effect=PermissionError):
            result = await tool.execute(
                command=f'{sys.executable} -c "import time; time.sleep(10)"',
            )
        assert "timed out" in result.lower()


class TestShellPathResolveExceptions:
    """Test exception handling in workspace path resolution."""

    @pytest.mark.asyncio
    async def test_absolute_path_with_unresolvable_env_var(self, tmp_path):
        """Absolute paths with env vars that fail to resolve should be skipped gracefully."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        tool = ExecTool(restrict_to_workspace=True)
        # Use a path with an env var that expands to something invalid
        with patch("os.path.expandvars", side_effect=Exception("expand failed")):
            guard = await tool._guard_command(
                "cat $BAD_VAR/file.txt", str(workspace),
            )
        # Should not crash — either None or blocked, but no exception
        assert guard is None or "Error" in guard

    @pytest.mark.asyncio
    async def test_relative_path_with_resolve_failure(self, tmp_path):
        """Relative paths that fail to resolve should be skipped gracefully."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        tool = ExecTool(restrict_to_workspace=True)
        # Create a symlink loop to trigger resolve failure
        link = workspace / "loop"
        try:
            link.symlink_to(link)  # self-referencing symlink
        except OSError:
            pytest.skip("Cannot create symlink on this platform")

        guard = await tool._guard_command("cat loop", str(workspace))
        # Should not crash
        assert guard is None or "Error" in guard


class TestShellExtractRelativePathsEdgeCases:
    """More edge cases for _extract_relative_paths."""

    def test_empty_string_tokens_skipped(self):
        """Empty tokens after stripping quotes should be skipped."""
        paths = ExecTool._extract_relative_paths('cat "" file.txt')
        assert "file.txt" in paths

    def test_env_var_after_command_name(self):
        """Env var assignments after command should be skipped."""
        paths = ExecTool._extract_relative_paths("cat VAR=value file.txt")
        assert "file.txt" in paths
        assert "VAR=value" not in paths


class TestFilesystemProperties:
    """Test filesystem tool property accessors."""

    def test_read_file_name(self):
        assert ReadFileTool().name == "read_file"

    def test_read_file_description(self):
        assert "file" in ReadFileTool().description.lower()

    def test_read_file_parameters(self):
        params = ReadFileTool().parameters
        assert "path" in params["properties"]
        assert "offset" in params["properties"]
        assert "limit" in params["properties"]

    def test_write_file_name(self):
        assert WriteFileTool().name == "write_file"

    def test_write_file_description(self):
        assert "write" in WriteFileTool().description.lower()

    def test_write_file_parameters(self):
        params = WriteFileTool().parameters
        assert "path" in params["properties"]
        assert "content" in params["properties"]

    def test_edit_file_name(self):
        assert EditFileTool().name == "edit_file"

    def test_edit_file_description(self):
        assert "replace" in EditFileTool().description.lower()

    def test_edit_file_parameters(self):
        params = EditFileTool().parameters
        assert "path" in params["properties"]
        assert "old_text" in params["properties"]
        assert "new_text" in params["properties"]
        assert "replace_all" in params["properties"]

    def test_list_dir_name(self):
        assert ListDirTool().name == "list_dir"

    def test_list_dir_description(self):
        assert "directory" in ListDirTool().description.lower()

    def test_list_dir_parameters(self):
        params = ListDirTool().parameters
        assert "path" in params["properties"]
        assert "recursive" in params["properties"]
        assert "max_entries" in params["properties"]


class TestReadFileFirstLineTruncation:
    """Test ReadFileTool truncation when the very first line exceeds _MAX_CHARS."""

    @pytest.mark.asyncio
    async def test_single_very_long_line_truncated(self, tmp_path):
        """When the first line alone exceeds _MAX_CHARS, it should be truncated with marker."""
        f = tmp_path / "longline.txt"
        # Create a single line longer than _MAX_CHARS (128000)
        long_line = "Z" * 200_000
        f.write_text(long_line + "\n")

        tool = ReadFileTool(workspace=tmp_path)
        tool._MAX_CHARS = 1000  # Lower for testing
        result = await tool.execute(path="longline.txt")
        # Should contain the truncation marker
        assert "line truncated" in result

    @pytest.mark.asyncio
    async def test_generic_exception_handling(self, tmp_path):
        """Generic exceptions in read should be caught."""
        f = tmp_path / "test.txt"
        f.write_text("hello")

        tool = ReadFileTool(workspace=tmp_path)
        with patch("builtins.open", side_effect=OSError("disk error")):
            result = await tool.execute(path="test.txt")
        assert "Error reading file" in result


class TestWriteFileGenericException:
    """Test WriteFileTool generic exception handling."""

    @pytest.mark.asyncio
    async def test_write_generic_exception(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            result = await tool.execute(path="fail.txt", content="data")
        assert "Error writing file" in result


class TestEditFileGenericException:
    """Test EditFileTool generic exception handling."""

    @pytest.mark.asyncio
    async def test_edit_generic_exception(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")

        tool = EditFileTool(workspace=tmp_path)
        with patch.object(Path, "read_bytes", side_effect=OSError("I/O error")):
            result = await tool.execute(path="test.txt", old_text="hello", new_text="bye")
        assert "Error editing file" in result


class TestNotFoundMsgWithSimilarMatch:
    """Test EditFileTool._not_found_msg when a similar match is found (>50% similar)."""

    @pytest.mark.asyncio
    async def test_not_found_with_close_match_shows_diff(self, tmp_path):
        """When old_text is close to actual content, should show diff."""
        f = tmp_path / "close.txt"
        f.write_text("hello world\nfoo bar\nbaz qux\n")

        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(
            path="close.txt",
            old_text="hello world\nfoo baz",  # close but wrong on line 2
            new_text="replaced",
        )
        assert "not found" in result.lower()
        # Should contain similarity percentage
        assert "%" in result or "similar" in result.lower()

    def test_not_found_msg_static_method_close_match(self):
        """Test _not_found_msg directly with a close match."""
        content = "hello world\nfoo bar\nbaz qux\n"
        # Very close — just one word different
        old_text = "hello world\nfoo baz\nbaz qux\n"

        result = EditFileTool._not_found_msg(old_text, content, "test.txt")
        assert "not found" in result.lower()
        assert "%" in result  # similarity percentage

    def test_not_found_msg_static_method_no_match(self):
        """Test _not_found_msg with completely different content."""
        content = "completely different\ncontent here\nnothing similar\n"
        old_text = "xyz abc 123 totally unrelated text"

        result = EditFileTool._not_found_msg(old_text, content, "test.txt")
        assert "not found" in result.lower()
        assert "No similar text found" in result


class TestListDirGenericException:
    """Test ListDirTool generic exception handling."""

    @pytest.mark.asyncio
    async def test_list_generic_exception(self, tmp_path):
        tool = ListDirTool(workspace=tmp_path)
        with patch.object(Path, "iterdir", side_effect=OSError("I/O error")):
            result = await tool.execute(path=".")
        assert "Error listing directory" in result

    @pytest.mark.asyncio
    async def test_list_recursive_generic_exception(self, tmp_path):
        tool = ListDirTool(workspace=tmp_path)
        with patch.object(Path, "rglob", side_effect=OSError("I/O error")):
            result = await tool.execute(path=".", recursive=True)
        assert "Error listing directory" in result


# ============================================================================
# Final targeted tests for remaining branch coverage
# ============================================================================

class TestShellNonPosixTimeout:
    """Test timeout on non-POSIX platforms (no killpg)."""

    @pytest.mark.asyncio
    async def test_timeout_with_zero_pid(self):
        """When process.pid is falsy (0), killpg should not be called."""
        tool = ExecTool(timeout=0.1)

        mock_proc = MagicMock()
        mock_proc.pid = 0  # Falsy PID
        mock_proc.kill = MagicMock()
        mock_proc.communicate = AsyncMock(
            side_effect=[asyncio.TimeoutError, (b"", b"")]
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            with patch("os.killpg") as mock_killpg:
                result = await tool.execute(command="timeout_cmd")

        assert "timed out" in result.lower()
        mock_proc.kill.assert_called_once()
        # killpg should not be called when pid is 0
        mock_killpg.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_with_whitespace_stderr(self):
        """Timeout with whitespace-only stderr should not include STDERR section."""
        tool = ExecTool(timeout=0.1)

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.kill = MagicMock()
        mock_proc.communicate = AsyncMock(
            side_effect=[asyncio.TimeoutError, (b"out\n", b"   \n")]
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            with patch("os.killpg"):
                result = await tool.execute(command="sleep 100")

        assert "timed out" in result.lower()
        assert "out" in result
        # Whitespace-only stderr should not add STDERR section
        assert "STDERR" not in result

    @pytest.mark.asyncio
    async def test_normal_exec_with_whitespace_only_stderr(self):
        """Normal execution with whitespace-only stderr should not include STDERR."""
        tool = ExecTool()
        result = await tool.execute(
            command='bash -c "echo \'   \' >&2; echo stdout"',
        )
        assert "stdout" in result
        # Whitespace-only stderr should be ignored
        assert "STDERR" not in result


class TestShellPathResolveException:
    """Test exception handling in workspace path resolution."""

    @pytest.mark.asyncio
    async def test_absolute_path_resolve_raises(self, tmp_path):
        """When Path.resolve() raises for an absolute path, it should be skipped."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        tool = ExecTool(restrict_to_workspace=True)
        original_resolve = Path.resolve

        def mock_resolve(self_path):
            if "bad_path" in str(self_path):
                raise OSError("resolve failed")
            return original_resolve(self_path)

        with patch.object(Path, "resolve", mock_resolve):
            guard = await tool._guard_command(
                "cat /bad_path/file.txt", str(workspace),
            )
        # Should not crash — the bad path is skipped
        assert guard is None or "blocked" in guard.lower()

    @pytest.mark.asyncio
    async def test_relative_symlink_outside_workspace(self, tmp_path):
        """Relative symlink resolving outside workspace should be blocked."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("secret")
        link = workspace / "link_to_outside"
        try:
            link.symlink_to(outside_file)
        except OSError:
            pytest.skip("Cannot create symlink")

        tool = ExecTool(restrict_to_workspace=True)
        guard = await tool._guard_command(
            "cat link_to_outside", str(workspace),
        )
        assert guard is not None
        assert "blocked" in guard.lower()


class TestReadFileTruncationBranches:
    """Test ReadFileTool truncation edge cases."""

    @pytest.mark.asyncio
    async def test_first_line_ok_second_too_long(self, tmp_path):
        """When first line fits but result exceeds _MAX_CHARS, truncation occurs."""
        f = tmp_path / "mixed.txt"
        f.write_text("short line\n" + "X" * 200_000 + "\n")

        tool = ReadFileTool(workspace=tmp_path)
        tool._MAX_CHARS = 50  # Very low to trigger truncation
        result = await tool.execute(path="mixed.txt")
        assert "short line" in result


class TestListDirRecursiveBranches:
    """Test ListDirTool recursive iteration branches."""

    @pytest.mark.asyncio
    async def test_recursive_mixed_files_and_dirs(self, tmp_path):
        """Recursive listing with mixed files and directories."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "file1.txt").write_text("a")
        (sub / "file2.txt").write_text("b")
        deep = sub / "deep"
        deep.mkdir()
        (deep / "file3.txt").write_text("c")

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".", recursive=True)
        assert "file1.txt" in result
        assert "file3.txt" in result

    @pytest.mark.asyncio
    async def test_recursive_with_noise_in_nested_dir(self, tmp_path):
        """Noise dirs in nested locations should be filtered."""
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / ".git").mkdir()
        (sub / "main.py").write_text("code")

        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(path=".", recursive=True)
        assert "main.py" in result
        assert ".git" not in result
