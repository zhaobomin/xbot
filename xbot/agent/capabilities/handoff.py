"""Claude SDK handoff observability helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HandoffAgentPolicy:
    name: str
    description: str
    when: str
    prompt: str


class HandoffPolicy:
    """Helpers for observing SDK-native handoff/task events."""

    def __init__(self, agents: dict[str, Any] | None):
        self._agents = agents or {}

    def has_agents(self) -> bool:
        return bool(self._agents)

    def list_agents(self) -> list[HandoffAgentPolicy]:
        items: list[HandoffAgentPolicy] = []
        for name, definition in self._agents.items():
            if isinstance(definition, dict):
                description = str(definition.get("description", "")).strip()
                when = str(definition.get("when", "")).strip()
                prompt = str(definition.get("prompt", "")).strip()
            else:
                description = definition.description.strip()
                when = getattr(definition, "when", "").strip()
                prompt = definition.prompt.strip()
            items.append(
                HandoffAgentPolicy(
                    name=name,
                    description=description,
                    when=when,
                    prompt=prompt,
                )
            )
        return items

    def classify_task_event(self, description: str = "", task_type: str | None = None) -> str | None:
        haystack = f"{task_type or ''} {description}".lower()
        if "handoff" in haystack or "subagent" in haystack:
            return "handoff"
        for agent in self.list_agents():
            if agent.name.lower() in haystack:
                return "handoff"
        return None

    def format_task_trace(self, description: str, task_type: str | None = None) -> str | None:
        if self.classify_task_event(description, task_type) != "handoff":
            return None
        return f"Handoff: {description}" if description else "Handoff: delegated task"
