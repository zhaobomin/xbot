from datetime import datetime, timezone
from pathlib import Path

from memory_ingest.models import CandidateMemory, ReviewPacket
from memory_ingest.review_packet import parse_review_packet, render_review_packet, validate_review_decisions


def test_render_and_parse_review_packet_round_trip(tmp_path: Path) -> None:
    packet = ReviewPacket(
        packet_id="packet-1",
        generated_at=datetime(2026, 4, 2, tzinfo=timezone.utc),
        mem0_user_id="aliu",
        mem0_app_id="memory-ingest",
        candidates=[
            CandidateMemory(
                memory_text="用户偏好 Python 和 TS。",
                memory_type="preference",
                confidence=0.9,
                why_it_matters="这是稳定技术偏好。",
                tags=["preference"],
                source_path="/tmp/a.md",
                source_title="技术选型",
                source_chunk_id="chunk-1",
                fingerprint="fp-1",
                metadata={"doc_type": "md", "content_hash": "hash-1"},
            )
        ],
    )
    packet_path = tmp_path / "packet.md"
    packet_path.write_text(render_review_packet(packet), encoding="utf-8")

    updated = packet_path.read_text(encoding="utf-8").replace("Status: pending", "Status: approve")
    packet_path.write_text(updated, encoding="utf-8")

    parsed, decisions = parse_review_packet(packet_path)
    counts = validate_review_decisions(decisions)

    assert parsed.packet_id == "packet-1"
    assert parsed.candidates[0].why_it_matters == "这是稳定技术偏好。"
    assert parsed.candidates[0].metadata["content_hash"] == "hash-1"
    assert counts["approve"] == 1


def test_parse_review_packet_supports_multiline_edit(tmp_path: Path) -> None:
    packet_path = tmp_path / "packet.md"
    packet_path.write_text(
        """# Memory Review Packet

Packet ID: packet-2
Generated At: 2026-04-02T00:00:00+00:00
Mem0 User ID: aliu
Mem0 App ID: memory-ingest

## Candidate 1
Fingerprint: `fp-2`
Source Path: `/tmp/b.md`
Source Chunk ID: `chunk-2`
Doc Type: `md`
Content Hash: `hash-2`
Type: `rule`
Confidence: `0.80`
Tags: `rule`

### Source Title
工作方式

### Proposed Memory
原始候选

### Why It Matters
适合长期记忆。

### Review
Status: edit
Edited Memory:
修改后的记忆第一句。
修改后的记忆第二句。
""",
        encoding="utf-8",
    )

    _, decisions = parse_review_packet(packet_path)
    counts = validate_review_decisions(decisions)

    assert counts["edit"] == 1
    assert decisions[0].edited_memory == "修改后的记忆第一句。\n修改后的记忆第二句。"
