"""Shell execution tool."""

import asyncio
import os
import re
import shlex
import signal
from pathlib import Path
from typing import Any

from xbot.tools.base import Tool


class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(
        self,
        working_dir: str | Path | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        path_append: str = "",
        timeout: float = 60.0,
    ):
        self.working_dir = Path(working_dir) if working_dir else None
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[a-z]*r[a-z]*\b",      # rm -r, rm -rf, rm -rfv, rm -rfi
            r"\brm\b[^\n;&|]*--(?:recursive|force)\b",  # GNU long options
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"(?:^|[;&|]\s*)format\b",       # format (as standalone command only)
            r"\b(mkfs|diskpart)\b",          # disk operations
            r"\bdd\b[^\n;&|]*(?:\bif=|\bof=)",  # dd read/write disk images
            r">\s*/dev/sd",                  # write to disk
            r"\btee\b[^\n;&|]*/dev/",        # tee writing to device nodes
            r"\b(nc|netcat|socat|nmap)\b",   # high-risk network tooling
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "exec"

    _MAX_OUTPUT = 10_000
    _HEX_ESCAPE_RE = re.compile(r"\\x([0-9a-fA-F]{2})")
    _UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
    _OCTAL_ESCAPE_RE = re.compile(r"\\([0-7]{1,3})")

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command",
                },
            },
            "required": ["command"],
        }

    async def execute(
        self, command: str, working_dir: str | Path | None = None, **kwargs: Any,
    ) -> str:
        cwd = Path(working_dir) if working_dir else self.working_dir or Path.cwd()
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error

        env = os.environ.copy()
        if self.path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + self.path_append

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                # Become a new process-group leader so a timeout can kill the
                # whole group (piped/background children included) instead of
                # orphaning them. No-op semantics on Windows.
                start_new_session=True,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                # Kill the entire process group so piped/background children
                # don't survive as orphans; fall back to killing just the
                # shell PID on non-POSIX platforms.
                if os.name == "posix" and process.pid:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                process.kill()
                stdout, stderr = await process.communicate()
                output_parts = []
                if stdout:
                    output_parts.append(stdout.decode("utf-8", errors="replace"))
                if stderr:
                    stderr_text = stderr.decode("utf-8", errors="replace")
                    if stderr_text.strip():
                        output_parts.append(f"STDERR:\n{stderr_text}")
                output_parts.append(f"Error: command timed out after {self.timeout:g}s")
                return "\n".join(output_parts)

            output_parts = []

            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            # Head + tail truncation to preserve both start and end of output
            max_len = self._MAX_OUTPUT
            if len(result) > max_len:
                half = max_len // 2
                result = (
                    result[:half]
                    + f"\n\n... ({len(result) - max_len:,} chars truncated) ...\n\n"
                    + result[-half:]
                )

            return result

        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _guard_command(self, command: str, cwd: str | Path) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()
        normalized_candidates = self._normalized_command_candidates(lower)

        for pattern in self.deny_patterns:
            if any(re.search(pattern, candidate) for candidate in normalized_candidates):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        # Block common shell obfuscation vectors used to hide blocked commands.
        if self._contains_obfuscated_shell_text(lower):
            return "Error: Command blocked by safety guard (obfuscated shell text detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        from xbot.platform.security.network import contains_internal_url
        if contains_internal_url(cmd):
            return "Error: Command blocked by safety guard (internal/private URL detected)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            for raw in self._extract_absolute_paths(cmd):
                try:
                    expanded = os.path.expandvars(raw.strip())
                    p = Path(expanded).expanduser().resolve()
                except Exception:
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

            for raw in self._extract_relative_paths(cmd):
                try:
                    expanded = os.path.expandvars(raw.strip())
                    candidate = Path(expanded).expanduser()
                    if candidate.is_absolute():
                        continue
                    candidate_in_cwd = cwd_path / candidate
                    if not candidate_in_cwd.exists() and "/" not in expanded and "\\" not in expanded:
                        continue
                    resolved = candidate_in_cwd.resolve()
                except Exception:
                    continue
                if cwd_path not in resolved.parents and resolved != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    def _normalized_command_candidates(self, lower_command: str) -> list[str]:
        """Return normalized variants for deny pattern matching."""
        candidates = {lower_command}

        decoded = self._decode_escapes(lower_command)
        if decoded != lower_command:
            candidates.add(decoded)

        # Decode ANSI-C quoted fragments: $'...'
        for fragment in re.findall(r"\$'([^']+)'", lower_command):
            decoded_fragment = self._decode_escapes(fragment)
            if decoded_fragment:
                candidates.add(lower_command.replace(fragment, decoded_fragment))
                candidates.add(decoded_fragment)

        return list(candidates)

    def _decode_escapes(self, text: str) -> str:
        text = self._HEX_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
        text = self._UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)

        def _decode_octal(match: re.Match[str]) -> str:
            value = int(match.group(1), 8)
            if value > 0x10FFFF:
                return match.group(0)
            return chr(value)

        return self._OCTAL_ESCAPE_RE.sub(_decode_octal, text)

    @staticmethod
    def _contains_obfuscated_shell_text(lower_command: str) -> bool:
        # ANSI-C strings like $'\x72\x6d'
        if re.search(r"\$'[^']*\\x[0-9a-f]{2}", lower_command):
            return True
        # Command substitution with escaped byte construction
        if re.search(r"\$\([^)]*\\x[0-9a-f]{2}[^)]*\)", lower_command):
            return True
        # printf + escaped bytes often used to construct blocked binaries at runtime
        if re.search(r"\bprintf\b[^\n;&|]*\\x[0-9a-f]{2}", lower_command):
            return True
        return False

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]+", command)   # Windows: C:\...
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command) # POSIX: /absolute only
        home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command) # POSIX/Windows home shortcut: ~
        return win_paths + posix_paths + home_paths

    @staticmethod
    def _extract_relative_paths(command: str) -> list[str]:
        try:
            tokens = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            # shlex fails on shell metacharacters/syntax errors; fall back to a
            # naive split so restrict_to_workspace can still extract relative
            # paths instead of silently skipping validation.
            tokens = command.split()

        candidates: list[str] = []
        separators = {"|", "||", "&&", ";"}
        redirections = {">", ">>", "<", "2>", "2>>"}
        command_name: str | None = None
        positional_index = 0
        expect_redirection_target = False

        for token in tokens:
            stripped = token.strip("\"'")
            if not stripped:
                continue
            if stripped in separators:
                command_name = None
                positional_index = 0
                expect_redirection_target = False
                continue
            if stripped in redirections:
                expect_redirection_target = True
                continue
            if command_name is None:
                command_name = os.path.basename(stripped)
                positional_index = 0
                continue
            if stripped.startswith("-"):
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*", stripped):
                continue
            if stripped.startswith("$(") or stripped.startswith("`"):
                continue
            if stripped.startswith("~"):
                continue
            if command_name in {"grep", "egrep", "fgrep", "rg"} and positional_index == 0 and not expect_redirection_target:
                positional_index += 1
                continue
            candidates.append(stripped)
            positional_index += 1
            expect_redirection_target = False
        return candidates
