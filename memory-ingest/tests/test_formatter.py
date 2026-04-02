from memory_ingest.formatter import format_query_pretty
from memory_ingest.models import MemoryQueryResponse, MemoryRelation, MemorySearchResult


def test_format_query_pretty_includes_results_and_relations() -> None:
    payload = MemoryQueryResponse(
        query="用户偏好什么技术栈？",
        user_id="xbot-global",
        app_id="memory-ingest",
        enable_graph=True,
        results=[
            MemorySearchResult(
                memory="用户偏好 Python",
                score=0.91,
                categories=["preference"],
                metadata={"source_path": "/tmp/a.md", "title": "A"},
            )
        ],
        relations=[
            MemoryRelation(
                source="user",
                relationship="prefers",
                target="Python",
                score=0.88,
            )
        ],
    )

    text = format_query_pretty(payload)

    assert "用户偏好 Python" in text
    assert "user --prefers--> Python" in text
