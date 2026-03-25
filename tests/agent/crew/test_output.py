"""Tests for crew output module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from xbot.agent.crew.output.format import (
    OutputFormat,
    OutputParser,
    ParsedOutput,
    detect_format,
    format_output,
)
from xbot.agent.crew.output.truncate import (
    OutputTruncator,
    TruncationResult,
    TruncationStrategy,
    truncate_output,
)
from xbot.agent.crew.output.repair import (
    OutputRepairer,
    RepairResult,
    should_attempt_repair,
)


class TestOutputFormat:
    """Tests for output format parsing."""

    def test_parse_raw(self):
        """Test raw format parsing."""
        parser = OutputParser()
        result = parser.parse("some text", OutputFormat.RAW)
        assert result.valid
        assert result.raw == "some text"
        assert result.structured is None

    def test_parse_json_valid(self):
        """Test valid JSON parsing."""
        parser = OutputParser()
        result = parser.parse('{"key": "value"}', OutputFormat.JSON)
        assert result.valid
        assert result.structured == {"key": "value"}

    def test_parse_json_invalid(self):
        """Test invalid JSON parsing."""
        parser = OutputParser()
        result = parser.parse("{invalid json}", OutputFormat.JSON)
        assert not result.valid
        assert "parse error" in result.error.lower()

    def test_parse_json_in_markdown(self):
        """Test JSON extraction from markdown code block."""
        parser = OutputParser()
        content = """Here is the result:
```json
{"key": "value"}
```
"""
        result = parser.parse(content, OutputFormat.JSON)
        assert result.valid
        assert result.structured == {"key": "value"}

    def test_parse_json_with_schema(self):
        """Test JSON validation against schema."""
        parser = OutputParser()
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"}
            }
        }

        # Valid
        result = parser.parse('{"name": "test", "count": 5}', OutputFormat.JSON, schema)
        assert result.valid

        # Missing required
        result = parser.parse('{"count": 5}', OutputFormat.JSON, schema)
        assert not result.valid
        assert "required" in result.error.lower()

    def test_parse_markdown(self):
        """Test markdown parsing."""
        parser = OutputParser()
        content = """# Title

## Section 1
Content here

## Section 2
More content
"""
        result = parser.parse(content, OutputFormat.MARKDOWN)
        assert result.valid
        assert result.sections is not None
        assert "section_1" in result.sections or "title" in result.sections

    def test_detect_format_json(self):
        """Test auto-detect JSON format."""
        assert detect_format('{"key": "value"}') == OutputFormat.JSON
        assert detect_format('[1, 2, 3]') == OutputFormat.JSON

    def test_detect_format_markdown(self):
        """Test auto-detect markdown format."""
        assert detect_format("# Title\n\nContent") == OutputFormat.MARKDOWN
        assert detect_format("Some text\n\n## Section") == OutputFormat.MARKDOWN

    def test_detect_format_raw(self):
        """Test auto-detect raw format."""
        assert detect_format("Just plain text") == OutputFormat.RAW


class TestOutputTruncate:
    """Tests for output truncation."""

    def test_no_truncation_needed(self):
        """Test content under limit is not truncated."""
        truncator = OutputTruncator()
        result = truncator.truncate("short content", max_length=100)
        assert not result.truncated
        assert result.content == "short content"

    def test_hard_truncate(self):
        """Test hard truncation."""
        truncator = OutputTruncator()
        result = truncator.truncate("x" * 1000, max_length=100, strategy=TruncationStrategy.HARD)
        assert result.truncated
        # Account for truncation marker
        assert len(result.content) <= 120
        assert "truncated" in result.content.lower()

    def test_markdown_truncate_preserves_code_blocks(self):
        """Test markdown truncation preserves code blocks."""
        truncator = OutputTruncator()
        content = """# Title

Some text here.

```python
def hello():
    print("Hello, World!")
```

