"""Tests for CJK (Chinese) memory recall and index path rendering."""
from __future__ import annotations

from pathlib import Path

from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.models import MemoryHeader
from xbot.memory.recall.selector import _extract_cjk_bigrams, select_relevant_memories


# ---------------------------------------------------------------------------
# _extract_cjk_bigrams
# ---------------------------------------------------------------------------

class TestExtractCjkBigrams:
    def test_empty_string(self) -> None:
        assert _extract_cjk_bigrams("") == set()

    def test_ascii_only(self) -> None:
        assert _extract_cjk_bigrams("hello world") == set()

    def test_single_chinese_char(self) -> None:
        # Single char -> no bigrams
        assert _extract_cjk_bigrams("我") == set()

    def test_two_chinese_chars(self) -> None:
        assert _extract_cjk_bigrams("阿六") == {"阿六"}

    def test_chinese_sentence(self) -> None:
        bigrams = _extract_cjk_bigrams("还记得我是谁吗")
        assert "还记" in bigrams
        assert "记得" in bigrams
        assert "得我" in bigrams
        assert "是谁" in bigrams
        assert "谁吗" in bigrams

    def test_mixed_ascii_and_cjk(self) -> None:
        bigrams = _extract_cjk_bigrams("用户阿六 dashboard")
        assert "用户" in bigrams
        assert "户阿" in bigrams
        assert "阿六" in bigrams
        # No ASCII bigrams
        assert not any(c.isascii() for bg in bigrams for c in bg)

    def test_strips_cjk_punctuation(self) -> None:
        bigrams = _extract_cjk_bigrams("你好，世界！")
        # Comma and exclamation are stripped
        assert "你好" in bigrams
        assert "世界" in bigrams


# ---------------------------------------------------------------------------
# select_relevant_memories - CJK matching
# ---------------------------------------------------------------------------

def _make_header(
    name: str,
    description: str,
    filename: str = "test.md",
    file_path: Path | None = None,
    memory_type: str = "project",
) -> MemoryHeader:
    return MemoryHeader(
        filename=filename,
        file_path=file_path or Path(f"/tmp/memory/{filename}"),
        mtime_ms=1000.0,
        name=name,
        description=description,
        memory_type=memory_type,
    )


class TestSelectRelevantMemoriesCjk:
    def test_chinese_query_matches_chinese_description(self) -> None:
        headers = [
            _make_header("用户信息", "阿六老板的个人信息和偏好设置", "user-info.md"),
        ]
        result = select_relevant_memories("还记得阿六是谁吗", headers)
        assert len(result) == 1
        assert result[0].name == "用户信息"

    def test_chinese_query_matches_name(self) -> None:
        headers = [
            _make_header("项目配置", "xbot项目的主要配置", "project-config.md"),
        ]
        result = select_relevant_memories("项目配置在哪里", headers)
        assert len(result) == 1

    def test_chinese_query_no_match(self) -> None:
        headers = [
            _make_header("Dashboard", "latency monitoring", "dashboard.md"),
        ]
        result = select_relevant_memories("用户偏好设置", headers)
        assert not any(h.name == "Dashboard" for h in result)

    def test_single_chinese_char_returns_empty(self) -> None:
        """Single char generates no bigrams -> returns empty (no user-type headers)."""
        headers = [
            _make_header("用户信息", "阿六的信息", "user.md"),
        ]
        result = select_relevant_memories("六", headers)
        assert len(result) == 0

    def test_two_char_chinese_query_works(self) -> None:
        headers = [
            _make_header("用户信息", "阿六的个人信息", "user.md"),
        ]
        result = select_relevant_memories("阿六", headers)
        assert len(result) == 1

    def test_mixed_chinese_ascii_query(self) -> None:
        headers = [
            _make_header("项目状态", "xbot project status and 部署记录", "project.md"),
        ]
        # "xbot" (ASCII, len>2) + "部署" (CJK bigram) both match
        result = select_relevant_memories("xbot 部署状态", headers)
        assert len(result) == 1

    def test_single_ascii_word_still_rejected(self) -> None:
        """Backward compat: single ASCII word -> returns empty."""
        headers = [
            _make_header("Dashboard", "latency dashboard", "dashboard.md"),
        ]
        result = select_relevant_memories("latency", headers)
        assert len(result) == 0

    def test_header_name_included_in_haystack(self) -> None:
        """header.name was missing from the old haystack, now included."""
        headers = [
            _make_header(
                name="Release Freeze",
                description="",
                filename="release.md",
            ),
        ]
        result = select_relevant_memories("release freeze please", headers)
        assert len(result) == 1

    def test_scores_prefer_higher_match_count(self) -> None:
        headers = [
            _make_header("低匹配", "包含阿六", "low.md"),
            _make_header("高匹配", "阿六老板的偏好设置", "high.md"),
        ]
        result = select_relevant_memories("阿六老板的偏好", headers)
        # Filter out any always-surfaced user-type headers
        non_user = [h for h in result if h.memory_type != "user"]
        assert non_user[0].name == "高匹配"


