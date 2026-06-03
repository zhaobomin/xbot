"""Tests for output format parsing: JSON, Markdown, Structured, Raw."""



from xbot.crew.models import OutputFormat
from xbot.crew.output.format import (
    OutputParser,
    ParsedOutput,
    detect_format,
    format_output,
)


class TestParsedOutput:
    """Test ParsedOutput dataclass."""

    def test_default_values(self) -> None:
        """Default values should be set correctly."""
        output = ParsedOutput(
            format=OutputFormat.RAW,
            raw="test content",
        )

        assert output.format == OutputFormat.RAW
        assert output.raw == "test content"
        assert output.structured is None
        assert output.sections is None
        assert output.valid is True
        assert output.error is None
        assert output.truncated is False
        assert output.repaired is False

    def test_all_fields(self) -> None:
        """All fields should be settable."""
        output = ParsedOutput(
            format=OutputFormat.JSON,
            raw='{"key": "value"}',
            structured={"key": "value"},
            valid=False,
            error="Test error",
            truncated=True,
            repaired=True,
        )

        assert output.format == OutputFormat.JSON
        assert output.structured == {"key": "value"}
        assert output.valid is False
        assert output.error == "Test error"
        assert output.truncated is True
        assert output.repaired is True


class TestRawFormat:
    """Test RAW format parsing."""

    def test_raw_returns_as_is(self) -> None:
        """RAW format should return content unchanged."""
        parser = OutputParser()
        result = parser.parse("Any content here", OutputFormat.RAW)

        assert result.valid is True
        assert result.raw == "Any content here"
        assert result.structured is None
        assert result.error is None

    def test_raw_with_special_characters(self) -> None:
        """RAW format should handle special characters."""
        parser = OutputParser()
        content = "Content with \n newlines \t tabs and 'quotes' \"double\""
        result = parser.parse(content, OutputFormat.RAW)

        assert result.valid is True
        assert result.raw == content


class TestJsonFormat:
    """Test JSON format parsing."""

    def test_simple_json(self) -> None:
        """Simple JSON should parse correctly."""
        parser = OutputParser()
        result = parser.parse('{"name": "test", "value": 123}', OutputFormat.JSON)

        assert result.valid is True
        assert result.structured == {"name": "test", "value": 123}

    def test_json_array(self) -> None:
        """JSON array should parse correctly."""
        parser = OutputParser()
        result = parser.parse('[1, 2, 3]', OutputFormat.JSON)

        assert result.valid is True
        assert result.structured == [1, 2, 3]

    def test_nested_json(self) -> None:
        """Nested JSON should parse correctly."""
        parser = OutputParser()
        result = parser.parse(
            '{"outer": {"inner": {"deep": "value"}}}',
            OutputFormat.JSON
        )

        assert result.valid is True
        assert result.structured == {"outer": {"inner": {"deep": "value"}}}

    def test_json_in_markdown_code_block(self) -> None:
        """JSON in markdown code block should be extracted."""
        parser = OutputParser()
        result = parser.parse(
            '```json\n{"key": "value"}\n```',
            OutputFormat.JSON
        )

        assert result.valid is True
        assert result.structured == {"key": "value"}

    def test_json_in_plain_code_block(self) -> None:
        """JSON in plain code block should be extracted."""
        parser = OutputParser()
        result = parser.parse(
            '```\n{"key": "value"}\n```',
            OutputFormat.JSON
        )

        assert result.valid is True
        assert result.structured == {"key": "value"}

    def test_invalid_json(self) -> None:
        """Invalid JSON should return error."""
        parser = OutputParser()
        result = parser.parse('{"invalid": json}', OutputFormat.JSON)

        assert result.valid is False
        assert result.error is not None
        assert "JSON parse error" in result.error

    def test_json_with_surrounding_text(self) -> None:
        """JSON embedded in text should be extracted."""
        parser = OutputParser()
        result = parser.parse(
            'Here is the result: {"status": "ok"} and some more text',
            OutputFormat.JSON
        )

        assert result.valid is True
        assert result.structured == {"status": "ok"}

    def test_nested_json_with_surrounding_text(self) -> None:
        """Nested JSON embedded in text should be extracted as a full balanced object."""
        parser = OutputParser()
        result = parser.parse(
            'Result: {"outer":{"inner":[1,2,{"k":"v"}]}} trailing text',
            OutputFormat.JSON,
        )

        assert result.valid is True
        assert result.structured == {"outer": {"inner": [1, 2, {"k": "v"}]}}


