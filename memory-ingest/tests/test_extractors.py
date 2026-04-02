from datetime import datetime, timezone

from memory_ingest.config import ExtractConfig
from memory_ingest.extractors import CandidateExtractor
from memory_ingest.models import DocumentSection, ParsedDocument


def test_rule_extractor_keeps_long_term_memory_and_drops_todo() -> None:
    document = ParsedDocument(
        source_path="/tmp/demo.md",
        doc_type="md",
        title="Demo",
        sections=[
            DocumentSection(text="用户偏好喝乌龙茶。今天待办是整理报价。团队规则是所有对外方案必须先复核。"),
        ],
        modified_time=datetime.now(timezone.utc),
        content_hash="abc123",
    )
    extractor = CandidateExtractor(
        ExtractConfig(
            model="kimi-k2.5",
            provider="openai_compatible",
            api_base="https://example.com/v1",
            api_key_env="MISSING_KEY",
            max_chunk_chars=4000,
            min_confidence=0.72,
        )
    )

    result = extractor.extract(document)

    texts = [item.memory_text for item in result.candidates]
    assert any("乌龙茶" in text for text in texts)
    assert any("必须先复核" in text for text in texts)
    assert all("待办" not in text for text in texts)
    assert result.mode == "rules"
