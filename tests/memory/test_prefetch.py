from pathlib import Path

from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.recall.prefetch import RelevantMemoryMatcher


def test_prefetch_collects_ready_relevant_memories(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    store.create_memory(
        memory_type="reference",
        title="Latency dashboard",
        description="Grafana latency dashboard for request path work",
        body="grafana/internal/d/api-latency",
    )

    matcher = RelevantMemoryMatcher(store)
    matcher.select("show me the latency dashboard for request path work")
    selected = matcher.collect_ready()

    assert len(selected) == 1
    assert selected[0].name == "Latency dashboard"