class TestJsonSchemaValidation:
    """Test JSON schema validation."""

    def test_valid_schema(self) -> None:
        """Valid data against schema should pass."""
        parser = OutputParser()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["name"],
        }

        result = parser.parse(
            '{"name": "test", "count": 5}',
            OutputFormat.JSON,
            schema=schema
        )

        assert result.valid is True
        assert result.structured == {"name": "test", "count": 5}

    def test_schema_enum_min_length_and_pattern(self) -> None:
        """Common JSON Schema keywords should be enforced."""
        parser = OutputParser()
        schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["ok", "failed"]},
                "name": {"type": "string", "minLength": 3},
                "ticket": {"type": "string", "pattern": r"^BUG-\d+$"},
            },
            "required": ["status", "name", "ticket"],
        }

        valid = parser.parse(
            '{"status": "ok", "name": "bot", "ticket": "BUG-123"}',
            OutputFormat.JSON,
            schema=schema,
        )
        invalid = parser.parse(
            '{"status": "maybe", "name": "bo", "ticket": "TASK-123"}',
            OutputFormat.JSON,
            schema=schema,
        )

        assert valid.valid is True
        assert invalid.valid is False
        assert invalid.error is not None
        assert "status" in invalid.error
        assert "name" in invalid.error
        assert "ticket" in invalid.error

    def test_missing_required_property(self) -> None:
        """Missing required property should fail validation."""
        parser = OutputParser()
        schema = {
            "type": "object",
            "required": ["name"],
        }

        result = parser.parse('{}', OutputFormat.JSON, schema=schema)

        assert result.valid is False
        assert "required property missing" in result.error

    def test_wrong_type_string(self) -> None:
        """Wrong type (string expected) should fail."""
        parser = OutputParser()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        }

        result = parser.parse('{"name": 123}', OutputFormat.JSON, schema=schema)

        assert result.valid is False
        assert "expected string" in result.error

    def test_wrong_type_integer(self) -> None:
        """Wrong type (integer expected) should fail."""
        parser = OutputParser()
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
            },
        }

        result = parser.parse('{"count": "five"}', OutputFormat.JSON, schema=schema)

        assert result.valid is False
        assert "expected integer" in result.error

    def test_array_validation(self) -> None:
        """Array items should be validated."""
        parser = OutputParser()
        schema = {
            "type": "array",
            "items": {"type": "string"},
        }

        result = parser.parse('["a", "b", "c"]', OutputFormat.JSON, schema=schema)
        assert result.valid is True

        result = parser.parse('[1, 2, 3]', OutputFormat.JSON, schema=schema)
        assert result.valid is False
        assert "expected string" in result.error

    def test_number_validation(self) -> None:
        """Number type should accept int and float."""
        parser = OutputParser()
        schema = {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
            },
        }

        result = parser.parse('{"value": 42}', OutputFormat.JSON, schema=schema)
        assert result.valid is True

        result = parser.parse('{"value": 3.14}', OutputFormat.JSON, schema=schema)
        assert result.valid is True

        result = parser.parse('{"value": "text"}', OutputFormat.JSON, schema=schema)
        assert result.valid is False

    def test_boolean_validation(self) -> None:
        """Boolean type should be validated."""
        parser = OutputParser()
        schema = {
            "type": "object",
            "properties": {
                "active": {"type": "boolean"},
            },
        }

        result = parser.parse('{"active": true}', OutputFormat.JSON, schema=schema)
        assert result.valid is True

        result = parser.parse('{"active": "yes"}', OutputFormat.JSON, schema=schema)
        assert result.valid is False

    def test_nested_object_validation(self) -> None:
        """Nested objects should be validated recursively."""
        parser = OutputParser()
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
        }

        result = parser.parse(
            '{"user": {"name": "Alice"}}',
            OutputFormat.JSON,
            schema=schema
        )
        assert result.valid is True

        result = parser.parse('{"user": {}}', OutputFormat.JSON, schema=schema)
        assert result.valid is False
        assert "required property missing" in result.error


