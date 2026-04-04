from xbot.memory.workers.auto_dream_prompt import build_auto_dream_prompt
from xbot.memory.workers.extract_prompts import build_extract_memories_prompt


def test_extract_prompt_contains_claude_style_constraints() -> None:
    prompt = build_extract_memories_prompt(
        new_message_count=7,
        existing_memories="- [Rule](feedback/rule.md) - existing rule",
    )

    assert "memory extraction subagent" in prompt
    assert "You MUST only use content from the last ~7 messages" in prompt
    assert "Do not waste any turns attempting to investigate or verify that content further" in prompt
    assert "If the user explicitly asks you to remember something" in prompt
    assert "If they ask you to forget something" in prompt


def test_auto_dream_prompt_contains_four_phases() -> None:
    prompt = build_auto_dream_prompt("/tmp/memory", "/tmp/sessions", extra="extra context")

    assert "Phase 1 — Orient" in prompt
    assert "Phase 2 — Gather recent signal" in prompt
    assert "Phase 3 — Maintain" in prompt
    assert "Phase 4 — Prune and index" in prompt
    assert "grep -rn" in prompt
    assert "extra context" in prompt
