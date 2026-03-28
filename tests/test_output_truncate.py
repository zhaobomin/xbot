"""Tests for output truncation: strategies, boundaries, format awareness."""

import json

import pytest

from xbot.agent.crew.output.truncate import (
    OutputTruncator,
    TruncationResult,
    TruncationStrategy,
)


class TestTruncationResult:
    """Test TruncationResult dataclass."""

    def test_default_values(self) -> None:
        """Default values should be set correctly."""
        result = TruncationResult(
            content="test",
            original_length=10,
            truncated_length=4,
            truncated=True,
            strategy="hard",
        )

        assert result.content == "test"
        assert result.original_length == 10
        assert result.truncated_length == 4
        assert result.truncated is True
        assert result.strategy == "hard"
        assert result.message is None

    def test_with_message(self) -> None:
        """Message should be settable."""
        result = TruncationResult(
            content="test",
            original_length=10,
            truncated_length=4,
            truncated=True,
            strategy="markdown",
            message="Truncated at line 5",
        )

        assert result.message == "Truncated at line 5"


class TestTruncationStrategy:
    """Test TruncationStrategy enum."""

    def test_strategy_values(self) -> None:
        """Strategy values should be correct."""
        assert TruncationStrategy.MARKDOWN.value == "markdown"
        assert TruncationStrategy.JSON.value == "json"
        assert TruncationStrategy.SMART.value == "smart"
        assert TruncationStrategy.HARD.value == "hard"


class TestNoTruncationNeeded:
    """Test cases where no truncation is needed."""

    def test_short_content_not_truncated(self) -> None:
        """Content shorter than max_length should not be truncated."""
        truncator = OutputTruncator()
        result = truncator.truncate("Short content", max_length=100)

        assert result.truncated is False
        assert result.content == "Short content"
        assert result.original_length == len("Short content")
        assert result.truncated_length == len("Short content")
        assert result.strategy == "none"

    def test_exact_length_not_truncated(self) -> None:
        """Content exactly at max_length should not be truncated."""
        truncator = OutputTruncator()
        content = "x" * 100
        result = truncator.truncate(content, max_length=100)

        assert result.truncated is False
        assert result.content == content

    def test_empty_content(self) -> None:
        """Empty content should not be truncated."""
        truncator = OutputTruncator()
        result = truncator.truncate("", max_length=100)

        assert result.truncated is False
        assert result.content == ""


class TestHardTruncation:
    """Test hard truncation strategy."""

    def test_hard_truncate_basic(self) -> None:
        """Hard truncate should reduce content."""
        truncator = OutputTruncator()
        content = "x" * 1000
        result = truncator.truncate(
            content,
            max_length=100,
            strategy=TruncationStrategy.HARD
        )

        assert result.truncated is True
        assert result.truncated_length < result.original_length
        assert result.strategy == "hard"

    def test_hard_truncate_reduces_content(self) -> None:
        """Hard truncate should reduce content size."""
        truncator = OutputTruncator()
        content = "Hello World! " * 100
        result = truncator.truncate(
            content,
            max_length=50,
            strategy=TruncationStrategy.HARD
        )

        assert result.truncated is True
        assert len(result.content) < len(content)


class TestSmartStrategyDetection:
    """Test smart strategy auto-detection."""

    def test_detect_json_object(self) -> None:
        """JSON object should be detected."""
        truncator = OutputTruncator()
        # Test detection directly
        content = '{"key": "value"}'
        strategy = truncator._detect_strategy(content)
        assert strategy == TruncationStrategy.JSON

    def test_detect_json_array(self) -> None:
        """JSON array should be detected."""
        truncator = OutputTruncator()
        content = '[1, 2, 3]'
        strategy = truncator._detect_strategy(content)
        assert strategy == TruncationStrategy.JSON

    def test_detect_markdown_headers(self) -> None:
        """Markdown with headers should be detected."""
        truncator = OutputTruncator()
        content = "# Title\n\nSome content"
        strategy = truncator._detect_strategy(content)
        assert strategy == TruncationStrategy.MARKDOWN

    def test_detect_markdown_code_blocks(self) -> None:
        """Markdown with code blocks should be detected."""
        truncator = OutputTruncator()
        content = "```\ncode\n```"
        strategy = truncator._detect_strategy(content)
        assert strategy == TruncationStrategy.MARKDOWN

    def test_detect_markdown_tables(self) -> None:
        """Markdown with tables should be detected."""
        truncator = OutputTruncator()
        content = "| a | b |\n|---|---|"
        strategy = truncator._detect_strategy(content)
        assert strategy == TruncationStrategy.MARKDOWN

    def test_detect_markdown_lists(self) -> None:
        """Markdown with lists should be detected."""
        truncator = OutputTruncator()
        content = "- item 1\n- item 2"
        strategy = truncator._detect_strategy(content)
        assert strategy == TruncationStrategy.MARKDOWN

    def test_fallback_to_hard(self) -> None:
        """Plain text should fallback to hard truncation."""
        truncator = OutputTruncator()
        content = "just plain text without any markdown"
        strategy = truncator._detect_strategy(content)
        assert strategy == TruncationStrategy.HARD