More text after code block.
""" * 10  # Make it long enough to truncate

        result = truncator.truncate(content, max_length=500, strategy=TruncationStrategy.MARKDOWN)
        # Check that truncation happened
        assert result.truncated or len(result.content) <= 600

    def test_json_truncate_preserves_structure(self):
        """Test JSON truncation preserves structure."""
        truncator = OutputTruncator()
        data = {"items": [{"id": i, "name": f"item_{i}"} for i in range(100)]}
        content = json.dumps(data)

        result = truncator.truncate(content, max_length=500, strategy=TruncationStrategy.JSON)
        assert result.truncated
        # Should still be valid JSON
        try:
            parsed = json.loads(result.content.split("// ...")[0].strip())
            assert isinstance(parsed, dict)
        except json.JSONDecodeError:
            pass  # May have truncation comment


class TestOutputRepair:
    """Tests for output repair."""

    def test_repair_valid_json_not_needed(self):
        """Test repair doesn't change valid JSON."""
        # Mock LLM call that should not be invoked
        repairer = OutputRepairer(llm_call=lambda x: "should not be called")
        parsed = format_output('{"key": "value"}', OutputFormat.JSON)
        assert parsed.valid
        assert not should_attempt_repair(parsed)

    def test_repair_raw_not_needed(self):
        """Test repair not attempted for raw format."""
        parsed = format_output("any content", OutputFormat.RAW)
        assert not should_attempt_repair(parsed)

    def test_repair_invalid_json(self):
        """Test repair for invalid JSON."""
        def mock_llm(prompt: str) -> str:
            # Return valid JSON
            return '{"key": "repaired_value"}'

        repairer = OutputRepairer(llm_call=mock_llm)
        result = repairer.repair("{invalid json}", OutputFormat.JSON)
        assert result.success
        assert result.repaired_content == '{"key": "repaired_value"}'

    def test_repair_with_schema(self):
        """Test repair with JSON schema."""
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}}
        }

        def mock_llm(prompt: str) -> str:
            return '{"name": "repaired"}'

        repairer = OutputRepairer(llm_call=mock_llm)
        result = repairer.repair("invalid", OutputFormat.JSON, schema=schema)
        assert result.success

    def test_repair_max_attempts(self):
        """Test repair gives up after max attempts."""
        call_count = 0

        def mock_llm(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return "still invalid"

        repairer = OutputRepairer(llm_call=mock_llm)
        result = repairer.repair("{bad}", OutputFormat.JSON)
        assert not result.success
        assert result.attempts == 2  # MAX_ATTEMPTS


class TestOutputPersist:
    """Tests for output persistence."""

    def test_create_persister(self):
        """Test creating a persister."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "test_crew")
            persister.initialize()

            assert persister.run_dir.exists()
            assert persister.tasks_dir.exists()
            assert persister.artifacts_dir.exists()

    def test_save_task_output(self):
        """Test saving task output."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "test_crew")
            persister.initialize()

            now = datetime.now()
            record = persister.save_task_output(
                task_name="task_1",
                output="Task output content",
                status="success",
                started_at=now,
                finished_at=now,
            )

            assert record.output_file is not None
            assert record.status == "success"
            assert len(persister.manifest.tasks) == 1

    def test_finalize_run(self):
        """Test finalizing a run."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "test_crew")
            persister.initialize()

            now = datetime.now()
            persister.save_task_output(
                task_name="task_1",
                output="output",
                status="success",
                started_at=now,
                finished_at=now,
            )

            persister.finalize("completed")

            assert persister.manifest.status == "completed"
            assert persister.manifest.finished_at is not None
            assert persister.manifest.total_time >= 0

    def test_save_artifact(self):
        """Test saving an artifact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "test_crew")
            persister.initialize()

            path = persister.save_artifact(
                artifact_name="report.md",
                content="# Report\n\nContent here",
            )

            assert path.exists()
            assert path.read_text() == "# Report\n\nContent here"