# ---------------------------------------------------------------------------
# user-type memories always surfaced
# ---------------------------------------------------------------------------

class TestUserTypeAlwaysSurfaced:
    def test_user_type_returned_even_without_keyword_match(self) -> None:
        """user-type memories should always be returned regardless of query."""
        headers = [
            _make_header("用户信息", "阿六老板的个人信息", "profile.md", memory_type="user"),
            _make_header("项目状态", "xbot deployment", "project.md"),
        ]
        result = select_relevant_memories("今天天气怎么样", headers)
        assert any(h.name == "用户信息" for h in result)

    def test_user_type_returned_on_empty_query(self) -> None:
        """Empty query still returns user-type headers."""
        headers = [
            _make_header("偏好设置", "输出格式", "prefs.md", memory_type="user"),
        ]
        result = select_relevant_memories("", headers)
        assert len(result) == 1

    def test_user_type_plus_keyword_match(self) -> None:
        """user-type always included + keyword-matched project headers."""
        headers = [
            _make_header("用户信息", "阿六", "profile.md", memory_type="user"),
            _make_header("xbot 项目", "xbot项目配置", "xbot.md"),
            _make_header("天气预报", "北京今日天气", "weather.md"),
        ]
        result = select_relevant_memories("xbot 项目配置", headers)
        names = [h.name for h in result]
        assert "用户信息" in names
        assert "xbot 项目" in names
        assert "天气预报" not in names

    def test_user_type_not_duplicated_when_also_keyword_matched(self) -> None:
        """If a user-type header also matches keywords, it should appear only once."""
        headers = [
            _make_header("用户信息", "阿六老板的偏好", "profile.md", memory_type="user"),
        ]
        result = select_relevant_memories("阿六老板", headers)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Index path rendering for root-level files
# ---------------------------------------------------------------------------

class TestIndexPathRendering:
    def test_root_level_file_has_correct_path(self, tmp_path: Path) -> None:
        """Files at memory root should have simple filename paths, not memory/filename."""
        store = MemoryDirStore(tmp_path)
        # Create a file directly in the memory root (not in a type subdirectory)
        root_file = store.memory_dir / "HISTORY.md"
        root_file.write_text(
            "---\nname: History\ntype: project\ndescription: Chat history\n---\n\nSome history.\n",
            encoding="utf-8",
        )
        store.rebuild_index()
        index = store.index_path.read_text(encoding="utf-8")
        # Path should be "HISTORY.md", NOT "memory/HISTORY.md"
        assert "(HISTORY.md)" in index
        assert "(memory/HISTORY.md)" not in index

    def test_subdirectory_file_has_correct_path(self, tmp_path: Path) -> None:
        """Files in type subdirectories should keep their type prefix."""
        store = MemoryDirStore(tmp_path)
        store.create_memory("project", "Test Topic", "A test", "Body text")
        index = store.index_path.read_text(encoding="utf-8")
        assert "(project/test-topic.md)" in index

    def test_root_level_file_is_readable_via_index_path(self, tmp_path: Path) -> None:
        """The path shown in the index should be resolvable by read_memory."""
        store = MemoryDirStore(tmp_path)
        root_file = store.memory_dir / "INFO.md"
        root_file.write_text(
            "---\nname: Info\ntype: reference\ndescription: General info\n---\n\nSome info.\n",
            encoding="utf-8",
        )
        store.rebuild_index()
        # Simulate what the agent does: read the path from the index
        doc = store.read_memory(Path("INFO.md"))
        assert doc.name == "Info"
        assert "Some info." in doc.body