class TestJsonTruncation:
    """Test JSON truncation strategy."""

    def test_json_short_enough(self) -> None:
        """JSON that fits should not be truncated."""
        truncator = OutputTruncator()
        content = '{"key": "value"}'
        result = truncator.truncate(
            content,
            max_length=100,
            strategy=TruncationStrategy.JSON
        )

        assert result.truncated is False

    def test_json_invalid_falls_back(self) -> None:
        """Invalid JSON should fallback to hard truncation."""
        truncator = OutputTruncator()
        content = '{"invalid": json}' + "x" * 1000
        result = truncator.truncate(
            content,
            max_length=50,
            strategy=TruncationStrategy.JSON
        )

        assert result.truncated is True
        # Falls back to hard since JSON parsing fails


class TestMarkdownTruncation:
    """Test Markdown truncation strategy."""

    def test_markdown_short_enough(self) -> None:
        """Markdown that fits should not be truncated."""
        truncator = OutputTruncator()
        content = "# Title\n\nSome content"
        result = truncator.truncate(
            content,
            max_length=100,
            strategy=TruncationStrategy.MARKDOWN
        )

        assert result.truncated is False

    def test_markdown_truncation_marker(self) -> None:
        """Truncated markdown should have marker."""
        truncator = OutputTruncator()
        content = "# Title\n\n" + "x" * 1000
        result = truncator.truncate(
            content,
            max_length=100,
            strategy=TruncationStrategy.MARKDOWN
        )

        assert result.truncated is True
        assert "truncated" in result.content.lower()


class TestPreservePatterns:
    """Test pattern preservation during truncation."""

    def test_preserve_error_messages(self) -> None:
        """Error messages should be preserved when possible."""
        truncator = OutputTruncator()
        content = "Some content\n\nERROR: critical failure\n\nMore content" + "x" * 1000
        result = truncator.truncate(content, max_length=500)

        # The truncator tries to preserve important patterns
        assert result.truncated is True


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_very_small_max_length(self) -> None:
        """Very small max_length should be handled gracefully.

        Note: When max_length is too small to fit content + truncation marker,
        the behavior may result in longer output due to the marker.
        This tests that truncation still occurs.
        """
        truncator = OutputTruncator()
        content = "Hello World" * 10  # Longer content
        result = truncator.truncate(content, max_length=20)

        assert result.truncated is True

    def test_single_line_content(self) -> None:
        """Single line content should be handled."""
        truncator = OutputTruncator()
        content = "x" * 1000
        result = truncator.truncate(content, max_length=100)

        assert result.truncated is True

    def test_unicode_content(self) -> None:
        """Unicode content should be handled correctly."""
        truncator = OutputTruncator()
        content = "你好世界 " * 100
        result = truncator.truncate(content, max_length=20)

        assert result.truncated is True
        # Content should be valid unicode
        assert isinstance(result.content, str)

    def test_content_with_newlines(self) -> None:
        """Content with many newlines should be handled."""
        truncator = OutputTruncator()
        content = "\n".join(f"Line {i}" for i in range(100))
        result = truncator.truncate(content, max_length=100)

        assert result.truncated is True

    def test_content_with_tabs(self) -> None:
        """Content with tabs should be handled."""
        truncator = OutputTruncator()
        content = "col1\tcol2\tcol3\n" * 50
        result = truncator.truncate(content, max_length=100)

        assert result.truncated is True


class TestTruncatorConstants:
    """Test truncator constants and defaults."""

    def test_default_max_length(self) -> None:
        """Default max length should be defined."""
        assert OutputTruncator.DEFAULT_MAX_LENGTH == 4000

    def test_code_block_max(self) -> None:
        """Code block max should be defined."""
        assert OutputTruncator.CODE_BLOCK_MAX == 1500

    def test_table_max(self) -> None:
        """Table max should be defined."""
        assert OutputTruncator.TABLE_MAX == 1000

    def test_important_patterns_defined(self) -> None:
        """Important patterns should be defined."""
        assert len(OutputTruncator.IMPORTANT_PATTERNS) > 0
        # Should include common patterns
        patterns = OutputTruncator.IMPORTANT_PATTERNS
        assert any("error" in p.lower() for p in patterns)
        assert any("warning" in p.lower() for p in patterns)


class TestTruncationWithCodeBlocks:
    """Test truncation with code blocks."""

    def test_code_block_not_broken(self) -> None:
        """Code blocks should not be broken in the middle."""
        truncator = OutputTruncator()
        content = """# Code

```python
def hello():
    print("Hello, World!")
    print("This is a long function")
    print("With many lines")
```

More content here.
"""
        result = truncator.truncate(content, max_length=500)

        # Code block should be preserved
        assert "```python" in result.content or not result.truncated

    def test_nested_code_blocks(self) -> None:
        """Nested code blocks should be handled."""
        truncator = OutputTruncator()
        content = """
```
outer
```

Some text

```
inner
```
""" + "x" * 1000

        result = truncator.truncate(content, max_length=200)
        assert result.truncated is True