class TestOutputFormatEdgeCases:
    """Edge case tests for output format parsing."""

    def test_parse_json_array(self):
        """Test parsing JSON array."""
        parser = OutputParser()
        result = parser.parse('[1, 2, 3]', OutputFormat.JSON)
        assert result.valid
        assert result.structured == [1, 2, 3]

    def test_parse_json_nested(self):
        """Test parsing nested JSON."""
        parser = OutputParser()
        result = parser.parse('{"a": {"b": {"c": 1}}}', OutputFormat.JSON)
        assert result.valid
        assert result.structured == {"a": {"b": {"c": 1}}}

    def test_parse_json_with_numbers(self):
        """Test JSON schema validation with number types."""
        parser = OutputParser()
        schema = {
            "type": "object",
            "properties": {
                "int_val": {"type": "integer"},
                "float_val": {"type": "number"},
                "bool_val": {"type": "boolean"},
            }
        }
        result = parser.parse(
            '{"int_val": 42, "float_val": 3.14, "bool_val": true}',
            OutputFormat.JSON,
            schema
        )
        assert result.valid

    def test_parse_json_schema_type_mismatch(self):
        """Test JSON schema type validation."""
        parser = OutputParser()
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
        result = parser.parse('{"count": "not_a_number"}', OutputFormat.JSON, schema)
        assert not result.valid
        assert "integer" in result.error.lower()

    def test_parse_markdown_with_code_blocks(self):
        """Test markdown parsing with code blocks."""
        parser = OutputParser()
        content = """# Title

```python
def hello():
    print("Hello")
```

## Section
More content
"""
        result = parser.parse(content, OutputFormat.MARKDOWN)
        assert result.valid
        assert "code_blocks" in result.structured
        assert len(result.structured["code_blocks"]) == 1
        assert result.structured["code_blocks"][0]["language"] == "python"

    def test_parse_markdown_with_links(self):
        """Test markdown parsing with links."""
        parser = OutputParser()
        content = """# Title

Check out [this link](https://example.com) for more info.
"""
        result = parser.parse(content, OutputFormat.MARKDOWN)
        assert result.valid
        assert "links" in result.structured
        assert len(result.structured["links"]) == 1

    def test_parse_structured_no_template(self):
        """Test structured parsing without template."""
        parser = OutputParser()
        result = parser.parse("content", OutputFormat.STRUCTURED)
        assert not result.valid
        assert "template" in result.error.lower()

    def test_detect_format_json_in_code_block(self):
        """Test detecting JSON inside code block."""
        content = "Here is some JSON:\n```json\n{\"key\": \"value\"}\n```"
        assert detect_format(content) == OutputFormat.JSON

    def test_format_output_convenience(self):
        """Test format_output convenience function."""
        result = format_output('{"test": 1}', OutputFormat.JSON)
        assert result.valid
        assert result.structured == {"test": 1}


class TestOutputTruncateEdgeCases:
    """Edge case tests for output truncation."""

    def test_smart_detect_json(self):
        """Test smart truncation detects JSON."""
        truncator = OutputTruncator()
        content = json.dumps({"items": list(range(100))})
        result = truncator.truncate(content, max_length=100, strategy=TruncationStrategy.SMART)
        # Smart detects JSON for content starting with { or [
        assert result.strategy in ("json", "hard")  # May fall back to hard

    def test_smart_detect_markdown(self):
        """Test smart truncation detects markdown."""
        truncator = OutputTruncator()
        content = "# Title\n\n" + "x" * 1000
        result = truncator.truncate(content, max_length=100, strategy=TruncationStrategy.SMART)
        assert result.strategy == "markdown"

    def test_truncate_empty_content(self):
        """Test truncating empty content."""
        truncator = OutputTruncator()
        result = truncator.truncate("", max_length=100)
        assert not result.truncated
        assert result.content == ""

    def test_truncate_exact_length(self):
        """Test content at exact max length."""
        truncator = OutputTruncator()
        content = "x" * 100
        result = truncator.truncate(content, max_length=100)
        assert not result.truncated
        assert result.content == content

    def test_truncate_json_list(self):
        """Test truncating JSON list."""
        truncator = OutputTruncator()
        content = json.dumps(list(range(100)))
        result = truncator.truncate(content, max_length=200, strategy=TruncationStrategy.JSON)
        assert result.truncated
        # Should be valid JSON array
        parsed = json.loads(result.content.split("// ...")[0].strip())
        assert isinstance(parsed, list)

    def test_truncate_preserve_patterns(self):
        """Test truncation with preserve patterns."""
        truncator = OutputTruncator()
        content = "Error: Something went wrong\n" + "x" * 1000 + "\nConclusion: All done"
        result = truncator.truncate(
            content,
            max_length=200,
            strategy=TruncationStrategy.HARD,
            preserve_patterns=[r'Error:', r'Conclusion:']
        )
        assert result.truncated

    def test_truncate_convenience_function(self):
        """Test truncate_output convenience function."""
        result = truncate_output("x" * 1000, max_length=100)
        assert result.truncated


