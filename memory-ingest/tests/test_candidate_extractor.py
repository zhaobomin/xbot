from datetime import datetime, timezone

from memory_ingest.config import ExtractConfig
from memory_ingest.extractors.candidate_extractor import CandidateExtractor
from memory_ingest.models import DocumentSection, ParsedDocument


def _document(text: str) -> ParsedDocument:
    return ParsedDocument(
        source_path="/tmp/test.md",
        doc_type="md",
        title="测试文档",
        sections=[DocumentSection(text=text)],
        modified_time=datetime.now(timezone.utc),
        content_hash="hash-1",
    )


def test_rules_extractor_rewrites_preference_to_atomic_memory() -> None:
    extractor = CandidateExtractor(
        ExtractConfig(
            model="kimi-k2.5",
            api_base="https://example.com/v1",
            api_key_env="MISSING",
        )
    )
    result = extractor.extract(
        _document("在技术选型上我偏向于python，TS和go，python和TS有更好的AI生态，go在部署上更加简洁，")
    )

    assert result.candidates[0].memory_text == "用户在技术选型上偏好 Python、TypeScript 和 Go。"


def test_rules_extractor_rewrites_problem_solving_style_to_atomic_memory() -> None:
    extractor = CandidateExtractor(
        ExtractConfig(
            model="kimi-k2.5",
            api_base="https://example.com/v1",
            api_key_env="MISSING",
        )
    )
    result = extractor.extract(
        _document("我注重逻辑思维，在解决问题之前，希望能先定义清楚问题，并尽量把目标量化，拆解任务，有计划的解决。")
    )

    assert result.candidates[0].memory_text == "用户解决问题时偏好先定义清楚问题、量化目标并拆解任务。"
