from datetime import datetime, timezone

from memory_ingest.models import CandidateMemory, ScannedFile
from memory_ingest.state_store import StateStore


def test_state_store_tracks_hashes_and_fingerprints(tmp_path) -> None:
    store = StateStore(str(tmp_path / "state.db"))
    scanned = ScannedFile(
        path="/tmp/a.md",
        doc_type="md",
        modified_time=datetime.now(timezone.utc),
        content_hash="hash-a",
    )
    candidate = CandidateMemory(
        memory_text="喜欢乌龙茶",
        memory_type="preference",
        confidence=0.9,
        tags=["preference"],
        source_path="/tmp/a.md",
        source_title="A",
        source_chunk_id="chunk-1",
        fingerprint="fp-1",
        metadata={},
    )

    assert store.should_process(scanned) is True
    store.mark_scanned(scanned, "imported:1")
    assert store.should_process(scanned) is False
    assert store.has_fingerprint("fp-1") is False
    store.record_import(candidate, "remote-1")
    assert store.has_fingerprint("fp-1") is True
    store.close()