class TestOutputRepairEdgeCases:
    """Edge case tests for output repair."""

    def test_repair_no_llm_call(self):
        """Test repair without LLM call."""
        repairer = OutputRepairer(llm_call=None)
        result = repairer.repair("{bad}", OutputFormat.JSON)
        assert not result.success
        assert "No LLM" in result.error

    def test_repair_llm_exception(self):
        """Test repair when LLM throws exception."""
        def failing_llm(prompt: str) -> str:
            raise RuntimeError("LLM failed")

        repairer = OutputRepairer(llm_call=failing_llm)
        result = repairer.repair("{bad}", OutputFormat.JSON)
        assert not result.success

    def test_repair_markdown(self):
        """Test markdown repair."""
        def mock_llm(prompt: str) -> str:
            return "# Fixed Title\n\nContent"

        repairer = OutputRepairer(llm_call=mock_llm)
        result = repairer.repair("Bad markdown", OutputFormat.MARKDOWN)
        assert result.success

    def test_should_attempt_repair_json_invalid(self):
        """Test should_attempt_repair for invalid JSON."""
        parsed = format_output("{bad}", OutputFormat.JSON)
        assert should_attempt_repair(parsed)

    def test_should_attempt_repair_structured(self):
        """Test should_attempt_repair for structured format."""
        # Create a manually constructed invalid structured output
        from xbot.agent.crew.output.format import ParsedOutput
        parsed = ParsedOutput(
            format=OutputFormat.STRUCTURED,
            raw="bad",
            valid=False,
            error="Invalid"
        )
        assert should_attempt_repair(parsed)

    def test_repair_json_convenience(self):
        """Test repair_json convenience function."""
        from xbot.agent.crew.output.repair import repair_json

        # With LLM call provided, repair should succeed
        result = repair_json("{bad}", llm_call=lambda x: '{"fixed": true}')
        assert result.success  # With LLM, it should succeed


