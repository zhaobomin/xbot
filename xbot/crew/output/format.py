"""Output format handling for crew task results.

Supported formats:
- raw: No processing, stored as-is
- json: Parsed and validated against schema
- markdown: Structured into sections
- structured: Parsed according to output_template
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Import OutputFormat from models to avoid duplicate definition
from xbot.crew.models import OutputFormat


@dataclass
class ParsedOutput:
    """Parsed output with metadata."""

    format: OutputFormat
    raw: str
    structured: dict[str, Any] | None = None
    sections: dict[str, str] | None = None  # For markdown: section_name -> content
    valid: bool = True
    error: str | None = None
    truncated: bool = False
    repaired: bool = False


class OutputParser:
    """Parses task output according to specified format."""

    def parse(
        self,
        content: str,
        output_format: OutputFormat = OutputFormat.RAW,
        schema: dict[str, Any] | None = None,
    ) -> ParsedOutput:
        """Parse output content.

        Args:
            content: Raw output string from agent
            output_format: Expected format
            schema: JSON schema for validation (JSON format only)

        Returns:
            ParsedOutput with parsed data and metadata
        """
        if output_format == OutputFormat.RAW:
            return ParsedOutput(
                format=output_format,
                raw=content,
                valid=True,
            )

        elif output_format == OutputFormat.JSON:
            return self._parse_json(content, schema)

        elif output_format == OutputFormat.MARKDOWN:
            return self._parse_markdown(content)

        elif output_format == OutputFormat.STRUCTURED:
            return self._parse_structured(content, schema)

        else:
            return ParsedOutput(
                format=output_format,
                raw=content,
                valid=False,
                error=f"Unknown format: {output_format}",
            )

    def _parse_json(self, content: str, schema: dict | None) -> ParsedOutput:
        """Parse JSON output, optionally validating against schema."""
        try:
            # Try to extract JSON from markdown code blocks
            json_content = self._extract_json(content)
            data = json.loads(json_content)

            # Validate schema if provided
            if schema:
                errors = self._validate_schema(data, schema)
                if errors:
                    return ParsedOutput(
                        format=OutputFormat.JSON,
                        raw=content,
                        structured=data,
                        valid=False,
                        error=f"Schema validation failed: {'; '.join(errors)}",
                    )

            return ParsedOutput(
                format=OutputFormat.JSON,
                raw=content,
                structured=data,
                valid=True,
            )

        except json.JSONDecodeError as e:
            return ParsedOutput(
                format=OutputFormat.JSON,
                raw=content,
                valid=False,
                error=f"JSON parse error: {e}",
            )

    def _extract_json(self, content: str) -> str:
        """Extract JSON from content, handling markdown code blocks."""
        content = content.strip()

        # Check for markdown code block
        json_block = re.search(r'```(?:json)?\s*\n(.*?)\n```', content, re.DOTALL)
        if json_block:
            return json_block.group(1).strip()

        # Check if content starts with { or [
        if content.startswith("{") or content.startswith("["):
            # Find the matching end
            try:
                json.loads(content)
                return content
            except json.JSONDecodeError:
                pass

        # Try to find balanced JSON object/array anywhere in content.
        # This avoids non-greedy regex truncation on nested structures.
        for candidate in self._iter_balanced_json_candidates(content):
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue

        return content

    def _iter_balanced_json_candidates(self, content: str):
        """Yield balanced JSON object/array substrings in source order."""
        for i, ch in enumerate(content):
            if ch not in "{[":
                continue
            end = self._find_balanced_end(content, i)
            if end is None:
                continue
            yield content[i:end + 1]

    def _find_balanced_end(self, text: str, start: int) -> int | None:
        """Find end index for a balanced JSON object/array starting at `start`."""
        opening = text[start]
        if opening not in "{[":
            return None

        stack = [opening]
        in_string = False
        escaped = False

        for i in range(start + 1, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue

            if ch in "{[":
                stack.append(ch)
                continue

            if ch in "}]":
                if not stack:
                    return None
                expected = "}" if stack[-1] == "{" else "]"
                if ch != expected:
                    return None
                stack.pop()
                if not stack:
                    return i

        return None

    def _validate_schema(self, data: Any, schema: dict) -> list[str]:
        """Validate data against JSON schema.

        Returns list of error messages, empty if valid.
        """
        try:
            from jsonschema import Draft202012Validator

            validator = Draft202012Validator(schema)
            return [
                self._format_jsonschema_error(error)
                for error in sorted(validator.iter_errors(data), key=lambda err: list(err.absolute_path))
            ]
        except ImportError:
            pass

        errors = []

        def validate(obj: Any, sch: dict, path: str = "") -> None:
            obj_type = sch.get("type")

            if obj_type == "object":
                if not isinstance(obj, dict):
                    errors.append(f"{path}: expected object, got {type(obj).__name__}")
                    return

                # Check required properties
                required = sch.get("required", [])
                for req in required:
                    if req not in obj:
                        errors.append(f"{path}.{req}: required property missing")

                # Validate properties
                properties = sch.get("properties", {})
                for key, value in obj.items():
                    if key in properties:
                        validate(value, properties[key], f"{path}.{key}")

            elif obj_type == "array":
                if not isinstance(obj, list):
                    errors.append(f"{path}: expected array, got {type(obj).__name__}")
                    return

                items_schema = sch.get("items")
                if items_schema:
                    for i, item in enumerate(obj):
                        validate(item, items_schema, f"{path}[{i}]")

            elif obj_type == "string":
                if not isinstance(obj, str):
                    errors.append(f"{path}: expected string, got {type(obj).__name__}")

            elif obj_type == "integer":
                if isinstance(obj, bool) or not isinstance(obj, int):
                    errors.append(f"{path}: expected integer, got {type(obj).__name__}")

            elif obj_type == "number":
                if not isinstance(obj, (int, float)):
                    errors.append(f"{path}: expected number, got {type(obj).__name__}")

            elif obj_type == "boolean":
                if not isinstance(obj, bool):
                    errors.append(f"{path}: expected boolean, got {type(obj).__name__}")

        validate(data, schema)
        return errors

    @staticmethod
    def _format_jsonschema_error(error: Any) -> str:
        path = ".".join(str(part) for part in error.absolute_path)
        if error.validator == "required":
            missing = ", ".join(str(item) for item in error.validator_value if item not in error.instance)
            target = f"{path}.{missing}" if path and missing else (path or "$")
            return f"{target}: required property missing"
        if error.validator == "type":
            expected = error.validator_value
            actual = type(error.instance).__name__
            label = path or "$"
            return f"{label}: expected {expected}, got {actual}"
        label = path or "$"
        return f"{label}: {error.message}"

    def _parse_markdown(self, content: str) -> ParsedOutput:
        """Parse markdown into sections."""
        sections: dict[str, str] = {}

        # Split by headers
        pattern = r'^(#{1,6})\s+(.+)$'
        lines = content.split('\n')

        current_section = "intro"
        current_content: list[str] = []

        for line in lines:
            match = re.match(pattern, line)
            if match:
                # Save previous section
                if current_content:
                    sections[current_section] = '\n'.join(current_content).strip()
                current_section = match.group(2).strip().lower().replace(' ', '_')
                current_content = []
            else:
                current_content.append(line)

        # Save last section
        if current_content:
            sections[current_section] = '\n'.join(current_content).strip()

        # Extract structured data from markdown
        structured = self._extract_markdown_data(content, sections)

        return ParsedOutput(
            format=OutputFormat.MARKDOWN,
            raw=content,
            structured=structured,
            sections=sections,
            valid=True,
        )

    def _extract_markdown_data(self, content: str, sections: dict[str, str]) -> dict[str, Any]:
        """Extract structured data from markdown content."""
        result: dict[str, Any] = {}

        # Extract code blocks
        code_blocks = re.findall(r'```(\w*)\s*\n(.*?)\n```', content, re.DOTALL)
        if code_blocks:
            result["code_blocks"] = [
                {"language": lang or "text", "code": code}
                for lang, code in code_blocks
            ]

        # Extract links
        links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)
        if links:
            result["links"] = [{"text": text, "url": url} for text, url in links]

        # Extract lists (simple detection)
        for section_name, section_content in sections.items():
            lines = section_content.split('\n')
            list_items = [line[2:].strip() for line in lines if line.strip().startswith('- ')]
            if list_items:
                result[f"{section_name}_list"] = list_items

        return result

    def _parse_structured(self, content: str, template: dict | None) -> ParsedOutput:
        """Parse structured output according to template.

        Template defines expected variables to extract.
        """
        if not template:
            return ParsedOutput(
                format=OutputFormat.STRUCTURED,
                raw=content,
                valid=False,
                error="No template provided for structured output",
            )

        structured: dict[str, Any] = {}

        # Extract variables from template
        var_pattern = re.compile(r'\{\{(\w+)\}\}')
        for key in template:
            if isinstance(template[key], str):
                vars_found = var_pattern.findall(template[key])
                for var in vars_found:
                    if var not in structured:
                        structured[var] = None

        # Try to extract values from content
        # This is a simple heuristic; real implementation could use LLM
        for var in structured:
            # Look for patterns like "var: value" or "var = value"
            patterns = [
                rf'{var}\s*[:=]\s*(.+)',
                rf'\*\*{var}\*\*[:\s]*(.+)',
                rf'###\s*{var}\s*\n(.+)',
            ]
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
                if match:
                    structured[var] = match.group(1).strip()
                    break

        return ParsedOutput(
            format=OutputFormat.STRUCTURED,
            raw=content,
            structured=structured,
            valid=True,
        )


def detect_format(content: str) -> OutputFormat:
    """Auto-detect output format from content.

    Args:
        content: Output content to analyze

    Returns:
        Detected OutputFormat
    """
    content = content.strip()

    # Check for JSON
    if content.startswith('{') or content.startswith('['):
        try:
            json.loads(content)
            return OutputFormat.JSON
        except json.JSONDecodeError:
            pass

    # Check for JSON in code block
    if '```json' in content:
        return OutputFormat.JSON

    # Check for markdown headers
    if re.search(r'^#{1,6}\s+', content, re.MULTILINE):
        return OutputFormat.MARKDOWN

    # Default to raw
    return OutputFormat.RAW


def format_output(
    content: str,
    output_format: OutputFormat = OutputFormat.RAW,
    schema: dict | None = None,
) -> ParsedOutput:
    """Convenience function to format output.

    Args:
        content: Raw output content
        output_format: Desired output format
        schema: Optional JSON schema for validation

    Returns:
        ParsedOutput with formatted content
    """
    parser = OutputParser()
    return parser.parse(content, output_format, schema)
