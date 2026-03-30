from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

IGNORED_STDOUT_PREFIXES = (
    "WARNING: proceeding, even though we could not update PATH:",
)
IGNORED_FOLLOWUP_PREFIXES = (
    "Caused by:",
    "Operation not permitted (os error 1)",
)
IGNORED_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "thread.updated",
    "turn.updated",
    "turn.completed",
}
IGNORED_LOG_MARKERS = (
    "codex_core::models_manager::cache:",
    "codex_state::runtime:",
    "codex_core::state_db:",
    "codex_core::shell_snapshot:",
    "codex_rmcp_client::rmcp_client:",
    "codex_rmcp_client::oauth:",
)


@dataclass(frozen=True)
class CodexEvent:
    type: str
    content: str = ""
    delta: str = ""


class CodexTransport:
    def __init__(self, binary_path: str, env: dict[str, str] | None = None):
        self.binary_path = binary_path
        self.env = env or {}
        self._running_processes: dict[str, asyncio.subprocess.Process] = {}

    async def run_prompt(
        self,
        session_key: str,
        prompt: str,
        *,
        model: str | None,
        mode: str | None,
        profile: str | None,
        workdir: str,
    ) -> AsyncIterator[CodexEvent]:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        for key in ("HOME", "CODEX_HOME"):
            value = self.env.get(key)
            if value:
                Path(value).mkdir(parents=True, exist_ok=True)
        argv = [self.binary_path, "exec", "--json", "--skip-git-repo-check", prompt]
        if model:
            argv.extend(["--model", model])
        if mode:
            if mode == "full-auto":
                argv.append("--full-auto")
            elif mode == "dangerous":
                argv.append("--dangerously-bypass-approvals-and-sandbox")
            else:
                argv.extend(["-c", f"approval_policy={mode}"])
        if profile:
            argv.extend(["--profile", profile])
        env = os.environ.copy()
        env.update(self.env)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=workdir,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            yield CodexEvent(type="error", content=f"Codex binary not found: {self.binary_path}")
            return
        self._running_processes[session_key] = process
        try:
            async for event in self.read_events(process):
                yield event
        finally:
            self._running_processes.pop(session_key, None)
            await process.wait()

    async def read_events(self, process: Any) -> AsyncIterator[CodexEvent]:
        auth_failure = False
        mcp_auth_failure = False
        suppress_followups = False
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                suppress_followups = False
                continue
            if text.startswith(IGNORED_STDOUT_PREFIXES):
                continue
            if "AuthRequired(AuthRequiredError" in text or "Missing or invalid access token" in text:
                mcp_auth_failure = True
                suppress_followups = True
                continue
            if "invalid_grant" in text or "TokenRefreshFailed" in text:
                auth_failure = True
                suppress_followups = True
                continue
            if any(marker in text for marker in IGNORED_LOG_MARKERS):
                if "codex_rmcp_client::oauth:" in text:
                    mcp_auth_failure = True
                suppress_followups = True
                continue
            if suppress_followups and text.startswith(IGNORED_FOLLOWUP_PREFIXES):
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                suppress_followups = False
                yield CodexEvent(type="message.delta", delta=text)
                continue

            event_type = str(payload.get("type", "message.delta"))
            suppress_followups = False
            if event_type in IGNORED_EVENT_TYPES:
                continue
            if event_type == "message.final":
                yield CodexEvent(type=event_type, content=str(payload.get("content", "")))
            elif event_type == "message.delta":
                yield CodexEvent(type=event_type, delta=str(payload.get("delta", "")))
            elif event_type == "item.completed":
                item = payload.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    text_content = item.get("text")
                    if isinstance(text_content, str) and text_content.strip():
                        yield CodexEvent(type="message.final", content=text_content)
            else:
                if "delta" in payload:
                    yield CodexEvent(type=event_type, delta=str(payload.get("delta", "")))
                else:
                    yield CodexEvent(type=event_type, content=str(payload.get("content", text)))
        returncode = await process.wait()
        if mcp_auth_failure:
            yield CodexEvent(
                type="error",
                content="Codex MCP authentication failed for the service environment. Remove MCP entries from the service Codex config or re-authenticate them for this service account.",
            )
        elif auth_failure:
            yield CodexEvent(
                type="error",
                content="Codex authentication failed: invalid refresh token. Re-run `codex login` for the service account.",
            )
        elif returncode not in (0, None):
            yield CodexEvent(type="error", content=f"Codex exited with status {returncode}")

    async def interrupt(self, session_key: str) -> bool:
        process = self._running_processes.get(session_key)
        if process is None:
            return False
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        finally:
            self._running_processes.pop(session_key, None)
        return True

    async def close(self) -> None:
        for session_key in list(self._running_processes.keys()):
            await self.interrupt(session_key)