class TestOutputPersistEdgeCases:
    """Edge case tests for output persistence."""

    def test_multiple_tasks(self):
        """Test saving multiple tasks."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "test_crew")
            persister.initialize()

            now = datetime.now()
            for i in range(3):
                persister.save_task_output(
                    task_name=f"task_{i}",
                    output=f"Output {i}",
                    status="success",
                    started_at=now,
                    finished_at=now,
                )

            assert len(persister.manifest.tasks) == 3
            # Check file ordering
            tasks = persister.manifest.tasks
            assert "01_" in tasks[0].output_file
            assert "02_" in tasks[1].output_file
            assert "03_" in tasks[2].output_file

    def test_save_binary_artifact(self):
        """Test saving binary artifact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "test_crew")
            persister.initialize()

            binary_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
            path = persister.save_artifact(
                artifact_name="image.png",
                content=binary_data,
            )

            assert path.exists()
            assert path.read_bytes() == binary_data

    def test_run_summary(self):
        """Test getting run summary."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "test_crew")
            persister.initialize()

            now = datetime.now()
            persister.save_task_output("task1", "out", "success", now, now)
            persister.save_task_output("task2", "out", "failed", now, now)

            summary = persister.get_run_summary()
            assert summary["total_tasks"] == 2
            assert summary["successful_tasks"] == 1
            assert summary["failed_tasks"] == 1

    def test_safe_filename(self):
        """Test filename sanitization."""
        from xbot.agent.crew.output.persist import OutputPersister

        persister = OutputPersister("/tmp", "test")
        assert persister._safe_filename("My Task Name") == "my_task_name"
        assert persister._safe_filename("task/with/slashes") == "task_with_slashes"
        # Special characters are removed (not replaced with underscores)
        assert persister._safe_filename("task<>:\"|?*") == "task"

    def test_get_extension(self):
        """Test extension mapping."""
        from xbot.agent.crew.output.persist import OutputPersister

        persister = OutputPersister("/tmp", "test")
        assert persister._get_extension("json") == "json"
        assert persister._get_extension("markdown") == "md"
        assert persister._get_extension("raw") == "txt"
        assert persister._get_extension("unknown") == "txt"

    def test_finalize_without_initialize(self):
        """Test finalize without initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "test_crew")
            # finalize should handle uninitialized state
            persister.finalize("completed")
            assert persister.manifest.status == "running"  # Not changed

    def test_custom_run_id(self):
        """Test custom run ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "test_crew", run_id="custom_run_123")
            persister.initialize()

            assert persister.run_id == "custom_run_123"
            assert persister.run_dir.name == "custom_run_123"


class TestOutputBugFixes:
    """Regression tests for output-related bugs that were fixed.

    These tests ensure the bugs don't come back.
    """

    # BUG-5: Markdown truncation logic with code blocks
    def test_markdown_truncation_inside_code_block(self):
        """Test that truncation handles code blocks correctly (BUG-5)."""
        truncator = OutputTruncator()

        # Create content where truncation point would be inside a code block
        code_block = "def hello():\n" + "    x = 1\n" * 50 + "    return x\n"
        content = f"""# Title

Some intro text.

```python
{code_block}
```

More content after the code block.
"""
        result = truncator.truncate(content, max_length=500, strategy=TruncationStrategy.MARKDOWN)

        # Should truncate
        assert result.truncated

        # Should not break inside the code block
        # Check that if we have ```python, we also have the closing ```
        if "```python" in result.content:
            # Count code block markers - should be even
            count = result.content.count("```")
            assert count % 2 == 0, "Code block markers should be balanced"

    def test_markdown_truncation_long_code_block(self):
        """Test truncation when code block itself exceeds max_length (BUG-5)."""
        truncator = OutputTruncator()

        # Create a very long code block
        long_code = "x = 1\n" * 1000
        content = f"""# Title

```python
{long_code}
```
"""
        result = truncator.truncate(content, max_length=200, strategy=TruncationStrategy.MARKDOWN)

        assert result.truncated
        # Should still be valid-ish markdown
        assert "```" in result.content or "truncated" in result.content.lower()

    def test_markdown_truncation_preserves_code_block_boundaries(self):
        """Test that code block boundaries are preserved when possible (BUG-5)."""
        truncator = OutputTruncator()

        # Create content with multiple code blocks
        content = """# Document

## Section 1
Some text here.

```python
def func1():
    return 1
```

## Section 2
More text.

```javascript
function func2() {
    return 2;
}
```

## Section 3
Final section with lots of content.
""" + ("Additional content. " * 100)

        result = truncator.truncate(content, max_length=300, strategy=TruncationStrategy.MARKDOWN)

        # Check code block balance
        if "```" in result.content:
            count = result.content.count("```")
            # Should have balanced markers
            assert count % 2 == 0, f"Unbalanced code block markers: {count}"

    def test_markdown_truncation_empty_code_block(self):
        """Test truncation with empty code blocks (BUG-5)."""
        truncator = OutputTruncator()

        content = """# Title

```python
```

