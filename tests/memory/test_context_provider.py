from pathlib import Path

from xbot.memory.integration.context_provider import MemoryContextProvider
from xbot.memory.memdir.store import MemoryDirStore


def test_context_provider_renders_instruction_index_and_relevant_memory(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("root instruction", encoding="utf-8")
    rules_dir = tmp_path / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "python.md").write_text(
        "---\npaths: src/**/*.py\n---\npython rule",
        encoding="utf-8",
    )

    store = MemoryDirStore(tmp_path)
    store.create_memory(
        memory_type="reference",
        title="Latency dashboard",
        description="Grafana latency dashboard for request path work",
        body="grafana/internal/d/api-latency",
    )

    provider = MemoryContextProvider(tmp_path, memory_store=store)
    fragments = provider.build_fragments(
        user_message="show me the latency dashboard for request path work",
        file_paths=["src/app.py"],
    )

    assert "root instruction" in fragments.instructions
    assert "python rule" in fragments.instructions
    assert "Latency dashboard" in fragments.memory_index
    assert "Grafana latency dashboard" in fragments.relevant_memories
    assert "api-latency" in fragments.relevant_memories


def test_context_provider_dedupes_surfaced_memories(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    path = store.create_memory(
        memory_type="reference",
        title="Latency dashboard",
        description="Grafana latency dashboard for request path work",
        body="grafana/internal/d/api-latency",
    )
    provider = MemoryContextProvider(tmp_path, memory_store=store)

    first = provider.build_fragments("latency dashboard", [])
    second = provider.build_fragments("latency dashboard", [])

    assert path.name in first.relevant_memories
    assert second.relevant_memories == ""
