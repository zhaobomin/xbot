from pathlib import Path

from memory_ingest.config import load_config
from memory_ingest.models import CandidateMemory
from memory_ingest.service import IngestService


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "memory-ingest.toml"
    cfg.write_text(
        f"""
[sources]
directories = ["{tmp_path.as_posix()}"]
include_globs = ["**/*.md"]
exclude_globs = []

[mem0]
host = "https://api.mem0.ai"
api_key_env = "MEM0_API_KEY"
user_id = "xbot-global"
app_id = "memory-ingest"
timeout_seconds = 30

[extract]
model = "kimi-k2.5"
provider = "openai_compatible"
api_base = "https://example.com/v1"
api_key_env = "MISSING_KEY"
max_chunk_chars = 4000
min_confidence = 0.72

[dedup]
enabled = true
fingerprint_similarity = 0.9

[state]
sqlite_path = "{(tmp_path / 'state.db').as_posix()}"

[logging]
level = "INFO"
log_path = "{(tmp_path / 'memory-ingest.log').as_posix()}"
""",
        encoding="utf-8",
    )
    return cfg


def test_service_run_dry_run_does_not_mark_scanned(tmp_path: Path) -> None:
    (tmp_path / "memory.md").write_text("# 规则\n团队规则是所有对外方案必须先复核。", encoding="utf-8")
    cfg_path = _write_config(tmp_path)
    config = load_config(cfg_path)
    service = IngestService(config)
    try:
        first = service.run(dry_run=True)
        second = service.run(dry_run=True)
    finally:
        service.close()

    assert first.scanned_files == 1
    assert first.imported_candidates >= 1
    assert second.skipped_files == 0
    assert second.parsed_files == 1


def test_service_query_uses_mem0_client(tmp_path: Path, monkeypatch) -> None:
    cfg_path = _write_config(tmp_path)
    monkeypatch.setenv("MEM0_API_KEY", "m0-test")
    config = load_config(cfg_path)
    service = IngestService(config)

    class _FakeClient:
        def search(self, query: str, *, top_k: int = 5, enable_graph: bool = True):
            return {
                "query": query,
                "top_k": top_k,
                "enable_graph": enable_graph,
            }

        def close(self) -> None:
            return None

    service.client = _FakeClient()
    try:
        result = service.query("测试查询", top_k=7, enable_graph=True)
    finally:
        service.close()

    assert result == {"query": "测试查询", "top_k": 7, "enable_graph": True}


def test_service_apply_review_packet_imports_only_approved_items(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path)
    config = load_config(cfg_path)
    service = IngestService(config)
    packet_path = tmp_path / "packet.md"
    packet_path.write_text(
        """# Memory Review Packet

Packet ID: packet-3
Generated At: 2026-04-02T00:00:00+00:00
Mem0 User ID: xbot-global
Mem0 App ID: memory-ingest

## Candidate 1
Fingerprint: `fp-approve`
Source Path: `/tmp/a.md`
Source Chunk ID: `chunk-1`
Doc Type: `md`
Content Hash: `hash-1`
Type: `preference`
Confidence: `0.90`
Tags: `preference`

### Source Title
标题A

### Proposed Memory
用户偏好 Python。

### Why It Matters
稳定偏好。

### Review
Status: approve
Edited Memory:

## Candidate 2
Fingerprint: `fp-edit`
Source Path: `/tmp/a.md`
Source Chunk ID: `chunk-2`
Doc Type: `md`
Content Hash: `hash-1`
Type: `rule`
Confidence: `0.80`
Tags: `rule`

### Source Title
标题A

### Proposed Memory
原始规则。

### Why It Matters
稳定规则。

### Review
Status: edit
Edited Memory:
修改后的规则。

## Candidate 3
Fingerprint: `fp-reject`
Source Path: `/tmp/a.md`
Source Chunk ID: `chunk-3`
Doc Type: `md`
Content Hash: `hash-1`
Type: `fact`
Confidence: `0.70`
Tags: `-`

### Source Title
标题A

### Proposed Memory
不需要导入。

### Why It Matters
一般。

### Review
Status: reject
Edited Memory:
""",
        encoding="utf-8",
    )

    imported: list[CandidateMemory] = []

    class _FakeClient:
        def add_memory(self, candidate: CandidateMemory):
            imported.append(candidate)
            return f"remote-{candidate.fingerprint}"

        def close(self) -> None:
            return None

    service.client = _FakeClient()
    try:
        summary = service.apply_review_packet(packet_path)
    finally:
        service.close()

    assert summary.approved == 1
    assert summary.edited == 1
    assert summary.rejected == 1
    assert summary.imported == 2
    assert [item.memory_text for item in imported] == ["用户偏好 Python。", "修改后的规则。"]