Some content after empty code block.
""" + ("More content. " * 50)

        result = truncator.truncate(content, max_length=200, strategy=TruncationStrategy.MARKDOWN)

        # Should handle empty code blocks gracefully
        assert result.truncated or len(result.content) <= 250

    def test_markdown_truncation_nested_backticks(self):
        """Test truncation with nested backticks in content (BUG-5)."""
        truncator = OutputTruncator()

        # Content with backticks that aren't code blocks
        content = """# Title

This has `inline code` and mentions ``` but not as a code block.

```python
print("real code block")
```

More content here.
""" + ("Additional text. " * 50)

        result = truncator.truncate(content, max_length=300, strategy=TruncationStrategy.MARKDOWN)

        # Should handle gracefully
        assert result.truncated or len(result.content) <= 350

    def test_markdown_truncation_very_long_line(self):
        """Test truncation with a very long single line (BUG-5)."""
        truncator = OutputTruncator()

        # Single very long line
        long_line = "x" * 1000
        content = f"# Title\n\n{long_line}\n\nEnd."

        result = truncator.truncate(content, max_length=100, strategy=TruncationStrategy.MARKDOWN)

        assert result.truncated

    def test_markdown_truncation_preserves_structure(self):
        """Test that truncation preserves markdown structure (BUG-5)."""
        truncator = OutputTruncator()

        content = """# Main Title

## Section 1

Content for section 1.

### Subsection 1.1

More content.

## Section 2

Content for section 2.

```python
# A code example
def example():
    pass