class TestMarkdownFormat:
    """Test Markdown format parsing."""

    def test_simple_markdown(self) -> None:
        """Simple markdown should parse into sections."""
        parser = OutputParser()
        content = """# Title

This is the intro.

## Section 1

Content for section 1.

## Section 2

Content for section 2.
"""
        result = parser.parse(content, OutputFormat.MARKDOWN)

        assert result.valid is True
        assert result.sections is not None
        assert "title" in result.sections
        assert "section_1" in result.sections
        assert "section_2" in result.sections

    def test_markdown_code_blocks_extracted(self) -> None:
        """Code blocks should be extracted."""
        parser = OutputParser()
        content = """# Code

```python
print("hello")
```

```javascript
console.log("hi");
```
"""
        result = parser.parse(content, OutputFormat.MARKDOWN)

        assert result.valid is True
        assert result.structured is not None
        assert "code_blocks" in result.structured
        assert len(result.structured["code_blocks"]) == 2
        assert result.structured["code_blocks"][0]["language"] == "python"
        assert result.structured["code_blocks"][1]["language"] == "javascript"

    def test_markdown_links_extracted(self) -> None:
        """Links should be extracted."""
        parser = OutputParser()
        content = """# Links

Check out [Google](https://google.com) and [GitHub](https://github.com).
"""
        result = parser.parse(content, OutputFormat.MARKDOWN)

        assert result.valid is True
        assert result.structured is not None
        assert "links" in result.structured
        assert len(result.structured["links"]) == 2

    def test_markdown_lists_extracted(self) -> None:
        """Lists should be extracted."""
        parser = OutputParser()
        content = """# Tasks

- Item 1
- Item 2
- Item 3
"""
        result = parser.parse(content, OutputFormat.MARKDOWN)

        assert result.valid is True
        assert result.structured is not None
        # Lists are extracted per section
        assert "tasks_list" in result.structured
        assert len(result.structured["tasks_list"]) == 3

    def test_markdown_no_headers(self) -> None:
        """Markdown without headers should still work."""
        parser = OutputParser()
        content = "Just some plain text without headers."
        result = parser.parse(content, OutputFormat.MARKDOWN)

        assert result.valid is True
        assert result.sections is not None
        assert "intro" in result.sections


class TestStructuredFormat:
    """Test Structured format parsing."""

    def test_structured_no_template(self) -> None:
        """Structured without template should return error."""
        parser = OutputParser()
        result = parser.parse("Some content", OutputFormat.STRUCTURED, schema=None)

        assert result.valid is False
        assert "No template provided" in result.error

    def test_structured_with_template(self) -> None:
        """Structured with template should extract variables."""
        parser = OutputParser()
        schema = {
            "output": "The value is {{value}}",
        }

        result = parser.parse(
            "value: 42",
            OutputFormat.STRUCTURED,
            schema=schema
        )

        assert result.valid is True
        assert result.structured is not None
        # Variable extraction is heuristic-based
        assert "value" in result.structured or result.structured.get("value")

    def test_structured_extraction_patterns(self) -> None:
        """Various patterns should be recognized."""
        parser = OutputParser()
        schema = {"output": "{{name}} {{age}}"}

        # Pattern: "name: value"
        result = parser.parse("name: Alice\nage: 30", OutputFormat.STRUCTURED, schema=schema)
        assert result.valid is True


