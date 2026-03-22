"""Unified formatting helpers for runtime-visible SDK events."""

from __future__ import annotations

from typing import Any


def format_compact_event(
    *,
    pre_tokens: int | None,
    post_tokens: int | None,
    trigger: str | None = None,
) -> str:
    """Format compact boundary event text."""
    if isinstance(pre_tokens, int) and isinstance(post_tokens, int):
        saved_tokens = pre_tokens - post_tokens
        trigger_text = f" ({trigger})" if trigger else ""
        return (
            f"Context compacted{trigger_text}: "
            f"{pre_tokens:,} -> {post_tokens:,} tokens "
            f"(saved ~{saved_tokens:,})."
        )
    return "Context compacted."


def format_task_notification(
    *,
    status: str | None,
    summary: str | None,
    task_id: str | None = None,
    output_file: str | None = None,
) -> str:
    """Format task notification status text."""
    status_label = {
        "completed": "Task completed",
        "failed": "Task failed",
        "stopped": "Task stopped",
    }.get((status or "").lower(), "Task update")
    detail = summary or status or ""
    suffix = f": {detail}" if detail else ""
    extra = []
    if task_id:
        extra.append(f"id={task_id}")
    if output_file:
        extra.append(f"output={output_file}")
    tail = f" ({', '.join(extra)})" if extra else ""
    return f"{status_label}{suffix}{tail}"


def format_usage_summary(usage: dict[str, Any] | None) -> str | None:
    """Format token usage summary for CLI/channel progress."""
    if not usage:
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        return f"Usage: input {input_tokens:,} tokens, output {output_tokens:,} tokens"
    return None