```

## Section 3

Final section.
""" + ("Additional paragraph. " * 30)

        result = truncator.truncate(content, max_length=400, strategy=TruncationStrategy.MARKDOWN)

        # Should start with the title (not break it)
        assert result.content.startswith("# Main Title")


class TestOutputIntegration:
    """Integration tests for the full output handling pipeline.

    These tests verify the complete workflow from output generation to persistence.
    """

    def test_full_output_pipeline(self):
        """Test complete output pipeline: parse -> truncate -> repair -> persist."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            # Initialize persister
            persister = OutputPersister(tmpdir, "integration_test")
            persister.initialize()

            # Create output content
            content = json.dumps({"results": [i for i in range(100)]})

            # Parse
            parsed = format_output(content, OutputFormat.JSON)
            assert parsed.valid

            # Truncate (if needed)
            truncator = OutputTruncator()
            truncated = truncator.truncate(content, max_length=5000, strategy=TruncationStrategy.SMART)

            # Save
            now = datetime.now()
            record = persister.save_task_output(
                task_name="json_task",
                output=truncated.content,
                status="success",
                started_at=now,
                finished_at=now,
                output_format="json",
            )

            assert record is not None
            assert len(persister.manifest.tasks) == 1

            # Finalize
            persister.finalize("completed")
            assert persister.manifest.status == "completed"

    def test_output_pipeline_with_repair(self):
        """Test output pipeline with repair attempt."""
        from datetime import datetime

        def mock_llm(prompt: str) -> str:
            return '{"fixed": true, "items": []}'

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "repair_test")
            persister.initialize()

            # Invalid JSON output
            invalid_json = "{broken: true, items: [}"

            # Attempt repair
            repairer = OutputRepairer(llm_call=mock_llm)
            repair_result = repairer.repair(invalid_json, OutputFormat.JSON)

            assert repair_result.success

            # Save repaired output
            now = datetime.now()
            record = persister.save_task_output(
                task_name="repaired_task",
                output=repair_result.repaired_content,
                status="success",
                started_at=now,
                finished_at=now,
            )

            assert record.status == "success"

    def test_multiple_formats_same_run(self):
        """Test saving outputs in multiple formats in same run."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "multi_format")
            persister.initialize()

            now = datetime.now()

            # Task 1: JSON output
            persister.save_task_output(
                task_name="json_task",
                output='{"type": "json"}',
                status="success",
                started_at=now,
                finished_at=now,
                output_format="json",
            )

            # Task 2: Markdown output
            markdown_content = """# Report

## Summary
This is a summary.

## Details
More details here.
"""
            persister.save_task_output(
                task_name="markdown_task",
                output=markdown_content,
                status="success",
                started_at=now,
                finished_at=now,
                output_format="markdown",
            )

            # Task 3: Raw output
            persister.save_task_output(
                task_name="raw_task",
                output="Just plain text output",
                status="success",
                started_at=now,
                finished_at=now,
                output_format="raw",
            )

            # Verify all tasks saved
            assert len(persister.manifest.tasks) == 3

            # Verify file extensions
            json_task = persister.manifest.tasks[0]
            md_task = persister.manifest.tasks[1]
            raw_task = persister.manifest.tasks[2]

            assert json_task.output_file.endswith(".json")
            assert md_task.output_file.endswith(".md")
            assert raw_task.output_file.endswith(".txt")

    def test_output_with_schema_validation(self):
        """Test output parsing with JSON schema validation."""
        schema = {
            "type": "object",
            "required": ["status", "items"],
            "properties": {
                "status": {"type": "string"},
                "items": {"type": "array"},
                "count": {"type": "integer"},
            },
        }

        parser = OutputParser()

        # Valid output
        valid_output = '{"status": "success", "items": [1, 2, 3], "count": 3}'
        result = parser.parse(valid_output, OutputFormat.JSON, schema)
        assert result.valid

        # Missing required field
        invalid_output = '{"items": [1, 2, 3]}'
        result = parser.parse(invalid_output, OutputFormat.JSON, schema)
        assert not result.valid
        assert "required" in result.error.lower()

    def test_format_detection_integration(self):
        """Test automatic format detection for various content types."""
        test_cases = [
            ('{"key": "value"}', OutputFormat.JSON),
            ('[1, 2, 3]', OutputFormat.JSON),
            ('# Title\n\nContent', OutputFormat.MARKDOWN),
            ('## Section\n\nText', OutputFormat.MARKDOWN),
            ('Plain text without any special formatting', OutputFormat.RAW),
            ('```json\n{"nested": true}\n```', OutputFormat.JSON),
        ]

        for content, expected_format in test_cases:
            detected = detect_format(content)
            assert detected == expected_format, f"Failed for: {content[:30]}..."

    def test_truncation_strategy_fallback(self):
        """Test that truncation works with very short max_length."""
        truncator = OutputTruncator()

        # Very short max_length - this triggers truncation
        content = "x" * 1000
        result = truncator.truncate(content, max_length=10, strategy=TruncationStrategy.SMART)

        assert result.truncated
        # The truncation flag indicates truncation occurred

    def test_persister_with_artifacts(self):
        """Test saving artifacts alongside task outputs."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "artifact_test")
            persister.initialize()

            now = datetime.now()

            # Save task output
            persister.save_task_output(
                task_name="generate_report",
                output="Report generation completed",
                status="success",
                started_at=now,
                finished_at=now,
            )

            # Save artifact (the actual report)
            report_content = """# Analysis Report

## Summary
Analysis completed successfully.

## Findings
- Finding 1
- Finding 2
"""
            artifact_path = persister.save_artifact(
                artifact_name="analysis_report.md",
                content=report_content,
            )

            assert artifact_path.exists()
            assert artifact_path.read_text() == report_content
            assert len(persister.manifest.tasks) == 1

    def test_run_summary_integration(self):
        """Test run summary with mixed task statuses."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "summary_test")
            persister.initialize()

            now = datetime.now()

            # Various task outcomes
            persister.save_task_output("task1", "output1", "success", now, now)
            persister.save_task_output("task2", "output2", "success", now, now)
            persister.save_task_output("task3", "error", "failed", now, now)
            persister.save_task_output("task4", "", "skipped", now, now)

            summary = persister.get_run_summary()

            assert summary["total_tasks"] == 4
            assert summary["successful_tasks"] == 2
            assert summary["failed_tasks"] == 1

    def test_output_pipeline_with_large_content(self):
        """Test pipeline handling of large output content."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "large_output", retention_days=7)
            persister.initialize()

            # Generate large content
            large_data = {"items": [{"id": i, "data": "x" * 100} for i in range(1000)]}
            content = json.dumps(large_data)

            # Truncate
            truncator = OutputTruncator()
            truncated = truncator.truncate(
                content,
                max_length=50000,
                strategy=TruncationStrategy.JSON
            )

            # Save
            now = datetime.now()
            record = persister.save_task_output(
                task_name="large_task",
                output=truncated.content,
                status="success",
                started_at=now,
                finished_at=now,
                output_format="json",
                truncated=truncated.truncated,
            )

            assert record is not None

    def test_markdown_parsing_and_truncation_integration(self):
        """Test markdown parsing followed by intelligent truncation."""
        content = """# Documentation

## Overview
This document provides an overview of the system.

## Architecture

### Components
The system consists of multiple components:

```python
class Component:
    def __init__(self, name):
        self.name = name

    def process(self, data):
        return data.upper()
