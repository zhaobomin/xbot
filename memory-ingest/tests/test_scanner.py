from pathlib import Path

from memory_ingest.config import SourcesConfig
from memory_ingest.scanner import scan_sources


def test_scan_sources_finds_supported_files(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("# Title\nbody", encoding="utf-8")
    (tmp_path / "ignore.bin").write_bytes(b"nope")

    config = SourcesConfig(
        directories=[str(tmp_path)],
        include_globs=["**/*.md"],
        exclude_globs=[],
    )

    results = scan_sources(config)

    assert len(results) == 1
    assert results[0].doc_type == "md"
    assert results[0].path.endswith("notes.md")
