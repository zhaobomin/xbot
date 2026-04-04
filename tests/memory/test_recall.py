from pathlib import Path

from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.recall.selector import select_relevant_memories


def test_recall_selector_skips_single_word_queries(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    store.create_memory(
        memory_type="reference",
        title="Latency dashboard",
        description="dashboard for request latency",
        body="dash",
    )

    selected = select_relevant_memories("latency", store.scan_headers())

    assert selected == []


def test_recall_selector_limits_results_and_prefers_description_matches(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    for idx in range(8):
        store.create_memory(
            memory_type="reference",
            title=f"Dashboard {idx}",
            description=f"latency dashboard {idx}",
            body=f"dashboard {idx}",
        )

    selected = select_relevant_memories("show me the latency dashboard to check request path issues", store.scan_headers())

    assert len(selected) == 5
    assert all("dashboard" in item.filename.lower() for item in selected)