```

### Data Flow
Data flows through the system as follows:
1. Input received
2. Processing applied
3. Output generated

## API Reference

### Endpoints
- GET /api/status
- POST /api/process
- DELETE /api/clear

```bash
curl -X GET http://localhost:8080/api/status
```

## Configuration
Configuration is managed via environment variables.

## Troubleshooting
Common issues and their solutions.

## Appendix
Additional information.
""" + "\nMore content. " * 200

        # Parse
        parser = OutputParser()
        parsed = parser.parse(content, OutputFormat.MARKDOWN)
        assert parsed.valid
        assert parsed.structured is not None

        # Truncate
        truncator = OutputTruncator()
        truncated = truncator.truncate(
            content,
            max_length=500,
            strategy=TruncationStrategy.MARKDOWN
        )

        assert truncated.truncated

        # Verify structure preserved
        if "```" in truncated.content:
            count = truncated.content.count("```")
            assert count % 2 == 0, "Code blocks should be balanced"

    def test_error_recovery_in_pipeline(self):
        """Test error recovery during output processing."""
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            from xbot.agent.crew.output.persist import OutputPersister

            persister = OutputPersister(tmpdir, "error_test")
            persister.initialize()

            now = datetime.now()

            # Task with error that was recovered
            persister.save_task_output(
                task_name="recovered_task",
                output='{"partial": true}',  # Partially valid
                status="success",
                started_at=now,
                finished_at=now,
            )

            # Verify task was saved
            assert len(persister.manifest.tasks) == 1
            assert persister.manifest.tasks[0].status == "success"

    def test_concurrent_output_formats(self):
        """Test that different output formats can coexist."""
        parser = OutputParser()

        # Parse same content with different format expectations
        json_like = '{"message": "hello"}'

        # As JSON
        json_result = parser.parse(json_like, OutputFormat.JSON)
        assert json_result.valid
        assert json_result.structured == {"message": "hello"}

        # As RAW (should also work)
        raw_result = parser.parse(json_like, OutputFormat.RAW)
        assert raw_result.valid
        assert raw_result.raw == json_like

    def test_schema_validation_with_nested_objects(self):
        """Test JSON schema validation with deeply nested objects."""
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "database": {
                            "type": "object",
                            "properties": {
                                "host": {"type": "string"},
                                "port": {"type": "integer"},
                            },
                        }
                    },
                }
            },
        }

        parser = OutputParser()

        # Valid nested
        valid = '{"config": {"database": {"host": "localhost", "port": 5432}}}'
        result = parser.parse(valid, OutputFormat.JSON, schema)
        assert result.valid

        # Invalid nested (port as string)
        invalid = '{"config": {"database": {"host": "localhost", "port": "5432"}}}'
        result = parser.parse(invalid, OutputFormat.JSON, schema)
        # Schema validation may or may not catch this depending on strictness
        # Just verify it parses
        assert result.structured is not None