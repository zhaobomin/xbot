from pathlib import Path

from xbot.memory.memdir.store import MemoryDirStore


def test_memdir_store_creates_topic_file_and_updates_index(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)

    path = store.create_memory(
        memory_type="feedback",
        title="Use rg for search",
        description="Prefer rg over grep for repository search",
        body="Use rg for search.\n\n**Why:** Faster.\n**How to apply:** Use rg before grep.",
    )

    assert path.exists()
    assert path.parent.name == "feedback"
    content = path.read_text(encoding="utf-8")
    assert "type: feedback" in content
    index = (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "[Use rg for search]" in index
    assert "Prefer rg over grep" in index


def test_memdir_store_updates_existing_topic_and_refreshes_index(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    path = store.create_memory(
        memory_type="project",
        title="Release freeze",
        description="Release freeze begins 2026-04-05",
        body="Release freeze begins 2026-04-05.\n\n**Why:** Release cut.\n**How to apply:** Avoid risky merges.",
    )

    store.update_memory(
        path,
        body="Release freeze begins 2026-04-05.\n\n**Why:** Mobile release cut.\n**How to apply:** Avoid risky merges after 2026-04-05.",
        description="Release freeze begins 2026-04-05 for mobile release cut",
    )

    content = path.read_text(encoding="utf-8")
    assert "mobile release cut" in content
    index = (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "mobile release cut" in index


def test_memdir_scan_headers_excludes_index_and_sorts_newest_first(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    older = store.create_memory(
        memory_type="user",
        title="Backend expert",
        description="User is experienced in backend systems",
        body="User is experienced in backend systems.",
    )
    newer = store.create_memory(
        memory_type="reference",
        title="Latency dashboard",
        description="Grafana latency dashboard for request path work",
        body="grafana/internal latency dashboard",
    )

    headers = store.scan_headers()

    assert all(header.filename != "MEMORY.md" for header in headers)
    assert headers[0].file_path == newer
    assert headers[1].file_path == older


def test_memdir_store_truncates_oversized_index_with_warning(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    for idx in range(205):
        store.create_memory(
            memory_type="reference",
            title=f"Ref {idx}",
            description=f"Description {idx}",
            body=f"reference {idx}",
        )

    rendered = store.load_index_for_prompt()

    assert "WARNING" in rendered
    assert "Only part of it was loaded" in rendered