class TestUnknownFormat:
    """Test unknown format handling."""

    def test_unknown_format_returns_error(self) -> None:
        """Unknown format should return error."""
        parser = OutputParser()
        # Use a mock format value
        result = parser.parse("content", "unknown_format")  # type: ignore

        assert result.valid is False
        assert "Unknown format" in result.error


class TestDetectFormat:
    """Test auto-detection of output format."""

    def test_detect_json_object(self) -> None:
        """JSON object should be detected."""
        assert detect_format('{"key": "value"}') == OutputFormat.JSON

    def test_detect_json_array(self) -> None:
        """JSON array should be detected."""
        assert detect_format('[1, 2, 3]') == OutputFormat.JSON

    def test_detect_json_code_block(self) -> None:
        """JSON code block should be detected."""
        assert detect_format('```json\n{"key": "value"}\n```') == OutputFormat.JSON

    def test_detect_markdown(self) -> None:
        """Markdown should be detected."""
        assert detect_format('# Title\n\nContent') == OutputFormat.MARKDOWN
        assert detect_format('## Section\n\nContent') == OutputFormat.MARKDOWN

    def test_detect_raw_fallback(self) -> None:
        """Unknown content should fallback to RAW."""
        assert detect_format('Just plain text') == OutputFormat.RAW
        assert detect_format('') == OutputFormat.RAW

    def test_detect_invalid_json_falls_back(self) -> None:
        """Invalid JSON-like content should fallback."""
        assert detect_format('{not valid json}') == OutputFormat.RAW


class TestFormatOutputFunction:
    """Test convenience format_output function."""

    def test_format_output_raw(self) -> None:
        """format_output should work for RAW."""
        result = format_output("content", OutputFormat.RAW)
        assert result.valid is True
        assert result.raw == "content"

    def test_format_output_json(self) -> None:
        """format_output should work for JSON."""
        result = format_output('{"key": "value"}', OutputFormat.JSON)
        assert result.valid is True
        assert result.structured == {"key": "value"}

    def test_format_output_with_schema(self) -> None:
        """format_output should accept schema."""
        schema = {"type": "object", "required": ["name"]}
        result = format_output('{"name": "test"}', OutputFormat.JSON, schema=schema)
        assert result.valid is True


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_content(self) -> None:
        """Empty content should be handled."""
        parser = OutputParser()
        result = parser.parse("", OutputFormat.RAW)
        assert result.valid is True
        assert result.raw == ""

    def test_whitespace_only(self) -> None:
        """Whitespace-only content should be handled."""
        parser = OutputParser()
        result = parser.parse("   \n\t\n   ", OutputFormat.RAW)
        assert result.valid is True

    def test_very_long_content(self) -> None:
        """Very long content should be handled."""
        parser = OutputParser()
        content = "x" * 100000
        result = parser.parse(content, OutputFormat.RAW)
        assert result.valid is True
        assert len(result.raw) == 100000

    def test_unicode_content(self) -> None:
        """Unicode content should be handled."""
        parser = OutputParser()
        content = "Hello 世界 🌍 مرحبا"
        result = parser.parse(content, OutputFormat.RAW)
        assert result.valid is True
        assert result.raw == content

    def test_json_with_unicode(self) -> None:
        """JSON with Unicode should be handled."""
        parser = OutputParser()
        result = parser.parse('{"message": "你好世界"}', OutputFormat.JSON)
        assert result.valid is True
        assert result.structured == {"message": "你好世界"}

    def test_json_with_escaped_characters(self) -> None:
        """JSON with escaped characters should be handled."""
        parser = OutputParser()
        result = parser.parse(r'{"text": "line1\nline2\ttab"}', OutputFormat.JSON)
        assert result.valid is True
