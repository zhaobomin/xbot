"""Claude SDK handoff policy helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class HandoffAgentPolicy:
    name: str
    description: str
    when: str
    prompt: str


@dataclass(frozen=True)
class HandoffDecision:
    mode: str
    reason: str
    candidate_agents: tuple[str, ...] = ()


class HandoffPolicy:
    """Product policy for main-thread work and SDK-native delegation."""

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

    def build_system_section(self) -> str:
        if not self.has_agents():
            return ""

        lines = [
            "## Delegation Policy",
            "",
            "- Stay on the main agent for routine requests, lightweight tool use, and normal follow-up chat.",
            "- Use native agent handoff when one specialist agent is a clear fit and the user expects the result in this reply.",
            "- Use SDK-native background task delegation for parallel or long-running work, or when the user explicitly wants async progress.",
            "- If handoff is unnecessary or fails, continue on the main agent with the normal tools instead of blocking.",
            "",
            "### Specialist Agents",
        ]
        for agent in self.list_agents():
            summary = agent.description or "Specialized support agent."
            lines.append(f"- `{agent.name}`: {summary}")
            if agent.when:
                lines.append(f"  Use when: {agent.when}")
        return "\n".join(lines)

    def build_agent_prompt(self, name: str, base_prompt: str) -> str:
        policy_lines = [
            "You are a specialist agent invoked by the main xbot agent.",
            "Stay narrowly scoped to the delegated task.",
            "Return useful results to the main agent, not channel-facing chatter.",
            "If the task is small enough for the main agent, keep the answer concise and hand control back quickly.",
        ]
        combined = [base_prompt.strip()] if base_prompt.strip() else []
        combined.append("\n".join(policy_lines))
        return "\n\n".join(combined)

    def build_activation_trace(self) -> str:
        agents = ", ".join(agent.name for agent in self.list_agents())
        return f"Running: delegation policy active ({agents})"

    def decide(self, prompt: str) -> HandoffDecision:
        text = prompt.lower()
        if any(token in text for token in ("后台", "background", "async", "asynchronously", "稍后")):
            return HandoffDecision(
                mode="background",
                reason="background cues detected",
            )

        candidates: list[str] = []
        for agent in self.list_agents():
            haystacks = [agent.name.lower(), agent.description.lower(), agent.when.lower()]
            if any(self._matches(text, haystack) for haystack in haystacks if haystack):
                candidates.append(agent.name)

        if candidates:
            return HandoffDecision(
                mode="native_handoff",
                reason="specialist agent matched request",
                candidate_agents=tuple(candidates),
            )

        return HandoffDecision(
            mode="main",
            reason="no specialist agent strongly matched request",
        )

    @staticmethod
    def _matches(text: str, haystack: str) -> bool:
        tokens = [token for token in re.split(r"[^a-z0-9_一-龥]+", haystack) if len(token) >= 3]
        return any(token in text for token in tokens)

    def build_decision_trace(self, decision: HandoffDecision) -> str:
        candidates = f" ({', '.join(decision.candidate_agents)})" if decision.candidate_agents else ""
        return f"Running: handoff policy decided {decision.mode}{candidates} - {decision.reason}"

    def build_request_prefix(self, decision: HandoffDecision) -> str:
        if decision.mode == "native_handoff":
            candidates = ", ".join(decision.candidate_agents) or "a specialist agent"
            return (
                "[Runtime Policy]\n"
                f"Prefer native handoff if one of these specialist agents clearly fits: {candidates}.\n"
                "If handoff is not needed, continue on the main agent.\n"
            )
        if decision.mode == "background":
            return (
                "[Runtime Policy]\n"
                "Prefer handling this with SDK-native background task delegation when feasible.\n"
                "Avoid native handoff unless background execution is clearly unsuitable.\n"
            )
        return (
            "[Runtime Policy]\n"
            "Keep this request on the main agent unless a handoff is absolutely necessary.\n"
        )

    def build_fallback_trace(self, reason: str) -> str:
        return f"Handoff: fallback to main agent ({reason})"

    def classify_task_event(self, description: str = "", task_type: str | None = None) -> str | None:
        haystack = f"{task_type or ''} {description}".lower()
        if "handoff" in haystack or "subagent" in haystack or "agent" in haystack:
            return "handoff"
        for agent in self.list_agents():
            if agent.name.lower() in haystack:
                return "handoff"
        return None

    def format_task_trace(self, description: str, task_type: str | None = None) -> str | None:
        if self.classify_task_event(description, task_type) != "handoff":
            return None
        return f"Handoff: {description}" if description else "Handoff: delegated task"
