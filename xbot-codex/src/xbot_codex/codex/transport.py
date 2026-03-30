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
    "thread.updated",
    "turn.updated",
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
    phase: str = ""
    tool_name: str = ""
    tool_summary: str = ""
    raw_event_type: str = ""


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
        pending_agent_message: str | None = None

        def tool_output_summary(text: str, limit: int = 800) -> str:
            normalized = text.strip()
            if not normalized:
                return ""
            if len(normalized) <= limit:
                return normalized
            return normalized[:limit].rstrip() + "\n...[truncated]"

        async def flush_pending(as_final: bool) -> AsyncIterator[CodexEvent]:
            nonlocal pending_agent_message
            if pending_agent_message and pending_agent_message.strip():
                text = pending_agent_message.strip()
                pending_agent_message = None
                if as_final:
                    yield CodexEvent(
                        type="message.final",
                        content=text,
                        phase="completed",
                        raw_event_type="item.completed",
                    )
                else:
                    yield CodexEvent(
                        type="thought",
                        content=text,
                        phase="thinking",
                        raw_event_type="item.completed",
                    )

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
            if event_type == "thread.started":
                async for event in flush_pending(as_final=False):
                    yield event
                yield CodexEvent(
                    type="phase.started",
                    content="Codex session started.",
                    phase="session",
                    raw_event_type=event_type,
                )
            elif event_type == "turn.started":
                async for event in flush_pending(as_final=False):
                    yield event
                yield CodexEvent(
                    type="phase.updated",
                    content="Codex is thinking.",
                    phase="thinking",
                    raw_event_type=event_type,
                )
            elif event_type == "turn.completed":
                async for event in flush_pending(as_final=True):
                    yield event
                yield CodexEvent(
                    type="phase.updated",
                    content="Codex finished this turn.",
                    phase="completed",
                    raw_event_type=event_type,
                )
            elif event_type == "message.final":
                async for event in flush_pending(as_final=False):
                    yield event
                yield CodexEvent(
                    type=event_type,
                    content=str(payload.get("content", "")),
                    phase="completed",
                    raw_event_type=event_type,
                )
            elif event_type == "message.delta":
                yield CodexEvent(type=event_type, delta=str(payload.get("delta", "")))
            elif event_type in {"error", "warning", "status"}:
                async for event in flush_pending(as_final=False):
                    yield event
                content = payload.get("message", payload.get("content", ""))
                yield CodexEvent(type=event_type, content=str(content), raw_event_type=event_type)
            elif event_type == "item.started":
                item = payload.get("item")
                if isinstance(item, dict) and item.get("type") == "command_execution":
                    async for event in flush_pending(as_final=False):
                        yield event
                    command = str(item.get("command", "")).strip()
                    yield CodexEvent(
                        type="tool.started",
                        content=f"Running command: {command}" if command else "Running command.",
                        phase="executing",
                        tool_name="command_execution",
                        tool_summary=command,
                        raw_event_type=event_type,
                    )
            elif event_type == "item.completed":
                item = payload.get("item")
                if isinstance(item, dict):
                    item_type = str(item.get("type", ""))
                    if item_type == "agent_message":
                        text_content = item.get("text")
                        if isinstance(text_content, str) and text_content.strip():
                            async for event in flush_pending(as_final=False):
                                yield event
                            pending_agent_message = text_content
                    elif item_type == "command_execution":
                        async for event in flush_pending(as_final=False):
                            yield event
                        command = str(item.get("command", "")).strip()
                        exit_code = item.get("exit_code")
                        status = str(item.get("status", "")).strip()
                        output = tool_output_summary(str(item.get("aggregated_output", "")))
                        suffix: list[str] = []
                        if status:
                            suffix.append(status)
                        if exit_code is not None:
                            suffix.append(f"exit={exit_code}")
                        suffix_text = f" ({', '.join(suffix)})" if suffix else ""
                        content = f"Finished command: {command}{suffix_text}" if command else f"Finished command{suffix_text}"
                        if output:
                            content = f"{content}\n{output}"
                        yield CodexEvent(
                            type="tool.finished",
                            content=content,
                            phase="executing",
                            tool_name="command_execution",
                            tool_summary=command,
                            raw_event_type=event_type,
                        )
            else:
                async for event in flush_pending(as_final=False):
                    yield event
                if "delta" in payload:
                    yield CodexEvent(type=event_type, delta=str(payload.get("delta", "")), raw_event_type=event_type)
                else:
                    yield CodexEvent(type=event_type, content=str(payload.get("content", text)), raw_event_type=event_type)
        async for event in flush_pending(as_final=True):
            yield event
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
